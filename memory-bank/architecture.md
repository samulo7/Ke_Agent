## Architecture Notes

Last Updated: 2026-03-26

## File Responsibilities

| File | Responsibility |
| --- | --- |
| memory-bank/PRD-钉钉企业内部Agent-MVP.md | Product requirements and acceptance scope |
| memory-bank/tech-stack.md | Technical stack baseline and design constraints |
| memory-bank/IMPLEMENTATION_PLAN.md | Decision-complete implementation plan for MVP milestones A+B, including FR mapping, quantified gates, and interface contracts |
| memory-bank/progress.md | Execution progress tracking and verification outcomes |
| AGENTS.md | Repository-wide contributor rules and Always constraints |
| infra/scripts/validate-memory-bank.ps1 | Automated gate to verify memory-bank completeness and update discipline |
| .githooks/pre-commit | Git pre-commit entrypoint that runs memory-bank validation |
| infra/scripts/setup-git-hooks.ps1 | One-time script to configure Git `core.hooksPath` to `.githooks` |

## A-01 Architecture Insights

| File | Role | When Used | Upstream/Downstream |
| --- | --- | --- | --- |
| AGENTS.md | Defines mandatory contributor discipline, required reads, and documentation update rules. | First read before any implementation step; referenced after each step/milestone to enforce updates. | Upstream: repository governance. Downstream: controls execution order for PRD/plan reads and progress/architecture updates. |
| memory-bank/PRD-钉钉企业内部Agent-MVP.md | Defines product scope, FR/NFR boundaries, and acceptance scenarios for MVP. | Used during requirement interpretation, intent routing boundaries, and acceptance mapping. | Upstream: business goals and scenarios. Downstream: constrains implementation behavior and test expectations. |
| memory-bank/tech-stack.md | Provides technical baseline and design constraints for implementation choices. | Used when selecting runtime, integration style, and infrastructure defaults. | Upstream: platform and architecture recommendations. Downstream: informs feasible implementation patterns and operational limits. |
| memory-bank/IMPLEMENTATION_PLAN.md | Defines execution sequence, quantified gates, FR-to-step mapping, and delivery boundaries (A+B). | Used as the operational checklist for each milestone step and pass/fail criteria. | Upstream: PRD and AGENTS constraints. Downstream: drives daily execution and handoff expectations. |
| memory-bank/progress.md | Records completed steps with evidence and verification outcomes for future contributors. | Updated immediately after each completed step. | Upstream: completed work and test evidence. Downstream: traceable delivery history for next-step planning. |
| infra/scripts/validate-memory-bank.ps1 | Enforces documentation gate checks (required files, format, expected step/date). | Run before milestone handoff and after critical memory-bank updates. | Upstream: current `progress.md`/`architecture.md`/PRD/plan state. Downstream: PASS/FAIL release gate for documentation readiness. |
