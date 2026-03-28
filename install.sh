#!/usr/bin/env bash
# vibalytics — one-shot installer
# Usage: bash install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
R_LIBS_DIR="$HOME/.vibalytics/r_libs"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "  ${GREEN}✓${RESET}  $*"; }
info() { echo -e "  ${BOLD}→${RESET}  $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $*"; }
fail() { echo -e "  ${RED}✗${RESET}  $*"; exit 1; }

# Detect OS for install hints
OS="unknown"
if [[ "$OSTYPE" == "darwin"* ]]; then
  OS="macos"
elif [[ -f /etc/arch-release ]]; then
  OS="arch"
elif [[ -f /etc/debian_version ]]; then
  OS="debian"
elif [[ -f /etc/fedora-release ]] || [[ -f /etc/redhat-release ]]; then
  OS="fedora"
elif [[ "$OSTYPE" == "msys"* ]] || [[ "$OSTYPE" == "cygwin"* ]]; then
  OS="windows"
fi

# Print platform-specific install hint for a missing tool
install_hint() {
  local tool="$1"
  echo ""
  case "$tool" in
    python)
      case "$OS" in
        macos)
          echo -e "  ${YELLOW}Install Python on macOS:${RESET}"
          echo "    brew install python@3.12"
          echo "    (Homebrew: https://brew.sh if not installed)"
          ;;
        arch)
          echo -e "  ${YELLOW}Install Python on Arch:${RESET}"
          echo "    sudo pacman -S python"
          ;;
        debian)
          echo -e "  ${YELLOW}Install Python on Debian/Ubuntu:${RESET}"
          echo "    sudo apt update && sudo apt install python3 python3-venv python3-pip"
          ;;
        fedora)
          echo -e "  ${YELLOW}Install Python on Fedora/RHEL:${RESET}"
          echo "    sudo dnf install python3"
          ;;
        windows)
          echo -e "  ${YELLOW}Install Python on Windows:${RESET}"
          echo "    Download from https://python.org — check 'Add to PATH' during install."
          echo "    Then re-run this script in Git Bash or WSL."
          ;;
        *)
          echo -e "  ${YELLOW}Install Python ≥ 3.10 from https://python.org${RESET}"
          ;;
      esac
      ;;
    node)
      case "$OS" in
        macos)
          echo -e "  ${YELLOW}Install Node.js on macOS:${RESET}"
          echo "    brew install node"
          ;;
        arch)
          echo -e "  ${YELLOW}Install Node.js on Arch:${RESET}"
          echo "    sudo pacman -S nodejs npm"
          ;;
        debian)
          echo -e "  ${YELLOW}Install Node.js on Debian/Ubuntu:${RESET}"
          echo "    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -"
          echo "    sudo apt install nodejs"
          ;;
        fedora)
          echo -e "  ${YELLOW}Install Node.js on Fedora/RHEL:${RESET}"
          echo "    sudo dnf install nodejs"
          ;;
        windows)
          echo -e "  ${YELLOW}Install Node.js on Windows:${RESET}"
          echo "    Download from https://nodejs.org — LTS version recommended."
          ;;
        *)
          echo -e "  ${YELLOW}Install Node.js ≥ 18 from https://nodejs.org${RESET}"
          ;;
      esac
      ;;
    r)
      case "$OS" in
        macos)
          echo -e "  ${YELLOW}Install R on macOS:${RESET}"
          echo "    brew install r"
          echo "    (or download from https://cran.r-project.org)"
          ;;
        arch)
          echo -e "  ${YELLOW}Install R on Arch:${RESET}"
          echo "    sudo pacman -S r gcc-fortran"
          echo "    # gcc-fortran is required for compiling packages like lme4/glmnet"
          ;;
        debian)
          echo -e "  ${YELLOW}Install R on Debian/Ubuntu:${RESET}"
          echo "    sudo apt update && sudo apt install r-base r-base-dev"
          echo "    # r-base-dev is required for compiling packages like lme4/xgboost"
          ;;
        fedora)
          echo -e "  ${YELLOW}Install R on Fedora/RHEL:${RESET}"
          echo "    sudo dnf install R"
          ;;
        windows)
          echo -e "  ${YELLOW}Install R on Windows:${RESET}"
          echo "    Download from https://cran.r-project.org/bin/windows/"
          echo "    Also install Rtools: https://cran.r-project.org/bin/windows/Rtools/"
          ;;
        *)
          echo -e "  ${YELLOW}Install R from https://cran.r-project.org${RESET}"
          ;;
      esac
      ;;
  esac
  echo ""
}

echo ""
echo -e "${BOLD}vibalytics installer${RESET}"
echo "────────────────────────────────────────"

# ── 1. System prerequisites ────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[1/5] Checking prerequisites${RESET}"

# Python ≥ 3.10
if command -v python3 &>/dev/null; then
  PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
  PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
  if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
    ok "Python $PY_VER"
    PYTHON=python3
  else
    fail "Python $PY_VER found, but vibalytics requires Python ≥ 3.10"
  fi
else
  install_hint python
  fail "python3 not found — see install instructions above"
fi

# Node.js ≥ 18
if command -v node &>/dev/null; then
  NODE_VER=$(node --version | sed 's/v//')
  NODE_MAJOR=$(echo "$NODE_VER" | cut -d. -f1)
  if [ "$NODE_MAJOR" -ge 18 ]; then
    ok "Node.js $NODE_VER"
  else
    install_hint node
    fail "Node.js $NODE_VER found, but vibalytics requires Node.js ≥ 18 — see install instructions above"
  fi
else
  install_hint node
  fail "node not found — see install instructions above"
fi

if ! command -v npm &>/dev/null; then
  fail "npm not found (should come with Node.js)"
fi

# R
if command -v Rscript &>/dev/null; then
  R_VER=$(Rscript -e 'cat(R.version$major, R.version$minor, sep=".")' 2>/dev/null)
  ok "R $R_VER"
else
  install_hint r
  fail "Rscript not found — see install instructions above"
fi

# Claude CLI (optional but expected)
if command -v claude &>/dev/null; then
  ok "claude CLI found"
else
  warn "claude CLI not found. Install it with: npm install -g @anthropic-ai/claude-code"
  warn "You can still run vibalytics with a different agent — edit config.yaml."
fi

# ── 2. Python virtual environment + backend packages ──────────────────────────
echo ""
echo -e "${BOLD}[2/5] Python virtual environment${RESET}"

if [ ! -f "$VENV/bin/python" ]; then
  info "Creating virtual environment at .venv …"
  "$PYTHON" -m venv "$VENV"
  ok "Virtual environment created"
else
  ok "Virtual environment already exists"
fi

info "Installing Python packages from requirements.txt …"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
ok "Python packages installed"

# ── 3. Frontend build ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[3/5] Frontend${RESET}"

info "Installing npm packages …"
cd "$SCRIPT_DIR/frontend"
npm install --silent
ok "npm packages installed"

info "Building frontend …"
npm run build --silent
ok "Frontend built → frontend/dist/"
cd "$SCRIPT_DIR"

# ── 4. R packages ─────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[4/5] R packages${RESET}"

mkdir -p "$R_LIBS_DIR"
info "User R library: $R_LIBS_DIR"
info "Installing essential R packages (2–3 minutes) …"
info "Everything else installs automatically on first use."

Rscript - "$R_LIBS_DIR" <<'R_SCRIPT'
args    <- commandArgs(trailingOnly = TRUE)
lib_dir <- args[1]
if (!lib_dir %in% .libPaths()) .libPaths(c(lib_dir, .libPaths()))

# Essential packages only — the ones needed for basic file loading,
# data manipulation, and plotting on almost every task.
# Everything else (lme4, xgboost, arrow, etc.) is auto-installed on first use.
pkgs <- c(
  "ggplot2",   # plotting
  "dplyr",     # data manipulation
  "tidyr",     # reshaping
  "readr",     # CSV loading (used internally)
  "readxl",    # Excel loading (used internally)
  "haven",     # Stata / SPSS loading (used internally)
  "tibble",    # modern data frames
  "stringr",   # string manipulation
  "broom",     # tidy model outputs
  "lmtest",    # coeftest() — robust SE testing
  "sandwich",  # vcovHC() — robust SEs
  "jsonlite"   # JSON I/O
)

missing <- pkgs[!sapply(pkgs, requireNamespace, quietly = TRUE)]

if (length(missing) == 0) {
  cat("All R packages already installed.\n")
  quit(status = 0)
}

cat(sprintf("Installing %d package(s): %s\n", length(missing), paste(missing, collapse = ", ")))

failed <- character(0)
for (pkg in missing) {
  cat(sprintf("  installing %-20s", pkg))
  result <- tryCatch({
    install.packages(pkg, lib = lib_dir, repos = "https://cloud.r-project.org/",
                     quiet = TRUE, dependencies = TRUE)
    if (requireNamespace(pkg, quietly = TRUE)) "ok" else "failed"
  }, error   = function(e) paste("error:", conditionMessage(e)),
     warning = function(w) paste("warn:",  conditionMessage(w)))
  if (result == "ok") {
    cat(" ✓\n")
  } else {
    cat(sprintf(" ✗ (%s)\n", result))
    failed <- c(failed, pkg)
  }
}

if (length(failed) > 0) {
  cat(sprintf("\nWarning: %d package(s) failed to install: %s\n",
              length(failed), paste(failed, collapse = ", ")))
  cat("These will be installed automatically on first use if missing.\n")
} else {
  cat("All R packages installed successfully.\n")
}
R_SCRIPT

ok "R packages done"

# ── 5. Config ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[5/5] Config${RESET}"

if [ -f "$SCRIPT_DIR/config.yaml" ]; then
  ok "config.yaml already exists — skipping agent selection"
else
  echo ""
  echo "  Which AI agent should vibalytics use?"
  echo "  ┌─────────────────────────────────────────────────────────┐"
  echo "  │  1) Claude Code  (claude -p)  — Claude.ai subscription  │"
  echo "  │  2) OpenAI Codex (codex -q)   — OpenAI account          │"
  echo "  │  3) Ollama       (local)      — no account needed        │"
  echo "  │  4) Custom command                                        │"
  echo "  └─────────────────────────────────────────────────────────┘"
  echo ""
  read -rp "  Choice [1]: " AGENT_CHOICE
  AGENT_CHOICE="${AGENT_CHOICE:-1}"

  case "$AGENT_CHOICE" in
    1)
      AGENT_CMD="claude -p"
      if ! command -v claude &>/dev/null; then
        warn "claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
      fi
      ;;
    2)
      AGENT_CMD="codex exec --skip-git-repo-check"
      if ! command -v codex &>/dev/null; then
        warn "codex CLI not found. Install with: npm install -g @openai/codex"
      fi
      ;;
    3)
      read -rp "  Ollama model name [llama3]: " OLLAMA_MODEL
      OLLAMA_MODEL="${OLLAMA_MODEL:-llama3}"
      AGENT_CMD="ollama run $OLLAMA_MODEL"
      if ! command -v ollama &>/dev/null; then
        warn "ollama not found. Install from https://ollama.com"
      fi
      ;;
    4)
      read -rp "  Enter full command (prompt will be appended as last arg): " AGENT_CMD
      if [ -z "$AGENT_CMD" ]; then
        AGENT_CMD="claude -p"
        warn "Empty input — defaulting to: $AGENT_CMD"
      fi
      ;;
    *)
      warn "Unknown choice '$AGENT_CHOICE', defaulting to claude -p"
      AGENT_CMD="claude -p"
      ;;
  esac

  cat > "$SCRIPT_DIR/config.yaml" <<YAML
agent:
  # CLI command to call. The prompt is appended as the last argument.
  # You can change this at any time via the agent picker in the UI,
  # or by editing this file.
  #
  # Examples:
  #   command: "claude -p"         # Claude Code (Claude.ai subscription)
  #   command: "codex exec --skip-git-repo-check"  # OpenAI Codex CLI
  #   command: "ollama run llama3" # Local Ollama model
  command: "$AGENT_CMD"
  language: r
  history_messages: 20
YAML
  ok "config.yaml created with agent: $AGENT_CMD"
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────"
echo -e "${GREEN}${BOLD}Installation complete.${RESET}"
echo ""
echo "  Start the server:  bash start.sh"
echo "  Then open:         http://localhost:8000"
echo ""
