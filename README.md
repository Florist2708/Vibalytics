# Vibalytics

An open-source AI data analysis workspace. Upload your datasets, ask questions in plain language, and get transparent, editable, reproducible results produced by whichever AI agent you already have.

> **This is an early prototype.** Core functionality works end-to-end, but rough edges exist and the workflow will improve significantly in upcoming releases. Feedback and contributions are welcome.

---

## What it's for

Vibalytics is built for simplifying data work with CLI agents. You stay in control of the process while the AI writes code which you can inspect, edit, revert and rerun.

Typical workflows:
- Exploratory analysis on a new dataset (distributions, missingness, outliers)
- Cleaning and transforming messy CSVs
- Regression models, survival analysis, mixed-effects models
- Producing publication-quality plots
- Joining multiple datasets and tracking the lineage

---

## Features

### Core
- **Chat-driven analysis** — ask questions in plain language; the agent generates R or Python code and runs it
- **Multi-file workspaces** — upload multiple files across formats; the system builds a structured data context (schema, stats, join hints) before every prompt
  | Format | Extension | R | Python |
  |---|---|---|---|
  | CSV / TSV | `.csv` | ✓ | ✓ |
  | Excel | `.xlsx`, `.xls` | ✓ | ✓ |
  | Stata | `.dta` | ✓ | ✓ |
  | Parquet | `.parquet` | — | ✓ |
  | R data | `.rds` | ✓ | — |
- **Editable generated code** — see every generated script, edit it inline, rerun it, and keep both versions
- **Versioned runs** — reruns create child runs linked to their parent; nothing is overwritten
- **Dataset versioning** — the agent proposes changes to your data; you accept or discard; full version history with diffs

### Transparency
- **Execution trace** — every run shows its internal steps: files parsed → context built → agent called → code executed → outputs saved
- **Data context viewer** — see exactly what context was sent to the agent before each run
- **Reproducibility report** — every run captures runtime version, package versions, and dataset fingerprints; downloadable as a plain-text report
- **Run lineage** — the UI shows when a dataset has changed since a run was produced

### Data inspection
- **Data table** — paginated, sortable, filterable table view with 9 filter operators
- **Column detail** — type, missingness %, distribution histogram, value counts
- **Missingness panel** — bar chart of NA % per column
- **File profile** — inline column stats with type icons and missingness bars
- **Version diff** — compare any two dataset versions cell by cell

### Workflow
- **Background jobs** — submit a long analysis and keep chatting; results stream back when ready
- **Saved workflows** — save any run's code as a reusable workflow, rerun it on demand across any workspace
- **Manual joins** — point-and-click join builder with live preview, generating dplyr or pandas code
- **Join discovery** — automatically finds shared columns across loaded files and suggests joins
- **File notes** — annotate files with schema notes that are injected into every agent prompt
- **Export** — download a ZIP of scripts, plots, tables, data, and a README for any run

### Agent flexibility
- **Agent-agnostic** — works with Claude Code, OpenAI Codex, Ollama, or any CLI tool that accepts a prompt as its last argument
- **Effort levels** — set thinking depth (Low / Medium / High / Max) for Claude and Codex
- **Live agent switching** — change the agent from the UI without restarting
- **R and Python** — switch per workspace; both sessions are persistent across runs
- **R package auto-install** — any package the agent writes `library()` for is installed automatically on first use into a persistent local library

### Storage
- **Full workspace history** — prompts, code, outputs, plots, tables, and agent explanations all persist across restarts
- **Workspace management** — create, rename, switch between, and bulk-delete workspaces
- **Cleanup tools** — selectively prune old artifacts, dataset versions, or run history

---

## Requirements

- Python ≥ 3.10
- Node.js ≥ 18
- R (any recent version; `r-base-dev` or equivalent for compiling packages)
- A CLI AI agent — one of:
  - [Claude Code](https://claude.ai/code): `npm install -g @anthropic-ai/claude-code`
  - [OpenAI Codex](https://github.com/openai/codex): `npm install -g @openai/codex`
  - [Ollama](https://ollama.com) for local models

---

## Installation

```bash
git clone https://github.com/your-username/vibalytics.git
cd vibalytics
bash install.sh
```

The installer will:
1. Check Python, Node, and R are present (with platform-specific install instructions if not)
2. Create a Python virtual environment and install backend dependencies
3. Build the frontend
4. Pre-install a curated set of R packages (this can take **15–30 minutes** on a fresh system — packages like lme4, xgboost, and arrow compile from source)
5. Ask which AI agent to configure and write `config.yaml`

### Platform notes

| Platform | Prerequisites |
|---|---|
| macOS | `brew install python@3.12 node r gcc-fortran` |
| Debian/Ubuntu | `sudo apt install python3 python3-venv nodejs r-base r-base-dev build-essential gfortran` |
| Arch | `sudo pacman -S python nodejs npm r gcc-fortran` |
| Fedora/RHEL | `sudo dnf install python3 nodejs R` |
| Windows | Manual installs required — see [python.org](https://python.org), [nodejs.org](https://nodejs.org), [CRAN](https://cran.r-project.org/bin/windows/), [Rtools](https://cran.r-project.org/bin/windows/Rtools/) |

---

## Starting the server

```bash
bash start.sh
```

Then open **http://localhost:8000** in your browser.

---

## Usage

### 1. Create a workspace
Click **+ New workspace** in the sidebar and give it a name.

### 2. Upload data
Drag and drop CSV, Excel, or other tabular files into the file panel. Vibalytics will compute schema summaries and suggest starter prompts automatically.

### 3. Ask questions
Type a request in the chat box — anything from "summarise this dataset" to "fit a mixed-effects model with random intercepts by subject". The agent generates code, runs it, and streams back results.

### 4. Inspect and edit
- Click **`</>`** on any run to see the generated code
- Click **Edit** to modify it and **Rerun** to execute your version
- Use the **⊞ Inspect** button on a file to open the full data table with sorting, filtering, and column detail

### 5. Manage your data
- Click **Apply** on a dataset proposal to accept a cleaned/transformed version
- Use **↩** to revert to any previous version
- Add file notes (📝) to give the agent persistent context about a file's meaning

### 6. Export
Click **↓ Export** on any run to download a ZIP containing the R/Python script, plots, tables, data, and a reproducibility report.

---

## Configuration

`config.yaml` is created by the installer and ignored by git (edit it freely):

```yaml
agent:
  command: "claude -p"          # CLI command — prompt appended as last arg
  language: r                   # "r" or "python"
  history_messages: 20          # chat turns sent to the agent (10 exchanges)
  effort: ""                    # "" | "low" | "medium" | "high" | "max"
  timeout: 60
```

You can also change the agent and effort level live from the agent picker in the chat header without restarting.

---

## License

MIT
