# SKILL: plane

Persistent context the agents load each run for the **plane** project, so they don't re-derive it
every cycle. Expand this as we learn the codebase (Phase 1+).

## Project
- Plane is an open-source project management tool (issues, cycles, modules, pages).
- Issue source (read-only): `makeplane/plane`. Work target (write): `zahid-arbisoft/plane` (fork).
- Do NOT open PRs against `makeplane/plane` while building/testing.

## Stack (to confirm during Phase 1 exploration)
- Backend: Django / Python (`apiserver/`).
- Frontend: Next.js / TypeScript (`web/`, `admin/`, `space/`).
- Monorepo managed with yarn workspaces + turbo.

## Conventions / build / test
- TODO (Phase 1): record the real lint/test/build commands once verified against the fork.
  - e.g. backend: `ruff`, `pytest`; frontend: `yarn lint`, `yarn test`, `yarn build`.

## Review checklist (used by Reviewer agent, Phase 2)
- Change is minimal and scoped to the issue.
- Tests cover the new behavior and edge cases from the spec.
- No secrets, no obvious injection/auth issues.
- Matches existing patterns in the touched area.
