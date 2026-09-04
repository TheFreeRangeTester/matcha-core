---
name: matcha-onboard
description: Onboard an existing software repository into Matcha by generating and reviewing evidence-backed draft specs. Use when a project needs its initial SPECS.md or when the user asks to derive Matcha specs from a codebase.
---

# Matcha Onboard

Create a trustworthy first specification without treating the current code as
authoritative product intent.

## Workflow

1. Inspect the target repository and check whether `SPECS.md` or
   `SPECS.draft.md` already exists. Preserve existing files unless the user
   explicitly asks to replace one.
2. Use the installed `matcha-core onboard` command to generate
   `SPECS.draft.md`. Prefer `--provider ollama` for local-first onboarding. If
   Ollama is unavailable or its result fails validation, report that outcome
   and offer an explicit remote-provider run; never send repository content to
   a remote model silently.
3. Read the complete draft and inspect cited repository paths where the claim
   or confidence is consequential. Treat generated features as observed
   behavior only.
4. Present the product owner with the highest-leverage unresolved questions in
   small batches. Focus on intended behavior, priority, boundaries, and whether
   apparently shipped behavior should remain part of the product contract.
5. Incorporate confirmed answers into the draft. Remove claims that lack code,
   test, documentation, or product-owner support. Keep planned behavior
   separate from shipped behavior.
6. Only after explicit confirmation, create or update canonical `SPECS.md` and
   replace each `Draft` status with a deliberate status such as `Done` or
   `Planned`.
7. Run `matcha-core analyze` and then the configured `matcha-core check` against
   the reviewed specs. Report model/provider failures as inconclusive or errors,
   never as missing implementation.

## Command Shape

```bash
matcha-core onboard /absolute/path/to/repository \
  --provider ollama \
  --output /absolute/path/to/repository/SPECS.draft.md
```

Add `--language` when the user has chosen a specs language. Use `--force` only
when they explicitly authorized replacing the exact draft file. Debug output
contains model responses and must not be committed without review.

## Boundaries

- Do not infer roadmap intent, desired UX, compliance guarantees, or production
  reliability from implementation alone.
- Do not promote a draft merely because it parses or matches the current code.
- Do not add low-level modules, dependencies, build configuration, or visual
  styling as product features unless they implement an observable contract.
- Preserve exact evidence paths so future reviewers can trace each criterion.
