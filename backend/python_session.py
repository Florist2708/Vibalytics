"""
Persistent Python subprocess session for vibalytics.

Duck-types RSession so main.py can use either via s.r without changes.
One Python worker process per session; data (pandas DataFrames) stays in memory.

Key differences from R:
  - Plots: matplotlib plt.show() → numbered PNGs in temp dir
  - Tables: print(df) → HTML saved to table_dir, same as R's print.data.frame override
  - Version proposals: dl_propose_version() prints same markers as R version
  - Stats: pandas dtype names instead of R class names
"""

import base64
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

PLOT_DIR    = Path("/tmp/vibalytics_py_plots")
TABLE_DIR   = Path("/tmp/vibalytics_py_tables")
EXPORT_DIR  = Path("/tmp/vibalytics_py_exports")
VERSION_DIR = Path("/tmp/vibalytics_py_versions")
for _d in (PLOT_DIR, TABLE_DIR, EXPORT_DIR, VERSION_DIR):
    _d.mkdir(exist_ok=True)

ERR_START = "__DL_ERR_S__"
ERR_END   = "__DL_ERR_E__"
VER_START = "__DL_VER_PROPOSE__"
VER_END   = "__DL_VER_END__"

EXEC_PREFIX = "__DL_PY_EXEC__ "
END_PREFIX  = "__DL_PY_END__ "
DONE_PREFIX = "__DL_PY_DONE__ "


def _reader_thread(proc_stdout, q: "queue.Queue[str | None]") -> None:
    """Drain subprocess stdout into a queue, one line at a time."""
    fd  = proc_stdout.fileno()
    buf = b""
    try:
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                q.put(None)
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                q.put(line.decode("utf-8", errors="replace"))
    except OSError:
        q.put(None)


# Per-call stats query code (same output format as R version for shared parsing)
_PY_STATS_CODE = """
_df = {name}
_nr = len(_df)
print(f"__NR__ {{_nr}}")
for _col in _df.columns:
    _x = _df[_col]
    _nm = int(_x.isna().sum())
    _mp = round(100 * _nm / _nr, 1) if _nr > 0 else 0.0
    _dt = str(_x.dtype)
    if _dt in ('float64','float32','float16','int64','int32','int16','int8',
               'uint64','uint32','uint16','uint8'):
        _v = _x.dropna()
        if len(_v) > 0:
            print(f"{{_col}}||numeric||{{_mp}}||"
                  f"{{round(float(_v.min()),3)}}||{{round(float(_v.mean()),3)}}||{{round(float(_v.max()),3)}}")
        else:
            print(f"{{_col}}||numeric||{{_mp}}||NA||NA||NA")
    else:
        _nu = int(_x.dropna().nunique())
        print(f"{{_col}}||{{_dt}}||{{_mp}}||{{_nu}}")
"""


class PythonSession:
    def __init__(self):
        self.schema: dict[str, dict] = {}
        self.stats:  dict[str, dict] = {}
        self.nrows:  dict[str, int]  = {}
        self.loaded_files: list[str] = []
        self._lock = threading.Lock()
        self._start_proc()

    # ── Process lifecycle ─────────────────────────────────────────────────────

    def _start_proc(self) -> None:
        worker = Path(__file__).parent / "py_worker.py"
        self.proc = subprocess.Popen(
            [sys.executable, str(worker)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        self._q: "queue.Queue[str | None]" = queue.Queue()
        threading.Thread(
            target=_reader_thread,
            args=(self.proc.stdout, self._q),
            daemon=True,
        ).start()

    def _kill_and_restart(self) -> None:
        """Kill frozen worker and restart. MUST be called while lock is held."""
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

    # ── Low-level I/O ─────────────────────────────────────────────────────────

    def _send_and_wait(self, code: str, timeout: float,
                       output_queue=None) -> str:
        """
        Send a code block to the worker and collect its stdout until the done marker.
        Does NOT acquire the lock — callers are responsible.
        """
        sentinel   = uuid.uuid4().hex
        end_marker = f"{END_PREFIX}{sentinel}"
        done_marker = f"{DONE_PREFIX}{sentinel}"

        payload = f"{EXEC_PREFIX}{sentinel}\n{code}\n{end_marker}\n"
        self.proc.stdin.write(payload.encode())
        self.proc.stdin.flush()

        lines:    list[str] = []
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Python worker frozen for {timeout:.0f}s")
            try:
                line = self._q.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Python worker frozen for {timeout:.0f}s")
                continue

            if line is None:
                raise RuntimeError("Python worker exited unexpectedly")
            if line == done_marker:
                break
            lines.append(line)
            if output_queue is not None:
                output_queue.put(line)

        if output_queue is not None:
            output_queue.put(None)
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
        Execute Python code in the persistent session.

        Returns the same dict shape as RSession.execute():
          output   (str)
          plots    (list[base64 PNG string])
          tables   (list[HTML string])   — empty for Python for now
          exports  (list[tuple[filename, bytes]])
          error    (str | None)
          proposals (list[dict])
          version_dir (str | None)
        """
        plot_dir    = PLOT_DIR    / uuid.uuid4().hex
        table_dir   = TABLE_DIR   / uuid.uuid4().hex
        export_dir  = EXPORT_DIR  / uuid.uuid4().hex
        version_dir = VERSION_DIR / uuid.uuid4().hex
        for d in (plot_dir, table_dir, export_dir, version_dir):
            d.mkdir()

        # Inject per-call dirs into the worker namespace before user code runs
        setup = (
            f'_dl_plot_dir    = "{plot_dir}"\n'
            f'_dl_plot_n[0]   = 0\n'
            f'_dl_table_dir   = "{table_dir}"\n'
            f'_dl_table_n[0]  = 0\n'
            f'_dl_export_dir  = "{export_dir}"\n'
            f'_dl_version_dir = "{version_dir}"\n'
        )
        full_code = setup + code

        try:
            raw = self._run_raw(full_code, timeout=timeout, output_queue=output_queue)
        except (TimeoutError, RuntimeError) as e:
            if output_queue is not None:
                output_queue.put(None)
            shutil.rmtree(plot_dir,    ignore_errors=True)
            shutil.rmtree(table_dir,   ignore_errors=True)
            shutil.rmtree(export_dir,  ignore_errors=True)
            shutil.rmtree(version_dir, ignore_errors=True)
            return {"output": "", "plots": [], "tables": [], "exports": [],
                    "error": str(e), "proposals": [], "version_dir": None}

        # Collect plots
        plots = []
        for f in sorted(plot_dir.glob("*.png")):
            if f.stat().st_size > 500:
                plots.append(base64.b64encode(f.read_bytes()).decode())
        shutil.rmtree(plot_dir, ignore_errors=True)

        # Collect tables (HTML written by print(df) override)
        tables: list[str] = []
        for f in sorted(table_dir.glob("*.html")):
            tables.append(f.read_text(encoding="utf-8"))
        shutil.rmtree(table_dir, ignore_errors=True)

        # Collect exports (CSVs written by df.to_csv() override)
        exports: list[tuple[str, bytes]] = []
        for f in sorted(export_dir.glob("*")):
            exports.append((f.name, f.read_bytes()))
        shutil.rmtree(export_dir, ignore_errors=True)

        # Parse version proposals (same markers as R session)
        proposals: list[dict] = []
        clean_lines: list[str] = []
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
                clean_lines.append(line)
            idx += 1

        if not proposals:
            shutil.rmtree(version_dir, ignore_errors=True)
            version_dir_str = None
        else:
            version_dir_str = str(version_dir)

        output_text = "\n".join(clean_lines).strip()

        # Extract error block (same format as R: ERR_START...ERR_END)
        error = None
        if ERR_START in output_text and ERR_END in output_text:
            i = output_text.index(ERR_START) + len(ERR_START)
            j = output_text.index(ERR_END)
            error = output_text[i:j].strip()
            output_text = (
                output_text[: output_text.index(ERR_START)]
                + output_text[output_text.index(ERR_END) + len(ERR_END):]
            ).strip()

        return {
            "output": output_text, "plots": plots, "tables": tables, "exports": exports,
            "error": error, "proposals": proposals, "version_dir": version_dir_str,
        }

    def _compute_stats(self, name: str) -> tuple[int, dict]:
        code = _PY_STATS_CODE.format(name=name)
        try:
            raw = self._run_raw(code, timeout=15)
        except Exception:
            return 0, {}

        nrow  = 0
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
                typ        = parts[1]
                miss_pct_s = parts[2]
                try:
                    miss_pct = float(miss_pct_s)
                except ValueError:
                    miss_pct = 0.0
                if typ == "numeric" and len(parts) >= 6:
                    stats[col] = {
                        "type": "numeric", "miss_pct": miss_pct,
                        "min": parts[3], "mean": parts[4], "max": parts[5],
                    }
                elif len(parts) >= 4:
                    try:
                        n_unique = int(parts[3])
                    except ValueError:
                        n_unique = 0
                    stats[col] = {"type": typ, "miss_pct": miss_pct, "n_unique": n_unique}
                else:
                    stats[col] = {"type": typ, "miss_pct": miss_pct}
        return nrow, stats

    def load_file(self, path: str, name: str = "df") -> dict:
        """Load CSV / Excel / Parquet / Stata into the persistent namespace."""
        ext = Path(path).suffix.lower()
        if ext == ".csv":
            load_code = f"{name} = pd.read_csv('{path}')"
        elif ext in (".xlsx", ".xls"):
            load_code = f"{name} = pd.read_excel('{path}')"
        elif ext == ".parquet":
            load_code = f"{name} = pd.read_parquet('{path}')"
        elif ext == ".dta":
            load_code = (
                f"try:\n"
                f"    import pyreadstat as _prs\n"
                f"    {name}, _ = _prs.read_dta('{path}')\n"
                f"    {name} = pd.DataFrame({name})\n"
                f"except Exception as _e:\n"
                f"    raise RuntimeError(f'Cannot load .dta: {{_e}}')"
            )
        else:
            return {"error": f"Unsupported file type: {ext}"}

        result = self.execute(load_code, timeout=30)
        if result["error"]:
            return {"error": result["error"]}

        # Retrieve schema via a quick introspection run
        schema_code = (
            f"_df = {name}\n"
            f"print(f'__NROW__{{len(_df)}}__NROW__')\n"
            f"for _c, _t in _df.dtypes.items():\n"
            f"    print(f'{{_c}}::{{_t}}')\n"
        )
        try:
            raw = self._run_raw(schema_code, timeout=10)
        except Exception as e:
            return {"error": str(e)}

        schema: dict[str, str] = {}
        nrow = 0
        for line in raw.splitlines():
            if "__NROW__" in line:
                try:
                    nrow = int(line.split("__NROW__")[1].strip())
                except (IndexError, ValueError):
                    pass
            elif "::" in line:
                col, dtype = line.split("::", 1)
                schema[col.strip()] = dtype.strip()

        self.schema[name] = schema

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
        """Build structured dataset summary for the agent prompt."""
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

        label  = "Active datasets" if active_files is not None else "Loaded datasets"
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
        code = (
            f"_df = {name}.head({n})\n"
            f"print('\\t'.join(str(c) for c in _df.columns))\n"
            f"print('__SEP__')\n"
            f"for _, _row in _df.iterrows():\n"
            f"    print('\\t'.join('' if (str(v) == 'nan' or str(v) == 'NaT') else str(v) for v in _row))\n"
        )
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

    def get_data(
        self, name: str, offset: int = 0, limit: int = 100,
        sort_by: str = "", sort_dir: str = "asc",
        filter_col: str = "", filter_val: str = "",
        filter_op: str = "contains",
    ) -> dict:
        sc = sort_by.replace("'", "\\'")
        fc = filter_col.replace("'", "\\'")
        fv = filter_val.replace("\\", "\\\\").replace("'", "\\'")

        lines = [f"_df = {name}.copy()"]
        if sort_by:
            asc = "True" if sort_dir == "asc" else "False"
            lines.append(f"_df = _df.sort_values('{sc}', ascending={asc}, na_position='last')")
        if filter_col:
            if filter_op == "is_null":
                lines.append(f"_df = _df[_df['{fc}'].isna() | (_df['{fc}'].astype(str) == '')]")
            elif filter_op == "not_null":
                lines.append(f"_df = _df[~_df['{fc}'].isna() & (_df['{fc}'].astype(str) != '')]")
            elif filter_val:
                if filter_op in (">", ">=", "<", "<="):
                    lines += [
                        f"try:",
                        f"    _cn = _df['{fc}'].apply(lambda x: float(x) if x==x else None)",
                        f"    _tv = float('{fv}')",
                        f"    _df = _df[_cn.notna() & (_cn {filter_op} _tv)]",
                        f"except: pass",
                    ]
                elif filter_op == "=":
                    lines.append(f"_df = _df[_df['{fc}'].astype(str) == '{fv}']")
                elif filter_op == "!=":
                    lines.append(f"_df = _df[_df['{fc}'].astype(str) != '{fv}']")
                elif filter_op == "starts_with":
                    lines.append(f"_df = _df[_df['{fc}'].astype(str).str.startswith('{fv}', na=False)]")
                elif filter_op == "ends_with":
                    lines.append(f"_df = _df[_df['{fc}'].astype(str).str.endswith('{fv}', na=False)]")
                else:  # contains
                    lines.append(
                        f"_df = _df[_df['{fc}'].astype(str).str.contains('{fv}', case=False, na=False)]"
                    )
        lines += [
            f"_total = len(_df)",
            f"print(f'__TOTAL__ {{_total}}')",
            f"_s = _df.iloc[{offset}:{offset + limit}]",
            f"print('\\t'.join(str(c) for c in _s.columns))",
            f"print('__SEP__')",
            f"for _, _row in _s.iterrows():",
            f"    print('\\t'.join('' if (str(v)=='nan' or str(v)=='NaT') else str(v) for v in _row))",
        ]
        code = "\n".join(lines)
        try:
            raw = self._run_raw(code, timeout=15)
        except Exception as e:
            return {"error": str(e)}

        total_rows = 0
        columns:   list[str] = []
        rows:      list[list[str]] = []
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

    def get_column_detail(self, name: str, col: str, bins: int = 10) -> dict:
        sc = col.replace("'", "\\'")
        code = (
            f"_df = {name}\n"
            f"_x = _df['{sc}']\n"
            f"_dt = str(_x.dtype)\n"
            f"_n = len(_x)\n"
            f"_nm = int(_x.isna().sum())\n"
            f"print(f'TYPE: {{_dt}}')\n"
            f"print(f'TOTAL: {{_n}}')\n"
            f"print(f'MISSING: {{_nm}}')\n"
            f"if _dt in ('float64','float32','int64','int32','int16','int8','uint64','uint32','uint16','uint8'):\n"
            f"    _v = _x.dropna()\n"
            f"    if len(_v) > 0:\n"
            f"        print(f'MIN: {{float(_v.min())}}')\n"
            f"        print(f'MAX: {{float(_v.max())}}')\n"
            f"        print(f'MEAN: {{round(float(_v.mean()),4)}}')\n"
            f"        print(f'MEDIAN: {{float(_v.median())}}')\n"
            f"        print(f'SD: {{round(float(_v.std()),4)}}')\n"
            f"        import numpy as _np\n"
            f"        _h = _np.histogram(_v, bins={bins})\n"
            f"        print('BREAKS:', ','.join(str(b) for b in _h[1]))\n"
            f"        print('COUNTS:', ','.join(str(c) for c in _h[0]))\n"
            f"else:\n"
            f"    _top = _x.dropna().value_counts().head(20)\n"
            f"    for _val, _cnt in _top.items():\n"
            f"        print(f'VAL: {{_val}} || {{_cnt}}')\n"
            f"_samp = _x.dropna().head(5)\n"
            f"print('SAMPLE:', '||'.join(str(v) for v in _samp))\n"
        )
        try:
            raw = self._run_raw(code, timeout=10)
        except Exception as e:
            return {"error": str(e)}

        result: dict = {
            "type": "", "total": 0, "missing": 0,
            "histogram": None, "value_counts": [], "sample": [],
        }
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
                result.setdefault("histogram", {})["breaks"] = [
                    float(x) for x in line[7:].strip().split(",") if x
                ]
            elif line.startswith("COUNTS:"):
                result.setdefault("histogram", {})["counts"] = [
                    int(x) for x in line[7:].strip().split(",") if x
                ]
            elif line.startswith("VAL:") and "||" in line:
                rest = line[4:]
                parts = rest.rsplit("||", 1)
                if len(parts) == 2:
                    result["value_counts"].append({
                        "value": parts[0].strip(),
                        "count": int(parts[1].strip()),
                    })
            elif line.startswith("SAMPLE:"):
                result["sample"] = [v for v in line[7:].strip().split("||") if v]
        return result

    def export_to_csv(self, name: str) -> str | None:
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        tmp.close()
        path = tmp.name.replace("\\", "/")
        code = f"{name}.to_csv('{path}', index=False)"
        try:
            self._run_raw(code, timeout=15)
            return path if Path(path).exists() else None
        except Exception:
            return None

    def drop_file(self, name: str) -> None:
        """Remove variable from the worker namespace."""
        try:
            self._run_raw(
                f"exec('del {name}', globals()) if '{name}' in globals() else None",
                timeout=5,
            )
        except Exception:
            pass
        self.schema.pop(name, None)
        self.stats.pop(name, None)
        self.nrows.pop(name, None)
        if name in self.loaded_files:
            self.loaded_files.remove(name)

    def hide_vars(self, names: list[str]) -> None:
        """Temporarily remove variables from the worker namespace."""
        if not names:
            return
        dels = "\n".join(
            f"exec('del {n}', globals()) if '{n}' in globals() else None"
            for n in names
        )
        try:
            self._run_raw(dels, timeout=5)
        except Exception:
            pass

    def restore_vars(self, file_records: list[dict]) -> None:
        """Reload variables that were hidden by hide_vars()."""
        for f in file_records:
            try:
                self.load_file(f["file_path"], f["var_name"])
            except Exception:
                pass

    def get_env_snapshot(self) -> dict:
        """
        Return runtime metadata for reproducibility: Python version, working dir,
        and versions of data-science packages present in the worker environment.
        Only tracks a curated list of analysis-relevant packages so the output
        stays readable (not the full pip freeze).
        """
        _TRACKED = repr({
            'pandas', 'numpy', 'matplotlib', 'scipy', 'scikit-learn', 'seaborn',
            'statsmodels', 'polars', 'pyarrow', 'openpyxl', 'xlrd',
            'plotly', 'bokeh', 'altair', 'xgboost', 'lightgbm', 'catboost',
        })
        code = f"""
import sys as _sys, os as _os
try:
    import importlib.metadata as _im
    _tracked = {_TRACKED}
    _pkgs = {{d.name.lower(): d.version for d in _im.distributions()
              if d.name.lower() in _tracked}}
except Exception:
    _pkgs = {{}}
print("PY_VER:" + _sys.version.replace("\\n", " "))
print("PY_WD:" + _os.getcwd())
for _k, _v in sorted(_pkgs.items()):
    print("PKG:" + _k + "=" + _v)
"""
        try:
            raw = self._run_raw(code, timeout=10.0)
        except Exception:
            return {"runtime": "Python (version unknown)", "packages": {}, "working_dir": ""}
        result: dict = {"runtime": "", "packages": {}, "working_dir": ""}
        for line in raw.splitlines():
            if line.startswith("PY_VER:"):
                result["runtime"] = "Python " + line[7:].strip()
            elif line.startswith("PY_WD:"):
                result["working_dir"] = line[6:].strip()
            elif line.startswith("PKG:"):
                name, _, ver = line[4:].partition("=")
                if name:
                    result["packages"][name] = ver
        if not result["runtime"]:
            result["runtime"] = "Python (version unknown)"
        return result

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
