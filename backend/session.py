"""
Active session management.

An "ActiveSession" is the in-memory half of a workspace:
  - the live R subprocess
  - the set of variable names currently loaded in that subprocess

The durable half lives in SQLite (see db.py):
  - uploaded file metadata + paths on disk
  - full conversation history
  - all runs with code/output/artifacts

On server restart the R process is gone, but files are on disk.
ensure_files_loaded() reloads them transparently before any operation.
"""

from dataclasses import dataclass, field
from typing import Any

import db
from agent import load_config
from r_session import RSession


def _make_executor(language: str = "r") -> Any:
    """Return RSession or PythonSession for the given language."""
    if language == "python":
        from python_session import PythonSession  # noqa: PLC0415
        return PythonSession()
    return RSession()


def ensure_language(s: "ActiveSession", language: str) -> None:
    """Swap executor if the workspace language has changed since session creation.
    Clears loaded_vars so ensure_files_loaded() will reload into the new executor.
    """
    from python_session import PythonSession  # noqa: PLC0415
    is_python  = isinstance(s.r, PythonSession)
    need_python = language == "python"
    if is_python != need_python:
        s.r.close()
        s.r = _make_executor(language)
        s.loaded_vars.clear()


@dataclass
class ActiveSession:
    workspace_id: str
    r: Any = None  # RSession | PythonSession
    loaded_vars: set[str] = field(default_factory=set)
    streaming: bool = False  # True while any SSE stream is active for this workspace
    abort_event: Any = field(default_factory=lambda: None)  # asyncio.Event, set lazily

    def get_abort_event(self):
        """Return (and lazily create) the asyncio.Event for this session."""
        if self.abort_event is None:
            import asyncio
            self.abort_event = asyncio.Event()
        return self.abort_event

    def __post_init__(self):
        if self.r is None:
            config = load_config()
            self.r = _make_executor(config.get("language", "r").lower())

    def ensure_files_loaded(self):
        """Reload any workspace files not currently in the R subprocess."""
        for f in db.get_files(self.workspace_id):
            var = f["var_name"]
            if var not in self.loaded_vars:
                result = self.r.load_file(f["file_path"], var)
                if not result.get("error"):
                    self.loaded_vars.add(var)

    def history(self) -> list[dict]:
        """Return [{role, content}] suitable for the agent prompt."""
        return [
            {"role": m["role"], "content": m["content"]}
            for m in db.get_messages(self.workspace_id)
        ]

    def operation_log_dicts(self) -> list[dict]:
        """Summarise past runs for the agent context."""
        return [
            {
                "task":    r["prompt"],
                "code":    r["edited_code"] or r["code"] or "",
                "success": bool(r["success"]),
            }
            for r in db.get_runs(self.workspace_id)
        ]

    def close(self):
        self.r.close()


# ── In-memory store of live R subprocesses ────────────────────────────────────

_active: dict[str, ActiveSession] = {}


def get_or_create(workspace_id: str | None) -> ActiveSession:
    """Return an existing ActiveSession or create a new workspace + session."""
    if workspace_id and workspace_id in _active:
        return _active[workspace_id]

    # If a workspace_id was supplied but the session isn't in memory,
    # verify it exists in the DB (server restart case).
    if workspace_id and db.get_workspace(workspace_id):
        s = ActiveSession(workspace_id=workspace_id)
        _active[workspace_id] = s
        return s

    # Create a brand-new workspace.
    wid = db.create_workspace()
    s = ActiveSession(workspace_id=wid)
    _active[wid] = s
    return s


def get(workspace_id: str) -> ActiveSession | None:
    if workspace_id in _active:
        return _active[workspace_id]
    # Server-restart recovery: workspace exists in DB but not in memory yet.
    if db.get_workspace(workspace_id):
        s = ActiveSession(workspace_id=workspace_id)
        _active[workspace_id] = s
        return s
    return None


def get_active(workspace_id: str) -> ActiveSession | None:
    """Return only an already-running session; never start a new executor."""
    return _active.get(workspace_id)


def delete(workspace_id: str):
    s = _active.pop(workspace_id, None)
    if s:
        s.close()
