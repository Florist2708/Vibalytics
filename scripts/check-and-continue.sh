#!/usr/bin/env bash
# check-and-continue.sh
# Runs daily at 6am.
#   - If features remain: implement the next one.
#   - If all features done: test, debug, and audit against the six core principles.
# Calls the claude CLI agent directly.

set -euo pipefail

VIBALYTICS="/home/user/Documents/vibalytics"
CLAUDE_MD="$VIBALYTICS/CLAUDE.md"
LOG="$VIBALYTICS/scripts/agent.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

log "--- daily check started ---"

# Count unimplemented features (✗ rows in the roadmap table).
remaining=$(grep -c "✗" "$CLAUDE_MD" || true)

# ── Case 1: features still pending — implement the next one ──────────────────

if [ "$remaining" -gt 0 ]; then
    next_feature=$(grep "✗" "$CLAUDE_MD" | head -1 | sed 's/.*\*\*\(.*\)\*\*.*/\1/' | xargs)
    log "Features remaining: $remaining. Next: $next_feature"
    log "Calling claude to implement..."

    PROMPT="You are working on the vibalytics project at $VIBALYTICS.

Read CLAUDE.md in full before doing anything. It contains the project vision,
architecture, development rules, and the current roadmap status table.

The next unimplemented feature is: $next_feature

Your job:
1. Implement $next_feature fully, following all rules in CLAUDE.md.
2. Test that it works end-to-end (start the server if needed, exercise the feature).
3. Update the roadmap table in CLAUDE.md:
   - Mark $next_feature as ✓ Done with a brief note on how it was implemented.
   - Update the Next priority line to point at what comes after.

Work entirely within $VIBALYTICS. Do not break existing functionality."

    cd "$VIBALYTICS"
    claude -p "$PROMPT" >> "$LOG" 2>&1
    exit_code=$?
    [ $exit_code -eq 0 ] && log "Agent finished successfully." || log "Agent exited with code $exit_code."
    log "--- done ---"
    exit 0
fi

# ── Case 2: all features done — test, debug, principles audit ────────────────

log "All features marked done. Running test + principles audit..."

PROMPT="You are working on the vibalytics project at $VIBALYTICS.

Read CLAUDE.md in full before doing anything.

All roadmap features are now marked done. Your job is to do a real end-to-end
validation pass:

PART 1 — TEST AND DEBUG
- Start the backend (cd backend && ../.venv/bin/uvicorn main:app --port 8000)
  and confirm it starts without errors.
- Run any existing test scripts. If none exist, write a minimal smoke test:
  POST /session, POST /upload with a real CSV, POST /chat/stream with a simple
  prompt, confirm SSE events arrive and R executes without error.
- Check that server restart recovery works: stop and restart the server, confirm
  /context returns the same files and /runs returns the same runs.
- Fix any bugs you find. Log what you fixed.

PART 2 — PRINCIPLES AUDIT
Check each of the six core principles from CLAUDE.md against the actual code.
For each principle, write a one-paragraph verdict: does the current implementation
satisfy it, partially satisfy it, or miss it? Be honest and specific.

The six principles are:
  1. Agent-agnostic — any CLI agent that takes a prompt as last arg and writes to stdout
  2. Multi-file by default — several datasets simultaneously
  3. Inspectable — user can see files used, context built, code generated, outputs, logs
  4. Editable — generated R code can be modified and rerun
  5. Reproducible — runs survive server restart with full provenance
  6. Accessible — a non-expert can upload a file and get a real answer
     NOTE: this is version 1. Full accessibility polish is not expected yet.
     Flag anything that is a hard blocker for a first-time user, but don't mark
     the whole principle as failed just because the UI isn't polished.

PART 3 — WRITE REPORT
Write the audit results to $VIBALYTICS/scripts/audit_report.md. Include:
- Date
- What was tested and results (pass / fail / fixed)
- Principles audit verdicts
- Any known issues or next recommended work

Update CLAUDE.md only if you fixed real bugs (update the relevant rows).
Do not mark principles as done/not-done in the roadmap — the roadmap tracks
features, not principles."

cd "$VIBALYTICS"
claude -p "$PROMPT" >> "$LOG" 2>&1
exit_code=$?
[ $exit_code -eq 0 ] && log "Audit finished successfully." || log "Audit exited with code $exit_code."
log "--- done ---"
