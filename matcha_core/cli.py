from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="matcha-core", description="Analyze a repository against SPECS.md.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Analyze a local repository path.")
    analyze.add_argument("repo_path", help="Path to the repository to analyze.")
    analyze.add_argument("--specs", dest="specs_path", help="Optional path to the specs file.")
    analyze.add_argument("--provider", choices=["openai", "ollama"], default="openai")
    analyze.add_argument("--api-key", dest="api_key", help="API key for the selected provider.")
    analyze.add_argument("--base-url", dest="base_url", help="OpenAI-compatible base URL.")
    analyze.add_argument("--model", dest="model", help="Model name to use.")
    analyze.add_argument("--format", choices=["json", "markdown", "html", "table"], default="json")
    analyze.add_argument("--show-evidence", action="store_true", help="Include detailed evidence blocks in terminal table output.")
    analyze.add_argument("--output", help="Optional output file path.")
    analyze.add_argument("--debug-llm", dest="debug_llm_path", help="Optional JSONL path for per-criteria LLM debug logs.")
    analyze.add_argument("--quiet", action="store_true", help="Suppress progress output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "analyze":
        parser.error(f"Unsupported command: {args.command}")

    repo_path = Path(args.repo_path).expanduser().resolve()
    if not repo_path.exists():
        parser.error(f"Repository path does not exist: {repo_path}")

    specs_path = None
    if args.specs_path:
        specs_path = str(Path(args.specs_path).expanduser().resolve())
    debug_llm_path = None
    if args.debug_llm_path:
        debug_llm_path = str(Path(args.debug_llm_path).expanduser().resolve())

    try:
        from .engine import RepositoryAnalyzer
        from .evaluator import OpenAICompatibleEvaluator
        from .reporting import report_to_html, report_to_json, report_to_markdown, report_to_table

        evaluator = OpenAICompatibleEvaluator.from_env(
            provider=args.provider,
            api_key=args.api_key,
            base_url=args.base_url,
            model=args.model,
        )
        analyzer = RepositoryAnalyzer(evaluator=evaluator)

        report = analyzer.analyze_path(
            repo_path=str(repo_path),
            specs_path=specs_path,
            progress_callback=None if args.quiet else progress_printer,
            debug_output_path=debug_llm_path,
        )

        if args.format == "json":
            rendered = report_to_json(report)
        elif args.format == "markdown":
            rendered = report_to_markdown(report)
        elif args.format == "table":
            rendered = report_to_table(report, include_details=args.show_evidence)
        else:
            rendered = report_to_html(report)

        if args.output:
            output_path = Path(args.output).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
            if not args.quiet:
                print(f"[done] wrote report to {output_path}", file=sys.stderr)
        else:
            print(rendered)
        if debug_llm_path and not args.quiet:
            print(f"[done] wrote llm debug log to {debug_llm_path}", file=sys.stderr)

        return 0
    except Exception as exc:
        print(f"[matcha] error: {exc}", file=sys.stderr)
        return 1


def progress_printer(status: str) -> None:
    labels = {
        "cloning": "cloning repository",
        "parsing": "parsing specs",
        "analyzing": "analyzing implementation",
    }
    print(f"[matcha] {labels.get(status, status)}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
