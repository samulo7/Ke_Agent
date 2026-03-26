## Architecture Notes

Last Updated: 2026-03-26

## File Responsibilities

| File | Responsibility |
| --- | --- |
| memory-bank/PRD-钉钉企业内部Agent-MVP.md | Product requirements and acceptance scope |
| memory-bank/tech-stack.md | Technical stack baseline and design constraints |
| memory-bank/IMPLEMENTATION_PLAN.md | Step-by-step implementation plan for AI contributors |
| memory-bank/progress.md | Execution progress tracking and verification outcomes |
| AGENTS.md | Repository-wide contributor rules and Always constraints |
| infra/scripts/validate-memory-bank.ps1 | Automated gate to verify memory-bank completeness and update discipline |
| .githooks/pre-commit | Git pre-commit entrypoint that runs memory-bank validation |
| infra/scripts/setup-git-hooks.ps1 | One-time script to configure Git `core.hooksPath` to `.githooks` |
