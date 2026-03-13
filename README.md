<p align="center">
  <img src="assets/branding/matcha-logo.png" alt="Matcha logo" width="180">
</p>

# Matcha Core

`matcha-core` analyzes a repository against its `SPECS.md` and produces structured implementation reports.

It can be used in three ways:

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
matcha-core analyze /path/to/repo --format html --output ./report.html
```

Ollama:

```bash
export OLLAMA_MODEL=llama3.2
matcha-core analyze /path/to/repo --provider ollama --format table
```

If the specs file is not at the repo root:

```bash
matcha-core analyze /path/to/repo --specs /path/to/repo/docs/SPECS.md
```

## Library

```python
from matcha_core import OpenAICompatibleEvaluator, RepositoryAnalyzer
from matcha_core.reporting import report_to_html

evaluator = OpenAICompatibleEvaluator.from_env(provider="openai")
analyzer = RepositoryAnalyzer(evaluator=evaluator)
report = analyzer.analyze_path("/path/to/repo")

html = report_to_html(report)
```

## Output formats

- `json`
- `markdown`
- `html`
- `table`

For terminal output with evidence snippets:

```bash
matcha-core analyze /path/to/repo --format table --show-evidence
```

## Tests

```bash
python3 -m unittest discover -s tests
```

## Specs Authoring

Use [docs/SPECS_TEMPLATE.md](docs/SPECS_TEMPLATE.md) as a starting point when
onboarding an existing repository into Matcha.

## Release

The package metadata and release flow are documented in `RELEASING.md`.

## License

This project is licensed under the Apache License 2.0. See `LICENSE`.
