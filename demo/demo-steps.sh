#!/usr/bin/env bash
# Demo driver for Probe's 2:30 script. Run inside asciinema from a fresh clone.
# cap N  -> emits an OSC marker the renderer turns into burned-in caption N (not shown on screen).
cd "${1:-/tmp/pb-fresh}" || exit 1
source .venv/bin/activate
export PATH="$HOME/.local/bin:$PATH"
cap() { printf '\033]1337;PBCAP=%s\007' "$1"; }
run() { printf '\033[1;32m$\033[0m \033[1m%s\033[0m\n' "$*"; sleep 0.8; eval "$@"; sleep "${PAUSE:-3}"; }
clear
cap 1
PAUSE=2 run "head -12 README.md | cut -c1-100"
cap 2
PAUSE=2 run "ollama list | grep qwen2.5"
run "openminion --home-root ./om-home doctor --check-turn 2>&1 | grep -E 'runtime.bootstrap|turn_smoke'"
clear
cap 3
run "pytest -q 2>&1 | tail -2"
clear
cap 4
run "patchbrief run fixtures/sample-release --home-root ./om-home --out-dir ./out --filename denied-check.md --session demo-deny --deny-write | grep -E '\"(ok|write_denied|written_path|stopped_after_tool|elapsed_seconds)\"'"
run "ls out/denied-check.md"
clear
cap 5
PAUSE=4 run "patchbrief run fixtures/sample-release --home-root ./om-home --out-dir ./out --filename v0.4.2-brief.md --session demo-approve --approve-write | grep -vE 'session_id|model\"|preloaded|tool_mode'"
clear
cap 6
PAUSE=7 run "cat out/v0.4.2-brief.md"
clear
cap 7
run "patchbrief history --out-dir ./out | grep -E 'session_id|output_path' | head -4"
clear
cap 8
PAUSE=5 run "sed -n '/What We Learned/,/^## License/p' README.md | grep -E '^[0-9]+\\.' | cut -c1-98"
