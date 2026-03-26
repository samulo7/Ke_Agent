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

## A-04 Architecture Insights

| File | Role | When Used | Upstream/Downstream |
| --- | --- | --- | --- |
| app/core/trace_context.py | Request-scoped trace context store based on `ContextVar`. | Read/write during request lifecycle when propagating `trace_id`. | Upstream: middleware-assigned trace ID. Downstream: probes/services can read current trace context. |
| app/core/trace_middleware.py | Trace middleware that enforces `X-Trace-Id` ingest/generate/echo and emits request logs with duration and error category. | Applied to every HTTP request in FastAPI app bootstrap. | Upstream: incoming headers and request state. Downstream: response headers and structured observability logger records. |
| app/core/structured_logging.py | JSON log formatter and observability logger bootstrap. | App startup and tests that need deterministic log capture. | Upstream: middleware `obs` payload. Downstream: machine-readable logs for troubleshooting and audits. |
| app/services/health.py | Health probe abstraction and status merge logic (`ok/degraded/down`). | Called by `/healthz` route to evaluate service/dependency probes. | Upstream: injected probe functions. Downstream: normalized health report payload returned by API. |
| app/api/health.py | `GET /healthz` endpoint and HTTP status mapping (`ok=200`, degraded/down=`503`). | Runtime health checks and failure drills. | Upstream: `HealthService` report + request trace context. Downstream: client-visible health payload and middleware error categorization hints. |
| app/api/main.py | Minimal FastAPI app factory wiring middleware, logging, and health route. | App entrypoint creation in runtime and integration tests. | Upstream: optional injected health service/log stream. Downstream: executable API surface constrained to A-04 scope. |
| tests/api/test_health_observability.py | Verification suite for trace propagation, log contract completeness, and probe failure behavior. | A-04 acceptance and regression checks. | Upstream: app factory + probe injection. Downstream: pass/fail evidence for A-04 gate. |

## A-05 Architecture Insights

| File | Role | When Used | Upstream/Downstream |
| --- | --- | --- | --- |
| app/schemas/dingtalk_chat.py | Canonical chat data contracts for incoming messages and agent replies (`text`/`interactive_card`). | Shared by HTTP and Stream paths when normalizing payloads and producing channel-specific output. | Upstream: parsed DingTalk event content. Downstream: `SingleChatService` decision and reply payload builders. |
| app/services/single_chat.py | A-05 single-chat decision engine (single-only, text-only, flow-guidance card, application-draft card). | Called for each valid incoming chatbot message. | Upstream: normalized `IncomingChatMessage`. Downstream: `ChatHandleResult` consumed by API/Stream responders. |
| app/integrations/dingtalk/stream_parser.py | Parses heterogeneous DingTalk callback payloads into internal chat schema. | Used by both `/dingtalk/stream/events` and Stream runtime callback handler. | Upstream: raw DingTalk callback body fields. Downstream: strict `IncomingChatMessage` for business handling. |
| app/integrations/dingtalk/reply_builder.py | Converts internal reply schema into DingTalk-compatible outbound payload for API test route. | Used in API response body for A-05 sandbox verification. | Upstream: `AgentReply`. Downstream: contract proof for DingTalk message shape (`msgtype`, text/card fields). |
| app/api/dingtalk.py | HTTP callback endpoint `/dingtalk/stream/events` for local/sandbox event-loop validation. | Used in local integration tests and manual callback simulation. | Upstream: request payload + trace middleware context. Downstream: parsed event, service outcome, and response payload evidence. |
| app/integrations/dingtalk/stream_runtime.py | Active DingTalk Stream runtime bootstrap, callback handler registration, trace-aware observability logging, and reply dispatch via SDK. | Used when running real Stream long-connection client for channel verification. | Upstream: env credentials + DingTalk Stream callback frames. Downstream: single-chat handler outcomes and SDK reply calls/ACK results. |
| infra/scripts/run-dingtalk-stream.py | Operator entrypoint for running A-05 Stream client from `.env` with config loading and startup guards. | Used during real DingTalk connect-channel verification and local runtime operations. | Upstream: `.env`/process env configuration. Downstream: starts `stream_runtime.run_stream_client_forever`. |
| tests/api/test_dingtalk_single_chat.py | API-level acceptance coverage for text/card routing, non-single rejection, and invalid payload handling. | Run in A-05 regression and gate checks. | Upstream: app factory + sample callback payloads. Downstream: pass/fail evidence for A-05 callback contract. |
| tests/integrations/test_stream_runtime.py | Stream runtime unit coverage for credential loading, payload dispatch, and sender channel routing. | Run when verifying active stream runtime behavior without real DingTalk dependency. | Upstream: fake sender + sample message payload. Downstream: confidence that runtime glue logic is correct before real connection. |
