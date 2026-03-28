#!/usr/bin/env python3
"""
Persistent Python worker subprocess for vibalytics.

Protocol (stdin/stdout):
  Parent → stdin:  "__DL_PY_EXEC__ <sentinel>\\n<code>\\n__DL_PY_END__ <sentinel>\\n"
  Worker → stdout: "<output lines>\\n__DL_PY_DONE__ <sentinel>\\n"

Markers embedded in output:
  __DL_ERR_S__<traceback>__DL_ERR_E__   on exception
  __DL_VER_PROPOSE__ / __DL_VER_END__   dataset version proposals

Intercepts set up at startup (in _ns):
  plt.show()              → saves figure to _dl_plot_dir/{n}.png, closes it
  plt.get_fignums()       → unclosed figures saved after each exec call
  builtins.print(df)      → DataFrame → HTML file in _dl_table_dir + text to stdout
  pd.DataFrame.to_csv()   → copy written CSV to _dl_export_dir
"""
import io
import os
import sys
import traceback

EXEC_PREFIX = "__DL_PY_EXEC__ "
END_PREFIX  = "__DL_PY_END__ "
DONE_PREFIX = "__DL_PY_DONE__ "
ERR_START   = "__DL_ERR_S__"
ERR_END     = "__DL_ERR_E__"

# Shared namespace — persists across all execute() calls in this process
_ns: dict = {}

# Executed after every user exec() to capture unclosed matplotlib figures
_COLLECT_UNCLOSED = """\
if _HAS_MPL and _dl_plot_dir:
    for _fn in plt.get_fignums():
        _dl_plot_n[0] += 1
        plt.figure(_fn)
        plt.savefig(
            os.path.join(_dl_plot_dir, "%03d.png" % _dl_plot_n[0]),
            dpi=120, bbox_inches="tight",
        )
    plt.close("all")
"""


def _init_ns() -> None:
    # Use triple single-quotes so inner """docstrings""" don't close this string
    code = '''
import pandas as pd
import numpy as np
import os, sys, re, math, json
import builtins as _builtins
from pathlib import Path

_dl_plot_dir    = ""
_dl_plot_n      = [0]
_dl_table_dir   = ""
_dl_table_n     = [0]
_dl_export_dir  = ""
_dl_version_dir = ""

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False

try:
    import seaborn as sns
    _HAS_SNS = True
except ImportError:
    _HAS_SNS = False


# Capture original to_csv before overriding (dl_propose_version uses this
# directly to avoid copying proposal files into the export dir)
_orig_to_csv = pd.DataFrame.to_csv


def dl_propose_version(df, var_name, description=""):
    # Propose a modified version of a dataset (Python equivalent of R dl_propose_version)
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    if _dl_version_dir:
        fpath = os.path.join(_dl_version_dir, "proposal_" + var_name + ".csv")
        _orig_to_csv(df, fpath, index=False)
        print("__DL_VER_PROPOSE__")
        print("var_name: " + str(var_name))
        print("description: " + str(description))
        print("nrow: " + str(len(df)))
        print("file: " + fpath)
        print("__DL_VER_END__")
    return df


if _HAS_MPL:
    def _dl_plt_show(*args, **kwargs):
        if _dl_plot_dir:
            _dl_plot_n[0] += 1
            path = os.path.join(_dl_plot_dir, "%03d.png" % _dl_plot_n[0])
            plt.savefig(path, dpi=120, bbox_inches="tight")
            plt.close("all")
    plt.show = _dl_plt_show


# Print override: only intercept the exact "single DataFrame, no kwargs" path.
# Any call with custom sep/end/file/flush falls straight through.
_orig_print = _builtins.print

def _dl_print(*args, **kwargs):
    if (
        _dl_table_dir
        and not kwargs
        and len(args) == 1
        and isinstance(args[0], pd.DataFrame)
    ):
        _dl_table_n[0] += 1
        fpath = os.path.join(_dl_table_dir, "%03d.html" % _dl_table_n[0])
        with open(fpath, "w") as _f:
            _f.write(args[0].to_html(index=False))
        _orig_print(args[0].to_string())
    else:
        _orig_print(*args, **kwargs)

_builtins.print = _dl_print


# to_csv override: copies any written CSV into _dl_export_dir.
# Re-entrancy flag prevents double-copy if pandas internals call to_csv again.
# Only acts on plain string/path targets; file-like objects are left alone.
_dl_to_csv_active = [False]

def _dl_to_csv(self, path_or_buf=None, *args, **kwargs):
    if _dl_to_csv_active[0]:
        return _orig_to_csv(self, path_or_buf, *args, **kwargs)
    _dl_to_csv_active[0] = True
    try:
        result = _orig_to_csv(self, path_or_buf, *args, **kwargs)
        if _dl_export_dir and isinstance(path_or_buf, (str, os.PathLike)):
            try:
                import shutil as _sh
                dest = os.path.join(_dl_export_dir, os.path.basename(str(path_or_buf)))
                if os.path.abspath(str(path_or_buf)) != os.path.abspath(dest):
                    _sh.copy2(path_or_buf, dest)
            except Exception:
                pass
        return result
    finally:
        _dl_to_csv_active[0] = False

pd.DataFrame.to_csv = _dl_to_csv
'''
    exec(code, _ns)  # noqa: S102


_init_ns()


def main() -> None:
    # Force line-buffered output so each line reaches the parent immediately
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

    while True:
        line = sys.stdin.readline()
        if not line:
            break  # EOF — parent closed stdin
        line = line.rstrip("\n")

        if not line.startswith(EXEC_PREFIX):
            continue

        sentinel   = line[len(EXEC_PREFIX):]
        end_marker = f"{END_PREFIX}{sentinel}"

        # Collect code lines until the per-call end marker
        code_lines: list[str] = []
        while True:
            raw = sys.stdin.readline()
            if not raw:
                break
            raw = raw.rstrip("\n")
            if raw == end_marker:
                break
            code_lines.append(raw)

        code = "\n".join(code_lines)

        # Redirect stdout so we capture print() output from user code
        real_out  = sys.stdout
        captured  = io.StringIO()
        sys.stdout = captured
        error: str | None = None

        try:
            exec(code, _ns)  # noqa: S102
        except Exception:
            error = traceback.format_exc().strip()
        finally:
            sys.stdout = real_out
            # Collect unclosed figures even when user code raised — prevents leaking
            # figures between calls. Runs after stdout is restored (disk-only operation).
            try:
                exec(_COLLECT_UNCLOSED, _ns)  # noqa: S102
            except Exception:
                pass

        # Forward captured output line by line
        cap = captured.getvalue()
        if cap:
            for ln in cap.splitlines():
                print(ln, flush=True)

        # Emit error marker (keeps same format as R session for shared parsing)
        if error:
            print(f"{ERR_START}{error}{ERR_END}", flush=True)

        # Done sentinel — signals end of this call's output
        print(f"{DONE_PREFIX}{sentinel}", flush=True)


if __name__ == "__main__":
    main()
