"""
Persistent R subprocess session.

Design notes:
  - One R process per Session; kept alive between queries (data stays in memory)
  - Reader thread drains stdout into a queue (avoids TextIOWrapper buffering issue
    where select() sees empty OS pipe even when Python holds unread data)
  - Queue is per-process: each _start_proc() creates a fresh queue and reader
    thread so old-process EOF can't pollute a new session
  - _kill_and_restart() is called while the lock is held, so it calls
    _send_and_wait() directly instead of _run_raw() to avoid deadlock
  - setTimeLimit() lets R interrupt itself cleanly; Python-side timeout is a
    safety net for truly frozen processes
  - Plot capture strategy:
      ggplot2: grid.newpage hook opens a numbered PNG for each plot
      base R:  a _bg.png device is pre-opened; plot.new hook writes a _drawn
               flag so Python knows to include it (avoids collecting blank PNGs)
  - .dl_cleanup() is called AFTER tryCatch (not in finally) so ggplot objects
    returned by tryCatch are auto-printed before devices are closed
"""

import base64
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

PLOT_DIR    = Path("/tmp/vibalytics_plots")
TABLE_DIR   = Path("/tmp/vibalytics_tables")
EXPORT_DIR  = Path("/tmp/vibalytics_exports")
VERSION_DIR = Path("/tmp/vibalytics_versions")

for _d in (PLOT_DIR, TABLE_DIR, EXPORT_DIR, VERSION_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _resolve_r_libs_dir() -> Path:
    """Prefer persistent user storage, but support read-only container homes."""
    configured = os.environ.get("VIBALYTICS_R_LIBS")
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend([
        Path.home() / ".vibalytics" / "r_libs",
        Path("/tmp/vibalytics_r_libs"),
    ])
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    raise RuntimeError("No writable directory is available for R packages")


R_LIBS_DIR = _resolve_r_libs_dir()

# Base R packages that are always available — never try to install these
_BASE_PKGS = frozenset({
    "base", "stats", "utils", "methods", "datasets", "grDevices", "graphics",
    "tools", "parallel", "grid", "compiler", "tcltk", "splines",
})

SENTINEL  = "__DL_DONE__"
ERR_START = "__DL_ERR_S__"
ERR_END   = "__DL_ERR_E__"
VER_START = "__DL_VER_PROPOSE__"
VER_END   = "__DL_VER_END__"

_R_LIBPATH_INIT = (
    f'.dl_libs <- "{R_LIBS_DIR}"\n'
    f'if (!.dl_libs %in% .libPaths()) .libPaths(c(.dl_libs, .libPaths()))\n'
)

R_INIT = _R_LIBPATH_INIT + """
options(warn = 1, scipen = 999)
suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(tidyr)
  library(readr)
  library(readxl)
  if (requireNamespace("haven", quietly = TRUE)) library(haven)
})

# ── Plot capture ──────────────────────────────────────────────────────────────
.dl_plot_dir <- ""
.dl_plot_n   <- 0L
.dl_cur_dev  <- NULL

# grid.newpage fires before each ggplot2 page.
# Close the previous ggplot device and open a fresh numbered PNG so every
# ggplot gets its own file. .dl_cur_dev tracks only ggplot2 devices.
setHook("grid.newpage", function() {
  if (nchar(.dl_plot_dir) > 0) {
    if (!is.null(.dl_cur_dev) && grDevices::dev.cur() == .dl_cur_dev)
      tryCatch(grDevices::dev.off(), error = function(e) NULL)
    .dl_plot_n <<- .dl_plot_n + 1L
    path <- file.path(.dl_plot_dir, sprintf("%03d.png", .dl_plot_n))
    grDevices::png(path, width = 960, height = 600, res = 120)
    .dl_cur_dev <<- grDevices::dev.cur()
  }
}, action = "replace")

# plot.new fires for base-R graphics (plot, hist, barplot, etc.).
# We don't open a device here (opening mid-hook confuses R's graphics state);
# instead we write a flag file so Python knows _bg.png has real content.
setHook("plot.new", function() {
  if (nchar(.dl_plot_dir) > 0)
    tryCatch(
      file.create(file.path(.dl_plot_dir, "_drawn")),
      error = function(e) NULL
    )
}, action = "replace")

.dl_cleanup <- function() {
  # Close the last ggplot2 device if still open
  if (!is.null(.dl_cur_dev) && grDevices::dev.cur() == .dl_cur_dev)
    tryCatch(grDevices::dev.off(), error = function(e) NULL)
  .dl_cur_dev <<- NULL
  # Close any remaining open devices (e.g. background base-R device)
  while (grDevices::dev.cur() != 1L)
    tryCatch(grDevices::dev.off(), error = function(e) break)
}

# ── Table capture ─────────────────────────────────────────────────────────────
.dl_table_dir <- ""
.dl_table_n   <- 0L

.dl_esc <- function(x) {
  x <- as.character(x)
  x <- gsub("&", "&amp;", x, fixed = TRUE)
  x <- gsub("<", "&lt;",  x, fixed = TRUE)
  x <- gsub(">", "&gt;",  x, fixed = TRUE)
  x
}

.dl_df_html <- function(df, max_rows = 500L) {
  if (!is.data.frame(df)) df <- as.data.frame(df)
  if (nrow(df) > max_rows) df <- head(df, max_rows)
  cols   <- names(df)
  header <- paste0("<th>", sapply(cols, .dl_esc), "</th>", collapse = "")
  body   <- paste0(vapply(seq_len(nrow(df)), function(i) {
    cells <- paste0(vapply(cols, function(col) {
      v <- df[[col]][i]
      if (is.na(v)) "<td><em>NA</em></td>"
      else paste0("<td>", .dl_esc(v), "</td>")
    }, character(1L)), collapse = "")
    paste0("<tr>", cells, "</tr>")
  }, character(1L)), collapse = "")
  paste0('<table class="r-table"><thead><tr>', header,
         '</tr></thead><tbody>', body, '</tbody></table>')
}

.dl_save_table <- function(x) {
  .dl_table_n <<- .dl_table_n + 1L
  path <- file.path(.dl_table_dir, sprintf("%03d.html", .dl_table_n))
  writeLines(.dl_df_html(as.data.frame(x)), path)
  cat(sprintf("[Table: %d rows x %d cols]\n", nrow(x), ncol(x)))
}

print.data.frame <- function(x, ...) {
  if (nchar(.dl_table_dir) > 0) .dl_save_table(x)
  else base::print.data.frame(x, ...)
}

# Capture tibble prints (tbl_df) the same way
print.tbl_df <- function(x, ...) {
  if (nchar(.dl_table_dir) > 0) .dl_save_table(x)
  else base::print.data.frame(as.data.frame(x), ...)
}

# ── Export capture ────────────────────────────────────────────────────────────
.dl_export_dir <- ""

write.csv <- function(x, file, ...) {
  utils::write.csv(x, file, ...)
  if (nchar(.dl_export_dir) > 0 && is.character(file))
    tryCatch(
      file.copy(file, file.path(.dl_export_dir, basename(file)), overwrite = TRUE),
      error = function(e) NULL
    )
  invisible(NULL)
}

# ── Dataset version proposals ─────────────────────────────────────────────────
.dl_version_dir <- ""

dl_propose_version <- function(df, var_name, description = "") {
  if (!is.data.frame(df)) df <- as.data.frame(df)
  if (nchar(.dl_version_dir) > 0) {
    fname <- paste0("proposal_", var_name, ".csv")
    fpath <- file.path(.dl_version_dir, fname)
    utils::write.csv(df, fpath, row.names = FALSE)
    cat(sprintf(
      "%s\nvar_name: %s\ndescription: %s\nnrow: %d\nfile: %s\n%s\n",
      "__DL_VER_PROPOSE__", var_name, description, nrow(df), fpath, "__DL_VER_END__"
    ))
  }
  invisible(df)
}
"""

# R code run once after each file load to gather per-column stats.
# Outputs: __NR__ N __NR__ on first line, then one line per column:
#   colname||type||miss_pct[||min||mean||max  (numeric)]
#                           [||n_unique       (categorical)]
_STATS_CODE = """
local({{
  .df <- {name}
  .nr <- nrow(.df)
  cat("__NR__", .nr, "\\n")
  for (.i in seq_len(ncol(.df))) {{
    .col <- names(.df)[.i]
    .x   <- .df[[.i]]
    .nm  <- sum(is.na(.x))
    .mp  <- if (.nr > 0) round(100 * .nm / .nr, 1) else 0
    if (is.numeric(.x)) {{
      .v <- .x[!is.na(.x)]
      if (length(.v) > 0) {{
        cat(.col, "||numeric||", .mp, "||",
            round(min(.v), 3), "||", round(mean(.v), 3), "||", round(max(.v), 3),
            "\\n", sep = "")
      }} else {{
        cat(.col, "||numeric||", .mp, "||NA||NA||NA\\n", sep = "")
      }}
    }} else {{
      .nu <- length(unique(.x[!is.na(.x)]))
      cat(.col, "||", class(.x)[1], "||", .mp, "||", .nu, "\\n", sep = "")
    }}
  }}
}})
"""


def _reader_thread(proc_stdout, q: "queue.Queue[str | None]"):
    """
    Background thread: read stdout from R into `q` one line at a time.

    Uses os.read() for direct syscalls — avoids TextIOWrapper's read-ahead
    that would drain the OS pipe buffer and make select() misleadingly empty.
    Each call to os.read() blocks until data arrives then returns ≤4096 bytes.
    """
    fd  = proc_stdout.fileno()
    buf = b""
    try:
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:           # EOF: R process exited
                q.put(None)
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                q.put(line.decode("utf-8", errors="replace"))
    except OSError:
        q.put(None)


class RSession:
    def __init__(self):
        self.schema: dict[str, dict] = {}
        self.stats:  dict[str, dict] = {}   # per-col stats computed at load
        self.nrows:  dict[str, int]  = {}   # row counts per variable
        self.loaded_files: list[str] = []
        self._lock = threading.Lock()
        self._start_proc()
        self._run_raw(R_INIT, timeout=60)

    # ── Process lifecycle ─────────────────────────────────────────────────────

    def _start_proc(self):
        self.proc = subprocess.Popen(
            ["Rscript", "--vanilla", "--quiet", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        # Fresh queue per process — old-process EOF won't contaminate new session
        self._q: "queue.Queue[str | None]" = queue.Queue()
        threading.Thread(
            target=_reader_thread,
            args=(self.proc.stdout, self._q),
            daemon=True,
        ).start()

    def _kill_and_restart(self):
        """Kill frozen R process and start fresh. MUST be called while lock is held."""
        try:
            self.proc.kill()
            self.proc.wait(timeout=3)
        except Exception:
            pass
        self.schema.clear()
        self.stats.clear()
        self.nrows.clear()
        self.loaded_files.clear()
        self._start_proc()
        try:
            self._send_and_wait(R_INIT, timeout=60)   # direct call — lock already held
        except Exception:
            pass

    # ── Low-level I/O ─────────────────────────────────────────────────────────

    def _send_and_wait(self, code: str, timeout: float,
                       output_queue=None) -> str:
        """
        Write code to R stdin and collect stdout until the sentinel line.
        Does NOT acquire the lock — callers are responsible for locking.
        If output_queue is provided, each output line is also put there as
        it arrives (for real-time streaming to the caller).
        """
        marker  = f"{SENTINEL}_{uuid.uuid4().hex}"
        payload = f"{code}\ncat('\\n{marker}\\n', sep = '')\n"

        self.proc.stdin.write(payload.encode())
        self.proc.stdin.flush()

        lines    = []
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"R frozen for {timeout:.0f}s")
            try:
                line = self._q.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"R frozen for {timeout:.0f}s")
                continue

            if line is None:
                raise RuntimeError("R process exited unexpectedly")
            if line == marker:
                break
            lines.append(line)
            if output_queue is not None:
                output_queue.put(line)

        if output_queue is not None:
            output_queue.put(None)  # signal end of stream
        return "\n".join(lines)

    def _run_raw(self, code: str, timeout: float = 30.0,
                 output_queue=None) -> str:
        """Thread-safe wrapper around _send_and_wait."""
        with self._lock:
            try:
                return self._send_and_wait(code, timeout, output_queue=output_queue)
            except (TimeoutError, RuntimeError) as exc:
                self._kill_and_restart()
                raise exc

    # ── Public API ─────────────────────────────────────────────────────────────

    def execute(self, code: str, timeout: float = 60.0,
                output_queue=None) -> dict:
        """
        Execute R code in the persistent session.

        Returns dict:
          output   (str)
          plots    (list[base64 PNG string])
          tables   (list[HTML string])
          exports  (list[tuple[filename, bytes]])
          error    (str | None)
        """
        plot_dir    = PLOT_DIR    / uuid.uuid4().hex
        table_dir   = TABLE_DIR   / uuid.uuid4().hex
        export_dir  = EXPORT_DIR  / uuid.uuid4().hex
        version_dir = VERSION_DIR / uuid.uuid4().hex
        plot_dir.mkdir()
        table_dir.mkdir()
        export_dir.mkdir()
        version_dir.mkdir()
        # Give R slightly less time than our Python timeout so setTimeLimit
        # fires before we kill the process.
        r_timeout = max(timeout - 1.0, 2.0)

        # _bg.png: background device for base-R plots (plot, hist, etc.)
        # Numbered PNGs (001.png, ...): one per ggplot2 page via grid.newpage hook.
        # .dl_cleanup() is OUTSIDE finally so ggplot objects returned by tryCatch
        # are auto-printed before devices close.
        wrapped = f"""
.dl_plot_dir    <- "{plot_dir}"
.dl_plot_n      <- 0L
.dl_cur_dev     <- NULL
.dl_table_dir   <- "{table_dir}"
.dl_table_n     <- 0L
.dl_export_dir  <- "{export_dir}"
.dl_version_dir <- "{version_dir}"
grDevices::png(file.path(.dl_plot_dir, "_bg.png"), width = 960, height = 600, res = 120)
setTimeLimit(elapsed = {r_timeout:.0f}, transient = TRUE)
tryCatch({{
  {code}
}}, error = function(e) {{
  cat("{ERR_START}", conditionMessage(e), "{ERR_END}\\n")
}}, finally = {{
  setTimeLimit()
  .dl_table_dir   <- ""
  .dl_export_dir  <- ""
  .dl_version_dir <- ""
}})
.dl_cleanup()
"""
        try:
            raw = self._run_raw(wrapped, timeout=timeout, output_queue=output_queue)
        except (TimeoutError, RuntimeError) as e:
            if output_queue is not None:
                output_queue.put(None)  # unblock consumer on error
            shutil.rmtree(plot_dir,    ignore_errors=True)
            shutil.rmtree(table_dir,   ignore_errors=True)
            shutil.rmtree(export_dir,  ignore_errors=True)
            shutil.rmtree(version_dir, ignore_errors=True)
            return {"output": "", "plots": [], "tables": [], "exports": [],
                    "error": str(e), "proposals": [], "version_dir": None}

        # Collect plots:
        # - Numbered files are ggplot2 output (one per grid.newpage call)
        # - _bg.png is base-R output; only include if _drawn flag was created
        plots = []
        bg_file    = plot_dir / "_bg.png"
        drawn_flag = plot_dir / "_drawn"
        if drawn_flag.exists() and bg_file.exists() and bg_file.stat().st_size > 500:
            plots.append(base64.b64encode(bg_file.read_bytes()).decode())
        for f in sorted(plot_dir.glob("*.png")):
            if f.name == "_bg.png":
                continue
            if f.stat().st_size > 500:
                plots.append(base64.b64encode(f.read_bytes()).decode())
        shutil.rmtree(plot_dir, ignore_errors=True)

        # Collect tables (HTML files written by print.data.frame override)
        tables = []
        for f in sorted(table_dir.glob("*.html")):
            tables.append(f.read_text(encoding="utf-8"))
        shutil.rmtree(table_dir, ignore_errors=True)

        # Collect exports (files written via write.csv override)
        exports = []
        for f in sorted(export_dir.iterdir()):
            exports.append((f.name, f.read_bytes()))
        shutil.rmtree(export_dir, ignore_errors=True)

        # Extract version proposals (strips __DL_VER_PROPOSE__...__DL_VER_END__ blocks)
        proposals: list[dict] = []
        after_proposal_lines: list[str] = []
        idx = 0
        raw_lines = raw.splitlines()
        while idx < len(raw_lines):
            line = raw_lines[idx]
            if line.strip() == VER_START:
                prop: dict = {}
                idx += 1
                while idx < len(raw_lines) and raw_lines[idx].strip() != VER_END:
                    if ": " in raw_lines[idx]:
                        k, v = raw_lines[idx].split(": ", 1)
                        prop[k.strip()] = v.strip()
                    idx += 1
                if "var_name" in prop and "file" in prop:
                    try:
                        prop["nrow"] = int(prop.get("nrow", 0))
                    except ValueError:
                        prop["nrow"] = 0
                    proposals.append(prop)
            else:
                after_proposal_lines.append(line)
            idx += 1

        # Clean up version_dir only if no proposals (proposals need it until accepted/rejected)
        if not proposals:
            shutil.rmtree(version_dir, ignore_errors=True)
            version_dir_str = None
        else:
            version_dir_str = str(version_dir)

        # Strip dev.off() noise ("null device" / lone "1")
        clean = [
            l for l in after_proposal_lines
            if (l.strip()
                and "null device" not in l.lower()
                and l.strip() != "1")
            or ERR_START in l
            or ERR_END in l
        ]
        output = "\n".join(clean).strip()

        # Extract R error block
        error = None
        if ERR_START in output and ERR_END in output:
            i = output.index(ERR_START) + len(ERR_START)
            j = output.index(ERR_END)
            error = output[i:j].strip()
            output = (
                output[: output.index(ERR_START)]
                + output[output.index(ERR_END) + len(ERR_END):]
            ).strip()

        return {
            "output": output, "plots": plots, "tables": tables, "exports": exports,
            "error": error, "proposals": proposals, "version_dir": version_dir_str,
        }

    def _compute_stats(self, name: str) -> tuple[int, dict]:
        """
        Run a stats query for variable `name` in R.
        Returns (nrow, stats_dict).  Safe to call — errors return (0, {}).
        """
        code = _STATS_CODE.format(name=name)
        try:
            raw = self._run_raw(code, timeout=15)
        except Exception:
            return 0, {}

        nrow = 0
        stats: dict[str, dict] = {}
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("__NR__"):
                try:
                    nrow = int(line.split()[1])
                except (IndexError, ValueError):
                    pass
            elif "||" in line:
                parts = line.split("||")
                col = parts[0]
                if len(parts) < 3:
                    continue
                typ      = parts[1]
                miss_pct_s = parts[2]
                try:
                    miss_pct = float(miss_pct_s)
                except ValueError:
                    miss_pct = 0.0
                if typ == "numeric" and len(parts) >= 6:
                    stats[col] = {
                        "type": "numeric",
                        "miss_pct": miss_pct,
                        "min":  parts[3],
                        "mean": parts[4],
                        "max":  parts[5],
                    }
                elif len(parts) >= 4:
                    try:
                        n_unique = int(parts[3])
                    except ValueError:
                        n_unique = 0
                    stats[col] = {
                        "type": typ,
                        "miss_pct": miss_pct,
                        "n_unique": n_unique,
                    }
                else:
                    stats[col] = {"type": typ, "miss_pct": miss_pct}
        return nrow, stats

    def load_file(self, path: str, name: str = "df") -> dict:
        """Load CSV / Excel / Stata / RDS as R variable `name`."""
        ext = Path(path).suffix.lower()
        if ext == ".csv":
            cmd = f'{name} <- readr::read_csv("{path}", show_col_types = FALSE)'
        elif ext in (".xlsx", ".xls"):
            cmd = f'{name} <- readxl::read_excel("{path}")'
        elif ext == ".dta":
            cmd = f'{name} <- haven::read_dta("{path}")'
        elif ext == ".rds":
            cmd = f'{name} <- readRDS("{path}")'
        else:
            return {"error": f"Unsupported file type: {ext}"}

        result = self.execute(cmd, timeout=30)
        if result["error"]:
            return {"error": result["error"]}

        schema_raw = self._run_raw(
            f"""
cat(paste(names({name}),
          sapply({name}, function(x) class(x)[1]),
          sep = "::", collapse = "\\n"))
cat("\\n__NROW__", nrow({name}), "__NROW__\\n")
""",
            timeout=10,
        )

        schema: dict[str, str] = {}
        nrow = 0
        for line in schema_raw.splitlines():
            if "__NROW__" in line:
                nrow = int(line.split("__NROW__")[1].strip())
            elif "::" in line:
                col, dtype = line.split("::", 1)
                schema[col.strip()] = dtype.strip()

        self.schema[name] = schema

        # Compute and cache per-column stats for richer agent context.
        nrow_stats, stats = self._compute_stats(name)
        if nrow_stats:
            nrow = nrow_stats
        self.stats[name]  = stats
        self.nrows[name]  = nrow

        if name not in self.loaded_files:
            self.loaded_files.append(name)

        return {"schema": schema, "nrow": nrow, "error": None}

    def get_context(self, active_files: list[str] | None = None,
                    file_notes: dict[str, str] | None = None) -> str:
        """
        Build a structured summary of loaded datasets for the agent prompt.
        If active_files is given, only include those datasets.
        Includes row counts, per-column types, missingness, basic stats, and
        any user-authored notes (schema memory).
        """
        files_to_use = (
            [f for f in self.loaded_files if f in active_files]
            if active_files is not None
            else self.loaded_files
        )
        if not files_to_use:
            return "No data loaded yet." if not self.loaded_files else "No active files selected."

        sections = []
        col_to_datasets: dict[str, list[str]] = {}

        for name in files_to_use:
            schema = self.schema.get(name, {})
            nrow   = self.nrows.get(name, "?")
            stats  = self.stats.get(name, {})

            col_lines = []
            for col, dtype in schema.items():
                s    = stats.get(col, {})
                miss = s.get("miss_pct", 0)
                miss_str = f", {miss}% NA" if miss > 0 else ""
                if s.get("type") == "numeric":
                    col_lines.append(
                        f"    {col} [numeric{miss_str}]"
                        f"  min={s.get('min','?')}  mean={s.get('mean','?')}  max={s.get('max','?')}"
                    )
                elif "n_unique" in s:
                    col_lines.append(
                        f"    {col} [{s['type']}{miss_str}]  {s['n_unique']} unique values"
                    )
                else:
                    col_lines.append(f"    {col} [{dtype}{miss_str}]")

                col_to_datasets.setdefault(col, []).append(name)

            header = f"  {name}  ({nrow} rows, {len(schema)} cols)"
            notes = (file_notes or {}).get(name, "").strip()
            if notes:
                header += f"\n  Notes: {notes}"
            sections.append(header + "\n" + "\n".join(col_lines))

        label = "Active datasets" if active_files is not None else "Loaded datasets"
        result = f"{label}:\n" + "\n\n".join(sections)

        join_hints = [
            f"    {col}: {' ↔ '.join(dsets)}"
            for col, dsets in col_to_datasets.items()
            if len(dsets) >= 2
        ]
        if join_hints:
            result += "\n\nPotential join keys (shared column names):\n" + "\n".join(join_hints)

        return result

    def get_preview(self, name: str, n: int = 20) -> dict:
        """
        Return the first `n` rows of variable `name` as {columns, rows}.
        Used by the /preview endpoint (data inspector).
        """
        code = f"""
local({{
  .h <- head({name}, {n})
  cat(paste(names(.h), collapse = "\\t"), "\\n__SEP__\\n")
  for (.i in seq_len(nrow(.h))) {{
    .r <- sapply(.h[.i, ], function(x) if (is.na(x)) "" else as.character(x))
    cat(paste(.r, collapse = "\\t"), "\\n")
  }}
}})
"""
        try:
            raw = self._run_raw(code, timeout=10)
        except Exception as e:
            return {"error": str(e)}

        columns: list[str] = []
        rows: list[list[str]] = []
        past_sep = False
        for line in raw.splitlines():
            if not past_sep:
                if line == "__SEP__":
                    past_sep = True
                else:
                    columns = line.split("\t")
            else:
                rows.append(line.split("\t"))

        return {"columns": columns, "rows": rows}

    def get_data(self, name: str, offset: int = 0, limit: int = 100,
                sort_by: str = "", sort_dir: str = "asc",
                filter_col: str = "", filter_val: str = "",
                filter_op: str = "contains") -> dict:
        """
        Return a paginated slice of variable `name`.
        Returns {columns, rows, total_rows}.

        filter_op values: contains, =, !=, starts_with, ends_with,
                          is_null, not_null, >, >=, <, <=
        """
        sort_code = ""
        if sort_by:
            dir_str = "TRUE" if sort_dir == "desc" else "FALSE"
            safe = sort_by.replace("'", "\\'")
            sort_code = (
                f".df <- .df[order(.df[['{safe}']], decreasing = {dir_str},"
                f" na.last = TRUE), , drop = FALSE]"
            )

        filter_code = ""
        if filter_col:
            safe_col = filter_col.replace("'", "\\'")
            if filter_op == "is_null":
                filter_code = (
                    f".df <- .df[is.na(.df[['{safe_col}']]) | "
                    f"as.character(.df[['{safe_col}']]) == '', , drop = FALSE]"
                )
            elif filter_op == "not_null":
                filter_code = (
                    f".df <- .df[!is.na(.df[['{safe_col}']]) & "
                    f"as.character(.df[['{safe_col}']]) != '', , drop = FALSE]"
                )
            elif filter_val:
                safe_val = filter_val.replace("'", "\\'").replace('"', '\\"')
                if filter_op in (">", ">=", "<", "<="):
                    filter_code = (
                        f".dl_nc <- suppressWarnings(as.numeric(as.character(.df[['{safe_col}']])))\n"
                        f"  .dl_tv <- suppressWarnings(as.numeric('{safe_val}'))\n"
                        f"  if (!is.na(.dl_tv)) .df <- .df[!is.na(.dl_nc) & .dl_nc {filter_op} .dl_tv, , drop = FALSE]"
                    )
                elif filter_op == "=":
                    filter_code = (
                        f".df <- .df[as.character(.df[['{safe_col}']]) == '{safe_val}', , drop = FALSE]"
                    )
                elif filter_op == "!=":
                    filter_code = (
                        f".df <- .df[as.character(.df[['{safe_col}']]) != '{safe_val}', , drop = FALSE]"
                    )
                elif filter_op == "starts_with":
                    filter_code = (
                        f".df <- .df[startsWith(tolower(as.character(.df[['{safe_col}']])),"
                        f" tolower('{safe_val}')), , drop = FALSE]"
                    )
                elif filter_op == "ends_with":
                    filter_code = (
                        f".df <- .df[endsWith(tolower(as.character(.df[['{safe_col}']])),"
                        f" tolower('{safe_val}')), , drop = FALSE]"
                    )
                else:  # contains (default)
                    filter_code = (
                        f".df <- .df[grepl('{safe_val}', as.character(.df[['{safe_col}']]), "
                        f"ignore.case = TRUE, fixed = FALSE), , drop = FALSE]"
                    )

        code = f"""
local({{
  .df <- as.data.frame({name})
  {sort_code}
  {filter_code}
  .total <- nrow(.df)
  cat("__TOTAL__", .total, "\\n")
  .start <- {offset + 1}
  .end   <- min({offset + limit}, .total)
  if (.total > 0 && .start <= .total) {{
    .slice <- .df[.start:.end, , drop = FALSE]
    cat(paste(names(.slice), collapse = "\\t"), "\\n__SEP__\\n")
    for (.i in seq_len(nrow(.slice))) {{
      .r <- sapply(.slice[.i, ], function(x) if (is.na(x)) "" else as.character(x))
      cat(paste(.r, collapse = "\\t"), "\\n")
    }}
  }} else {{
    cat(paste(names(.df), collapse = "\\t"), "\\n__SEP__\\n")
  }}
}})
"""
        try:
            raw = self._run_raw(code, timeout=15)
        except Exception as e:
            return {"error": str(e)}

        total_rows = 0
        columns: list[str] = []
        rows: list[list[str]] = []
        past_sep = False

        for line in raw.splitlines():
            if line.startswith("__TOTAL__"):
                try:
                    total_rows = int(line.split()[1])
                except (IndexError, ValueError):
                    pass
            elif not past_sep:
                if line == "__SEP__":
                    past_sep = True
                else:
                    columns = line.split("\t")
            else:
                rows.append(line.split("\t"))

        return {"columns": columns, "rows": rows, "total_rows": total_rows}

    def drop_file(self, name: str):
        """Remove variable `name` from the R session."""
        try:
            self._run_raw(f"tryCatch(rm({name}), error = function(e) NULL)", timeout=5)
        except Exception:
            pass
        self.schema.pop(name, None)
        self.stats.pop(name, None)
        self.nrows.pop(name, None)
        if name in self.loaded_files:
            self.loaded_files.remove(name)

    def get_column_detail(self, name: str, col: str, bins: int = 10) -> dict:
        """Return detailed stats for one column: histogram (numeric) or value counts (categorical)."""
        safe_col = col.replace("'", "\\'")
        code = f"""
local({{
  .df <- as.data.frame({name})
  .x  <- .df[['{safe_col}']]
  .typ <- class(.x)[1]
  .nm  <- sum(is.na(.x))
  .n   <- length(.x)
  cat("TYPE:", .typ, "\\n")
  cat("TOTAL:", .n, "\\n")
  cat("MISSING:", .nm, "\\n")
  if (is.numeric(.x)) {{
    .v <- .x[!is.na(.x)]
    if (length(.v) > 0) {{
      cat("MIN:", min(.v), "\\n")
      cat("MAX:", max(.v), "\\n")
      cat("MEAN:", round(mean(.v), 4), "\\n")
      cat("MEDIAN:", median(.v), "\\n")
      cat("SD:", round(sd(.v), 4), "\\n")
      .h <- hist(.v, breaks={bins}, plot=FALSE)
      cat("BREAKS:", paste(.h$breaks, collapse=","), "\\n")
      cat("COUNTS:", paste(.h$counts, collapse=","), "\\n")
    }}
  }} else {{
    .tbl <- sort(table(.x[!is.na(.x)]), decreasing=TRUE)
    .top <- head(.tbl, 20)
    for (.i in seq_along(.top)) {{
      cat("VAL:", names(.top)[.i], "||", .top[.i], "\\n")
    }}
  }}
  .sample <- head(.x[!is.na(.x)], 5)
  cat("SAMPLE:", paste(as.character(.sample), collapse="||"), "\\n")
}})
"""
        try:
            raw = self._run_raw(code, timeout=10)
        except Exception as e:
            return {"error": str(e)}

        result: dict = {"type": "", "total": 0, "missing": 0, "histogram": None, "value_counts": [], "sample": []}
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("TYPE:"):
                result["type"] = line[5:].strip()
            elif line.startswith("TOTAL:"):
                result["total"] = int(line[6:].strip())
            elif line.startswith("MISSING:"):
                result["missing"] = int(line[8:].strip())
            elif line.startswith("MIN:"):
                result["min"] = float(line[4:].strip())
            elif line.startswith("MAX:"):
                result["max"] = float(line[4:].strip())
            elif line.startswith("MEAN:"):
                result["mean"] = float(line[5:].strip())
            elif line.startswith("MEDIAN:"):
                result["median"] = float(line[7:].strip())
            elif line.startswith("SD:"):
                result["sd"] = float(line[3:].strip())
            elif line.startswith("BREAKS:"):
                result.setdefault("histogram", {})["breaks"] = [float(x) for x in line[7:].strip().split(",") if x]
            elif line.startswith("COUNTS:"):
                result.setdefault("histogram", {})["counts"] = [int(x) for x in line[7:].strip().split(",") if x]
            elif line.startswith("VAL:") and "||" in line:
                rest = line[4:]
                parts = rest.rsplit("||", 1)
                if len(parts) == 2:
                    result["value_counts"].append({"value": parts[0].strip(), "count": int(parts[1].strip())})
            elif line.startswith("SAMPLE:"):
                result["sample"] = [v for v in line[7:].strip().split("||") if v]

        return result

    def export_to_csv(self, name: str) -> str | None:
        """Export variable as CSV to a temp file. Returns path or None on error."""
        import tempfile
        import os
        tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        tmp.close()
        path = tmp.name.replace("\\", "/")
        code = f'utils::write.csv(as.data.frame({name}), "{path}", row.names=FALSE)'
        try:
            self._run_raw(code, timeout=15)
            return path if os.path.exists(path) else None
        except Exception:
            return None

    def hide_vars(self, names: list[str]) -> None:
        """Remove vars from R global env (temporarily). Call restore_vars after execute()."""
        if not names:
            return
        r_list = ", ".join(f'"{n}"' for n in names)
        try:
            self._run_raw(
                f"suppressWarnings(rm(list=c({r_list}), envir=.GlobalEnv))",
                timeout=5,
            )
        except Exception:
            pass

    def restore_vars(self, file_records: list[dict]) -> None:
        """Reload vars that were hidden by hide_vars()."""
        for f in file_records:
            try:
                self.load_file(f["file_path"], f["var_name"])
            except Exception:
                pass

    def get_env_snapshot(self) -> dict:
        """
        Return runtime metadata for reproducibility: R version, loaded packages
        with versions, and working directory.  Called after code execution so
        any packages loaded by the user's code (library() calls) are included.
        """
        code = r"""
local({
  cat("R_VER:", R.version.string, "\n", sep = "")
  cat("R_WD:", getwd(), "\n", sep = "")
  pkgs <- sort(loadedNamespaces())
  for (p in pkgs) {
    v <- tryCatch(as.character(packageVersion(p)), error = function(e) "?")
    cat("PKG:", p, "=", v, "\n", sep = "")
  }
})
"""
        try:
            raw = self._run_raw(code, timeout=10.0)
        except Exception:
            return {"runtime": "R (version unknown)", "packages": {}, "working_dir": ""}
        result: dict = {"runtime": "", "packages": {}, "working_dir": ""}
        for line in raw.splitlines():
            if line.startswith("R_VER:"):
                result["runtime"] = line[6:].strip()
            elif line.startswith("R_WD:"):
                result["working_dir"] = line[5:].strip()
            elif line.startswith("PKG:"):
                name, _, ver = line[4:].partition("=")
                if name:
                    result["packages"][name] = ver
        if not result["runtime"]:
            result["runtime"] = "R (version unknown)"
        return result

    def is_package_installed(self, pkg: str) -> bool:
        """Return True if the package can be loaded (installed in any lib path)."""
        result = self._run_raw(
            f'cat(requireNamespace("{pkg}", quietly=TRUE), "\\n")',
            timeout=10,
        )
        return result.strip() == "TRUE"

    def install_package(self, pkg: str) -> str | None:
        """Install pkg into the persistent user lib. Returns error string or None."""
        result = self._run_raw(
            f'tryCatch(\n'
            f'  install.packages("{pkg}", lib=.dl_libs,\n'
            f'    repos="https://cloud.r-project.org/", quiet=TRUE),\n'
            f'  warning=function(w) cat("__INSTALL_WARN__", conditionMessage(w), "\\n"),\n'
            f'  error=function(e)   cat("__INSTALL_ERR__",  conditionMessage(e), "\\n")\n'
            f')\n'
            f'cat("__INSTALL_OK__", requireNamespace("{pkg}", quietly=TRUE), "\\n")',
            timeout=300,
        )
        if "__INSTALL_OK__ TRUE" in result:
            return None
        # Extract the actual R error/warning message if present
        for marker in ("__INSTALL_ERR__", "__INSTALL_WARN__"):
            idx = result.find(marker)
            if idx != -1:
                msg = result[idx + len(marker):].split("\n")[0].strip()
                if msg:
                    return msg
        return f"install.packages('{pkg}') failed — package may not exist on CRAN"

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
