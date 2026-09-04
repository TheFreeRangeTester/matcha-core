# Using Matcha as a CI gate

Matcha separates analysis from policy. The model produces evidence-backed
findings; `.matcha/policy.yml` decides which findings block the pipeline.

## GitHub Actions

Store the provider key as a repository or organization secret. This example
preserves the gate artifact even when the gate blocks the workflow:

```yaml
name: Matcha gate

on:
  pull_request:
  push:
    branches: [main]

jobs:
  matcha:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - run: python -m pip install matcha-core

      - id: matcha
        continue-on-error: true
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: matcha-core check . --output matcha-gate.json

      - if: always()
        uses: actions/upload-artifact@v4
        with:
          name: matcha-gate
          path: matcha-gate.json

      - if: steps.matcha.outcome == 'failure'
        run: exit 1
```

For a self-hosted runner with Ollama, replace the secret with the local
provider:

```yaml
- run: matcha-core check . --provider ollama --model llama3.2 --output matcha-gate.json
```

## Exit codes

- `0`: passed
- `10`: definitive policy violation
- `20`: incomplete or low-confidence analysis blocked by policy
- `30`: invalid configuration or execution failure

All four outcomes write a JSON artifact when the output path is writable.
