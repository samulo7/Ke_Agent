# Repository Guidelines

## Project Structure & Module Organization
This repository is currently documentation-first. The baseline docs live under `memory-bank/`, including `PRD-钉钉企业内部Agent-MVP.md`, `tech-stack.md`, `IMPLEMENTATION_PLAN.md`, `architecture.md`, and `progress.md`.

When code is added, keep a multi-file layout: `app/api/`, `app/agents/`, `app/rag/`, `app/integrations/`, `app/services/`, `app/repos/`, `app/schemas/`, `infra/`, and `docs/`. Do not introduce monolithic files that mix routes, business logic, data access, prompts, and models.

## Always Rules For AI Contributors
These rules are mandatory for every coding task.

1. Before writing any code, fully read `memory-bank/architecture.md`.
2. Before writing any code, fully read `memory-bank/PRD-钉钉企业内部Agent-MVP.md`.
3. Before writing any code, fully read `memory-bank/IMPLEMENTATION_PLAN.md`.
4. After each completed step, update `memory-bank/progress.md` with step ID, status, date, and verification result.
5. After each major feature or milestone, update `memory-bank/architecture.md` with module responsibilities and changed file roles.
6. Before merge or milestone handoff, run `.\infra\scripts\validate-memory-bank.ps1` and fix all reported failures.
7. If any required memory-bank file is missing, stop implementation and request it before coding.

## Build, Test, and Development Commands
No project-local build, test, or run scripts are committed yet.

- `rg --files -uu` lists the current repository contents.
- `Get-Content .\memory-bank\architecture.md -Encoding UTF8` reads the required architecture memory file.
- `Get-Content .\memory-bank\PRD-钉钉企业内部Agent-MVP.md -Encoding UTF8` reads the required PRD memory file.
- `Get-Content .\memory-bank\IMPLEMENTATION_PLAN.md -Encoding UTF8` reads the execution plan.
- `Get-Content .\memory-bank\progress.md -Encoding UTF8` reads or checks completion history.
- `Get-Content .\memory-bank\tech-stack.md -Encoding UTF8` reads the technical design baseline.
- `.\infra\scripts\validate-memory-bank.ps1` validates required memory-bank updates before handoff.
- `.\infra\scripts\setup-git-hooks.ps1` configures `core.hooksPath` to `.githooks` and enables pre-commit validation.

## Coding Style & Naming Conventions
Keep Markdown concise, structured, and UTF-8 encoded to avoid garbled Chinese text. Prefer ATX headings (`##`), short paragraphs, and fenced code blocks for directory trees or command examples.

For the planned Python service, use 4-space indentation, `snake_case` for modules and functions, and `PascalCase` for schema or model classes. Keep files focused; if a file accumulates unrelated concerns, split it. Prefer provider-scoped adapters under `app/integrations/<provider>/`.

## Implementation Rules
Use explicit layers: API handlers should stay thin, business rules belong in `services/` or `agents/`, and persistence belongs in `repos/`. Keep network access behind typed integration clients; do not scatter raw HTTP calls across the codebase. Avoid hidden global state; pass state through services, repositories, and well-defined schemas.

## Testing Guidelines
There is no committed test suite yet. When the FastAPI codebase is scaffolded, add a top-level `tests/` directory that mirrors `app/`, for example `tests/rag/test_retrieval.py`. Prefer `pytest` and cover intent routing, permission filtering, and fallback behavior before merging.

## Commit & Pull Request Guidelines
This workspace snapshot does not include `.git` history, so no existing commit convention can be inferred directly. Use short, imperative commit messages with a scope prefix when possible, such as `docs: refine MVP scope` or `feat: scaffold dingtalk stream adapter`.

Pull requests should state what changed, why it changed, and how it was validated. Link the relevant requirement in the PRD, tech stack, or memory-bank docs. Include screenshots only when diagrams, card layouts, or visible UX output change.

## Security & Configuration Tips
Do not commit real DingTalk credentials, Qwen API keys, or internal sensitive documents. Keep model names, vector settings, and endpoints configurable rather than hard-coded.
