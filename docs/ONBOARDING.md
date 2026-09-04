# Onboarding a Repository into Matcha

`matcha-core onboard` turns repository evidence into a reviewable specs
draft. It does not infer authoritative product intent from code.

## Local prerequisites

Ollama is the default provider. Local onboarding does not require an OpenAI API
key and Matcha does not silently contact a remote provider.

1. [Install Ollama](https://ollama.com/download).
2. Start the Ollama application. If it is not managed by the desktop app or a
   system service, run `ollama serve` and leave that process running.
3. Pull the model Matcha will use:

```bash
ollama pull llama3.2
```

4. Confirm that Ollama can see the downloaded model:

```bash
ollama ls
```

By default Matcha connects to the local OpenAI-compatible endpoint at
`http://localhost:11434/v1`. Override it with `--base-url` or
`OLLAMA_BASE_URL` only when intentionally using another Ollama instance.

## Generate the draft

```bash
matcha-core onboard /path/to/repo
```

Useful options:

```bash
matcha-core onboard /path/to/repo \
  --language Spanish \
  --max-features 15 \
  --output /path/to/repo/SPECS.draft.md
```

To use another OpenAI-compatible provider:

```bash
export OPENAI_API_KEY=...
matcha-core onboard /path/to/repo \
  --provider openai \
  --model gpt-5.6-sol
```

`matcha-core bootstrap` is retained as a compatibility alias for existing
scripts. New integrations should use `matcha-core onboard`.

There is no automatic remote fallback. If Ollama is unavailable or produces an
invalid evidence payload, the command fails without writing a partial draft.
The Ollama defaults are intentionally bounded to 18,000 context characters and
eight features. Increase them only when the selected local model has a larger
context window; a larger prompt can leave too little room for structured output.
The default Ollama timeout is 180 seconds; override it with `--timeout` when a
slower local model needs more time.

For OpenAI reasoning models, Matcha defaults to `--reasoning-effort low` and
allocates the completion budget according to `--max-features`. If a completion
ends at its token limit without producing content, Matcha retries once with a
larger budget. Use `--reasoning-effort medium` or higher only after measuring a
useful quality improvement for the repository type.

## Trust boundary

The generated document describes behavior that appears to exist. It is not a
product contract until a person reviews it.

- Each feature and criterion must cite a file actually supplied to the model.
- Evidence paths invented by the model fail validation.
- Environment files, likely credentials, private keys, dependencies, and
  generated directories are excluded from model context.
- Existing `SPECS.md` files are excluded so the draft reflects the codebase.
- Output is atomic and existing drafts require `--force` to replace.
- All features use `Status: Draft`, which Matcha skips during analysis and CI
  gates.

Review the evidence, resolve the product questions, remove unsupported claims,
and decide priority and lifecycle status. Only then rename or copy the reviewed
content to `SPECS.md`.

## Run the first gate

After review:

```bash
matcha-core analyze /path/to/repo --output initial-report.html
matcha-core check /path/to/repo --output matcha-gate.json
```

Keep the first CI integration non-blocking until the team has reviewed the
generated specs and observed stable results on repeated runs.

## Codex skill

The repository includes `skills/matcha-onboard`, a thin orchestration layer for
the human review workflow. From a source checkout, install it with:

```bash
cp -R skills/matcha-onboard ~/.codex/skills/
```

The skill does not duplicate discovery logic. It invokes the public Matcha CLI,
reviews its evidence, asks focused product questions, and only promotes the
draft after explicit confirmation.
