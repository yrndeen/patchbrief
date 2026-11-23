"""CLI entry point for Patchbrief."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from patchbrief.agent import PatchbriefRunner, check_ollama_health
from patchbrief.memory import load_brief_memory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patchbrief",
        description="Local OpenMinion 0.1 + Ollama release and issue-triage agent.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="Generate a structured patch brief from a local path")
    run_cmd.add_argument("source", help="Path to a local git repo, changelog file, or release folder")
    run_cmd.add_argument("--out-dir", default="./out", help="Allowed directory for written patch briefs")
    run_cmd.add_argument("--filename", default="patch-brief.md", help="Output markdown filename")
    run_cmd.add_argument("--home-root", default="./om-home", help="OpenMinion home root")
    run_cmd.add_argument("--model", default="qwen2.5:1.5b-16k", help="Local Ollama model tag")
    run_cmd.add_argument("--session", default="patchbrief-default", help="OpenMinion session id")
    run_cmd.add_argument("--timeout", type=float, default=120.0, help="Turn timeout in seconds")
    approval = run_cmd.add_mutually_exclusive_group()
    approval.add_argument("--approve-write", action="store_true", help="Approve writing the brief file")
    approval.add_argument("--deny-write", action="store_true", help="Deny writing the brief file (dry-run)")

    doc_cmd = sub.add_parser("doctor", help="Check local Ollama and OpenMinion config readiness")
    doc_cmd.add_argument("--home-root", default="./om-home", help="OpenMinion home root")
    doc_cmd.add_argument("--ollama-url", default="http://127.0.0.1:11434", help="Local Ollama URL")

    hist_cmd = sub.add_parser("history", help="Show recorded brief history from local memory")
    hist_cmd.add_argument("--out-dir", default="./out", help="Directory holding brief-memory.jsonl")
    hist_cmd.add_argument("--limit", type=int, default=5, help="Max entries to display")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "doctor":
        ok, msg = check_ollama_health(args.ollama_url)
        cfg = Path(args.home_root).expanduser().resolve() / ".openminion" / "agents.json"
        payload = {
            "ollama_ok": ok,
            "ollama_detail": msg,
            "openminion_config_exists": cfg.exists(),
            "openminion_config_path": str(cfg),
        }
        print(json.dumps(payload, indent=2))
        return 0 if (ok and cfg.exists()) else 1

    if args.command == "history":
        mem_file = Path(args.out_dir).expanduser().resolve() / "brief-memory.jsonl"
        entries = load_brief_memory(mem_file, limit=args.limit)
        print(json.dumps({"memory_file": str(mem_file), "entries": entries}, indent=2))
        return 0

    if args.command == "run":
        approve_flag: bool | None = None
        if args.approve_write:
            approve_flag = True
        elif args.deny_write:
            approve_flag = False

        runner = PatchbriefRunner(
            home_root=Path(args.home_root),
            output_root=Path(args.out_dir),
            model=args.model,
        )
        summary = runner.run(
            source_path=Path(args.source),
            output_filename=args.filename,
            session_id=args.session,
            approve_write=approve_flag,
            timeout_seconds=args.timeout,
        )
        print(
            json.dumps(
                {
                    "ok": summary.ok,
                    "session_id": summary.session_id,
                    "model": summary.model,
                    "context_preloaded": summary.context_preloaded,
                    "memory_preloaded": summary.memory_preloaded,
                    "tool_mode": summary.tool_mode,
                    "quality_warnings": summary.quality_warnings,
                    "initial_quality_warnings": summary.initial_quality_warnings,
                    "blurb_repair": summary.blurb_repair,
                    "stopped_after_tool": summary.stopped_after_tool,
                    "elapsed_seconds": summary.elapsed_seconds,
                    "written_path": summary.written_path,
                    "write_denied": summary.write_denied,
                    "diagnostic": summary.diagnostic,
                },
                indent=2,
            )
        )
        if summary.output_text:
            print("\n--- AGENT OUTPUT ---\n")
            print(summary.output_text)
        return 0 if summary.ok else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
