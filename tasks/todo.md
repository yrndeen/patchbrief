# Patchbrief Build Plan (Cortensor Hackathon #5 — OpenMinion 0.1 + Ollama)

- [x] Read `/workspace/bounties/cortensor-hack5/brief.md` and verify local environment (Python 3.13.5, `openminion==0.1.22`, local Ollama `v0.40.17` with `qwen2.5:1.5b`).
- [x] Verify `openminion setup --provider ollama --model qwen2.5:1.5b --no-chat` and `openminion doctor --check-turn` pass locally ($0, zero API keys).
- [ ] Implement `patchbrief/` package using OpenMinion 0.1 SDK (`Agent`, `@tool`, `approval_callback`, `session_id`, memory record, diagnostics).
- [ ] Add deterministic fixture (`fixtures/sample-release/`) and `pytest` suite covering tools, secret redaction, missing-input refusal, approval allow/deny, memory history, and OpenMinion agent integration.
- [ ] Execute a real model-backed end-to-end run with local Ollama `qwen2.5:1.5b` and verify output brief + history persistence.
- [ ] Write `README.md` (setup, architecture, Building Blocks Tested, What We Learned, demo steps) and `/workspace/bounties/cortensor-hack5/notes.md` for Probe.
