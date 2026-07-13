"""Open Nova command line entrypoint."""

from __future__ import annotations

import argparse
import json
import sys

from .external_agent_memory import DEFAULT_SEARCH_TIMEOUT_SECONDS, compact_memory_results, search_memory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="open-nova")
    subcommands = parser.add_subparsers(dest="command")

    rag = subcommands.add_parser("rag", help="RAG read-only memory helpers")
    rag_subcommands = rag.add_subparsers(dest="rag_command")
    search = rag_subcommands.add_parser(
        "search-memory",
        help="Search nova-RAG memory through the external read-only Dashboard facade.",
    )
    search.add_argument("query", help="Search query")
    search.add_argument("--top-k", type=int, default=5, help="Maximum results, capped at 20")
    search.add_argument("--dashboard-url", default=None, help="Dashboard base URL; defaults to active Runtime settings")
    search.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_SEARCH_TIMEOUT_SECONDS,
        help="HTTP timeout in seconds (default: server budget plus transport grace)",
    )
    search.add_argument("--date", default="", help="Optional single business date filter")
    search.add_argument("--date-from", default="", help="Optional date range start")
    search.add_argument("--date-to", default="", help="Optional date range end")
    search.add_argument("--project", default="", help="Optional project filter")
    search.add_argument("--role", default="", help="Optional role/agent filter")
    search.add_argument("--source-set", action="append", default=[], help="Optional sourceSet filter; may repeat")
    search.add_argument("--json", action="store_true", help="Print raw JSON response")
    return parser


def main(argv: list[str] | None = None) -> int:
    selected_args = list(argv) if argv is not None else sys.argv[1:]
    if not selected_args or selected_args[0] != "rag":
        from .operator_cli import main as operator_main

        return operator_main(selected_args)
    parser = build_parser()
    args = parser.parse_args(selected_args)
    if args.command == "rag" and args.rag_command == "search-memory":
        filters = {
            "date": args.date,
            "dateFrom": args.date_from,
            "dateTo": args.date_to,
            "project": args.project,
            "role": args.role,
            "sourceSets": args.source_set,
        }
        try:
            result = search_memory(
                args.query,
                top_k=args.top_k,
                dashboard_url=args.dashboard_url,
                timeout_seconds=args.timeout,
                filters=filters,
            )
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(compact_memory_results(result, max_results=args.top_k))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
