"""
SQLite persistence layer for vibalytics.

Schema:
  workspaces       — named, persistent workspaces
  files            — uploaded datasets (metadata + path on disk)
  runs             — every prompt→code→output cycle
  artifacts        — plots / exported tables / scripts (BLOBs)
  messages         — full conversation history per workspace
  dataset_versions — immutable snapshots of each dataset (for versioning)

Design notes:
  - Per-call connections; check_same_thread=False so any thread can open one.
  - WAL mode for better concurrent read performance.
  - No ORM — plain sqlite3 so the schema stays readable.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH  = DATA_DIR / "vibalytics.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS files (
                id            TEXT PRIMARY KEY,
                workspace_id  TEXT NOT NULL REFERENCES workspaces(id),
                var_name      TEXT NOT NULL,
                original_name TEXT NOT NULL,
                file_path     TEXT NOT NULL,
                nrow          INTEGER NOT NULL,
                col_schema    TEXT NOT NULL,
                uploaded_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                id            TEXT PRIMARY KEY,
                workspace_id  TEXT NOT NULL REFERENCES workspaces(id),
                prompt        TEXT NOT NULL,
                agent         TEXT NOT NULL DEFAULT '',
                code          TEXT,
                edited_code   TEXT,
                output        TEXT,
                error         TEXT,
                success       INTEGER NOT NULL DEFAULT 0,
                active_files  TEXT NOT NULL DEFAULT '[]',
                duration_ms   INTEGER,
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                id            TEXT PRIMARY KEY,
                run_id        TEXT NOT NULL REFERENCES runs(id),
                artifact_type TEXT NOT NULL,
                data          BLOB NOT NULL,
                mime_type     TEXT NOT NULL,
                label         TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id           TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                role         TEXT NOT NULL,
                content      TEXT NOT NULL,
                run_id       TEXT,
                created_at   TEXT NOT NULL
            );
        """)
        con.executescript("""
            CREATE TABLE IF NOT EXISTS workflows (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                code       TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)

        con.executescript("""
            CREATE TABLE IF NOT EXISTS dataset_versions (
                id           TEXT PRIMARY KEY,
                file_id      TEXT NOT NULL REFERENCES files(id),
                version_num  INTEGER NOT NULL,
                file_path    TEXT NOT NULL,
                nrow         INTEGER NOT NULL,
                description  TEXT NOT NULL DEFAULT '',
                run_id       TEXT,
                is_original  INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL
            );
        """)

        # v2 migrations — safe to run on existing databases
        for sql in [
            "ALTER TABLE files ADD COLUMN col_stats TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE runs ADD COLUMN parent_run_id TEXT",
            "ALTER TABLE runs ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE runs ADD COLUMN agent_text TEXT",
            "ALTER TABLE runs ADD COLUMN context_snapshot TEXT",
            "ALTER TABLE runs ADD COLUMN trace_steps TEXT",
            "ALTER TABLE artifacts ADD COLUMN label TEXT NOT NULL DEFAULT ''",
            # v3 migrations — dataset versioning
            "ALTER TABLE files ADD COLUMN current_version_id TEXT",
            "ALTER TABLE files ADD COLUMN version_num INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE runs ADD COLUMN pending_proposals TEXT NOT NULL DEFAULT '[]'",
            # v4 migrations — workflow input signature
            "ALTER TABLE workflows ADD COLUMN input_vars TEXT NOT NULL DEFAULT '[]'",
            # v5 migrations — per-run dataset version provenance
            "ALTER TABLE runs ADD COLUMN active_file_versions TEXT NOT NULL DEFAULT '{}'",
            # v6 migrations — run summary + soft-delete
            "ALTER TABLE runs ADD COLUMN summary TEXT",
            "ALTER TABLE files ADD COLUMN archived_at TEXT",
            # v7 migrations — file notes (schema memory)
            "ALTER TABLE files ADD COLUMN notes TEXT NOT NULL DEFAULT ''",
            # v8 migrations — soft-reject proposals (undo support)
            "ALTER TABLE runs ADD COLUMN rejected_proposals TEXT NOT NULL DEFAULT '[]'",
            # v9 migrations — per-run language for provenance
            "ALTER TABLE runs ADD COLUMN language TEXT NOT NULL DEFAULT 'r'",
            # v10 migrations — per-workspace language toggle
            "ALTER TABLE workspaces ADD COLUMN language TEXT NOT NULL DEFAULT 'r'",
            # v11 migrations — background job status (NULL=sync, 'running'/'done'/'error'=bg)
            "ALTER TABLE runs ADD COLUMN job_status TEXT",
            # v12 migrations — per-run reproducibility metadata
            "ALTER TABLE runs ADD COLUMN env_snapshot TEXT",
            # v14 migrations — auto-retry first-attempt error
            "ALTER TABLE runs ADD COLUMN first_attempt_error TEXT",
            # v15 migrations — per-workspace auto-approve (dangerous mode)
            "ALTER TABLE workspaces ADD COLUMN auto_approve INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                con.execute(sql)
            except Exception:
                pass  # column already exists

        # v13 — data contracts / assertions
        con.executescript("""
            CREATE TABLE IF NOT EXISTS assertions (
                id          TEXT PRIMARY KEY,
                file_id     TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                check_type  TEXT NOT NULL,
                column_name TEXT,
                params      TEXT NOT NULL DEFAULT '{}',
                enabled     INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS assertion_results (
                id              TEXT PRIMARY KEY,
                assertion_id    TEXT NOT NULL REFERENCES assertions(id) ON DELETE CASCADE,
                file_id         TEXT NOT NULL,
                run_id          TEXT,
                passed          INTEGER NOT NULL,
                failure_count   INTEGER NOT NULL DEFAULT 0,
                sample_failures TEXT NOT NULL DEFAULT '[]',
                checked_at      TEXT NOT NULL
            );
        """)


# ── Workspaces ────────────────────────────────────────────────────────────────

def create_workspace(name: str = "default") -> str:
    wid = uuid.uuid4().hex
    now = _now()
    with get_db() as con:
        con.execute(
            "INSERT INTO workspaces (id, name, created_at, updated_at) VALUES (?,?,?,?)",
            (wid, name, now, now),
        )
    return wid


def get_workspace(workspace_id: str) -> dict | None:
    with get_db() as con:
        row = con.execute(
            "SELECT * FROM workspaces WHERE id=?", (workspace_id,)
        ).fetchone()
    return dict(row) if row else None


def touch_workspace(workspace_id: str):
    with get_db() as con:
        con.execute(
            "UPDATE workspaces SET updated_at=? WHERE id=?",
            (_now(), workspace_id),
        )


def rename_workspace(workspace_id: str, name: str):
    with get_db() as con:
        con.execute(
            "UPDATE workspaces SET name=?, updated_at=? WHERE id=?",
            (name, _now(), workspace_id),
        )


def set_workspace_language(workspace_id: str, language: str):
    with get_db() as con:
        con.execute(
            "UPDATE workspaces SET language=?, updated_at=? WHERE id=?",
            (language, _now(), workspace_id),
        )


def set_workspace_auto_approve(workspace_id: str, enabled: bool):
    with get_db() as con:
        con.execute(
            "UPDATE workspaces SET auto_approve=?, updated_at=? WHERE id=?",
            (1 if enabled else 0, _now(), workspace_id),
        )


def list_workspaces() -> list[dict]:
    with get_db() as con:
        rows = con.execute(
            "SELECT * FROM workspaces ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Files ─────────────────────────────────────────────────────────────────────

def upsert_file(
    workspace_id: str,
    var_name: str,
    original_name: str,
    file_path: str,
    nrow: int,
    schema: dict,
    stats: dict | None = None,
) -> str:
    """Insert or replace a file record (same var_name replaces old entry)."""
    stats_json = json.dumps(stats or {})
    with get_db() as con:
        existing = con.execute(
            "SELECT id FROM files WHERE workspace_id=? AND var_name=?",
            (workspace_id, var_name),
        ).fetchone()
        if existing:
            fid = existing["id"]
            con.execute(
                """UPDATE files
                   SET original_name=?, file_path=?, nrow=?, col_schema=?, col_stats=?, uploaded_at=?
                   WHERE id=?""",
                (original_name, file_path, nrow, json.dumps(schema), stats_json, _now(), fid),
            )
        else:
            fid = uuid.uuid4().hex
            con.execute(
                """INSERT INTO files
                   (id, workspace_id, var_name, original_name, file_path, nrow, col_schema, col_stats, uploaded_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (fid, workspace_id, var_name, original_name, file_path, nrow,
                 json.dumps(schema), stats_json, _now()),
            )
    return fid


def get_file(file_id: str) -> dict | None:
    with get_db() as con:
        row = con.execute(
            """SELECT f.*, dv.version_num AS current_version_seq
               FROM files f
               LEFT JOIN dataset_versions dv ON dv.id = f.current_version_id
               WHERE f.id=?""",
            (file_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["col_schema"] = json.loads(d["col_schema"])
    d["col_stats"]  = json.loads(d.get("col_stats") or "{}")
    return d


def get_files(workspace_id: str, include_archived: bool = False) -> list[dict]:
    with get_db() as con:
        if include_archived:
            rows = con.execute(
                """SELECT f.*, dv.version_num AS current_version_seq
                   FROM files f
                   LEFT JOIN dataset_versions dv ON dv.id = f.current_version_id
                   WHERE f.workspace_id=? ORDER BY f.uploaded_at""",
                (workspace_id,),
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT f.*, dv.version_num AS current_version_seq
                   FROM files f
                   LEFT JOIN dataset_versions dv ON dv.id = f.current_version_id
                   WHERE f.workspace_id=? AND f.archived_at IS NULL ORDER BY f.uploaded_at""",
                (workspace_id,),
            ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["col_schema"] = json.loads(d["col_schema"])
        d["col_stats"]  = json.loads(d.get("col_stats") or "{}")
        result.append(d)
    return result


# ── Runs ──────────────────────────────────────────────────────────────────────

def create_run(
    workspace_id: str,
    prompt: str,
    agent: str,
    active_files: list[str],
    parent_run_id: str | None = None,
    version: int = 1,
    code: str | None = None,
    active_file_versions: dict | None = None,
    language: str = "r",
    job_status: str | None = None,
) -> str:
    rid = uuid.uuid4().hex
    with get_db() as con:
        con.execute(
            """INSERT INTO runs
               (id, workspace_id, prompt, agent, active_files, parent_run_id, version, code,
                active_file_versions, language, job_status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, workspace_id, prompt, agent, json.dumps(active_files),
             parent_run_id, version, code,
             json.dumps(active_file_versions or {}), language, job_status, _now()),
        )
    return rid


def update_run(run_id: str, **kwargs):
    """Update arbitrary run columns. Accepted keys: code, edited_code, output,
    error, success, duration_ms, agent_text, context_snapshot, trace_steps, summary."""
    allowed = {
        "code", "edited_code", "output", "error", "success", "duration_ms",
        "agent_text", "context_snapshot", "trace_steps", "pending_proposals",
        "rejected_proposals", "summary", "language", "job_status", "env_snapshot",
        "first_attempt_error",
    }
    sets = {k: v for k, v in kwargs.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k}=?" for k in sets)
    vals = list(sets.values()) + [run_id]
    with get_db() as con:
        con.execute(f"UPDATE runs SET {cols} WHERE id=?", vals)


def get_run(run_id: str) -> dict | None:
    with get_db() as con:
        row = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["active_files"] = json.loads(d["active_files"])
    try:
        d["active_file_versions"] = json.loads(d.get("active_file_versions") or "{}")
    except Exception:
        d["active_file_versions"] = {}
    if d.get("trace_steps"):
        try:
            d["trace_steps"] = json.loads(d["trace_steps"])
        except Exception:
            d["trace_steps"] = []
    return d


def get_runs(workspace_id: str) -> list[dict]:
    with get_db() as con:
        rows = con.execute(
            "SELECT * FROM runs WHERE workspace_id=? ORDER BY created_at",
            (workspace_id,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["active_files"] = json.loads(d["active_files"])
        result.append(d)
    return result


# ── Artifacts ─────────────────────────────────────────────────────────────────

def create_artifact(run_id: str, artifact_type: str, data: bytes, mime_type: str, label: str = "") -> str:
    aid = uuid.uuid4().hex
    with get_db() as con:
        con.execute(
            """INSERT INTO artifacts (id, run_id, artifact_type, data, mime_type, label, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (aid, run_id, artifact_type, data, mime_type, label, _now()),
        )
    return aid


def get_artifact(artifact_id: str) -> dict | None:
    with get_db() as con:
        row = con.execute(
            "SELECT * FROM artifacts WHERE id=?", (artifact_id,)
        ).fetchone()
    return dict(row) if row else None


def get_run_artifacts(run_id: str) -> list[dict]:
    with get_db() as con:
        rows = con.execute(
            "SELECT * FROM artifacts WHERE run_id=? ORDER BY created_at",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_file(file_id: str) -> dict | None:
    with get_db() as con:
        row = con.execute(
            """SELECT f.*, dv.version_num AS current_version_seq
               FROM files f
               LEFT JOIN dataset_versions dv ON dv.id = f.current_version_id
               WHERE f.id=?""",
            (file_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["col_schema"] = json.loads(d["col_schema"])
    d["col_stats"]  = json.loads(d.get("col_stats") or "{}")
    return d


def set_file_notes(file_id: str, notes: str):
    with get_db() as con:
        con.execute("UPDATE files SET notes=? WHERE id=?", (notes, file_id))


def archive_file(file_id: str):
    with get_db() as con:
        con.execute("UPDATE files SET archived_at=? WHERE id=?", (_now(), file_id))


def restore_file(file_id: str):
    with get_db() as con:
        con.execute("UPDATE files SET archived_at=NULL WHERE id=?", (file_id,))


def hard_delete_file(file_id: str):
    with get_db() as con:
        # Collect version file paths before deleting rows
        version_rows = con.execute(
            "SELECT file_path FROM dataset_versions WHERE file_id=?", (file_id,)
        ).fetchall()
        file_row = con.execute(
            "SELECT file_path FROM files WHERE id=?", (file_id,)
        ).fetchone()

        # Delete DB rows
        con.execute("DELETE FROM dataset_versions WHERE file_id=?", (file_id,))
        con.execute("DELETE FROM files WHERE id=?", (file_id,))

    # Remove version files from disk (outside the DB transaction)
    for row in version_rows:
        try:
            Path(row["file_path"]).unlink(missing_ok=True)
        except Exception:
            pass
    # Remove the original uploaded file
    if file_row:
        try:
            Path(file_row["file_path"]).unlink(missing_ok=True)
        except Exception:
            pass


# Keep old name as alias for backwards compatibility
def delete_file(file_id: str):
    hard_delete_file(file_id)


# ── Dataset versions ──────────────────────────────────────────────────────────

def create_dataset_version(
    file_id: str,
    version_num: int,
    file_path: str,
    nrow: int,
    description: str = "",
    run_id: str | None = None,
    is_original: bool = False,
) -> str:
    vid = uuid.uuid4().hex
    with get_db() as con:
        con.execute(
            """INSERT INTO dataset_versions
               (id, file_id, version_num, file_path, nrow, description, run_id, is_original, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (vid, file_id, version_num, file_path, nrow, description,
             run_id, int(is_original), _now()),
        )
    return vid


def get_version_nrow(file_id: str, version_num: int) -> int | None:
    """Return the nrow recorded for a specific version, or None if not found."""
    with get_db() as con:
        row = con.execute(
            "SELECT nrow FROM dataset_versions WHERE file_id=? AND version_num=?",
            (file_id, version_num),
        ).fetchone()
    return dict(row)["nrow"] if row else None


def get_dataset_versions(file_id: str) -> list[dict]:
    with get_db() as con:
        rows = con.execute(
            "SELECT * FROM dataset_versions WHERE file_id=? ORDER BY version_num",
            (file_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_run_produced_versions(run_id: str) -> list[dict]:
    """Return dataset versions created when proposals from this run were accepted."""
    with get_db() as con:
        rows = con.execute(
            """SELECT dv.id, dv.version_num, dv.nrow, dv.description, f.var_name
               FROM dataset_versions dv
               JOIN files f ON f.id = dv.file_id
               WHERE dv.run_id = ?
               ORDER BY dv.created_at""",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_next_version_num(file_id: str) -> int:
    with get_db() as con:
        row = con.execute(
            "SELECT MAX(version_num) as max_n FROM dataset_versions WHERE file_id=?",
            (file_id,),
        ).fetchone()
    return (row["max_n"] or 0) + 1


def set_current_version(
    file_id: str, version_id: str, file_path: str, nrow: int,
) -> None:
    """Update the file's current version pointer and increment version_num."""
    with get_db() as con:
        con.execute(
            "UPDATE files SET current_version_id=?, file_path=?, nrow=?, version_num=version_num+1 WHERE id=?",
            (version_id, file_path, nrow, file_id),
        )


def init_file_version(file_id: str, version_id: str) -> None:
    """Set current_version_id on a newly uploaded file (no path/nrow change)."""
    with get_db() as con:
        con.execute(
            "UPDATE files SET current_version_id=? WHERE id=?",
            (version_id, file_id),
        )


def prune_dataset_versions(file_id: str) -> None:
    """Keep original + the 6 most recent non-original versions (7 max total)."""
    with get_db() as con:
        rows = con.execute(
            """SELECT id, file_path FROM dataset_versions
               WHERE file_id=? AND is_original=0 ORDER BY version_num ASC""",
            (file_id,),
        ).fetchall()
    to_delete = rows[:-6] if len(rows) > 6 else []
    with get_db() as con:
        for row in to_delete:
            try:
                Path(row["file_path"]).unlink(missing_ok=True)
            except Exception:
                pass
            con.execute("DELETE FROM dataset_versions WHERE id=?", (row["id"],))


# ── Workflows ─────────────────────────────────────────────────────────────────

def create_workflow(name: str, code: str, input_vars: list | None = None) -> dict:
    wid = uuid.uuid4().hex
    now = _now()
    vars_json = json.dumps(input_vars or [])
    with get_db() as con:
        con.execute(
            "INSERT INTO workflows (id, name, code, input_vars, created_at) VALUES (?,?,?,?,?)",
            (wid, name, code, vars_json, now),
        )
    return {"id": wid, "name": name, "code": code, "input_vars": input_vars or [], "created_at": now}


def list_workflows() -> list[dict]:
    with get_db() as con:
        rows = con.execute(
            "SELECT * FROM workflows ORDER BY created_at DESC"
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["input_vars"] = json.loads(d.get("input_vars") or "[]")
        result.append(d)
    return result


def get_workflow(workflow_id: str) -> dict | None:
    with get_db() as con:
        row = con.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["input_vars"] = json.loads(d.get("input_vars") or "[]")
    return d


def delete_workflow(workflow_id: str):
    with get_db() as con:
        con.execute("DELETE FROM workflows WHERE id=?", (workflow_id,))


# ── Messages ──────────────────────────────────────────────────────────────────

def add_message(workspace_id: str, role: str, content: str, run_id: str | None = None):
    mid = uuid.uuid4().hex
    with get_db() as con:
        con.execute(
            """INSERT INTO messages (id, workspace_id, role, content, run_id, created_at)
               VALUES (?,?,?,?,?,?)""",
            (mid, workspace_id, role, content, run_id, _now()),
        )


def get_messages(workspace_id: str) -> list[dict]:
    with get_db() as con:
        rows = con.execute(
            "SELECT * FROM messages WHERE workspace_id=? ORDER BY created_at",
            (workspace_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Storage / cleanup ──────────────────────────────────────────────────────────

def get_workspace_storage_stats(workspace_id: str) -> dict:
    """Return counts used by the storage cleanup panel."""
    with get_db() as con:
        n_messages = con.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()["n"]

        n_archived = con.execute(
            "SELECT COUNT(*) AS n FROM files WHERE workspace_id=? AND archived_at IS NOT NULL",
            (workspace_id,),
        ).fetchone()["n"]

        # Count versions that prune_dataset_versions would remove (keep orig + 6 newest)
        file_ids = [r["id"] for r in con.execute(
            "SELECT id FROM files WHERE workspace_id=?", (workspace_id,)
        ).fetchall()]
        n_old_versions = 0
        for fid in file_ids:
            n_non_orig = con.execute(
                "SELECT COUNT(*) AS n FROM dataset_versions WHERE file_id=? AND is_original=0",
                (fid,),
            ).fetchone()["n"]
            if n_non_orig > 6:
                n_old_versions += n_non_orig - 6

        art = con.execute(
            """SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(data)), 0) AS total_bytes
               FROM artifacts
               WHERE run_id IN (SELECT id FROM runs WHERE workspace_id=?)""",
            (workspace_id,),
        ).fetchone()

        n_runs = con.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()["n"]

    return {
        "chat_messages":       n_messages,
        "archived_files":      n_archived,
        "old_versions":        n_old_versions,
        "run_artifacts":       art["n"],
        "run_artifacts_bytes": art["total_bytes"],
        "runs":                n_runs,
    }


def delete_chat_history(workspace_id: str) -> int:
    with get_db() as con:
        n = con.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE workspace_id=?", (workspace_id,)
        ).fetchone()["n"]
        con.execute("DELETE FROM messages WHERE workspace_id=?", (workspace_id,))
    return n


def delete_archived_files_permanent(workspace_id: str) -> int:
    with get_db() as con:
        rows = con.execute(
            "SELECT id FROM files WHERE workspace_id=? AND archived_at IS NOT NULL",
            (workspace_id,),
        ).fetchall()
    for row in rows:
        hard_delete_file(row["id"])
    return len(rows)


def prune_all_dataset_versions(workspace_id: str) -> int:
    """Prune old versions for every file in the workspace. Returns count deleted."""
    with get_db() as con:
        file_ids = [r["id"] for r in con.execute(
            "SELECT id FROM files WHERE workspace_id=?", (workspace_id,)
        ).fetchall()]
    total = 0
    for fid in file_ids:
        with get_db() as con:
            rows = con.execute(
                """SELECT id, file_path FROM dataset_versions
                   WHERE file_id=? AND is_original=0 ORDER BY version_num ASC""",
                (fid,),
            ).fetchall()
        to_delete = rows[:-6] if len(rows) > 6 else []
        for row in to_delete:
            try:
                Path(row["file_path"]).unlink(missing_ok=True)
            except Exception:
                pass
        if to_delete:
            with get_db() as con:
                placeholders = ",".join("?" * len(to_delete))
                con.execute(
                    f"DELETE FROM dataset_versions WHERE id IN ({placeholders})",
                    [r["id"] for r in to_delete],
                )
        total += len(to_delete)
    return total


def delete_run_artifacts(workspace_id: str) -> int:
    """Delete all artifact blobs for a workspace. Returns count deleted."""
    with get_db() as con:
        n = con.execute(
            """SELECT COUNT(*) AS n FROM artifacts
               WHERE run_id IN (SELECT id FROM runs WHERE workspace_id=?)""",
            (workspace_id,),
        ).fetchone()["n"]
        con.execute(
            """DELETE FROM artifacts
               WHERE run_id IN (SELECT id FROM runs WHERE workspace_id=?)""",
            (workspace_id,),
        )
    return n


def delete_run_history(workspace_id: str) -> int:
    """Delete all runs and their artifacts. Returns run count."""
    with get_db() as con:
        run_ids = [r["id"] for r in con.execute(
            "SELECT id FROM runs WHERE workspace_id=?", (workspace_id,)
        ).fetchall()]
        n = len(run_ids)
        if run_ids:
            ph = ",".join("?" * n)
            con.execute(f"DELETE FROM artifacts WHERE run_id IN ({ph})", run_ids)
        con.execute("DELETE FROM runs WHERE workspace_id=?", (workspace_id,))
    return n


def delete_workspace(workspace_id: str) -> None:
    """Permanently delete a workspace and all its data from DB. Disk cleanup is
    done by the caller (main.py knows FILES_DIR)."""
    with get_db() as con:
        file_rows = con.execute(
            "SELECT id, file_path FROM files WHERE workspace_id=?", (workspace_id,)
        ).fetchall()
        file_ids = [r["id"] for r in file_rows]

        if file_ids:
            ph = ",".join("?" * len(file_ids))
            version_paths = [
                r["file_path"] for r in con.execute(
                    f"SELECT file_path FROM dataset_versions WHERE file_id IN ({ph})",
                    file_ids,
                ).fetchall()
            ]
            con.execute(f"DELETE FROM dataset_versions WHERE file_id IN ({ph})", file_ids)
        else:
            version_paths = []

        run_ids = [r["id"] for r in con.execute(
            "SELECT id FROM runs WHERE workspace_id=?", (workspace_id,)
        ).fetchall()]
        if run_ids:
            ph2 = ",".join("?" * len(run_ids))
            con.execute(f"DELETE FROM artifacts WHERE run_id IN ({ph2})", run_ids)

        con.execute("DELETE FROM runs WHERE workspace_id=?",    (workspace_id,))
        con.execute("DELETE FROM messages WHERE workspace_id=?", (workspace_id,))
        con.execute("DELETE FROM files WHERE workspace_id=?",    (workspace_id,))
        con.execute("DELETE FROM workspaces WHERE id=?",         (workspace_id,))

    # Disk cleanup
    for row in file_rows:
        try:
            Path(row["file_path"]).unlink(missing_ok=True)
        except Exception:
            pass
    for path in version_paths:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass


# ── Assertions ─────────────────────────────────────────────────────────────────

def get_assertion(assertion_id: str) -> dict | None:
    with get_db() as con:
        row = con.execute("SELECT * FROM assertions WHERE id=?", (assertion_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["params"] = json.loads(d.get("params") or "{}")
    return d


def get_assertions(file_id: str) -> list[dict]:
    with get_db() as con:
        rows = con.execute(
            "SELECT * FROM assertions WHERE file_id=? ORDER BY created_at",
            (file_id,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["params"] = json.loads(d.get("params") or "{}")
        result.append(d)
    return result


def create_assertion(
    file_id: str, name: str, check_type: str,
    column_name: str | None, params: dict, enabled: bool = True,
) -> str:
    aid = uuid.uuid4().hex
    with get_db() as con:
        con.execute(
            """INSERT INTO assertions (id, file_id, name, check_type, column_name, params, enabled, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (aid, file_id, name, check_type, column_name,
             json.dumps(params), 1 if enabled else 0, _now()),
        )
    return aid


def update_assertion(assertion_id: str, **kwargs) -> None:
    allowed = {"name", "check_type", "column_name", "params", "enabled"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    vals = []
    parts = []
    for k, v in updates.items():
        parts.append(f"{k}=?")
        vals.append(json.dumps(v) if k == "params" else (1 if v else 0) if k == "enabled" else v)
    with get_db() as con:
        con.execute(f"UPDATE assertions SET {', '.join(parts)} WHERE id=?", vals + [assertion_id])


def delete_assertion(assertion_id: str) -> None:
    with get_db() as con:
        con.execute("DELETE FROM assertions WHERE id=?", (assertion_id,))


def save_assertion_results(results: list[dict]) -> None:
    now = _now()
    with get_db() as con:
        for r in results:
            rid = uuid.uuid4().hex
            con.execute(
                """INSERT INTO assertion_results
                   (id, assertion_id, file_id, run_id, passed, failure_count, sample_failures, checked_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (rid, r["assertion_id"], r["file_id"], r.get("run_id"),
                 1 if r["passed"] else 0, r.get("failure_count", 0),
                 json.dumps([str(x) for x in r.get("sample_failures", [])]), now),
            )


def get_latest_assertion_results(file_id: str) -> dict[str, dict]:
    """Returns the most recent result for each assertion_id on this file.

    Uses MAX(rowid) as the tie-breaker so results within the same second are
    deterministic: the row inserted last always wins.
    """
    with get_db() as con:
        rows = con.execute(
            """SELECT ar.* FROM assertion_results ar
               INNER JOIN (
                 SELECT assertion_id, MAX(rowid) AS max_rowid
                 FROM assertion_results WHERE file_id=?
                 GROUP BY assertion_id
               ) latest ON ar.rowid = latest.max_rowid
               WHERE ar.file_id=?""",
            (file_id, file_id),
        ).fetchall()
    return {
        r["assertion_id"]: {
            **dict(r),
            "sample_failures": json.loads(r["sample_failures"] or "[]"),
        }
        for r in rows
    }
