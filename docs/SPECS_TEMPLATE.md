# SPECS Template for Matcha

Use this template when onboarding an existing repository into Matcha.

The goal is to describe the system in a way that is easy for both humans and
`matcha-core` to evaluate against the codebase.

## Authoring Guidelines

- Use one section per feature.
- Prefer headings like `## FEAT-1 Feature name`.
- Write 3-5 acceptance criteria per feature.
- Keep each criterion specific and observable in code.
- Use `Status: Done` for implemented features and `Status: Planned` for future work.
- Reference important files in backticks when you know them.
- Avoid vague product language that cannot be validated from the repository.

## Recommended Structure

```md
# Project Specs

## FEAT-1 Feature name
**Priority**: High
**Status**: Done
**Related Components**: `path/to/file.py`, `src/module.ts`

Acceptance Criteria:
- The system does X.
- The system rejects Y when condition Z is true.
- The system stores or returns A.
- The implementation is handled in `path/to/file.py`.
```

## Full Example

```md
# Project Specs

## FEAT-1 User authentication
**Priority**: High
**Status**: Done
**Related Components**: `src/auth.py`, `src/session.py`, `src/api/auth_routes.py`

Acceptance Criteria:
- Users can sign in with email and password.
- Invalid credentials return an authentication error.
- Successful login creates a persisted session.
- Unauthenticated users cannot access protected routes.

## FEAT-2 Repository analysis
**Priority**: High
**Status**: Done
**Related Components**: `src/analyzer.py`, `src/specs_parser.py`, `src/reporting.py`

Acceptance Criteria:
- The system can analyze a local repository path.
- The system loads `SPECS.md` by default when no explicit specs path is provided.
- The analysis output includes per-feature findings.
- Reports can be rendered as JSON and HTML.

## FEAT-3 Team invitations
**Priority**: Medium
**Status**: Done
**Related Components**: `src/invitations.py`, `src/email.py`

Acceptance Criteria:
- Admins can invite users by email.
- Invited users receive an email containing an invitation link.
- Expired invitations cannot be accepted.

## FEAT-4 Single sign-on
**Priority**: Medium
**Status**: Planned
**Related Components**: `src/auth/sso.py`

Acceptance Criteria:
- Users can sign in with Google OAuth.
- Organization admins can restrict login by email domain.
```

## Best Practices

### Good acceptance criteria

- `The API returns 403 when a non-owner attempts to delete a project.`
- `The system stores audit events for approval actions.`
- `The report renderer can export HTML output.`
- `The logic is implemented in src/auth.py and src/session.py.`

### Weak acceptance criteria

- `The UX should feel modern.`
- `The app should be scalable.`
- `The platform should be enterprise-ready.`
- `Authentication should work well.`

## Onboarding Checklist

- Capture the core implemented features first.
- Include the most important business rules and restrictions.
- Separate shipped behavior from planned roadmap items.
- Keep feature names short and descriptive.
- Keep each criterion focused on a single behavior.
- Start small; the first version does not need to document the entire product.
