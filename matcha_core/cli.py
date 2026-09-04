from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPORT_FORMAT_EXTENSIONS = {
    ".json": "json",
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
    ".txt": "table",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="matcha-core", description="Analyze a repository against SPECS.md.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Analyze a local repository path.")
    _add_analysis_arguments(analyze)
    analyze.add_argument(
        "--format",
        "--reporter",
        dest="report_format",
        choices=["json", "markdown", "html", "table"],
        default=None,
        help="Report format. Inferred from --output when possible.",
    )
    analyze.add_argument("--show-evidence", action="store_true", help="Include detailed evidence blocks in terminal table output.")
    analyze.add_argument("--output", help="Optional output file path.")

    check = subparsers.add_parser("check", help="Evaluate a repository against a deterministic CI gate policy.")
    _add_analysis_arguments(check)
    check.add_argument(
        "--policy",
        help="Policy YAML path. Defaults to <repo>/.matcha/policy.yml.",
    )
    check.add_argument("--baseline", help="Optional baseline report or gate artifact. Overrides the policy baseline.")
    check.add_argument(
        "--output",
        default="matcha-gate.json",
        help="Gate artifact JSON path. Defaults to ./matcha-gate.json.",
    )

    onboard = subparsers.add_parser(
        "onboard",
        aliases=["bootstrap"],
        help="Onboard a repository by generating a reviewable SPECS draft (alias: bootstrap).",
    )
    onboard.add_argument("repo_path", help="Path to the repository to onboard.")
    _add_provider_arguments(onboard, default_provider="ollama")
    onboard.add_argument(
        "--output",
        help="Draft Markdown path. Defaults to <repo>/SPECS.draft.md.",
    )
    onboard.add_argument("--language", default="English", help="Language for generated specs. Defaults to English.")
    onboard.add_argument(
        "--max-features",
        type=int,
        default=None,
        help="Maximum number of draft features (1-50). Defaults to 8 for Ollama and 12 for OpenAI.",
    )
    onboard.add_argument(
        "--max-context-chars",
        type=int,
        default=None,
        help="Maximum repository context sent to the model. Defaults to 18000 for Ollama and 90000 for OpenAI.",
    )
    onboard.add_argument("--debug-llm", dest="debug_llm_path", help="Optional JSON path for onboarding debug data.")
    onboard.add_argument("--force", action="store_true", help="Overwrite an existing draft output.")
    onboard.add_argument("--quiet", action="store_true", help="Suppress progress output.")
    return parser


def _add_analysis_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("repo_path", help="Path to the repository to analyze.")
    command.add_argument("--specs", dest="specs_path", help="Optional path to the specs file.")
    _add_provider_arguments(command)
    command.add_argument(
        "--feature",
        dest="feature_ids",
        action="append",
        help="Analyze only the given feature ID. Repeat the flag to include multiple features.",
    )
    command.add_argument("--debug-llm", dest="debug_llm_path", help="Optional JSONL path for per-criteria LLM debug logs.")
    command.add_argument("--quiet", action="store_true", help="Suppress progress output.")


def _add_provider_arguments(command: argparse.ArgumentParser, *, default_provider: str = "openai") -> None:
    command.add_argument("--provider", choices=["openai", "ollama"], default=default_provider)
    command.add_argument("--api-key", dest="api_key", help="API key for the selected provider.")
    command.add_argument("--base-url", dest="base_url", help="OpenAI-compatible base URL.")
    command.add_argument("--model", dest="model", help="Model name to use.")
    command.add_argument("--timeout", type=float, default=None, help="Provider request timeout in seconds.")
    command.add_argument(
        "--reasoning-effort",
        choices=["none", "low", "medium", "high", "xhigh", "max"],
        default=None,
        help="Optional reasoning effort. OpenAI reasoning models default to low.",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return _run_check(args)
    if args.command in {"onboard", "bootstrap"}:
        return _run_onboard(args)
    return _run_analyze(args)


def _run_onboard(args: argparse.Namespace) -> int:
    try:
        from .onboarding import OpenAICompatibleSpecGenerator, RepositoryBootstrapper, write_specs_draft

        repo_path = Path(args.repo_path).expanduser().resolve()
        output_path = Path(args.output).expanduser().resolve() if args.output else repo_path / "SPECS.draft.md"
        debug_path = Path(args.debug_llm_path).expanduser().resolve() if args.debug_llm_path else None
        generator = OpenAICompatibleSpecGenerator.from_env(
            provider=args.provider,
            api_key=args.api_key,
            base_url=args.base_url,
            model=args.model,
            timeout=args.timeout or (180.0 if args.provider == "ollama" else 60.0),
            reasoning_effort=args.reasoning_effort,
        )
        onboarder = RepositoryBootstrapper(generator)
        draft = onboarder.bootstrap_path(
            repo_path,
            provider=args.provider,
            max_features=args.max_features or (8 if args.provider == "ollama" else 12),
            max_context_chars=args.max_context_chars or (18_000 if args.provider == "ollama" else 90_000),
            language=args.language,
            progress_callback=None if args.quiet else progress_printer,
            debug_output_path=debug_path,
        )
        write_specs_draft(draft, output_path, overwrite=args.force)
        if not args.quiet:
            print(
                f"[matcha] created {len(draft.features)} draft feature(s) with {len(draft.questions)} review question(s)",
                file=sys.stderr,
            )
            print(f"[done] wrote specs draft to {output_path}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"[matcha] onboarding error: {exc}", file=sys.stderr)
        return 1


def _run_analyze(args: argparse.Namespace) -> int:
    parser = build_parser()

    repo_path, specs_path, debug_llm_path = _resolve_paths(args, parser)

    output_path = None
    if args.output:
        output_path = Path(args.output).expanduser().resolve()

    report_format = resolve_report_format(
        explicit_format=args.report_format,
        output_path=output_path,
        quiet=args.quiet,
    )

    try:
        from .engine import RepositoryAnalyzer
        from .evaluator import OpenAICompatibleEvaluator
        from .reporting import report_to_html, report_to_json, report_to_markdown, report_to_table

        evaluator = OpenAICompatibleEvaluator.from_env(
            provider=args.provider,
            api_key=args.api_key,
            base_url=args.base_url,
            model=args.model,
            timeout=args.timeout or 60.0,
            reasoning_effort=args.reasoning_effort,
        )
        analyzer = RepositoryAnalyzer(evaluator=evaluator)

        report = analyzer.analyze_path(
            repo_path=str(repo_path),
            specs_path=specs_path,
            feature_ids=args.feature_ids,
            progress_callback=None if args.quiet else progress_printer,
            debug_output_path=debug_llm_path,
        )

        if report_format == "json":
            rendered = report_to_json(report)
        elif report_format == "markdown":
            rendered = report_to_markdown(report)
        elif report_format == "table":
            rendered = report_to_table(report, include_details=args.show_evidence)
        else:
            rendered = report_to_html(report)

        if output_path:
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


def _run_check(args: argparse.Namespace) -> int:
    from . import __version__
    from .engine import RepositoryAnalyzer, find_specs_file
    from .evaluator import OpenAICompatibleEvaluator
    from .gate import (
        EXIT_ERROR,
        build_gate_artifact,
        build_gate_error_artifact,
        evaluate_gate,
        load_baseline_findings,
        load_policy,
        resolve_baseline_path,
        resolve_policy_path,
        write_json_atomic,
    )

    output_path = Path(args.output).expanduser().resolve()
    try:
        repo_path = Path(args.repo_path).expanduser().resolve()
        if not repo_path.exists() or not repo_path.is_dir():
            raise ValueError(f"Repository path does not exist or is not a directory: {repo_path}")

        policy_path = resolve_policy_path(repo_path, args.policy)
        policy = load_policy(policy_path)
        baseline_path = resolve_baseline_path(policy, policy_path, args.baseline)
        baseline_findings = load_baseline_findings(baseline_path) if baseline_path else set()

        specs_path = Path(args.specs_path).expanduser().resolve() if args.specs_path else None
        if specs_path is None:
            discovered_specs = find_specs_file(str(repo_path))
            if not discovered_specs:
                raise ValueError("SPECS.md file not found in repository")
            specs_path = Path(discovered_specs).resolve()

        debug_llm_path = str(Path(args.debug_llm_path).expanduser().resolve()) if args.debug_llm_path else None
        evaluator = OpenAICompatibleEvaluator.from_env(
            provider=args.provider,
            api_key=args.api_key,
            base_url=args.base_url,
            model=args.model,
            timeout=args.timeout or 60.0,
            reasoning_effort=args.reasoning_effort,
        )
        analyzer = RepositoryAnalyzer(evaluator=evaluator)
        report = analyzer.analyze_path(
            repo_path=str(repo_path),
            specs_path=str(specs_path),
            feature_ids=args.feature_ids,
            progress_callback=None if args.quiet else progress_printer,
            debug_output_path=debug_llm_path,
        )
        evaluation = evaluate_gate(report, policy, baseline_findings)
        artifact = build_gate_artifact(
            report,
            evaluation,
            repo_path=repo_path,
            specs_path=specs_path,
            policy_path=policy_path,
            policy=policy,
            baseline_path=baseline_path,
            provider=args.provider,
            model=evaluator.model,
            matcha_version=__version__,
            feature_ids=args.feature_ids,
            generated_paths=[path for path in [output_path, debug_llm_path] if path],
        )
        write_json_atomic(artifact, output_path)

        if not args.quiet:
            print(
                (
                    f"[matcha] gate {evaluation.decision.upper()}: "
                    f"{len(evaluation.violations)} violation(s), "
                    f"{len(evaluation.incomplete)} incomplete, "
                    f"{len(evaluation.waived)} waived"
                ),
                file=sys.stderr,
            )
            print(f"[done] wrote gate artifact to {output_path}", file=sys.stderr)
        return evaluation.exit_code
    except Exception as exc:
        error_artifact = build_gate_error_artifact(
            str(exc),
            repo_path=args.repo_path,
            policy_path=resolve_policy_path(args.repo_path, args.policy),
            provider=args.provider,
            model=args.model,
            matcha_version=__version__,
            generated_paths=[output_path],
        )
        try:
            write_json_atomic(error_artifact, output_path)
        except Exception:
            pass
        print(f"[matcha] gate ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR


def _resolve_paths(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[Path, str | None, str | None]:
    repo_path = Path(args.repo_path).expanduser().resolve()
    if not repo_path.exists():
        parser.error(f"Repository path does not exist: {repo_path}")

    specs_path = str(Path(args.specs_path).expanduser().resolve()) if args.specs_path else None
    debug_llm_path = str(Path(args.debug_llm_path).expanduser().resolve()) if args.debug_llm_path else None
    return repo_path, specs_path, debug_llm_path


def resolve_report_format(explicit_format: str | None, output_path: Path | None, quiet: bool) -> str:
    inferred_format = None
    if output_path:
        inferred_format = REPORT_FORMAT_EXTENSIONS.get(output_path.suffix.lower())

    if explicit_format and inferred_format and explicit_format != inferred_format:
        print(
            (
                f"[matcha] warning: output file extension suggests '{inferred_format}' "
                f"but using explicit format '{explicit_format}'."
            ),
            file=sys.stderr,
        )
        return explicit_format

    if explicit_format:
        return explicit_format
    if inferred_format:
        return inferred_format
    if output_path:
        if not quiet:
            print("[matcha] no format specified; defaulting to json output.", file=sys.stderr)
        return "json"
    return "table"


def progress_printer(status: str) -> None:
    labels = {
        "cloning": "cloning repository",
        "parsing": "parsing specs",
        "analyzing": "analyzing implementation",
        "indexing": "indexing repository evidence",
        "generating": "generating specs draft",
        "rendering": "validating specs draft",
    }
    print(f"[matcha] {labels.get(status, status)}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
