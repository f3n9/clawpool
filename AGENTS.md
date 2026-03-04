# Repository Guidelines

## Project Structure & Module Organization
This repository is currently a clean bootstrap (no source files or commits yet). Use the layout below as the default when adding code:
- `src/` application code, organized by feature or domain
- `tests/` unit and integration tests mirroring `src/`
- `assets/` static files (images, fixtures, sample data)
- `docs/` design notes, architecture decisions, and plans

Example: `src/auth/login.ts` with matching test `tests/auth/login.test.ts`.

## Build, Test, and Development Commands
No build system is configured yet. Until project tooling is added, use:
- `git status` check workspace state before and after changes
- `git diff` review local modifications
- `git log --oneline --decorate -n 10` inspect recent history

When language tooling is introduced, add project-local scripts and document them here (for example: `npm test`, `make build`, or `pytest`).

## Coding Style & Naming Conventions
Adopt consistent defaults from the first commit:
- Indentation: 2 spaces for YAML/JSON/Markdown, 4 spaces for Python, language standard otherwise
- Filenames: `kebab-case` for docs/assets, language-idiomatic naming for code
- Keep modules focused; avoid files that mix unrelated responsibilities
- Prefer automated formatters/linters once configured (for example `prettier`, `eslint`, `ruff`)

## Testing Guidelines
No test framework is configured yet. Required baseline once tests are added:
- Place tests under `tests/` with paths matching `src/`
- Name tests clearly (`<module>.test.*` or `<module>_test.*` depending on stack)
- Cover new logic and bug fixes in the same change
- Run all project tests locally before opening a PR

## Commit & Pull Request Guidelines
Since there is no existing history, start with Conventional Commit style:
- `feat: add initial project scaffold`
- `fix: correct login validation edge case`
- `docs: add contributor guidelines`

PRs should include:
- concise summary of what changed and why
- linked issue (if applicable)
- test evidence (command + result)
- screenshots/log snippets for UI or behavior changes

## Security & Configuration Tips
- Do not commit secrets; use `.env.local` and keep `.env.example` updated
- Pin dependencies where possible
- Review new third-party packages before adoption
