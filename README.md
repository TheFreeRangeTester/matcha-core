<p align="center">
  <img src="https://raw.githubusercontent.com/TheFreeRangeTester/matcha-core/main/assets/branding/matcha-logo.png" alt="Matcha logo" width="180">
</p>

# Matcha Core

`matcha-core` analyzes a repository against its `SPECS.md` and produces structured implementation reports.

It can be used in two ways:

- as an installable CLI
- as a Python library

## Install

From PyPI:

```bash
python3 -m pip install matcha-core
```

For local development:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev]"
```

## CLI

OpenAI:

```bash
export OPENAI_API_KEY=...
matcha-core analyze /path/to/repo --output ./report.html
```

Ollama:

```bash
export OLLAMA_MODEL=llama3.2
matcha-core analyze /path/to/repo --provider ollama
```

If the specs file is not at the repo root:

```bash
matcha-core analyze /path/to/repo --specs /path/to/repo/docs/SPECS.md
```

To analyze only one feature:

```bash
matcha-core analyze /path/to/repo --feature FEAT-004 --output report.html
```

`matcha-core` infers the report format from `--output` when possible, so
`report.html`, `report.json`, and `report.md` automatically render the expected
format. If no format is specified and no output file is provided, the CLI
defaults to a terminal table view. You can also use `--reporter` as an alias
for `--format`.

## Library

```python
from matcha_core import OpenAICompatibleEvaluator, RepositoryAnalyzer
from matcha_core.reporting import report_to_html

evaluator = OpenAICompatibleEvaluator.from_env(provider="openai")
analyzer = RepositoryAnalyzer(evaluator=evaluator)
report = analyzer.analyze_path("/path/to/repo")

html = report_to_html(report)
```

## Onboard an existing repository

Onboarding is local-first because it sends broader repository context than a
normal criterion analysis. Ollama is the default provider and does not require
an OpenAI API key.

Local prerequisites:

1. [Install Ollama](https://ollama.com/download).
2. Start the Ollama application, or run `ollama serve` and leave it running.
3. Download the model used by Matcha and verify that it is available:

```bash
ollama pull llama3.2
ollama ls
```

Then generate the draft:

```bash
matcha-core onboard /path/to/repo
```

This writes `/path/to/repo/SPECS.draft.md`. It does not create or overwrite the
canonical `SPECS.md`. Every proposed feature is marked `Draft`, cites repository
evidence, carries a confidence score, and can include questions that require
product-owner judgment.

Choose a different OpenAI-compatible model explicitly when local output is not
good enough:

```bash
export OPENAI_API_KEY=...
matcha-core onboard /path/to/repo \
  --provider openai \
  --model gpt-5.6-sol \
  --language English
```

Matcha never silently falls back from Ollama to a remote provider. Files that
look like credentials, private keys, environment files, generated output, and
dependency directories are excluded from onboarding context. Use `--force`
only when intentionally replacing an existing draft. Ollama defaults to a
smaller discovery context and eight features so local models have room to
produce complete structured output. It also allows 180 seconds for local
generation. Context, feature count, and `--timeout` can all be overridden.
OpenAI reasoning models default to `--reasoning-effort low` for structured
onboarding so reasoning does not consume the entire completion budget. Override
it explicitly when an evaluated quality gain justifies the added latency and
cost.

`matcha-core bootstrap` remains available as a compatibility alias, but
`matcha-core onboard` is the documented command.

The Python API exposes the same workflow through `RepositoryBootstrapper` and
`OpenAICompatibleSpecGenerator`. See [docs/ONBOARDING.md](docs/ONBOARDING.md) for
the review and promotion workflow and the optional Codex skill.

## Output formats

- `json`
- `markdown`
- `html`
- `table`

For terminal output with evidence snippets:

```bash
matcha-core analyze /path/to/repo --format table --show-evidence
```

## CI gate

Copy [docs/POLICY_TEMPLATE.yml](docs/POLICY_TEMPLATE.yml) to
`.matcha/policy.yml` in the repository being checked, then run:

```yaml
version: 1
fail_on: [not_implemented, implemented_differently, not_specified]
priorities: [critical, high]
min_confidence: 0.75
on_inconclusive: block
allow: []
```

```bash
matcha-core check /path/to/repo --output matcha-gate.json
```

The gate applies deterministic policy rules after analysis. Model or retrieval
failures do not become implementation findings.

Exit codes are stable:

- `0`: policy passed
- `10`: one or more definitive findings violated the policy
- `20`: the result is inconclusive and the policy uses `on_inconclusive: block`
- `30`: configuration or execution error

These values do not overlap with argparse's standard exit code `2` for invalid
command-line usage.

Use `baseline` in the policy, or `--baseline`, to grandfather findings from a
previous Matcha JSON report. Only the same feature, criterion, and status are
waived. New or changed findings still fail.

Temporary exceptions belong in `allow`. Every exception requires an ISO
`expires` date and a reason; expired exceptions
no longer apply. Findings below `min_confidence` make the gate inconclusive
rather than allowing a false pass. Every run writes a JSON gate artifact with
the decision, violations, incomplete findings, waivers, complete analysis,
provider/model identity, repository commit and dirty state, and SHA-256 hashes
for specs, policy, and baseline.

A complete GitHub Actions example is available in
[docs/CI.md](docs/CI.md).

## Tests

```bash
python3 -m unittest discover -s tests
```

## Specs Authoring

Use [docs/SPECS_TEMPLATE.md](docs/SPECS_TEMPLATE.md) as a starting point when
onboarding an existing repository into Matcha.

Matcha validates specs before analysis. Feature headings must include a stable
ID such as `## FEAT-1 User login`, IDs must be unique, and every feature must
have at least one acceptance criterion. Fenced Markdown examples are ignored.

Reports distinguish definitive findings from execution state:

- `skipped` means the feature is planned and was intentionally excluded
- `inconclusive` means Matcha did not find enough relevant context
- `analysis_failed` means the evaluator could not produce a valid result

These states are never counted as `not_implemented`. JSON reports include a
`schema_version` field so consumers can validate the output contract.

Bootstrap-generated `Draft` features are also skipped. Change each reviewed
feature to an intentional lifecycle status such as `Done` or `Planned` before
using it in a gate.

## Release

The package metadata and release flow are documented in `RELEASING.md`.

## License

This project is licensed under the Apache License 2.0. See `LICENSE`.
