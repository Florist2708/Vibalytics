"""
CLI agent adapter.

Builds prompts, calls whatever CLI agent is configured (claude, ollama, codex…),
streams stdout back as raw text chunks, and extracts R code from the response.

The "bring your own agent" contract:
  - The configured command receives the prompt as its final argument
  - It writes its response to stdout (streaming or not — both work)
  - The response contains a  ```r  code block
"""

import asyncio
import re
import yaml
from pathlib import Path


DEFAULT_CONFIG: dict = {
    "command": "claude -p",
    "model": "",                 # empty = use the CLI/account default
    "code_fence": "",          # empty = derive from language
    "language": "r",           # "r" or "python"
    "timeout": 60,
    "history_messages": 20,   # how many chat messages to include in prompt (10 exchanges)
    "effort": "",              # "" = no flag; "low"/"medium"/"high"/"max" for --effort (Claude only)
}

_EFFORT_LEVELS = ("low", "medium", "high", "max")


def load_config() -> dict:
    path = Path(__file__).parent.parent / "config.yaml"
    if path.exists():
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}
        return {**DEFAULT_CONFIG, **cfg.get("agent", {})}
    return DEFAULT_CONFIG.copy()


# ── Prompt builders ───────────────────────────────────────────────────────────

def _detect_agent(command: str) -> str:
    """Return 'claude', 'codex', or 'other' from the CLI command string."""
    cmd0 = command.strip().split()[0] if command.strip() else ""
    if "claude" in cmd0:
        return "claude"
    if "codex" in cmd0:
        return "codex"
    return "other"


def build_prompt(
    task: str,
    data_context: str,
    history: list[dict],
    operation_log: list[dict],
    history_messages: int = 20,
    language: str = "r",
    agent: str = "claude",
) -> str:
    lang = language.lower()
    lang_upper = lang.upper()
    fence = "python" if lang == "python" else "r"

    # Recent chat — configurable window (default 10 exchanges = 20 messages)
    recent_chat = ""
    if history:
        lines = []
        for msg in history[-history_messages:]:
            role = "User" if msg["role"] == "user" else "Assistant"
            lines.append(f"{role}: {msg['content']}")
        recent_chat = "Recent conversation:\n" + "\n".join(lines)

    # Operation log — always included so agent knows data state
    recent_ops = ""
    if operation_log:
        lines = []
        for op in operation_log[-10:]:
            mark = "✓" if op["success"] else "✗"
            lines.append(f"  {mark} {op['task']}")
        recent_ops = "Previous runs:\n" + "\n".join(lines)

    if lang == "python":
        propose_rule = [
            "── dl_propose_version ──────────────────────────────────────────────────",
            "Ask: 'Is the user asking to permanently change a stored dataset?'",
            "  YES → use dl_propose_version   (clean, filter, add columns, fix values, load new data)",
            "  NO  → use variables directly   (plots, summaries, models, any read-only work)",
            "",
            "Correct pattern:",
            "  cleaned = df[df['age'] > 0].copy()",
            "  cleaned['log_income'] = np.log(cleaned['income'])",
            "  dl_propose_version(cleaned, 'df', 'Removed invalid ages, added log_income')",
            "",
            "Wrong — never do either of these:",
            "  df = df[df['age'] > 0]                    # reassigning the original",
            "  dl_propose_version(df, 'df', '...')        # proposing the already-mutated original",
            "",
            "The second argument to dl_propose_version is the workspace variable name (string),",
            "not the local variable you just built. They are always different names.",
            "",
            "To load NEW data (not already in the workspace):",
            "  new_df = pd.read_csv('/absolute/path/to/file.csv')",
            "  dl_propose_version(new_df, 'new_var_name', 'Loaded from <source>')",
            "",
            "To append rows to an existing dataset:",
            "  extra = pd.read_csv('/path/to/extra.csv')",
            "  combined = pd.concat([existing_df, extra], ignore_index=True)",
            "  dl_propose_version(combined, 'existing_df', 'Appended N rows from <source>')",
            "────────────────────────────────────────────────────────────────────────",
        ]
        code_rules = [
            "Code rules:",
            "- Workspace data is already loaded as pandas DataFrames — never re-read those files.",
            "- Use matplotlib or seaborn for plots; call plt.show() after each figure.",
            "- Keep code concise and correct.",
        ] + propose_rule
    else:
        propose_rule = [
            "── dl_propose_version ──────────────────────────────────────────────────",
            "Ask: 'Is the user asking to permanently change a stored dataset?'",
            "  YES → use dl_propose_version   (clean, filter, add columns, fix values, load new data)",
            "  NO  → use variables directly   (plots, summaries, models, any read-only work)",
            "",
            "Correct pattern:",
            "  cleaned <- df |> filter(age > 0) |> mutate(log_income = log(income))",
            "  dl_propose_version(cleaned, 'df', 'Removed invalid ages, added log_income')",
            "",
            "Wrong — never do either of these:",
            "  df <- df |> filter(age > 0)               # reassigning the original",
            "  dl_propose_version(df, 'df', '...')        # proposing the already-mutated original",
            "",
            "The second argument to dl_propose_version is the workspace variable name (string),",
            "not the local variable you just built. They are always different names.",
            "",
            "To load NEW data (not already in the workspace):",
            "  new_df <- read.csv('/absolute/path/to/file.csv')",
            "  dl_propose_version(new_df, 'new_var_name', 'Loaded from <source>')",
            "",
            "To append rows to an existing dataset:",
            "  extra <- read.csv('/path/to/extra.csv')",
            "  combined <- rbind(existing_df, extra)",
            "  dl_propose_version(combined, 'existing_df', 'Appended N rows from <source>')",
            "────────────────────────────────────────────────────────────────────────",
        ]
        code_rules = [
            "Code rules:",
            "- Workspace data is already loaded — never re-read those files.",
            "- Use ggplot2 for plots.",
            "- Keep code concise and correct.",
            "- Any package you need is available: just call library(pkg) at the top of",
            "  your code — missing packages are installed automatically before execution.",
            "  Never write install.packages() in your code.",
        ] + propose_rule

    output_standards = [
        "Output standards:",
        "- Summaries: show n, mean, sd, and range — not just head() or a bare print.",
        "- Tables: round to 2–3 significant figures; use readable column names.",
        "- Plots: always label axes and add a title. One plot per question unless a",
        "  comparison explicitly requires multiple panels.",
        "- Data quality: proactively flag issues the user may not have asked about —",
        "  high NA rates (>5%), obvious outliers, columns with unexpected types or",
        "  near-zero variance. One line is enough; don't derail the main task.",
        "- Models: report the key fit statistic (R², AIC, accuracy) alongside",
        "  coefficients. Flag if sample size is too small to trust the result.",
    ]

    if agent == "codex":
        response_format = [
            "How to respond:",
            f"- Only include a ```{fence} code block if the task actually requires",
            "  computation, analysis, or data manipulation. For greetings, clarifying",
            "  questions, or anything that needs no code — answer in plain text only.",
            "- When code IS needed: write a short plain-text explanation first",
            "  (2–5 sentences covering what you found and what the code does),",
            f"  then the ```{fence} block.",
            "- If the task is ambiguous or the data cannot support it, say so in",
            "  plain text — no code needed.",
        ]
    else:
        response_format = [
            "How to respond:",
            f"- Include a ```{fence} code block only when the task requires computation",
            "  or data work. For conversational messages or questions that need no",
            "  analysis, reply in plain text only — no code block.",
            "- Explain only when it adds real value: a surprising result, a number that",
            "  needs context, or an answer not obvious from the output alone.",
            "  Skip explanation when the result speaks for itself.",
            "- Match depth to the question: 'What is the average?' → just the number.",
            "  'Explain this regression' → full walkthrough.",
            "- If a task is ambiguous or impossible with the available data, say so",
            "  briefly before the code block.",
            "- Do not narrate what you are about to do. Just do it.",
        ]

    parts = [
        f"You are a data analyst working in {lang_upper}.",
        "",
        *response_format,
        "",
        data_context,
    ]
    if recent_ops:
        parts += ["", recent_ops]
    if recent_chat:
        parts += ["", recent_chat]
    parts += ["", f"User task: {task}", ""] + code_rules + [""] + output_standards
    return "\n".join(parts)


def build_retry_prompt(
    task: str,
    code: str,
    error: str,
    data_context: str,
    language: str = "r",
) -> str:
    lang  = language.lower()
    fence = "python" if lang == "python" else "r"
    return "\n".join([
        f"The following {lang.upper()} code produced an error. Fix it.",
        "",
        data_context,
        "",
        f"Original task: {task}",
        "",
        "Code that failed:",
        f"```{fence}\n{code}\n```",
        "",
        f"Error: {error}",
        "",
        f"Return only the corrected {lang.upper()} code in a ```{fence} block.",
    ])


# ── Code extraction ───────────────────────────────────────────────────────────

def extract_code(response: str, fence: str = "r") -> str | None:
    """Pull the first fenced code block out of the agent response."""
    # Try exact fence first, then any fence
    for pattern in (rf"```{fence}\s*\n(.*?)```", r"```\w*\s*\n(.*?)```"):
        m = re.search(pattern, response, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


# ── Agent calls ───────────────────────────────────────────────────────────────

async def stream_agent(
    prompt: str,
    config: dict,
    attachment_paths: list[str] | None = None,
    abort_event=None,
):
    """
    Async generator — yields raw text chunks from the CLI agent's stdout.
    Raises RuntimeError on failure.  No timeout — runs until done or until
    abort_event is set, which kills the subprocess and returns cleanly.

    attachment_paths — list of local file paths to pass as --file flags.
    abort_event      — asyncio.Event; set it from outside to stop the agent.
    """
    cmd = config["command"].split()

    args = list(cmd)
    binary = cmd[0]
    model = (config.get("model") or "").strip()
    if model and ("claude" in binary or "codex" in binary):
        args += ["--model", model]

    effort = (config.get("effort") or "").strip().lower()
    if effort in _EFFORT_LEVELS:
        if "claude" in binary:
            args += ["--effort", effort]
        elif "codex" in binary:
            # codex uses model_reasoning_effort; "max" maps to "xhigh"
            codex_effort = "xhigh" if effort == "max" else effort
            args += ["-c", f'model_reasoning_effort="{codex_effort}"']
    for path in (attachment_paths or []):
        args += ["--file", path]
    args.append(prompt)

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"CLI agent '{cmd[0]}' not found. "
            "Check the 'command' setting in config.yaml."
        )

    aborted = False
    while True:
        if abort_event and abort_event.is_set():
            aborted = True
            proc.kill()
            break

        try:
            chunk = await asyncio.wait_for(proc.stdout.read(512), timeout=0.5)
        except asyncio.TimeoutError:
            continue   # poll abort_event again

        if not chunk:
            break

        yield chunk.decode("utf-8", errors="replace")

    await proc.wait()

    if not aborted and proc.returncode not in (0, None):
        stderr = await proc.stderr.read()
        msg = stderr.decode().strip()
        if msg:
            raise RuntimeError(f"Agent error: {msg}")


async def call_agent(
    prompt: str,
    config: dict,
    attachment_paths: list[str] | None = None,
    abort_event=None,
) -> str:
    """Non-streaming agent call — collects full response and returns it."""
    full = []
    async for chunk in stream_agent(prompt, config, attachment_paths=attachment_paths,
                                     abort_event=abort_event):
        full.append(chunk)
    return "".join(full)
