"""
FastAPI backend for vibalytics.

Endpoints:
  POST   /session                       → create workspace, return workspace_id
  DELETE /session/:id                   → destroy session + R process
  GET    /workspaces                    → list all workspaces
  PATCH  /workspace/:id                 → rename workspace
  POST   /upload                        → persist file to disk, load into R
  POST   /chat/stream                   → SSE: agent → R execution → results
  GET    /context/:id                   → workspace name, files+stats, run count
  DELETE /history/:id                   → wipe chat history (keep data + runs)
  GET    /run/:id                       → run detail + artifact list
  PATCH  /run/:id                       → save edited_code
  POST   /run/:id/rerun                 → SSE: creates child run, re-executes code
  GET    /runs/:session_id              → lightweight run list for a workspace
  GET    /artifact/:id                  → serve a stored artifact (PNG, etc.)
  GET    /preview/:session_id/:var      → first N rows of a loaded variable
  GET    /export/:session_id            → ZIP of all scripts, outputs, plots
  GET    /health
"""

import asyncio
import base64
import csv as csv_mod
import hashlib
import io
import json
import os
import re
import queue as _queue
import shutil
import time
import uuid
import yaml
import zipfile
from dataclasses import dataclass, field as _dc_field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import session as store
from agent import (
    _detect_agent,
    build_prompt,
    build_retry_prompt,
    call_agent,
    extract_code,
    load_config,
    stream_agent,
)

db.init_db()

app = FastAPI(title="vibalytics", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FILES_DIR = Path(__file__).parent.parent / "data" / "files"
ATTACHMENTS_DIR = Path(__file__).parent.parent / "data" / "attachments"
FILES_DIR.mkdir(parents=True, exist_ok=True)
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)


def _file_sha256(path: str) -> str:
    """SHA-256 of a file's content, first 16 hex chars. Returns '?' on error."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception:
        return "?"


# ── Background job registry ───────────────────────────────────────────────────

@dataclass
class _Job:
    run_id:     str
    status:     str           # "running" | "done" | "error"
    events:     list = _dc_field(default_factory=list)  # raw SSE strings
    created_at: float = _dc_field(default_factory=time.monotonic)

_jobs: dict[str, _Job] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def sse(event_type: str, content: str) -> str:
    return f"data: {json.dumps({'type': event_type, 'content': content})}\n\n"


# Lines to suppress when streaming R output in real-time
_STREAM_SKIP_EXACT = {"null device", "1", ""}
_STREAM_SKIP_STARTS = ("__DL_",)

def _is_streamable(line: str) -> bool:
    s = line.strip()
    if s in _STREAM_SKIP_EXACT:
        return False
    if any(s.startswith(p) for p in _STREAM_SKIP_STARTS):
        return False
    return True


async def _with_stream_flag(session, gen):
    """Wrap any SSE async generator: set session.streaming for its lifetime."""
    session.streaming = True
    try:
        async for chunk in gen:
            yield chunk
    finally:
        session.streaming = False


async def _execute_streaming(r_session, code: str, timeout: float, loop) -> tuple:
    """
    Run r_session.execute in a thread while yielding output lines via an
    async generator.  Returns (async_gen, future) — caller must await future
    to get the final result dict after draining the generator.
    """
    line_q: _queue.Queue = _queue.Queue()

    future = loop.run_in_executor(
        None,
        lambda: r_session.execute(code, timeout, output_queue=line_q),
    )

    async def _stream():
        while True:
            # Poll the queue without blocking the event loop
            try:
                line = line_q.get_nowait()
            except _queue.Empty:
                if future.done():
                    # Drain anything left
                    while True:
                        try:
                            line = line_q.get_nowait()
                        except _queue.Empty:
                            return
                        if line is None:
                            return
                        if _is_streamable(line):
                            yield line
                    return
                await asyncio.sleep(0.05)
                continue
            if line is None:
                return
            if _is_streamable(line):
                yield line

    return _stream(), future


def sanitise_name(filename: str) -> str:
    stem = Path(filename).stem
    name = re.sub(r"[^a-zA-Z0-9_]", "_", stem)
    if name and name[0].isdigit():
        name = "df_" + name
    return name or "df"


def extract_summary(agent_text: str) -> str:
    """Extract a concise one-line summary from agent text.

    Strategy: strip code blocks, find first sentence of the first prose paragraph.
    Falls back to the full first paragraph (truncated) if no sentence boundary found.
    """
    if not agent_text:
        return ""
    # Strip code blocks
    clean = re.sub(r"```[\w]*\n[\s\S]*?```", "", agent_text)
    clean = re.sub(r"```[\s\S]*", "", clean)
    clean = clean.strip()
    # Find first non-empty paragraph
    for para in clean.split("\n\n"):
        p = para.strip()
        if p and len(p) > 10:
            # Try to extract the first complete sentence
            m = re.match(r"^(.{15,200}?[.!?])(?:\s|$)", p, re.DOTALL)
            if m:
                return m.group(1).strip()
            # No sentence boundary: use full paragraph up to 200 chars
            return p[:200] + ("…" if len(p) > 200 else "")
    return ""


# ── Session / Workspace ───────────────────────────────────────────────────────

@app.post("/session")
def create_session():
    s = store.get_or_create(None)
    return {"session_id": s.workspace_id}


@app.delete("/session/{session_id}")
def delete_session(session_id: str):
    store.delete(session_id)
    return {"ok": True}


@app.get("/workspaces")
def list_workspaces():
    return db.list_workspaces()


# ── Upload ────────────────────────────────────────────────────────────────────

@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    name: str = Form(default=""),
):
    s = store.get_or_create(session_id)

    var_name = name.strip() or sanitise_name(file.filename or "df")
    suffix   = Path(file.filename or "data.csv").suffix.lower()

    ws_dir = FILES_DIR / s.workspace_id
    ws_dir.mkdir(parents=True, exist_ok=True)
    dest = ws_dir / f"{var_name}{suffix}"

    contents = await file.read()
    dest.write_bytes(contents)

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, s.r.load_file, str(dest), var_name)

    if result.get("error"):
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=result["error"])

    s.loaded_vars.add(var_name)

    file_stats = s.r.stats.get(var_name, {})
    file_id = db.upsert_file(
        s.workspace_id, var_name, file.filename or var_name,
        str(dest), result["nrow"], result["schema"], file_stats,
    )

    # Create original version record (if not already present)
    existing_versions = db.get_dataset_versions(file_id)
    if not existing_versions:
        vid = db.create_dataset_version(
            file_id=file_id, version_num=1, file_path=str(dest),
            nrow=result["nrow"], description="Original upload", is_original=True,
        )
        db.init_file_version(file_id, vid)

    db.touch_workspace(s.workspace_id)

    return {
        "id":                  file_id,
        "session_id":          s.workspace_id,
        "name":                var_name,
        "original":            file.filename,
        "nrow":                result["nrow"],
        "schema":              result["schema"],
        "stats":               file_stats,
        "version_num":         1,
        "current_version_seq": 1,
        "notes":               "",
    }


# ── Chat attachments ──────────────────────────────────────────────────────────

@app.post("/workspace/{workspace_id}/chat/attachment")
async def upload_chat_attachment(workspace_id: str, file: UploadFile = File(...)):
    """
    Upload a file to attach to a chat message.
    Supported: images (PNG/JPG/GIF/WEBP) and PDFs.
    Returns {id, filename, mime_type, size} — pass id in ChatRequest.attachments.
    """
    if not db.get_workspace(workspace_id):
        raise HTTPException(404, "Workspace not found")

    ext  = Path(file.filename or "file").suffix.lower() or ".bin"
    aid  = uuid.uuid4().hex
    dest = ATTACHMENTS_DIR / workspace_id
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{aid}{ext}"

    content = await file.read()
    path.write_bytes(content)

    return {"id": aid, "filename": file.filename, "mime_type": file.content_type or "application/octet-stream", "size": len(content)}


def _strip_r_comments(code: str) -> str:
    """Remove R line comments (#...) and string literals to avoid false matches."""
    result = []
    i = 0
    n = len(code)
    while i < n:
        ch = code[i]
        # String literal: skip everything until matching unescaped quote
        if ch in ('"', "'"):
            quote = ch
            i += 1
            while i < n:
                if code[i] == '\\':
                    i += 2
                    continue
                if code[i] == quote:
                    i += 1
                    break
                i += 1
        # R comment: skip to end of line
        elif ch == '#':
            while i < n and code[i] != '\n':
                i += 1
        else:
            result.append(ch)
            i += 1
    return ''.join(result)


def _extract_r_packages(code: str) -> list[str]:
    """Return unique package names from library()/require()/requireNamespace() calls,
    ignoring occurrences inside comments or string literals."""
    from r_session import _BASE_PKGS
    clean = _strip_r_comments(code)
    pkgs: list[str] = []
    for pat in (
        r'\blibrary\s*\(\s*["\']?(\w[\w.]*)["\']?\s*\)',
        r'\brequire\s*\(\s*["\']?(\w[\w.]*)["\']?\s*\)',
        r'\brequireNamespace\s*\(\s*["\'](\w[\w.]*)["\']',
    ):
        pkgs.extend(re.findall(pat, clean))
    seen: set[str] = set()
    out: list[str] = []
    for p in pkgs:
        if p not in seen and p not in _BASE_PKGS:
            seen.add(p)
            out.append(p)
    return out


async def _ensure_packages(s, packages: list[str], loop) -> list[str]:
    """
    Install any missing packages. Returns list of packages that were installed.
    Runs install_package in a thread executor (blocking I/O).
    """
    missing = [p for p in packages
               if not await loop.run_in_executor(None, s.r.is_package_installed, p)]
    for pkg in missing:
        await loop.run_in_executor(None, s.r.install_package, pkg)
    return missing


def _resolve_attachments(workspace_id: str, attachment_ids: list[str]):
    """
    Resolve attachment IDs to (file_path, mime_type) tuples.
    Returns (image_paths, pdf_texts) where:
      image_paths — paths to pass as --file to the agent CLI
      pdf_texts   — extracted text from PDFs to inject into the prompt
    """
    image_paths: list[str] = []
    pdf_texts: list[str] = []
    att_dir = ATTACHMENTS_DIR / workspace_id
    if not att_dir.exists():
        return image_paths, pdf_texts

    for aid in attachment_ids:
        # Find file with this ID prefix
        matches = list(att_dir.glob(f"{aid}*"))
        if not matches:
            continue
        path = matches[0]
        ext  = path.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            image_paths.append(str(path))
        elif ext == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(str(path))
                pages  = "\n\n".join(p.extract_text() or "" for p in reader.pages)
                if pages.strip():
                    pdf_texts.append(f"[Attached PDF: {path.name}]\n{pages.strip()}")
            except Exception:
                pdf_texts.append(f"[Attached PDF: {path.name} — could not extract text]")
        else:
            # Generic text file
            try:
                text = path.read_text(errors="replace")
                if text.strip():
                    pdf_texts.append(f"[Attached file: {path.name}]\n{text.strip()}")
            except Exception:
                pass

    return image_paths, pdf_texts


# ── Chat (SSE streaming) ──────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str
    message: str
    active_files: list[str] | None = None
    attachments: list[str] | None = None   # attachment IDs from /chat/attachment


async def _analyze(s, req: ChatRequest, config: dict, pre_run_id: str | None = None):
    """
    Core SSE generator for a chat analysis run.
    Yields raw SSE strings.  Used by both /chat/stream (foreground) and
    /chat/background (buffered background task).

    pre_run_id — if provided, skip db.create_run() and use this id instead.
    The caller is responsible for creating the run with correct metadata before
    passing pre_run_id.
    """
    loop = asyncio.get_event_loop()
    trace_steps: list[str] = []

    def t(msg: str) -> str:
        trace_steps.append(msg)
        return sse("trace", msg)

    yield t("Loading files…")
    await loop.run_in_executor(None, s.ensure_files_loaded)

    task      = req.message
    agent_cmd = config.get("command", "")

    # Use caller-specified active files, or all files in workspace.
    all_files    = db.get_files(s.workspace_id)
    all_vars     = [f["var_name"] for f in all_files]
    active_files = req.active_files if req.active_files is not None else all_vars
    file_by_var  = {f["var_name"]: f for f in all_files}
    active_file_versions = {
        v: file_by_var[v].get("current_version_seq") or 1
        for v in active_files if v in file_by_var
    }

    ws_record    = db.get_workspace(s.workspace_id)
    language     = (ws_record.get("language") if ws_record else None) or config.get("language", "r")
    language     = language.lower()
    auto_approve = bool(ws_record.get("auto_approve", 0)) if ws_record else False
    store.ensure_language(s, language)

    # Resolve file attachments
    image_paths: list[str] = []
    attachment_context = ""
    if req.attachments:
        image_paths, pdf_texts = _resolve_attachments(s.workspace_id, req.attachments)
        if pdf_texts:
            attachment_context = "\n\n" + "\n\n".join(pdf_texts)

    # Reset abort event so a previous stop doesn't cancel this new run.
    abort_event = s.get_abort_event()
    abort_event.clear()

    if pre_run_id is not None:
        run_id = pre_run_id
    else:
        run_id = db.create_run(s.workspace_id, task, agent_cmd, active_files,
                               active_file_versions=active_file_versions,
                               language=language)
    start_ms = time.monotonic()

    # Determine which files are inactive and should be hidden during execution
    inactive_files = [f for f in all_files if f["var_name"] not in set(active_files) and f["var_name"] in s.loaded_vars]

    if inactive_files:
        await loop.run_in_executor(None, s.r.hide_vars, [f["var_name"] for f in inactive_files])
        for f in inactive_files:
            s.loaded_vars.discard(f["var_name"])

    yield sse("run_id", run_id)

    hint = f" + {len(image_paths)} image(s)" if image_paths else ""
    hint += f" + {len(req.attachments or []) - len(image_paths)} doc(s)" if (req.attachments and len(req.attachments) > len(image_paths)) else ""
    yield t(f"Building context ({len(active_files)} file(s){hint})…")
    file_notes = {f["var_name"]: f["notes"] for f in all_files if f.get("notes")}
    data_context = s.r.get_context(active_files if req.active_files else None, file_notes=file_notes)
    yield sse("context_snapshot", data_context)
    task_with_attachments = task + attachment_context
    prompt = build_prompt(task_with_attachments, data_context, s.history(), s.operation_log_dicts(),
                          history_messages=config.get("history_messages", 20),
                          language=language,
                          agent=_detect_agent(config.get("command", "")))

    # ── 1. Stream agent response ──────────────────────────────────────────
    yield t("Calling agent…")
    full_response = ""
    try:
        async for chunk in stream_agent(prompt, config,
                                        attachment_paths=image_paths or None,
                                        abort_event=abort_event):
            full_response += chunk
            yield sse("text", chunk)
    except RuntimeError as e:
        if inactive_files:
            await loop.run_in_executor(None, s.r.restore_vars, inactive_files)
            for f in inactive_files:
                s.loaded_vars.add(f["var_name"])
        db.update_run(run_id, error=str(e), success=0,
                      duration_ms=int((time.monotonic() - start_ms) * 1000),
                      agent_text=full_response.strip(),
                      context_snapshot=data_context,
                      trace_steps=json.dumps(trace_steps))
        yield sse("error", str(e))
        yield sse("done", json.dumps({"success": False}))
        return

    # Check if the user stopped the run mid-agent
    if abort_event.is_set():
        if inactive_files:
            await loop.run_in_executor(None, s.r.restore_vars, inactive_files)
            for f in inactive_files:
                s.loaded_vars.add(f["var_name"])
        db.update_run(run_id, error="Stopped by user", success=0,
                      duration_ms=int((time.monotonic() - start_ms) * 1000),
                      agent_text=full_response.strip(),
                      context_snapshot=data_context,
                      trace_steps=json.dumps(trace_steps))
        yield sse("stopped", "")
        yield sse("done", json.dumps({"success": False, "stopped": True}))
        return

    # ── 2. Extract code ───────────────────────────────────────────────────
    code = extract_code(full_response, config.get("code_fence") or language)
    summary = extract_summary(full_response)
    if not code:
        db.add_message(s.workspace_id, "user", task)
        db.add_message(s.workspace_id, "assistant", full_response.strip())
        db.update_run(run_id, success=1,
                      duration_ms=int((time.monotonic() - start_ms) * 1000),
                      agent_text=full_response.strip(),
                      context_snapshot=data_context,
                      trace_steps=json.dumps(trace_steps),
                      summary=summary)
        if summary:
            yield sse("summary", summary)
        yield sse("done", json.dumps({"success": True}))
        return

    db.update_run(run_id, code=code)
    yield sse("code", code)

    # ── 3. Auto-install missing R packages before execution ────────────────
    if language == "r":
        required_pkgs = _extract_r_packages(code)
        if required_pkgs:
            missing = [p for p in required_pkgs
                       if not await loop.run_in_executor(None, s.r.is_package_installed, p)]
            for pkg in missing:
                yield sse("installing", pkg)
                err = await loop.run_in_executor(None, s.r.install_package, pkg)
                if err:
                    yield sse("install_error", f"{pkg}: {err}")
                else:
                    yield sse("installed", pkg)

    # ── 4. Execute code (with real-time output streaming) ─────────────────
    yield t(f"Executing {language.upper()} code…")
    out_stream, r_future = await _execute_streaming(s.r, code, 60.0, loop)
    streamed_output = False
    async for line in out_stream:
        yield sse("output_chunk", line)
        streamed_output = True
    r_result = await r_future

    # ── 3b. Capture reproducibility snapshot (post-execution) ────────────
    # Runs after execute() so loaded packages include any library() calls in
    # user code.  Done in a thread to avoid blocking the event loop.
    try:
        env_meta = await loop.run_in_executor(None, s.r.get_env_snapshot)
        dataset_fps: dict = {}
        for v in active_files:
            if v in file_by_var:
                f = file_by_var[v]
                dataset_fps[v] = {
                    "version_id":  f.get("current_version_id") or "",
                    "version_num": f.get("current_version_seq") or 1,
                    "nrow":        f["nrow"],
                    "sha256":      await loop.run_in_executor(None, _file_sha256, f["file_path"]),
                }
        env_snapshot_dict = {
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "language":    language,
            "runtime":     env_meta.get("runtime", ""),
            "packages":    env_meta.get("packages", {}),
            "working_dir": str(FILES_DIR / s.workspace_id),
            "dataset_fingerprints": dataset_fps,
        }
        db.update_run(run_id, env_snapshot=json.dumps(env_snapshot_dict))
    except Exception:
        pass  # reproducibility metadata is best-effort; don't abort the run

    # ── 4. Retry once on error ────────────────────────────────────────────
    if r_result["error"]:
        # Emit as "retry_error" (not "error") so the frontend can display it
        # distinctly as a collapsed "first attempt failed" note rather than a
        # primary error block — only promoted to a real error if retry also fails.
        yield sse("retry_error", r_result["error"])
        db.update_run(run_id, first_attempt_error=r_result["error"])
        yield t(f"{language.upper()} error — retrying with fix…")

        retry_prompt = build_retry_prompt(task, code, r_result["error"], data_context,
                                          language=language)
        try:
            retry_response = await call_agent(retry_prompt, config, abort_event=abort_event)
            retry_code     = extract_code(retry_response, config.get("code_fence", "r"))
        except RuntimeError as e:
            if inactive_files:
                await loop.run_in_executor(None, s.r.restore_vars, inactive_files)
                for f in inactive_files:
                    s.loaded_vars.add(f["var_name"])
            yield sse("error", f"Retry failed: {e}")
            db.update_run(run_id, error=r_result["error"], success=0,
                          duration_ms=int((time.monotonic() - start_ms) * 1000),
                          agent_text=full_response.strip(),
                          context_snapshot=data_context,
                          trace_steps=json.dumps(trace_steps))
            db.add_message(s.workspace_id, "user", task)
            yield sse("done", json.dumps({"success": False}))
            return

        if retry_code and retry_code != code:
            code = retry_code
            db.update_run(run_id, code=code)
            yield sse("code", code)
            yield t(f"Executing fixed {language.upper()} code…")
            out_stream2, r_future2 = await _execute_streaming(s.r, code, 60.0, loop)
            async for line in out_stream2:
                yield sse("output_chunk", line)
                streamed_output = True
            r_result = await r_future2

    # ── 5. Send results ───────────────────────────────────────────────────
    if r_result["output"]:
        yield sse("output", r_result["output"])

    for plot_b64 in r_result["plots"]:
        plot_bytes = base64.b64decode(plot_b64)
        db.create_artifact(run_id, "plot", plot_bytes, "image/png")
        yield sse("plot", plot_b64)

    for table_html in r_result.get("tables", []):
        db.create_artifact(run_id, "table", table_html.encode(), "text/html")
        yield sse("table", table_html)

    for filename, data in r_result.get("exports", []):
        mime = "text/csv" if filename.lower().endswith(".csv") else "application/octet-stream"
        aid = db.create_artifact(run_id, "dataset", data, mime, label=filename)
        yield sse("export", json.dumps({"artifact_id": aid, "filename": filename}))

    if r_result["error"]:
        yield sse("error", r_result["error"])

    # ── 5b. Dataset version proposals (or auto-accept) ───────────────────
    if r_result.get("proposals"):
        all_files_now = db.get_files(s.workspace_id)
        stored_proposals = []
        for prop in r_result["proposals"]:
            file_rec    = next((f for f in all_files_now if f["var_name"] == prop["var_name"]), None)
            nrow_before = file_rec["nrow"] if file_rec else None
            prop_id     = uuid.uuid4().hex
            var_name    = prop["var_name"]

            if auto_approve:
                # Auto-accept: commit the change immediately without user review
                temp_path = Path(prop["file"])
                if temp_path.exists():
                    is_new = file_rec is None
                    if is_new:
                        fid = db.upsert_file(s.workspace_id, var_name, f"{var_name}.csv",
                                             str(temp_path), prop["nrow"], {}, None)
                        versions_dir = FILES_DIR / s.workspace_id / "versions" / fid
                        versions_dir.mkdir(parents=True, exist_ok=True)
                        new_path = versions_dir / "v1.csv"
                        shutil.copy(str(temp_path), str(new_path))
                        vid = db.create_dataset_version(
                            file_id=fid, version_num=1, file_path=str(new_path),
                            nrow=prop["nrow"],
                            description=prop.get("description", "auto-created"),
                            run_id=run_id, is_original=True,
                        )
                        db.set_current_version(fid, vid, str(new_path), prop["nrow"])
                    else:
                        fid          = file_rec["id"]
                        versions_dir = FILES_DIR / s.workspace_id / "versions" / fid
                        versions_dir.mkdir(parents=True, exist_ok=True)
                        version_num  = db.get_next_version_num(fid)
                        new_path     = versions_dir / f"v{version_num}.csv"
                        shutil.copy(str(temp_path), str(new_path))
                        vid = db.create_dataset_version(
                            file_id=fid, version_num=version_num,
                            file_path=str(new_path), nrow=prop["nrow"],
                            description=prop.get("description", ""), run_id=run_id,
                        )
                        db.set_current_version(fid, vid, str(new_path), prop["nrow"])
                        db.prune_dataset_versions(fid)

                    result = await loop.run_in_executor(None, s.r.load_file, str(new_path), var_name)
                    if not result.get("error"):
                        orig = f"{var_name}.csv" if is_new else file_rec["original_name"]
                        db.upsert_file(s.workspace_id, var_name, orig, str(new_path),
                                       prop["nrow"], result["schema"], s.r.stats.get(var_name, {}))
                        if is_new:
                            s.loaded_vars.add(var_name)

                    yield sse("dataset_auto_accepted", json.dumps({
                        "var_name":    var_name,
                        "description": prop.get("description", ""),
                        "nrow":        prop["nrow"],
                        "nrow_before": nrow_before,
                        "is_new":      is_new,
                    }))
            else:
                stored_proposals.append({
                    "id":          prop_id,
                    "var_name":    var_name,
                    "description": prop.get("description", ""),
                    "nrow":        prop["nrow"],
                    "file":        prop["file"],
                })
                yield sse("dataset_proposal", json.dumps({
                    "id":          prop_id,
                    "run_id":      run_id,
                    "var_name":    var_name,
                    "description": prop.get("description", ""),
                    "nrow_before": nrow_before,
                    "nrow_after":  prop["nrow"],
                }))

        if stored_proposals:
            db.update_run(run_id, pending_proposals=json.dumps(stored_proposals))

    # ── 6. Persist run state ──────────────────────────────────────────────
    # Restore inactive vars
    if inactive_files:
        await loop.run_in_executor(None, s.r.restore_vars, inactive_files)
        for f in inactive_files:
            s.loaded_vars.add(f["var_name"])

    yield t("Saving results…")
    duration_ms = int((time.monotonic() - start_ms) * 1000)
    db.update_run(
        run_id,
        output=r_result.get("output"),
        error=r_result.get("error"),
        success=int(not bool(r_result["error"])),
        duration_ms=duration_ms,
        agent_text=full_response.strip(),
        context_snapshot=data_context,
        trace_steps=json.dumps(trace_steps),
        summary=summary,
    )
    db.add_message(s.workspace_id, "user",      task,                         run_id=run_id)
    db.add_message(s.workspace_id, "assistant", f"```{language}\n{code}\n```", run_id=run_id)
    db.touch_workspace(s.workspace_id)

    final_success = not bool(r_result.get("error"))
    if summary:
        yield sse("summary", summary)
    yield sse("done", json.dumps({"success": final_success}))


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    s = store.get(req.session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found. Upload data first.")
    config = load_config()
    return StreamingResponse(_with_stream_flag(s, _analyze(s, req, config)),
                             media_type="text/event-stream")


# ── Background chat jobs ──────────────────────────────────────────────────────

@app.post("/chat/background")
async def chat_background(req: ChatRequest):
    """
    Submit a chat analysis as a background job.
    Returns {run_id} immediately; the job runs in a background asyncio task.
    Poll GET /run/{run_id}/status for progress, or connect to
    GET /run/{run_id}/stream to receive SSE events (replays buffered + live).
    """
    s = store.get(req.session_id)
    if not s:
        raise HTTPException(404, "Session not found. Upload data first.")
    if s.streaming:
        raise HTTPException(409, "A run is already active in this workspace. Wait for it to finish.")

    config   = load_config()
    loop     = asyncio.get_event_loop()

    # Pre-create the run so we can return run_id before the task starts.
    await loop.run_in_executor(None, s.ensure_files_loaded)

    agent_cmd  = config.get("command", "")
    all_files  = db.get_files(s.workspace_id)
    all_vars   = [f["var_name"] for f in all_files]
    act_files  = req.active_files if req.active_files is not None else all_vars
    file_by_var = {f["var_name"]: f for f in all_files}
    afv = {v: file_by_var[v].get("current_version_seq") or 1 for v in act_files if v in file_by_var}

    ws_record = db.get_workspace(s.workspace_id)
    language  = (ws_record.get("language") if ws_record else None) or config.get("language", "r")
    language  = language.lower()
    store.ensure_language(s, language)

    run_id = db.create_run(s.workspace_id, req.message, agent_cmd, act_files,
                           active_file_versions=afv, language=language,
                           job_status="running")

    job = _Job(run_id=run_id, status="running")
    _jobs[run_id] = job

    async def _bg_task():
        s.streaming = True
        try:
            async for event_str in _analyze(s, req, config, pre_run_id=run_id):
                job.events.append(event_str)
        except Exception as e:
            job.status = "error"
            job.events.append(sse("error", str(e)))
            job.events.append(sse("done", json.dumps({"success": False})))
        finally:
            s.streaming = False
            if job.status != "error":
                # Use DB success field as authoritative source — _analyze() sets it
                # before yielding done, so it's already written when we reach here.
                run_record = db.get_run(run_id)
                if run_record and run_record.get("success") != 1:
                    job.status = "error"
                else:
                    job.status = "done"
            db.update_run(run_id, job_status=job.status)

    asyncio.create_task(_bg_task())
    return {"run_id": run_id}


@app.get("/run/{run_id}/status")
def get_job_status(run_id: str):
    """Return background job status. Useful for polling."""
    if run_id in _jobs:
        job = _jobs[run_id]
        return {
            "run_id":      run_id,
            "status":      job.status,
            "event_count": len(job.events),
            "is_background": True,
        }
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    js = run.get("job_status")
    return {
        "run_id":        run_id,
        "status":        js if js else "done",
        "event_count":   0,
        "is_background": js is not None,
    }


@app.get("/run/{run_id}/stream")
async def stream_run_events(run_id: str):
    """
    SSE: stream events for a background job.
    - If job is in memory (still running or just finished): replay buffered
      events from a cursor, polling every 100 ms until done.
    - If job is no longer in memory (server restart, or sync run):
      reconstruct key events from the DB record and artifacts.
    """
    if run_id in _jobs:
        job = _jobs[run_id]

        async def _stream_live():
            cursor = 0
            while True:
                new_events = job.events[cursor:]
                cursor += len(new_events)
                for ev in new_events:
                    yield ev
                if job.status in ("done", "error") and cursor >= len(job.events):
                    break
                await asyncio.sleep(0.1)

        return StreamingResponse(_stream_live(), media_type="text/event-stream")

    # Job not in memory — reconstruct from DB
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    async def _replay_db():
        yield sse("run_id", run_id)
        # Replay metadata first so the frontend processes it before code/output
        if run.get("context_snapshot"):
            yield sse("context_snapshot", run["context_snapshot"])
        trace = run.get("trace_steps")
        if trace:
            steps = trace if isinstance(trace, list) else json.loads(trace)
            for step in steps:
                yield sse("trace", step)
        if run.get("code"):
            yield sse("code", run["code"])
        if run.get("output"):
            yield sse("output", run["output"])
        for artifact in db.get_run_artifacts(run_id):
            atype = artifact["artifact_type"]
            if atype == "plot":
                yield sse("plot", base64.b64encode(bytes(artifact["data"])).decode())
            elif atype == "table":
                yield sse("table", artifact["data"].decode() if isinstance(artifact["data"], (bytes, bytearray)) else artifact["data"])
            elif atype == "dataset":
                yield sse("export", json.dumps({"artifact_id": artifact["id"], "filename": artifact.get("label", "")}))
        if run.get("pending_proposals"):
            try:
                proposals = json.loads(run["pending_proposals"]) if isinstance(run["pending_proposals"], str) else run["pending_proposals"]
                for prop in proposals:
                    yield sse("dataset_proposal", json.dumps({
                        "id":          prop.get("id", ""),
                        "run_id":      run_id,
                        "var_name":    prop.get("var_name", ""),
                        "description": prop.get("description", ""),
                        "nrow_before": None,
                        "nrow_after":  prop.get("nrow"),
                    }))
            except Exception:
                pass
        if run.get("error"):
            yield sse("error", run["error"])
        if run.get("summary"):
            yield sse("summary", run["summary"])
        yield sse("done", json.dumps({"success": bool(run.get("success"))}))

    return StreamingResponse(_replay_db(), media_type="text/event-stream")


# ── Shared SSE code-execution helper ─────────────────────────────────────────

async def _execute_code_sse(s, run_id: str, code: str,
                            active_files: list[str] | None = None,
                            language: str = "r"):
    """
    Async generator: load files → execute code → stream results → persist.
    Used by both /run/:id/rerun and /workflows/:id/run.
    Stores trace_steps and context_snapshot for full run provenance.
    """
    loop = asyncio.get_event_loop()
    trace_steps: list[str] = []

    def t(msg: str) -> str:
        trace_steps.append(msg)
        return sse("trace", msg)

    yield sse("run_id", run_id)
    yield t("Loading files…")
    await loop.run_in_executor(None, s.ensure_files_loaded)

    context_snapshot = s.r.get_context(None)

    # Hide inactive files if active_files scope provided
    inactive_files_sse: list[dict] = []
    if active_files is not None:
        all_ws_files = db.get_files(s.workspace_id)
        inactive_files_sse = [f for f in all_ws_files if f["var_name"] not in set(active_files) and f["var_name"] in s.loaded_vars]
        if inactive_files_sse:
            await loop.run_in_executor(None, s.r.hide_vars, [f["var_name"] for f in inactive_files_sse])
            for f in inactive_files_sse:
                s.loaded_vars.discard(f["var_name"])

    yield sse("code", code)

    # Auto-install any packages the agent code requires but aren't yet installed
    if language == "r":
        required_pkgs = _extract_r_packages(code)
        if required_pkgs:
            missing = [p for p in required_pkgs
                       if not await loop.run_in_executor(None, s.r.is_package_installed, p)]
            for pkg in missing:
                yield sse("installing", pkg)
                err = await loop.run_in_executor(None, s.r.install_package, pkg)
                if err:
                    yield sse("install_error", f"{pkg}: {err}")
                else:
                    yield sse("installed", pkg)

    yield t(f"Executing {language.upper()} code…")
    start_ms = time.monotonic()
    r_result = await loop.run_in_executor(None, s.r.execute, code)

    if r_result["output"]:
        yield sse("output", r_result["output"])

    for plot_b64 in r_result["plots"]:
        plot_bytes = base64.b64decode(plot_b64)
        db.create_artifact(run_id, "plot", plot_bytes, "image/png")
        yield sse("plot", plot_b64)

    for table_html in r_result.get("tables", []):
        db.create_artifact(run_id, "table", table_html.encode(), "text/html")
        yield sse("table", table_html)

    for filename, data in r_result.get("exports", []):
        mime = "text/csv" if filename.lower().endswith(".csv") else "application/octet-stream"
        aid = db.create_artifact(run_id, "dataset", data, mime, label=filename)
        yield sse("export", json.dumps({"artifact_id": aid, "filename": filename}))

    if r_result["error"]:
        yield sse("error", r_result["error"])

    # Dataset version proposals
    if r_result.get("proposals"):
        all_files = db.get_files(s.workspace_id)
        stored_proposals = []
        for prop in r_result["proposals"]:
            file_rec = next((f for f in all_files if f["var_name"] == prop["var_name"]), None)
            nrow_before = file_rec["nrow"] if file_rec else None
            prop_id = uuid.uuid4().hex
            stored_proposals.append({
                "id":          prop_id,
                "var_name":    prop["var_name"],
                "description": prop.get("description", ""),
                "nrow":        prop["nrow"],
                "file":        prop["file"],
            })
            yield sse("dataset_proposal", json.dumps({
                "id":          prop_id,
                "run_id":      run_id,
                "var_name":    prop["var_name"],
                "description": prop.get("description", ""),
                "nrow_before": nrow_before,
                "nrow_after":  prop["nrow"],
            }))
        db.update_run(run_id, pending_proposals=json.dumps(stored_proposals))

    # Restore inactive vars
    if inactive_files_sse:
        await loop.run_in_executor(None, s.r.restore_vars, inactive_files_sse)
        for f in inactive_files_sse:
            s.loaded_vars.add(f["var_name"])

    duration_ms = int((time.monotonic() - start_ms) * 1000)
    trace_steps.append("Done")
    db.update_run(
        run_id,
        code=code,
        output=r_result.get("output"),
        error=r_result.get("error"),
        success=int(not bool(r_result["error"])),
        duration_ms=duration_ms,
        context_snapshot=context_snapshot,
        trace_steps=json.dumps(trace_steps),
    )
    db.touch_workspace(s.workspace_id)
    yield sse("trace", "Done")
    yield sse("done", "")


# ── Run management ────────────────────────────────────────────────────────────

class PatchRunRequest(BaseModel):
    edited_code: str


class PatchWorkspaceRequest(BaseModel):
    name: str | None = None
    language: str | None = None
    auto_approve: bool | None = None


@app.patch("/workspace/{workspace_id}")
def patch_workspace(workspace_id: str, req: PatchWorkspaceRequest):
    if not db.get_workspace(workspace_id):
        raise HTTPException(404, "Workspace not found")
    if req.name is not None:
        db.rename_workspace(workspace_id, req.name.strip() or "workspace")
    if req.language is not None:
        lang = req.language.lower()
        if lang not in ("r", "python"):
            raise HTTPException(400, "language must be 'r' or 'python'")
        active = store.get(workspace_id)
        if active and active.streaming:
            raise HTTPException(409, "A run is currently streaming — wait for it to finish before changing language.")
        db.set_workspace_language(workspace_id, lang)
    if req.auto_approve is not None:
        db.set_workspace_auto_approve(workspace_id, req.auto_approve)
    return {"ok": True}


@app.patch("/run/{run_id}")
def patch_run(run_id: str, req: PatchRunRequest):
    if not db.get_run(run_id):
        raise HTTPException(404, "Run not found")
    db.update_run(run_id, edited_code=req.edited_code)
    return {"ok": True}


class RerunRequest(BaseModel):
    session_id: str


@app.post("/run/{run_id}/rerun")
async def rerun(run_id: str, req: RerunRequest):
    parent_run = db.get_run(run_id)
    if not parent_run:
        raise HTTPException(404, "Run not found")
    s = store.get(req.session_id)
    if not s:
        raise HTTPException(404, "Session not found")

    code = parent_run["edited_code"] or parent_run["code"]
    if not code:
        raise HTTPException(400, "No code to run")

    # Create a child run linked to the parent — capture current version IDs at rerun time
    new_version  = (parent_run.get("version") or 1) + 1
    rerun_files  = db.get_files(parent_run["workspace_id"])
    file_by_var  = {f["var_name"]: f for f in rerun_files}
    afv = {
        v: file_by_var[v].get("current_version_seq") or 1
        for v in parent_run["active_files"] if v in file_by_var
    }
    parent_language = parent_run.get("language") or "r"
    new_run_id  = db.create_run(
        parent_run["workspace_id"],
        parent_run["prompt"],
        parent_run.get("agent", ""),
        parent_run["active_files"],
        parent_run_id=run_id,
        version=new_version,
        code=code,
        active_file_versions=afv,
        language=parent_language,
    )

    store.ensure_language(s, parent_language)

    return StreamingResponse(
        _with_stream_flag(s, _execute_code_sse(s, new_run_id, code,
                          active_files=parent_run.get("active_files"),
                          language=parent_language)),
        media_type="text/event-stream",
    )


# ── Agent config endpoints ────────────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

AGENT_PRESETS = [
    {"id": "claude",  "label": "Claude (claude -p)",       "command": "claude -p"},
    {"id": "codex",   "label": "Codex  (codex exec)",      "command": "codex exec --skip-git-repo-check"},
    {"id": "ollama",  "label": "Ollama (ollama run …)",    "command": "ollama run llama3"},
]

MODEL_OPTIONS = {
    "claude": [
        {"value": "",       "label": "Default"},
        {"value": "best",   "label": "Best available"},
        {"value": "fable",  "label": "Fable (latest)"},
        {"value": "opus",   "label": "Opus (latest)"},
        {"value": "sonnet", "label": "Sonnet (latest)"},
        {"value": "haiku",  "label": "Haiku (latest)"},
    ],
    "codex": [
        {"value": "",              "label": "Default"},
        {"value": "gpt-5.6-sol",   "label": "GPT-5.6 Sol"},
        {"value": "gpt-5.6-terra", "label": "GPT-5.6 Terra"},
        {"value": "gpt-5.6-luna",  "label": "GPT-5.6 Luna"},
    ],
}


_EFFORT_LEVELS = ("low", "medium", "high", "max")


@app.get("/config")
def get_config():
    cfg = load_config()
    command = cfg.get("command", "claude -p")
    effort  = cfg.get("effort", "") or ""
    model   = cfg.get("model", "") or ""
    preset_id = next(
        (p["id"] for p in AGENT_PRESETS if p["command"] == command),
        "custom",
    )
    return {
        "command":       command,
        "language":      cfg.get("language", "r"),
        "preset_id":     preset_id,
        "presets":       AGENT_PRESETS,
        "model":         model,
        "model_options": MODEL_OPTIONS.get(_detect_agent(command), []),
        "effort":        effort,
        "effort_levels": list(_EFFORT_LEVELS),
    }


class PatchConfigRequest(BaseModel):
    command: str | None = None
    model:   str | None = None
    effort:  str | None = None


@app.patch("/config")
def patch_config(req: PatchConfigRequest):
    existing: dict = {}
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            existing = yaml.safe_load(f) or {}
    if "agent" not in existing or not isinstance(existing.get("agent"), dict):
        existing["agent"] = {}

    if req.command is not None:
        command = req.command.strip()
        if not command:
            raise HTTPException(400, "command must not be empty")
        existing["agent"]["command"] = command

    if req.model is not None:
        existing["agent"]["model"] = req.model.strip()

    if req.effort is not None:
        effort = req.effort.strip().lower()
        if effort and effort not in _EFFORT_LEVELS:
            raise HTTPException(400, f"effort must be one of {_EFFORT_LEVELS} or empty")
        existing["agent"]["effort"] = effort

    with open(_CONFIG_PATH, "w") as f:
        yaml.dump(existing, f, default_flow_style=False, allow_unicode=True)
    return {"ok": True}


# ── Utility endpoints ─────────────────────────────────────────────────────────

@app.get("/context/{session_id}")
def get_context(session_id: str):
    workspace = db.get_workspace(session_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    files          = db.get_files(session_id)
    all_files_arch = db.get_files(session_id, include_archived=True)
    runs  = db.get_runs(session_id)
    msgs  = db.get_messages(session_id)
    return {
        "workspace_name": workspace["name"],
        "files": [
            {
                "id":                  f["id"],
                "name":                f["var_name"],
                "original":            f["original_name"],
                "nrow":                f["nrow"],
                "schema":              f["col_schema"],
                "stats":               f["col_stats"],
                "version_num":         f.get("version_num", 1),
                "current_version_seq": f.get("current_version_seq") or 1,
                "notes":               f.get("notes", ""),
            }
            for f in files
        ],
        "archived_files": [
            {"id": f["id"], "name": f["var_name"], "original": f["original_name"], "archived_at": f.get("archived_at")}
            for f in all_files_arch if f.get("archived_at")
        ],
        "run_count":      len(runs),
        "history_length": len(msgs),
        "language":       workspace.get("language", "r"),
        "auto_approve":   bool(workspace.get("auto_approve", 0)),
    }


@app.delete("/history/{session_id}")
def clear_history(session_id: str):
    if not store.get(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    with db.get_db() as con:
        con.execute("DELETE FROM messages WHERE workspace_id=?", (session_id,))
    return {"ok": True}


@app.get("/run/{run_id}")
def get_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    run["artifacts"] = [
        {"id": a["id"], "type": a["artifact_type"], "mime_type": a["mime_type"], "label": a.get("label", "")}
        for a in db.get_run_artifacts(run_id)
    ]
    # Return pending proposals without internal file paths
    raw_proposals = json.loads(run.get("pending_proposals") or "[]")
    run["pending_proposals"] = [
        {k: v for k, v in p.items() if k != "file"}
        for p in raw_proposals
    ]
    raw_rejected = json.loads(run.get("rejected_proposals") or "[]")
    run["rejected_proposals"] = [
        {k: v for k, v in p.items() if k != "file"}
        for p in raw_rejected
    ]
    # Versions produced when proposals from this run were accepted
    run["produced_versions"] = db.get_run_produced_versions(run_id)
    # Parse env_snapshot if present
    if run.get("env_snapshot"):
        try:
            run["env_snapshot"] = json.loads(run["env_snapshot"])
        except Exception:
            run["env_snapshot"] = None
    return run


@app.get("/run/{run_id}/repro")
def download_repro(run_id: str):
    """
    Download a plain-text reproducibility report for a run: runtime, packages,
    dataset fingerprints, active file version IDs.
    """
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    snap_raw = run.get("env_snapshot")
    snap: dict = {}
    if snap_raw:
        try:
            snap = json.loads(snap_raw)
        except Exception:
            pass

    lines = [
        "vibalytics — reproducibility report",
        "=" * 40,
        f"run_id:      {run_id}",
        f"created_at:  {run.get('created_at', '')}",
        f"language:    {run.get('language', 'r')}",
        f"prompt:      {run.get('prompt', '')}",
        "",
    ]

    if snap:
        lines += [
            f"runtime:     {snap.get('runtime', '')}",
            f"captured_at: {snap.get('captured_at', '')}",
            f"working_dir: {snap.get('working_dir', '')}",
            "",
        ]
        fps = snap.get("dataset_fingerprints", {})
        if fps:
            lines.append("dataset fingerprints:")
            for var, fp in fps.items():
                lines.append(
                    f"  {var}: v{fp.get('version_num', '?')} · "
                    f"{fp.get('nrow', '?')} rows · "
                    f"sha256:{fp.get('sha256', '?')} · "
                    f"version_id:{fp.get('version_id', '')[:8]}"
                )
            lines.append("")
        pkgs = snap.get("packages", {})
        if pkgs:
            lines.append(f"packages ({len(pkgs)}):")
            for pkg, ver in sorted(pkgs.items()):
                lines.append(f"  {pkg}: {ver}")
            lines.append("")

    afv = run.get("active_file_versions") or {}
    if afv:
        lines.append("active file versions at run time:")
        if isinstance(afv, str):
            try:
                afv = json.loads(afv)
            except Exception:
                afv = {}
        for var, ver in afv.items():
            lines.append(f"  {var}: v{ver}")
        lines.append("")

    code = run.get("edited_code") or run.get("code") or ""
    if code:
        lines += ["code:", "---", code, "---", ""]

    content = "\n".join(lines)
    lang = run.get("language") or "r"
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="repro_{run_id[:8]}.txt"'},
    )


@app.get("/runs/{session_id}")
def list_runs(session_id: str):
    if not db.get_workspace(session_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    runs = db.get_runs(session_id)
    return [
        {
            "id":          r["id"],
            "prompt":      r["prompt"],
            "success":     r["success"],
            "created_at":  r["created_at"],
            "duration_ms": r["duration_ms"],
        }
        for r in runs
    ]


@app.get("/run/{run_id}/script")
def download_script(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    code = run.get("edited_code") or run.get("code") or ""
    if not code:
        raise HTTPException(404, "No script for this run")
    lang = run.get("language") or "r"
    ext  = ".py" if lang == "python" else ".R"
    return Response(
        content=code,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="run_{run_id[:8]}{ext}"'},
    )


def _clean_code(code: str, language: str) -> str:
    """Strip all vibalytics-internal markers from generated code."""
    # Remove __DL_VER_PROPOSE__....__DL_VER_END__ blocks (may span lines)
    code = re.sub(r"__DL_VER_PROPOSE__.*?__DL_VER_END__\n?", "", code, flags=re.DOTALL)
    # Remove any line containing a __DL_*__ sentinel
    code = re.sub(r"^[^\n]*__DL_[A-Z_]+__[^\n]*\n?", "", code, flags=re.MULTILINE)
    # Replace dl_propose_version() calls — the clean script assigns directly
    if language == "r":
        code = re.sub(
            r"dl_propose_version\s*\([^)]*\)",
            "# (dl_propose_version removed — assign the result directly)",
            code,
        )
    else:
        code = re.sub(
            r"dl_propose_version\s*\([^)]*\)",
            "# (dl_propose_version removed — assign the result directly)",
            code,
        )
    # Collapse excess blank lines
    code = re.sub(r"\n{3,}", "\n\n", code)
    return code.strip()


def _resolve_run_files(run: dict) -> dict:
    """Return {var_name: {original_name, nrow, ncol}} using the run's recorded
    provenance.  Uses include_archived=True so files that were later archived
    are still found, and looks up the historical nrow from dataset_versions
    for the exact version that was active when the run executed."""
    active_files = run.get("active_files") or []
    afv          = run.get("active_file_versions") or {}   # {var_name: version_num}
    all_files    = db.get_files(run["workspace_id"], include_archived=True)
    file_map     = {f["var_name"]: f for f in all_files}
    result       = {}
    for var in active_files:
        f = file_map.get(var)
        if not f:
            continue
        col_schema = f.get("col_schema") or {}
        ncol       = len(col_schema)                        # flat {name: type} dict
        # Use the version's recorded nrow when possible; fall back to current.
        nrow = f.get("nrow", "?")
        version_num = afv.get(var)
        if version_num:
            hist = db.get_version_nrow(f["id"], int(version_num))
            if hist is not None:
                nrow = hist
        result[var] = {
            "original_name": f.get("original_name") or f"{var}.csv",
            "nrow":          nrow,
            "ncol":          ncol,
        }
    return result


def _file_load_stubs(file_info: dict, language: str) -> list[str]:
    """Return data-loading lines given resolved file info from _resolve_run_files."""
    lines = []
    for var, info in file_info.items():
        fname = info["original_name"]
        meta  = f"  # {info['nrow']} rows × {info['ncol']} cols"
        if language == "r":
            lines.append(f'{var} <- read.csv("{fname}"){meta}')
        else:
            lines.append(f'{var} = pd.read_csv("{fname}"){meta}')
    return lines


@app.get("/run/{run_id}/clean_script")
def download_clean_script(run_id: str):
    """Export a standalone runnable script: stripped of vibalytics markers, with a
    header comment and data-loading stubs so it runs outside the workspace."""
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    code = run.get("edited_code") or run.get("code") or ""
    if not code:
        raise HTTPException(404, "No code for this run")
    lang         = (run.get("language") or "r").lower()
    active_files = run.get("active_files") or []
    file_info    = _resolve_run_files(run)
    clean        = _clean_code(code, lang)
    prompt       = (run.get("prompt") or "").replace("\n", " ")
    created      = (run.get("created_at") or "")[:19]

    if lang == "r":
        stubs = _file_load_stubs(file_info, "r")
        parts = [
            "# ── Generated by vibalytics ──────────────────────────────────────────",
            f"# Prompt:  {prompt}",
            f"# Date:    {created}",
            f"# Files:   {', '.join(active_files) or '(none)'}",
            "# ───────────────────────────────────────────────────────────────────",
            "",
        ]
        if stubs:
            parts += stubs + [""]
        parts.append(clean)
        content = "\n".join(parts)
        ext = ".R"
    else:
        stubs = _file_load_stubs(file_info, "python")
        parts = [
            "# ── Generated by vibalytics ──────────────────────────────────────────",
            f"# Prompt:  {prompt}",
            f"# Date:    {created}",
            f"# Files:   {', '.join(active_files) or '(none)'}",
            "# ───────────────────────────────────────────────────────────────────",
            "import pandas as pd",
            "",
        ]
        if stubs:
            parts += stubs + [""]
        parts.append(clean)
        content = "\n".join(parts)
        ext = ".py"

    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="analysis_{run_id[:8]}{ext}"'},
    )


@app.get("/run/{run_id}/notebook")
def download_notebook(run_id: str):
    """Export a Jupyter notebook (.ipynb) for Python runs, or R Markdown (.Rmd)
    for R runs — ready to open in Jupyter / RStudio."""
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    code = run.get("edited_code") or run.get("code") or ""
    if not code:
        raise HTTPException(404, "No code for this run")
    lang         = (run.get("language") or "r").lower()
    active_files = run.get("active_files") or []
    file_info    = _resolve_run_files(run)
    clean        = _clean_code(code, lang)
    prompt       = (run.get("prompt") or "").replace('"', "'")
    created      = (run.get("created_at") or "")[:10]

    if lang == "python":
        stubs = _file_load_stubs(file_info, "python")
        load_cell_src = ["import pandas as pd\n"] + [s + "\n" for s in stubs]

        def cell_id():
            return uuid.uuid4().hex[:8]

        nb = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {
                "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                "language_info": {"name": "python", "version": "3"},
            },
            "cells": [
                {
                    "cell_type": "markdown",
                    "id": cell_id(),
                    "metadata": {},
                    "source": [
                        f"# {prompt or 'Analysis'}\n\n",
                        f"**Date:** {created}  \n",
                        f"**Files:** {', '.join(active_files) or '(none)'}  \n",
                        "\n*Generated by [vibalytics](https://github.com/anthropics/vibalytics)*",
                    ],
                },
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "id": cell_id(),
                    "metadata": {},
                    "outputs": [],
                    "source": load_cell_src,
                },
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "id": cell_id(),
                    "metadata": {},
                    "outputs": [],
                    "source": [clean],
                },
            ],
        }
        content  = json.dumps(nb, indent=1)
        ext      = ".ipynb"
        mime     = "application/x-ipynb+json"
    else:
        # R Markdown
        stubs    = _file_load_stubs(file_info, "r")
        libs     = re.findall(r"library\s*\(\s*(\w+)\s*\)", clean)
        lib_lines = [f"library({l})" for l in dict.fromkeys(libs)]  # deduplicated, ordered

        rmd_parts = [
            "---",
            f'title: "{prompt or "Analysis"}"',
            f'date: "{created}"',
            "output: html_document",
            "---",
            "",
            "```{r setup, include=FALSE}",
            "knitr::opts_chunk$set(echo = TRUE)",
        ] + lib_lines + [
            "```",
            "",
        ]
        if stubs:
            rmd_parts += [
                "```{r load-data}",
            ] + stubs + [
                "```",
                "",
            ]
        rmd_parts += [
            "```{r analysis}",
            clean,
            "```",
        ]
        content  = "\n".join(rmd_parts)
        ext      = ".Rmd"
        mime     = "text/plain"

    return Response(
        content=content,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="analysis_{run_id[:8]}{ext}"'},
    )


@app.get("/run/{run_id}/log")
def download_log(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    output = run.get("output") or ""
    if not output:
        raise HTTPException(404, "No log for this run")
    return Response(
        content=output,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="run_{run_id[:8]}.log"'},
    )


@app.get("/artifact/{artifact_id}")
def get_artifact(artifact_id: str):
    a = db.get_artifact(artifact_id)
    if not a:
        raise HTTPException(status_code=404, detail="Artifact not found")
    headers = {}
    if a["artifact_type"] == "dataset":
        label = a.get("label") or f"export_{artifact_id[:8]}.csv"
        headers["Content-Disposition"] = f'attachment; filename="{label}"'
    return Response(content=a["data"], media_type=a["mime_type"], headers=headers)


@app.get("/preview/{session_id}/{var_name}")
async def preview_data(session_id: str, var_name: str, n: int = 20):
    s = store.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, s.ensure_files_loaded)
    result = await loop.run_in_executor(None, s.r.get_preview, var_name, n)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/export/{session_id}")
async def export_workspace(session_id: str):
    """Download a ZIP with all R scripts, outputs, plots, and a README."""
    s = store.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    runs = db.get_runs(session_id)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # README
        lines = [
            "vibalytics export",
            f"Workspace: {session_id}",
            f"Runs: {len(runs)}",
            "",
        ]
        for i, r in enumerate(runs, 1):
            lines.append(f"Run {i}: {r['prompt']}")
            lines.append(f"  Status: {'OK' if r['success'] else 'FAILED'}")
            lines.append(f"  Time: {r['created_at']}")
            lines.append("")
        zf.writestr("README.txt", "\n".join(lines))

        # Scripts + outputs
        for i, r in enumerate(runs, 1):
            slug = re.sub(r"[^a-z0-9]+", "_", r["prompt"].lower())[:40]
            code = r["edited_code"] or r["code"] or ""
            if code:
                zf.writestr(f"scripts/run_{i:02d}_{slug}.R", code)
            if r["output"]:
                zf.writestr(f"outputs/run_{i:02d}_{slug}.txt", r["output"])

            # Artifacts (plots)
            artifacts = db.get_run_artifacts(r["id"])
            plot_num = 1
            for a in artifacts:
                if a["artifact_type"] == "plot":
                    ext = ".png"
                    zf.writestr(
                        f"plots/run_{i:02d}_{slug}_plot{plot_num}{ext}",
                        bytes(a["data"]),
                    )
                    plot_num += 1

        # Uploaded data files
        ws_dir = FILES_DIR / session_id
        if ws_dir.exists():
            for f in ws_dir.iterdir():
                zf.write(f, f"data/{f.name}")

    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=vibalytics_{session_id[:8]}.zip"},
    )


# ── HTML report ────────────────────────────────────────────────────────────────

_REPORT_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       font-size: 15px; line-height: 1.6; color: #1a1a1a; background: #fff;
       max-width: 900px; margin: 0 auto; padding: 2rem 1.5rem 4rem; }
h1 { font-size: 1.6rem; font-weight: 700; margin-bottom: .25rem; }
h2 { font-size: 1.1rem; font-weight: 600; margin-bottom: .75rem; color: #111; }
.meta { font-size: .8rem; color: #666; margin-bottom: 2.5rem; }
.toc { background: #f8f8f8; border: 1px solid #e0e0e0; border-radius: 6px;
       padding: 1rem 1.25rem; margin-bottom: 2.5rem; }
.toc h2 { font-size: .9rem; color: #555; text-transform: uppercase;
           letter-spacing: .05em; margin-bottom: .5rem; }
.toc ol { padding-left: 1.25rem; }
.toc li { font-size: .85rem; margin: .15rem 0; }
.toc a { color: #2563eb; text-decoration: none; }
.toc a:hover { text-decoration: underline; }
.files { margin-bottom: 2.5rem; }
.files h2 { font-size: .9rem; color: #555; text-transform: uppercase;
             letter-spacing: .05em; margin-bottom: .5rem; }
.file-tag { display: inline-block; background: #f0f4ff; border: 1px solid #c7d7fd;
             border-radius: 4px; padding: .15rem .55rem; font-size: .78rem;
             margin: .2rem .2rem 0 0; color: #1d4ed8; font-family: monospace; }
.run { border-top: 2px solid #e5e7eb; padding-top: 1.75rem; margin-top: 2rem; }
.run-prompt { font-size: 1.05rem; font-weight: 600; margin-bottom: .5rem; }
.run-meta { font-size: .78rem; color: #888; margin-bottom: 1rem; }
.run-meta .badge { display: inline-block; border-radius: 3px; padding: .05rem .4rem;
                    font-size: .72rem; font-weight: 600; margin-left: .4rem;
                    vertical-align: middle; }
.badge-ok  { background: #dcfce7; color: #15803d; }
.badge-err { background: #fee2e2; color: #dc2626; }
.badge-py  { background: #fef9c3; color: #854d0e; }
.narrative { margin-bottom: 1rem; color: #222; white-space: pre-wrap; }
.plot-wrap { margin: 1rem 0; text-align: center; }
.plot-wrap img { max-width: 100%; border: 1px solid #e5e7eb; border-radius: 6px; }
.table-wrap { overflow-x: auto; margin: 1rem 0; font-size: .82rem; }
.table-wrap table { border-collapse: collapse; width: 100%; }
.table-wrap th, .table-wrap td { border: 1px solid #e5e7eb; padding: .3rem .6rem; }
.table-wrap th { background: #f8f8f8; font-weight: 600; }
.output-block { background: #1e1e1e; color: #d4d4d4; border-radius: 6px;
                padding: .75rem 1rem; font-family: monospace; font-size: .8rem;
                white-space: pre-wrap; overflow-x: auto; margin: .75rem 0; }
.error-block { background: #fff5f5; border: 1px solid #fca5a5; border-radius: 6px;
               padding: .75rem 1rem; font-family: monospace; font-size: .8rem;
               white-space: pre-wrap; color: #dc2626; margin: .75rem 0; }
details { margin: .75rem 0; }
summary { cursor: pointer; font-size: .82rem; color: #555; user-select: none;
          padding: .3rem 0; }
summary:hover { color: #111; }
.code-block { background: #f6f8fa; border: 1px solid #e0e0e0; border-radius: 6px;
              padding: .75rem 1rem; font-family: monospace; font-size: .8rem;
              white-space: pre-wrap; overflow-x: auto; margin-top: .4rem; }
.context-block { background: #fafaf8; border: 1px solid #e8e5d8; border-radius: 6px;
                 padding: .75rem 1rem; font-family: monospace; font-size: .75rem;
                 white-space: pre-wrap; overflow-x: auto; margin-top: .4rem;
                 color: #555; }
@media print {
  body { max-width: 100%; padding: 1cm; }
  details { open: true; }
  summary { display: none; }
  details > :not(summary) { display: block !important; }
}
"""


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@app.get("/workspace/{workspace_id}/report")
async def export_html_report(workspace_id: str):
    """Generate a self-contained shareable HTML report for a workspace."""
    ws = db.get_workspace(workspace_id)
    if not ws:
        raise HTTPException(404, "Workspace not found")

    runs  = db.get_runs(workspace_id)
    files = db.get_files(workspace_id)
    now   = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    ws_name = ws.get("name") or workspace_id[:8]

    # ── Table of contents ──────────────────────────────────────────────────────
    toc_items = []
    for i, r in enumerate(runs, 1):
        prompt_short = r["prompt"][:80] + ("…" if len(r["prompt"]) > 80 else "")
        toc_items.append(
            f'<li><a href="#run-{i}">{_html_escape(prompt_short)}</a></li>'
        )

    # ── File tags ──────────────────────────────────────────────────────────────
    file_tags = "".join(
        f'<span class="file-tag">{_html_escape(f["var_name"])}'
        f' <span style="opacity:.6">{f["nrow"] or "?"} rows</span></span>'
        for f in files
    )

    # ── Run sections ──────────────────────────────────────────────────────────
    run_sections = []
    for i, r in enumerate(runs, 1):
        parts = []

        # Header
        lang_badge = (
            '<span class="badge badge-py">Python</span>'
            if r.get("language") == "python" else ""
        )
        status_badge = (
            '<span class="badge badge-ok">✓ OK</span>'
            if r.get("success")
            else '<span class="badge badge-err">✗ Error</span>'
        )
        ts = r.get("created_at", "")[:16].replace("T", " ")
        parts.append(
            f'<div id="run-{i}" class="run">'
            f'<div class="run-prompt">{i}. {_html_escape(r["prompt"])}</div>'
            f'<div class="run-meta">{ts}{status_badge}{lang_badge}</div>'
        )

        # Narrative (agent text stripped of code fences)
        narrative = (r.get("agent_text") or "").strip()
        if narrative:
            parts.append(
                f'<div class="narrative">{_html_escape(narrative)}</div>'
            )

        # Artifacts: plots and tables
        artifacts = db.get_run_artifacts(r["id"])
        for a in artifacts:
            if a["artifact_type"] == "plot":
                b64 = base64.b64encode(bytes(a["data"])).decode()
                parts.append(
                    f'<div class="plot-wrap">'
                    f'<img src="data:image/png;base64,{b64}" alt="plot">'
                    f'</div>'
                )
            elif a["artifact_type"] == "table":
                html_blob = bytes(a["data"]).decode("utf-8", errors="replace")
                parts.append(f'<div class="table-wrap">{html_blob}</div>')

        # Text output (non-empty, not redundant with table/plot)
        output = (r.get("output") or "").strip()
        if output:
            parts.append(f'<div class="output-block">{_html_escape(output)}</div>')

        # Error
        error = (r.get("error") or "").strip()
        if error:
            parts.append(f'<div class="error-block">{_html_escape(error)}</div>')

        # Code (collapsible)
        code = (r.get("edited_code") or r.get("code") or "").strip()
        lang = r.get("language") or "r"
        edited_label = " · edited" if r.get("edited_code") else ""
        if code:
            parts.append(
                f'<details><summary>▶ {lang.upper()} code{edited_label}</summary>'
                f'<div class="code-block">{_html_escape(code)}</div>'
                f'</details>'
            )

        # Data context (collapsible)
        ctx = (r.get("context_snapshot") or "").strip()
        if ctx:
            parts.append(
                f'<details><summary>▶ Data context</summary>'
                f'<div class="context-block">{_html_escape(ctx)}</div>'
                f'</details>'
            )

        parts.append("</div>")
        run_sections.append("\n".join(parts))

    toc_html = (
        f'<div class="toc"><h2>Contents</h2><ol>'
        + "\n".join(toc_items)
        + "</ol></div>"
    ) if toc_items else ""

    files_html = (
        f'<div class="files"><h2>Datasets</h2>{file_tags}</div>'
    ) if file_tags else ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_html_escape(ws_name)} — vibalytics report</title>
<style>{_REPORT_CSS}</style>
</head>
<body>
<h1>{_html_escape(ws_name)}</h1>
<div class="meta">Generated {now} &nbsp;·&nbsp; {len(runs)} run{"s" if len(runs) != 1 else ""}</div>
{files_html}
{toc_html}
{"".join(run_sections)}
</body>
</html>"""

    filename = re.sub(r"[^a-z0-9_-]", "_", ws_name.lower())[:40] or "report"
    return Response(
        content=html.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}_report.html"'},
    )


@app.get("/data/{session_id}/{file_id}")
async def get_file_data(
    session_id: str, file_id: str,
    offset: int = 0, limit: int = 100,
    sort_by: str = "", sort_dir: str = "asc",
    filter_col: str = "", filter_val: str = "", filter_op: str = "contains",
):
    """Return paginated rows for a file. Used by the data inspector."""
    file_rec = db.get_file(file_id)
    if not file_rec or file_rec["workspace_id"] != session_id:
        raise HTTPException(404, "File not found")
    s = store.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, s.ensure_files_loaded)
    result = await loop.run_in_executor(
        None, s.r.get_data, file_rec["var_name"], offset, limit, sort_by, sort_dir,
        filter_col, filter_val, filter_op
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


class CellEditsRequest(BaseModel):
    edits: list[dict]  # [{row: int, col: str, value: str}]


@app.post("/data/{session_id}/{file_id}/edit")
async def edit_file_cells(session_id: str, file_id: str, req: CellEditsRequest):
    """Apply inline cell edits and save as a new dataset version."""
    file_rec = db.get_file(file_id)
    if not file_rec or file_rec["workspace_id"] != session_id:
        raise HTTPException(404, "File not found")
    s = store.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    if not req.edits:
        raise HTTPException(400, "No edits provided")

    current_path = Path(file_rec["file_path"])
    suffix = current_path.suffix.lower()
    if suffix != ".csv":
        # Export from R to temp CSV so we can edit it regardless of original format
        loop = asyncio.get_event_loop()
        temp_csv = await loop.run_in_executor(None, s.r.export_to_csv, file_rec["var_name"])
        if not temp_csv:
            raise HTTPException(500, "Failed to export file to CSV for editing")
        edit_source = Path(temp_csv)
    else:
        edit_source = current_path
        if not edit_source.exists():
            raise HTTPException(410, "File no longer exists on disk")

    # Read, apply edits, write
    with open(edit_source, newline="", encoding="utf-8") as f:
        all_rows = list(csv_mod.reader(f))
    if not all_rows:
        raise HTTPException(400, "File is empty")

    headers = all_rows[0]
    col_index = {col: i for i, col in enumerate(headers)}
    nrow = len(all_rows) - 1

    for edit in req.edits:
        col = edit.get("col", "")
        row = edit.get("row")
        if col not in col_index:
            raise HTTPException(400, f"Column '{col}' not found")
        if not isinstance(row, int) or row < 0 or row >= nrow:
            raise HTTPException(400, f"Row index {row} out of range (0–{nrow - 1})")
        all_rows[row + 1][col_index[col]] = str(edit.get("value", ""))

    versions_dir = FILES_DIR / session_id / "versions" / file_rec["id"]
    versions_dir.mkdir(parents=True, exist_ok=True)
    version_num = db.get_next_version_num(file_rec["id"])
    new_path = versions_dir / f"v{version_num}.csv"

    with open(new_path, "w", newline="", encoding="utf-8") as f:
        csv_mod.writer(f).writerows(all_rows)

    n = len(req.edits)
    vid = db.create_dataset_version(
        file_id=file_rec["id"], version_num=version_num,
        file_path=str(new_path), nrow=nrow,
        description=f"Manual edit: {n} cell{'s' if n != 1 else ''} changed",
    )
    db.set_current_version(file_rec["id"], vid, str(new_path), nrow)
    db.prune_dataset_versions(file_rec["id"])

    s.loaded_vars.discard(file_rec["var_name"])
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, s.r.load_file, str(new_path), file_rec["var_name"])
    if not result.get("error"):
        s.loaded_vars.add(file_rec["var_name"])
        file_stats = s.r.stats.get(file_rec["var_name"], {})
        db.upsert_file(
            session_id, file_rec["var_name"], file_rec["original_name"],
            str(new_path), nrow, result["schema"], file_stats,
        )

    return {"ok": True, "version_id": vid, "version_num": version_num, "nrow": nrow}


@app.delete("/workspace/{workspace_id}/file/{file_id}")
async def delete_file(workspace_id: str, file_id: str):
    """Soft-archive a file (keeps disk + DB for provenance)."""
    file_rec = db.get_file(file_id)
    if not file_rec or file_rec["workspace_id"] != workspace_id:
        raise HTTPException(404, "File not found")
    # Remove from live R session
    s = store.get(workspace_id)
    if s:
        s.r.drop_file(file_rec["var_name"])
        s.loaded_vars.discard(file_rec["var_name"])
    db.archive_file(file_id)
    db.touch_workspace(workspace_id)
    return {"ok": True}


@app.post("/workspace/{workspace_id}/file/{file_id}/restore")
async def restore_file(workspace_id: str, file_id: str, session_id: str):
    """Restore an archived file back into the active workspace."""
    file_rec = db.get_file(file_id)
    if not file_rec or file_rec["workspace_id"] != workspace_id:
        raise HTTPException(404, "File not found")
    db.restore_file(file_id)
    # Reload in R
    s = store.get(session_id)
    if s:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, s.r.load_file, file_rec["file_path"], file_rec["var_name"])
        if not result.get("error"):
            s.loaded_vars.add(file_rec["var_name"])
    db.touch_workspace(workspace_id)
    return {"ok": True}


@app.delete("/workspace/{workspace_id}/file/{file_id}/hard")
async def hard_delete_file(workspace_id: str, file_id: str):
    """Permanently delete a file (removes from disk and DB)."""
    file_rec = db.get_file(file_id)
    if not file_rec or file_rec["workspace_id"] != workspace_id:
        raise HTTPException(404, "File not found")
    s = store.get(workspace_id)
    if s:
        s.r.drop_file(file_rec["var_name"])
        s.loaded_vars.discard(file_rec["var_name"])
    try:
        Path(file_rec["file_path"]).unlink(missing_ok=True)
    except Exception:
        pass
    db.hard_delete_file(file_id)
    db.touch_workspace(workspace_id)
    return {"ok": True}


class FileNotesRequest(BaseModel):
    notes: str


@app.patch("/workspace/{workspace_id}/file/{file_id}/notes")
def set_file_notes(workspace_id: str, file_id: str, req: FileNotesRequest):
    file_rec = db.get_file(file_id)
    if not file_rec or file_rec["workspace_id"] != workspace_id:
        raise HTTPException(404, "File not found")
    db.set_file_notes(file_id, req.notes)
    return {"ok": True}


@app.get("/data/{session_id}/{file_id}/column/{col_name}")
async def get_column_detail(session_id: str, file_id: str, col_name: str):
    file_rec = db.get_file(file_id)
    if not file_rec or file_rec["workspace_id"] != session_id:
        raise HTTPException(404, "File not found")
    s = store.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, s.ensure_files_loaded)
    result = await loop.run_in_executor(None, s.r.get_column_detail, file_rec["var_name"], col_name)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.get("/file/{file_id}/missingness")
def get_missingness(file_id: str):
    file_rec = db.get_file(file_id)
    if not file_rec:
        raise HTTPException(404, "File not found")
    stats = file_rec.get("col_stats", {})
    cols = file_rec.get("col_schema", {})
    result = []
    for col in cols:
        s = stats.get(col, {})
        result.append({
            "col": col,
            "type": s.get("type", cols[col]),
            "miss_pct": s.get("miss_pct", 0),
            "n_unique": s.get("n_unique"),
        })
    return {"columns": result, "nrow": file_rec["nrow"]}


@app.get("/file/{file_id}/diff")
def get_version_diff(file_id: str, a: str, b: str):
    """Compare two versions of a file. a and b are version IDs."""
    file_rec = db.get_file(file_id)
    if not file_rec:
        raise HTTPException(404, "File not found")

    versions = {v["id"]: v for v in db.get_dataset_versions(file_id)}
    ver_a = versions.get(a)
    ver_b = versions.get(b)
    if not ver_a or not ver_b:
        raise HTTPException(404, "Version not found")

    # Read both files as CSV
    def read_csv_rows(path: str) -> tuple[list[str], list[list[str]]]:
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv_mod.reader(f)
                rows = list(reader)
            if not rows:
                return [], []
            return rows[0], rows[1:]
        except Exception:
            return [], []

    cols_a, rows_a = read_csv_rows(ver_a["file_path"])
    cols_b, rows_b = read_csv_rows(ver_b["file_path"])

    # Changed columns
    shared_cols = [c for c in cols_a if c in cols_b]
    col_idx_a = {c: i for i, c in enumerate(cols_a)}
    col_idx_b = {c: i for i, c in enumerate(cols_b)}

    changed_cols = []
    for c in shared_cols:
        ia, ib = col_idx_a[c], col_idx_b[c]
        for ra, rb in zip(rows_a, rows_b):
            va = ra[ia] if ia < len(ra) else ""
            vb = rb[ib] if ib < len(rb) else ""
            if va != vb:
                changed_cols.append(c)
                break

    added_cols   = [c for c in cols_b if c not in cols_a]
    removed_cols = [c for c in cols_a if c not in cols_b]

    # Sample changed rows (first 10 with any cell difference)
    sample_rows = []
    for i, (ra, rb) in enumerate(zip(rows_a[:200], rows_b[:200])):
        diffs = {}
        for c in shared_cols:
            ia, ib = col_idx_a[c], col_idx_b[c]
            va = ra[ia] if ia < len(ra) else ""
            vb = rb[ib] if ib < len(rb) else ""
            if va != vb:
                diffs[c] = {"before": va, "after": vb}
        if diffs:
            sample_rows.append({"row": i, "changes": diffs})
        if len(sample_rows) >= 10:
            break

    return {
        "nrow_a":       ver_a["nrow"],
        "nrow_b":       ver_b["nrow"],
        "row_delta":    ver_b["nrow"] - ver_a["nrow"],
        "changed_cols": changed_cols,
        "added_cols":   added_cols,
        "removed_cols": removed_cols,
        "sample_rows":  sample_rows,
        "version_a":    {"id": a, "version_num": ver_a["version_num"], "description": ver_a["description"]},
        "version_b":    {"id": b, "version_num": ver_b["version_num"], "description": ver_b["description"]},
    }


@app.get("/run/{run_id}/proposal/{proposal_id}/diff")
def get_proposal_diff(run_id: str, proposal_id: str):
    """Compare current file state against a pending proposal's temp CSV."""
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    proposals = json.loads(run.get("pending_proposals") or "[]")
    prop = next((p for p in proposals if p["id"] == proposal_id), None)
    if not prop:
        raise HTTPException(404, "Proposal not found")
    file_rec = next(
        (f for f in db.get_files(run["workspace_id"]) if f["var_name"] == prop["var_name"]),
        None,
    )
    temp_path = Path(prop["file"])
    if not temp_path.exists():
        raise HTTPException(410, "Proposal file has expired — re-run the analysis")

    def read_csv_rows(path):
        try:
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv_mod.reader(f))
            return (rows[0], rows[1:]) if rows else ([], [])
        except Exception:
            return [], []

    # New variable (agent-created): no before-state
    cols_a, rows_a = ([], []) if not file_rec else read_csv_rows(file_rec["file_path"])
    cols_b, rows_b = read_csv_rows(str(temp_path))

    shared_cols = [c for c in cols_a if c in cols_b]
    col_idx_a = {c: i for i, c in enumerate(cols_a)}
    col_idx_b = {c: i for i, c in enumerate(cols_b)}

    changed_cols = []
    for c in shared_cols:
        ia, ib = col_idx_a[c], col_idx_b[c]
        for ra, rb in zip(rows_a, rows_b):
            if (ra[ia] if ia < len(ra) else "") != (rb[ib] if ib < len(rb) else ""):
                changed_cols.append(c)
                break

    added_cols   = [c for c in cols_b if c not in cols_a]
    removed_cols = [c for c in cols_a if c not in cols_b]

    sample_rows = []
    for i, (ra, rb) in enumerate(zip(rows_a[:200], rows_b[:200])):
        diffs = {}
        for c in shared_cols:
            ia, ib = col_idx_a[c], col_idx_b[c]
            va = ra[ia] if ia < len(ra) else ""
            vb = rb[ib] if ib < len(rb) else ""
            if va != vb:
                diffs[c] = {"before": va, "after": vb}
        if diffs:
            sample_rows.append({"row": i, "changes": diffs})
        if len(sample_rows) >= 10:
            break

    return {
        "nrow_before":  file_rec["nrow"] if file_rec else 0,
        "nrow_after":   prop["nrow"],
        "row_delta":    prop["nrow"] - (file_rec["nrow"] if file_rec else 0),
        "is_new_variable": file_rec is None,
        "changed_cols": changed_cols,
        "added_cols":   added_cols,
        "removed_cols": removed_cols,
        "sample_rows":  sample_rows,
    }


# ── Workflows ─────────────────────────────────────────────────────────────────

class CreateWorkflowRequest(BaseModel):
    name: str
    run_id: str


@app.get("/workflows")
def get_workflows():
    return db.list_workflows()


@app.post("/workflows")
def create_workflow_endpoint(req: CreateWorkflowRequest):
    run = db.get_run(req.run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    code = run.get("edited_code") or run.get("code")
    if not code:
        raise HTTPException(400, "Run has no code to save")
    name = req.name.strip() or "workflow"
    input_vars = run.get("active_files") or []
    return db.create_workflow(name, code, input_vars)


@app.delete("/workflows/{workflow_id}")
def delete_workflow_endpoint(workflow_id: str):
    if not db.get_workflow(workflow_id):
        raise HTTPException(404, "Workflow not found")
    db.delete_workflow(workflow_id)
    return {"ok": True}


@app.post("/workflows/{workflow_id}/run")
async def run_workflow(workflow_id: str, req: RerunRequest):
    wf = db.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    s = store.get(req.session_id)
    if not s:
        raise HTTPException(404, "Session not found")

    # Validate that all required variables are present in this workspace
    if wf.get("input_vars"):
        current_vars = {f["var_name"] for f in db.get_files(s.workspace_id)}
        missing = [v for v in wf["input_vars"] if v not in current_vars]
        if missing:
            raise HTTPException(400, f"Missing datasets: {', '.join(missing)}")

    wf_files    = db.get_files(s.workspace_id)
    wf_vars     = [f["var_name"] for f in wf_files]
    wf_afv      = {f["var_name"]: f.get("current_version_seq") or 1 for f in wf_files}
    wf_ws = db.get_workspace(s.workspace_id)
    wf_language = ((wf_ws.get("language") if wf_ws else None) or load_config().get("language", "r")).lower()
    store.ensure_language(s, wf_language)
    run_id = db.create_run(
        s.workspace_id,
        f"[workflow] {wf['name']}",
        "",
        wf_vars,
        active_file_versions=wf_afv,
        language=wf_language,
    )
    return StreamingResponse(
        _with_stream_flag(s, _execute_code_sse(s, run_id, wf["code"], active_files=wf_vars,
                          language=wf_language)),
        media_type="text/event-stream",
    )


# ── Versioning ─────────────────────────────────────────────────────────────────

@app.get("/file/{file_id}/versions")
def get_file_versions(file_id: str):
    file_rec = db.get_file(file_id)
    if not file_rec:
        raise HTTPException(404, "File not found")
    versions = db.get_dataset_versions(file_id)
    return {
        "file_id":           file_id,
        "var_name":          file_rec["var_name"],
        "version_num":       file_rec.get("version_num", 1),
        "current_version_id": file_rec.get("current_version_id"),
        "versions": [
            {
                "id":          v["id"],
                "version_num": v["version_num"],
                "nrow":        v["nrow"],
                "description": v["description"],
                "is_original": bool(v["is_original"]),
                "run_id":      v["run_id"],
                "created_at":  v["created_at"],
            }
            for v in versions
        ],
    }


class AcceptVersionRequest(BaseModel):
    session_id: str


@app.post("/run/{run_id}/accept_version/{proposal_id}")
async def accept_version(run_id: str, proposal_id: str, req: AcceptVersionRequest):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    proposals = json.loads(run.get("pending_proposals") or "[]")
    prop = next((p for p in proposals if p["id"] == proposal_id), None)
    if not prop:
        raise HTTPException(404, "Proposal not found")

    file_rec = next(
        (f for f in db.get_files(run["workspace_id"]) if f["var_name"] == prop["var_name"]),
        None,
    )

    temp_path = Path(prop["file"])
    if not temp_path.exists():
        raise HTTPException(410, "Proposal file has expired — please re-run the analysis")

    workspace_id = run["workspace_id"]
    var_name     = prop["var_name"]
    is_new       = file_rec is None

    if is_new:
        # Agent-created variable: create a new file record with version 1
        # Use var_name as the display name (e.g. "df_joined.csv")
        original_name = f"{var_name}.csv"
        # First, create a placeholder file record to get the file ID
        placeholder_path = str(temp_path)  # temporary; will be updated below
        fid = db.upsert_file(
            workspace_id, var_name, original_name,
            placeholder_path, prop["nrow"], {}, None,
        )
        versions_dir = FILES_DIR / workspace_id / "versions" / fid
        versions_dir.mkdir(parents=True, exist_ok=True)
        new_path = versions_dir / "v1.csv"
        shutil.copy(str(temp_path), str(new_path))

        vid = db.create_dataset_version(
            file_id=fid, version_num=1,
            file_path=str(new_path), nrow=prop["nrow"],
            description=prop.get("description", "agent-created dataset"),
            run_id=run_id, is_original=True,
        )
        db.set_current_version(fid, vid, str(new_path), prop["nrow"])
        version_num = 1
    else:
        fid = file_rec["id"]
        versions_dir = FILES_DIR / workspace_id / "versions" / fid
        versions_dir.mkdir(parents=True, exist_ok=True)
        version_num = db.get_next_version_num(fid)
        new_path = versions_dir / f"v{version_num}.csv"
        shutil.copy(str(temp_path), str(new_path))

        vid = db.create_dataset_version(
            file_id=fid, version_num=version_num,
            file_path=str(new_path), nrow=prop["nrow"],
            description=prop.get("description", ""), run_id=run_id,
        )
        db.set_current_version(fid, vid, str(new_path), prop["nrow"])
        db.prune_dataset_versions(fid)

    # Reload in executor and refresh stats
    s = store.get(req.session_id)
    if s:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, s.r.load_file, str(new_path), var_name)
        if not result.get("error"):
            file_stats = s.r.stats.get(var_name, {})
            original_name = var_name + ".csv" if is_new else file_rec["original_name"]
            fid = db.upsert_file(
                workspace_id, var_name, original_name,
                str(new_path), prop["nrow"], result["schema"], file_stats,
            )
            if is_new:
                s.loaded_vars.add(var_name)

    # Remove from pending proposals
    remaining = [p for p in proposals if p["id"] != proposal_id]
    db.update_run(run_id, pending_proposals=json.dumps(remaining))

    # Auto-run assertions after version is accepted (best-effort)
    try:
        assertions = [a for a in db.get_assertions(fid) if a.get("enabled", True)]
        if assertions:
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None, _run_assertions, str(new_path), assertions, fid, run_id
            )
            db.save_assertion_results(results)
    except Exception:
        pass

    return {"ok": True, "version_id": vid, "version_num": version_num, "is_new_file": is_new, "file_id": fid}


@app.post("/run/{run_id}/reject_version/{proposal_id}")
def reject_version(run_id: str, proposal_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    proposals = json.loads(run.get("pending_proposals") or "[]")
    prop = next((p for p in proposals if p["id"] == proposal_id), None)
    if not prop:
        raise HTTPException(404, "Proposal not found")

    # Soft-reject: move to rejected_proposals (keep temp file for possible restore)
    remaining = [p for p in proposals if p["id"] != proposal_id]
    rejected = json.loads(run.get("rejected_proposals") or "[]")
    rejected.append(prop)
    db.update_run(run_id,
                  pending_proposals=json.dumps(remaining),
                  rejected_proposals=json.dumps(rejected))
    return {"ok": True}


@app.post("/run/{run_id}/restore_version/{proposal_id}")
def restore_version(run_id: str, proposal_id: str):
    """Move a previously rejected proposal back to pending."""
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    rejected = json.loads(run.get("rejected_proposals") or "[]")
    prop = next((p for p in rejected if p["id"] == proposal_id), None)
    if not prop:
        raise HTTPException(404, "Rejected proposal not found")

    # Verify the temp file still exists
    if not Path(prop.get("file", "")).exists():
        raise HTTPException(410, "Temp file no longer exists — cannot restore")

    remaining_rejected = [p for p in rejected if p["id"] != proposal_id]
    pending = json.loads(run.get("pending_proposals") or "[]")
    pending.append(prop)
    db.update_run(run_id,
                  pending_proposals=json.dumps(pending),
                  rejected_proposals=json.dumps(remaining_rejected))
    return {"ok": True}


@app.post("/file/{file_id}/revert/{version_id}")
async def revert_to_version(file_id: str, version_id: str, session_id: str):
    file_rec = db.get_file(file_id)
    if not file_rec:
        raise HTTPException(404, "File not found")

    versions = db.get_dataset_versions(file_id)
    ver = next((v for v in versions if v["id"] == version_id), None)
    if not ver:
        raise HTTPException(404, "Version not found")

    if not Path(ver["file_path"]).exists():
        raise HTTPException(410, "Version file no longer exists on disk")

    # Update file to point to this version
    db.set_current_version(file_id, ver["id"], ver["file_path"], ver["nrow"])

    # Reload in R — discard first so ensure_files_loaded can't short-circuit the reload
    s = store.get(session_id)
    if s:
        s.loaded_vars.discard(file_rec["var_name"])
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, s.r.load_file, ver["file_path"], file_rec["var_name"])
        if not result.get("error"):
            s.loaded_vars.add(file_rec["var_name"])
            file_stats = s.r.stats.get(file_rec["var_name"], {})
            db.upsert_file(
                file_rec["workspace_id"], file_rec["var_name"], file_rec["original_name"],
                ver["file_path"], ver["nrow"], result["schema"], file_stats,
            )

    return {"ok": True}


# ── Join ──────────────────────────────────────────────────────────────────────

import re as _re

_IDENT_RE = _re.compile(r'^[A-Za-z_][A-Za-z0-9_.]{0,99}$')


def _require_identifier(name: str, label: str) -> None:
    """Raise 400 if name is not a safe R/Python identifier."""
    if not _IDENT_RE.match(name):
        raise HTTPException(
            400,
            f"Invalid {label} '{name}': must start with a letter or underscore and "
            "contain only letters, digits, underscores, or dots (max 100 chars).",
        )


def _escape_r_str(s: str) -> str:
    """Escape a value for safe embedding inside an R double-quoted string."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")


def _build_join_code(is_python: bool, left_var: str, right_var: str,
                     left_key: str, right_key: str,
                     join_type: str, output_var: str) -> str:
    # All variable-name args must already be validated by _require_identifier.
    if is_python:
        # Column keys are passed through repr() — safe regardless of content.
        how = "outer" if join_type == "full" else join_type
        return (
            f"{output_var} = {left_var}.merge("
            f"{right_var}, left_on={repr(left_key)}, right_on={repr(right_key)}, how={repr(how)})"
        )
    fn = {"inner": "inner_join", "left": "left_join",
          "right": "right_join", "full": "full_join"}.get(join_type, "inner_join")
    lk = _escape_r_str(left_key)
    rk = _escape_r_str(right_key)
    by = f'"{lk}"' if left_key == right_key else f'c("{lk}" = "{rk}")'
    return f'{output_var} <- dplyr::{fn}({left_var}, {right_var}, by = {by})'


class JoinPreviewRequest(BaseModel):
    session_id: str
    left_var: str
    right_var: str
    left_key: str
    right_key: str
    join_type: str = "inner"


@app.post("/workspace/{workspace_id}/join/preview")
async def join_preview(workspace_id: str, req: JoinPreviewRequest):
    _require_identifier(req.left_var,  "left dataset")
    _require_identifier(req.right_var, "right dataset")
    if req.join_type not in ("inner", "left", "right", "full"):
        raise HTTPException(400, "join_type must be inner, left, right, or full")

    s = store.get(req.session_id)
    if not s:
        raise HTTPException(404, "Session not found")

    from python_session import PythonSession  # noqa: PLC0415
    is_python = isinstance(s.r, PythonSession)

    tmp = "_dl_join_preview"
    code = _build_join_code(is_python, req.left_var, req.right_var,
                            req.left_key, req.right_key, req.join_type, tmp)

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, s.r.execute, code)
    if result.get("error"):
        raise HTTPException(400, result["error"])

    preview = await loop.run_in_executor(None, s.r.get_preview, tmp, 20)

    # Get actual nrow (preview is capped at 20)
    try:
        nrow_raw = await loop.run_in_executor(
            None, s.r._run_raw,
            f"print(len({tmp}))" if is_python else f"cat(nrow({tmp}))",
            5,
        )
        nrow = int(nrow_raw.strip())
    except Exception:
        nrow = len(preview.get("rows", []))

    cleanup = f"del {tmp}" if is_python else f"rm({tmp})"
    await loop.run_in_executor(None, s.r.execute, cleanup)

    return {**preview, "nrow": nrow}


class JoinSaveRequest(BaseModel):
    session_id: str
    left_var: str
    right_var: str
    left_key: str
    right_key: str
    join_type: str = "inner"
    output_var: str


@app.post("/workspace/{workspace_id}/join/save")
async def join_save(workspace_id: str, req: JoinSaveRequest):
    _require_identifier(req.left_var,   "left dataset")
    _require_identifier(req.right_var,  "right dataset")
    _require_identifier(req.output_var, "output variable name")
    if req.join_type not in ("inner", "left", "right", "full"):
        raise HTTPException(400, "join_type must be inner, left, right, or full")

    # Refuse to overwrite an existing active file
    existing = next(
        (f for f in db.get_files(workspace_id) if f["var_name"] == req.output_var),
        None,
    )
    if existing:
        raise HTTPException(
            409,
            f"A dataset named '{req.output_var}' already exists in this workspace. "
            "Choose a different output name.",
        )

    s = store.get(req.session_id)
    if not s:
        raise HTTPException(404, "Session not found")

    from python_session import PythonSession  # noqa: PLC0415
    is_python = isinstance(s.r, PythonSession)

    code = _build_join_code(is_python, req.left_var, req.right_var,
                            req.left_key, req.right_key, req.join_type,
                            req.output_var)

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, s.r.execute, code)
    if result.get("error"):
        raise HTTPException(400, result["error"])

    # Export to temp CSV, then load_file to register stats
    csv_path = await loop.run_in_executor(None, s.r.export_to_csv, req.output_var)
    if not csv_path:
        raise HTTPException(500, "Failed to export join result")

    load_result = await loop.run_in_executor(None, s.r.load_file, csv_path, req.output_var)
    if load_result.get("error"):
        raise HTTPException(500, load_result["error"])

    nrow    = s.r.nrows.get(req.output_var, 0)
    schema  = s.r.schema.get(req.output_var, {})
    stats   = s.r.stats.get(req.output_var, {})

    # Persist to permanent versioned storage
    original_name = f"{req.output_var}.csv"
    fid = db.upsert_file(workspace_id, req.output_var, original_name,
                         csv_path, nrow, schema, stats)
    versions_dir = FILES_DIR / workspace_id / "versions" / fid
    versions_dir.mkdir(parents=True, exist_ok=True)
    perm_path = versions_dir / "v1.csv"
    shutil.copy(csv_path, str(perm_path))

    desc = (f"{req.join_type} join of {req.left_var} × {req.right_var} "
            f"on {req.left_key}/{req.right_key}")
    vid = db.create_dataset_version(file_id=fid, version_num=1,
                                    file_path=str(perm_path), nrow=nrow,
                                    description=desc, run_id=None, is_original=True)
    db.set_current_version(fid, vid, str(perm_path), nrow)
    db.upsert_file(workspace_id, req.output_var, original_name,
                   str(perm_path), nrow, schema, stats)

    s.loaded_vars.add(req.output_var)

    return {"file_id": fid, "var_name": req.output_var, "nrow": nrow, "schema": schema}


# ── Data contracts / assertions ───────────────────────────────────────────────

def _run_assertions(file_path: str, assertions: list[dict], file_id: str, run_id: str | None = None) -> list[dict]:
    """Run a list of assertion dicts against a CSV file using pandas. Returns result dicts."""
    import pandas as pd  # noqa: PLC0415

    def _err(a: dict, msg: str) -> dict:
        return {"assertion_id": a["id"], "file_id": file_id, "run_id": run_id,
                "passed": False, "failure_count": -1, "sample_failures": [msg]}

    try:
        df = pd.read_csv(file_path, low_memory=False)
    except Exception as e:
        return [_err(a, f"Could not read file: {e}") for a in assertions]

    results = []
    for a in assertions:
        ct   = a["check_type"]
        col  = a.get("column_name")
        p    = a.get("params") or {}
        aid  = a["id"]

        try:
            # ── column-level helpers ──────────────────────────────────────────
            if ct in ("unique", "not_null", "gte", "gt", "lte", "lt",
                      "date_parseable", "in_set", "regex"):
                if col not in df.columns:
                    results.append(_err(a, f"Column '{col}' not found in dataset"))
                    continue
                series = df[col]

                if ct == "unique":
                    dupes = series[series.duplicated(keep=False)]
                    passed = len(dupes) == 0
                    samples = [str(v) for v in dupes.head(5).tolist()]
                    results.append({"assertion_id": aid, "file_id": file_id, "run_id": run_id,
                                    "passed": passed, "failure_count": int(len(dupes)),
                                    "sample_failures": samples})

                elif ct == "not_null":
                    nulls = series[series.isna()]
                    passed = len(nulls) == 0
                    results.append({"assertion_id": aid, "file_id": file_id, "run_id": run_id,
                                    "passed": passed, "failure_count": int(len(nulls)),
                                    "sample_failures": [f"row {i}" for i in nulls.index[:5].tolist()]})

                elif ct in ("gte", "gt", "lte", "lt"):
                    threshold = float(p.get("value", 0))
                    numeric = pd.to_numeric(series, errors="coerce")
                    ops = {"gte": numeric >= threshold, "gt": numeric > threshold,
                           "lte": numeric <= threshold, "lt": numeric < threshold}
                    bad = series[~ops[ct] | numeric.isna()]
                    passed = len(bad) == 0
                    results.append({"assertion_id": aid, "file_id": file_id, "run_id": run_id,
                                    "passed": passed, "failure_count": int(len(bad)),
                                    "sample_failures": [str(v) for v in bad.head(5).tolist()]})

                elif ct == "date_parseable":
                    fmt = p.get("format")
                    def _try_parse(v):
                        try:
                            if fmt:
                                pd.to_datetime(v, format=fmt)
                            else:
                                pd.to_datetime(v, infer_datetime_format=True)
                            return True
                        except Exception:
                            return False
                    mask = ~series.map(_try_parse)
                    bad = series[mask]
                    passed = len(bad) == 0
                    results.append({"assertion_id": aid, "file_id": file_id, "run_id": run_id,
                                    "passed": passed, "failure_count": int(len(bad)),
                                    "sample_failures": [str(v) for v in bad.head(5).tolist()]})

                elif ct == "in_set":
                    allowed = set(str(x) for x in p.get("values", []))
                    bad = series[~series.astype(str).isin(allowed)]
                    passed = len(bad) == 0
                    results.append({"assertion_id": aid, "file_id": file_id, "run_id": run_id,
                                    "passed": passed, "failure_count": int(len(bad)),
                                    "sample_failures": [str(v) for v in bad.head(5).tolist()]})

                elif ct == "regex":
                    pattern = p.get("pattern", ".*")
                    import re as _re_mod  # noqa: PLC0415
                    bad = series[~series.astype(str).str.match(pattern, na=False)]
                    passed = len(bad) == 0
                    results.append({"assertion_id": aid, "file_id": file_id, "run_id": run_id,
                                    "passed": passed, "failure_count": int(len(bad)),
                                    "sample_failures": [str(v) for v in bad.head(5).tolist()]})

            elif ct == "row_count_gte":
                threshold = int(p.get("value", 0))
                passed = len(df) >= threshold
                results.append({"assertion_id": aid, "file_id": file_id, "run_id": run_id,
                                 "passed": passed, "failure_count": 0 if passed else 1,
                                 "sample_failures": [] if passed else [f"{len(df)} rows < {threshold}"]})

            else:
                results.append(_err(a, f"Unknown check_type '{ct}'"))

        except Exception as exc:
            results.append(_err(a, f"Check error: {exc}"))

    return results


class CreateAssertionRequest(BaseModel):
    name: str
    check_type: str
    column_name: str | None = None
    params: dict = {}
    enabled: bool = True


class PatchAssertionRequest(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    params: dict | None = None


@app.get("/file/{file_id}/assertions")
def list_assertions(file_id: str):
    file_rec = db.get_file(file_id)
    if not file_rec:
        raise HTTPException(404, "File not found")
    assertions = db.get_assertions(file_id)
    latest = db.get_latest_assertion_results(file_id)
    for a in assertions:
        a["last_result"] = latest.get(a["id"])
    return assertions


@app.post("/file/{file_id}/assertions")
def create_assertion_endpoint(file_id: str, req: CreateAssertionRequest):
    file_rec = db.get_file(file_id)
    if not file_rec:
        raise HTTPException(404, "File not found")
    valid_types = {"unique", "not_null", "gte", "gt", "lte", "lt",
                   "date_parseable", "in_set", "regex", "row_count_gte"}
    if req.check_type not in valid_types:
        raise HTTPException(400, f"check_type must be one of: {', '.join(sorted(valid_types))}")
    aid = db.create_assertion(file_id, req.name, req.check_type,
                              req.column_name, req.params, req.enabled)
    return {"id": aid}


@app.patch("/file/{file_id}/assertion/{assertion_id}")
def patch_assertion(file_id: str, assertion_id: str, req: PatchAssertionRequest):
    a = db.get_assertion(assertion_id)
    if not a or a["file_id"] != file_id:
        raise HTTPException(404, "Assertion not found")
    kwargs = {}
    if req.name is not None:
        kwargs["name"] = req.name
    if req.enabled is not None:
        kwargs["enabled"] = req.enabled
    if req.params is not None:
        kwargs["params"] = req.params
    if kwargs:
        db.update_assertion(assertion_id, **kwargs)
    return {"ok": True}


@app.delete("/file/{file_id}/assertion/{assertion_id}")
def delete_assertion_endpoint(file_id: str, assertion_id: str):
    a = db.get_assertion(assertion_id)
    if not a or a["file_id"] != file_id:
        raise HTTPException(404, "Assertion not found")
    db.delete_assertion(assertion_id)
    return {"ok": True}


@app.post("/file/{file_id}/check")
async def run_file_checks(file_id: str):
    """Run all enabled assertions for a file right now. Returns results list."""
    file_rec = db.get_file(file_id)
    if not file_rec:
        raise HTTPException(404, "File not found")
    assertions = [a for a in db.get_assertions(file_id) if a.get("enabled", True)]
    if not assertions:
        return {"results": [], "passed": 0, "failed": 0}

    file_path = file_rec["file_path"]
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(
        None, _run_assertions, file_path, assertions, file_id, None
    )
    db.save_assertion_results(results)

    passed = sum(1 for r in results if r["passed"])
    return {
        "results": results,
        "passed": passed,
        "failed": len(results) - passed,
    }


# ── Direct import (local path or URL) ─────────────────────────────────────────

class ImportRequest(BaseModel):
    session_id: str
    source: str                   # absolute local path or http(s) URL
    var_name: str = ""            # auto-derived from filename if empty
    mode: str = "new"             # "new" | "append"
    target_file_id: str = ""      # required when mode="append"
    description: str = ""


@app.post("/workspace/{workspace_id}/import")
async def import_data(workspace_id: str, req: ImportRequest):
    """
    Import data into the workspace from a local file path or a URL.

    mode="new"    — registers the file as a brand-new dataset (like /upload but no browser limit).
    mode="append" — appends the new rows to an existing dataset, creating a new version.
    """
    import urllib.request  # noqa: PLC0415

    # Fix 1: require an existing session that belongs to this workspace.
    # get_or_create would silently mint a fresh session for any bogus id,
    # letting a caller mix one workspace's session with another's storage.
    s = store.get(req.session_id)
    if not s:
        raise HTTPException(404, "Session not found. Upload a file first to start a session.")
    if s.workspace_id != workspace_id:
        raise HTTPException(403, "session_id does not belong to this workspace")

    source  = req.source.strip()
    is_url  = source.lower().startswith(("http://", "https://"))

    raw_name = Path(source.split("?")[0]).name or "import"
    var_name = req.var_name.strip() or sanitise_name(raw_name)
    suffix   = Path(source.split("?")[0]).suffix.lower() or ".csv"

    ws_dir = FILES_DIR / workspace_id
    ws_dir.mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_event_loop()

    # ── 1. Fetch / copy source to a staging path ──────────────────────────────
    # Append mode uses a temp name to avoid a permanent artefact in the workspace dir.
    staging = (ws_dir / f"_import_tmp_{uuid.uuid4().hex[:8]}{suffix}"
               if req.mode == "append"
               else ws_dir / f"{var_name}{suffix}")

    if is_url:
        try:
            await loop.run_in_executor(
                None, lambda: urllib.request.urlretrieve(source, str(staging))
            )
        except Exception as e:
            raise HTTPException(400, f"Download failed: {e}")
    else:
        src = Path(source)
        if not src.exists():
            raise HTTPException(400, f"File not found: {source}")
        if not src.is_file():
            raise HTTPException(400, f"Path is not a file: {source}")
        await loop.run_in_executor(None, shutil.copy, str(src), str(staging))

    # ── 2a. New file ──────────────────────────────────────────────────────────
    if req.mode == "new":
        # Fix 4: reject on name collision — upsert_file would silently overwrite.
        conflict = next((f for f in db.get_files(workspace_id) if f["var_name"] == var_name), None)
        if conflict:
            staging.unlink(missing_ok=True)
            raise HTTPException(
                409,
                f"A dataset named '{var_name}' already exists in this workspace. "
                "Choose a different variable name.",
            )

        result = await loop.run_in_executor(None, s.r.load_file, str(staging), var_name)
        if result.get("error"):
            staging.unlink(missing_ok=True)
            raise HTTPException(400, result["error"])

        s.loaded_vars.add(var_name)
        file_stats = s.r.stats.get(var_name, {})
        file_id = db.upsert_file(
            workspace_id, var_name, raw_name,
            str(staging), result["nrow"], result["schema"], file_stats,
        )
        if not db.get_dataset_versions(file_id):
            vid = db.create_dataset_version(
                file_id=file_id, version_num=1, file_path=str(staging),
                nrow=result["nrow"],
                description=req.description.strip() or f"Imported from {source}",
                is_original=True,
            )
            db.init_file_version(file_id, vid)
        db.touch_workspace(workspace_id)
        return {
            "id": file_id,
            "var_name": var_name,
            "nrow": result["nrow"],
            "schema": result["schema"],
            "version_num": 1,
            "current_version_seq": 1,
        }

    # ── 2b. Append to existing file ───────────────────────────────────────────
    if req.mode != "append":
        staging.unlink(missing_ok=True)
        raise HTTPException(400, "mode must be 'new' or 'append'")

    target_rec = db.get_file(req.target_file_id)
    if not target_rec or target_rec["workspace_id"] != workspace_id:
        staging.unlink(missing_ok=True)
        raise HTTPException(404, "Target file not found")

    await loop.run_in_executor(None, s.ensure_files_loaded)

    # Load the incoming data into a uniquely-named temp variable
    tmp_var = f"_dl_import_{uuid.uuid4().hex[:6]}"
    load_result = await loop.run_in_executor(None, s.r.load_file, str(staging), tmp_var)
    staging.unlink(missing_ok=True)  # staging file no longer needed after load

    if load_result.get("error"):
        raise HTTPException(400, f"Could not load import file: {load_result['error']}")

    s.loaded_vars.add(tmp_var)
    existing_var = target_rec["var_name"]
    new_nrow_raw = load_result["nrow"]

    # Combine in the live session, then always clean up tmp_var from both
    # the runtime env AND the executor metadata caches (schema/stats/nrows/
    # loaded_files).  Without drop_file() the temp name leaks into get_context()
    # and appears as a phantom dataset in future agent prompts.
    from python_session import PythonSession  # noqa: PLC0415
    is_python = isinstance(s.r, PythonSession)
    if is_python:
        combine_code = (
            f"import pandas as _pd\n"
            f"{existing_var} = _pd.concat([{existing_var}, {tmp_var}], ignore_index=True)\n"
            f"del {tmp_var}"
        )
    else:
        combine_code = (
            f"{existing_var} <- rbind({existing_var}, {tmp_var})\n"
            f"rm({tmp_var})"
        )

    try:
        r_result = await loop.run_in_executor(None, s.r.execute, combine_code)
    finally:
        # Fix 2: purge tmp_var from executor metadata unconditionally.
        # combine_code already ran rm/del inside the runtime, so drop_file's
        # runtime rm is a no-op — but it is the only way to clear the cached
        # schema/stats/nrows/loaded_files entries that load_file() registered.
        s.r.drop_file(tmp_var)
        s.loaded_vars.discard(tmp_var)

    if r_result.get("error"):
        raise HTTPException(400, f"Combine failed: {r_result['error']}")

    # Export the combined variable to a permanent versioned path
    csv_path = await loop.run_in_executor(None, s.r.export_to_csv, existing_var)
    if not csv_path:
        raise HTTPException(500, "Failed to export combined dataset")

    fid = target_rec["id"]
    versions_dir = FILES_DIR / workspace_id / "versions" / fid
    versions_dir.mkdir(parents=True, exist_ok=True)
    version_num = db.get_next_version_num(fid)
    new_path = versions_dir / f"v{version_num}.csv"
    shutil.copy(csv_path, str(new_path))

    # Fix 3: reload from the permanent CSV so schema/stats/nrow caches are
    # authoritative.  s.r.stats / s.r.schema are only refreshed by load_file(),
    # not by execute(), so reading them after the in-place rbind/concat gives
    # stale values if the append changed columns or distributions.
    reload = await loop.run_in_executor(None, s.r.load_file, str(new_path), existing_var)
    if not reload.get("error"):
        s.loaded_vars.add(existing_var)
    combined_nrow = reload.get("nrow") or (target_rec["nrow"] + new_nrow_raw)
    schema     = reload.get("schema") or target_rec.get("col_schema") or {}
    file_stats = s.r.stats.get(existing_var, {})

    desc = req.description.strip() or f"Appended {new_nrow_raw} rows from {raw_name}"
    vid = db.create_dataset_version(
        file_id=fid, version_num=version_num,
        file_path=str(new_path), nrow=combined_nrow,
        description=desc,
    )
    db.set_current_version(fid, vid, str(new_path), combined_nrow)
    db.prune_dataset_versions(fid)
    db.upsert_file(workspace_id, existing_var, target_rec["original_name"],
                   str(new_path), combined_nrow, schema, file_stats)
    db.touch_workspace(workspace_id)
    return {
        "id": fid,
        "var_name": existing_var,
        "nrow": combined_nrow,
        "version_num": version_num,
        "appended_nrow": new_nrow_raw,
    }


# ── Storage / cleanup ──────────────────────────────────────────────────────────

@app.get("/workspace/{workspace_id}/storage")
def get_storage_stats(workspace_id: str):
    workspace = db.get_workspace(workspace_id)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    stats = db.get_workspace_storage_stats(workspace_id)
    return {**stats, "workspace_name": workspace["name"]}


@app.post("/workspace/{workspace_id}/cleanup/chat")
def cleanup_chat(workspace_id: str):
    n = db.delete_chat_history(workspace_id)
    return {"ok": True, "deleted": n}


@app.post("/workspace/{workspace_id}/cleanup/archived_files")
def cleanup_archived_files(workspace_id: str):
    # Remove from live session too
    s = store.get(workspace_id)
    archived = db.get_files(workspace_id, include_archived=True)
    if s:
        for f in archived:
            if f.get("archived_at"):
                s.r.drop_file(f["var_name"])
                s.loaded_vars.discard(f["var_name"])
    n = db.delete_archived_files_permanent(workspace_id)
    return {"ok": True, "deleted": n}


@app.post("/workspace/{workspace_id}/cleanup/old_versions")
def cleanup_old_versions(workspace_id: str):
    n = db.prune_all_dataset_versions(workspace_id)
    return {"ok": True, "deleted": n}


@app.post("/workspace/{workspace_id}/cleanup/run_artifacts")
def cleanup_run_artifacts(workspace_id: str):
    n = db.delete_run_artifacts(workspace_id)
    return {"ok": True, "deleted": n}


@app.post("/workspace/{workspace_id}/cleanup/run_history")
def cleanup_run_history(workspace_id: str):
    n = db.delete_run_history(workspace_id)
    return {"ok": True, "deleted": n}


@app.delete("/workspace/{workspace_id}")
def delete_workspace_endpoint(workspace_id: str):
    s = store.get(workspace_id)
    if s:
        s.close()
    store.delete(workspace_id)
    db.delete_workspace(workspace_id)
    # Remove workspace directory from FILES_DIR
    ws_dir = FILES_DIR / workspace_id
    if ws_dir.exists():
        shutil.rmtree(str(ws_dir), ignore_errors=True)
    return {"ok": True}


@app.post("/workspace/{workspace_id}/stop")
def stop_workspace_run(workspace_id: str):
    """Signal the currently-running agent to stop immediately."""
    s = store.get(workspace_id)
    if s:
        s.get_abort_event().set()
    return {"ok": True}


@app.get("/health")
def health():
    return {"ok": True}


# Serve built frontend — mounted LAST so API routes take priority.
_frontend = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend.exists():
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="ui")
