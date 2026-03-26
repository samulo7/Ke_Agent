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

## A-06 Architecture Insights

| File | Role | When Used | Upstream/Downstream |
| --- | --- | --- | --- |
| app/schemas/user_context.py | Defines the normalized identity contract (`user_id`, `user_name`, `dept_id`, `dept_name`, `identity_source`, `is_degraded`, `resolved_at`). | Used after each incoming chat event is parsed, before business reply logic. | Upstream: resolver output and fallback normalization rules. Downstream: API response evidence + observability fields. |
| app/services/user_context.py | Implements OpenAPI-first identity resolution and in-process TTL cache (`fresh=5m`, `stale=30m`) with event-field fallback. | Called on each single-chat event in both HTTP callback and Stream runtime paths. | Upstream: DingTalk sender identifiers + optional OpenAPI client. Downstream: stable `UserContext` injected into request/runtime scope and logs. |
| app/integrations/dingtalk/openapi_identity.py | Encapsulates DingTalk token/user/department retrieval with v1-first and legacy topapi fallback behavior. | Used by `UserContextResolver` when resolving identity from DingTalk OpenAPI. | Upstream: `DINGTALK_CLIENT_ID` and `DINGTALK_CLIENT_SECRET`. Downstream: `IdentityRecord` for resolver and cache writes. |
| app/api/dingtalk.py | Injects `user_context` into request state and returns response-level `user_context` metadata for A-06 acceptance visibility. | Used when `/dingtalk/stream/events` receives callback payloads. | Upstream: parsed `IncomingChatMessage` + `UserContextResolver`. Downstream: unchanged A-05 reply flow plus auditable identity metadata. |
| app/core/trace_middleware.py | Extends request-completed logs with A-06 identity audit fields (`user_id`, `dept_id`, `identity_source`, `is_degraded`). | Runs on all HTTP requests after route handling finalizes request state. | Upstream: route-populated request state fields. Downstream: structured log records used for acceptance and troubleshooting. |
| app/api/main.py | Wires default `UserContextResolver` into app state for runtime usage without changing reply-service behavior. | App startup for local dev, tests, and deployment runtime. | Upstream: process environment variables. Downstream: availability of resolver dependency in API handlers. |
| app/integrations/dingtalk/stream_runtime.py | Applies the same identity resolution path for Stream callbacks so HTTP/Stream behavior stays consistent. | Used during active DingTalk Stream long-connection runtime. | Upstream: Stream callback payload parsing + resolver. Downstream: auditable runtime logs and channel-consistent context behavior. |
| tests/services/test_user_context.py | Verifies resolver priority and cache/fallback thresholds (openapi, cache_fresh, cache_stale, event_fallback). | A-06 unit gate and regression safety net for identity logic. | Upstream: fake identity client and simulated time. Downstream: deterministic proof of resolution ordering and degradation behavior. |
| tests/api/test_identity_context.py | Verifies `/dingtalk/stream/events` returns `user_context` and logs match response identity fields. | A-06 API acceptance tests. | Upstream: app factory with stub resolver and callback payloads. Downstream: evidence for context visibility and non-mismatch across users/departments. |
| tests/integrations/test_openapi_identity.py | Verifies DingTalk identity client request path: v1 success and topapi fallback success. | Integration-layer guard for OpenAPI client behavior. | Upstream: mocked DingTalk HTTP responses. Downstream: confidence that production identity lookup paths are wired correctly. |

## A-07 Architecture Insights

| File | Role | When Used | Upstream/Downstream |
| --- | --- | --- | --- |
| app/services/intent_classifier.py | Implements deterministic six-class intent routing (`policy_process`, `document_request`, `reimbursement`, `leave`, `fixed_quote`, `other`) with confidence scoring and overlap priority. | Called by chat handling before channel-specific response composition. | Upstream: normalized user text. Downstream: typed intent result consumed by service/API/stream paths. |
| app/schemas/dingtalk_chat.py | Extends chat contract with `IntentType` and `ChatHandleResult.intent` for explicit route traceability. | Used whenever chat outcomes are serialized to API/stream responses. | Upstream: classifier output. Downstream: response payloads, runtime logs, and test assertions. |
| app/services/single_chat.py | Replaces keyword heuristics with injected `IntentClassifier` and keeps card routing scoped to `document_request` and `reimbursement/leave`. | Executed for each parsed single-chat text message. | Upstream: `IncomingChatMessage` + classifier dependency. Downstream: intent-aware `ChatHandleResult` with stable reason/channel mapping. |
| app/api/dingtalk.py | Persists resolved intent into request state and response body (`intent`) for callback-side audit visibility. | Used on `/dingtalk/stream/events` HTTP callback handling. | Upstream: `SingleChatService` outcome. Downstream: middleware logging and API contract checks. |
| app/core/trace_middleware.py | Adds `intent` to request lifecycle observability payloads (`request_completed` and `request_exception`). | Runs for all HTTP requests after route execution. | Upstream: route-populated `request.state.intent`. Downstream: structured logs for analytics and troubleshooting. |
| app/core/structured_logging.py | Serializes `intent` into JSON log records alongside trace and identity fields. | Applied when observability logger formats records. | Upstream: middleware `obs` payload. Downstream: machine-readable logs for intent distribution analysis. |
| app/integrations/dingtalk/stream_runtime.py | Carries intent through stream callback outcomes and logs so Stream and HTTP channels remain behaviorally aligned. | Used during active DingTalk Stream callback processing. | Upstream: parsed stream payload + `SingleChatService` outcome. Downstream: sender dispatch metrics and runtime audit records. |
| tests/services/test_intent_classifier.py | Defines offline labeled dataset (6 intents, each >=10 samples) and enforces accuracy threshold `>=85%` plus overlap-priority checks. | A-07 acceptance and regression gate for classifier behavior. | Upstream: classifier implementation. Downstream: quantitative pass/fail evidence for A-07. |
| tests/services/test_single_chat_service.py | Verifies single-chat response routing remains consistent after intent integration. | Service-layer regression for non-single, text fallback, and card scenarios. | Upstream: classifier + single-chat service. Downstream: confidence that A-05 behavior is preserved with A-07 routing. |
| tests/api/test_dingtalk_single_chat.py / tests/api/test_identity_context.py / tests/integrations/test_stream_runtime.py | Verifies intent field propagation in HTTP response, identity-context logs, and Stream runtime outcomes. | Integration and API regression checks after introducing intent metadata. | Upstream: service outcome serialization and observability wiring. Downstream: cross-channel consistency evidence for A-07 delivery. |

## A-08 Architecture Insights

| File | Role | When Used | Upstream/Downstream |
| --- | --- | --- | --- |
| app/schemas/knowledge.py | Defines A-08 retrieval contracts: `KnowledgeEntry`, `RetrievedEvidence`, `KnowledgeAnswer`, and citation metadata with UTC answer timestamp helpers. | Used by repository, retriever, and answer-composition services. | Upstream: in-memory knowledge corpus and retrieval results. Downstream: `SingleChatService` traceable text-answer payload fields. |
| app/rag/sample_corpus.py | Provides versioned built-in sample knowledge corpus (`a08-sample-v1`) containing document + FAQ entries with source metadata. | Loaded by default in-memory repository for deterministic tests and local runs. | Upstream: curated sample requirements for FR-04 acceptance. Downstream: retrieval candidates and offline evaluation fixtures. |
| app/repos/knowledge_repository.py | Declares pluggable repository contract (`list_entries`, `knowledge_version`) for retrieval backend abstraction. | Consumed by retriever and answer service regardless of storage backend. | Upstream: repository implementation choice (in-memory now, SQL later). Downstream: A-10 migration boundary for SQL/permission filtering. |
| app/repos/in_memory_knowledge_repository.py | Implements repository contract with static sample corpus and fixed version tag. | Default repository for A-08 runtime and tests. | Upstream: sample corpus loader. Downstream: deterministic retrieval/evaluation behavior without external DB dependencies. |
| app/rag/knowledge_retriever.py | Implements deterministic document-first retrieval, top-k cutoff, low-match filtering, and env-driven `PGVECTOR_TOP_K` fallback parsing. | Called for policy/fixed-quote/other text-answer intents. | Upstream: normalized question + intent + repository entries. Downstream: ranked evidence list for answer composition and citation tracing. |
| app/services/knowledge_answering.py | Composes structured FR-04 answer template (conclusion, applicability, sources, next step) and no-hit fallback messaging. | Called by `SingleChatService` when intent is not card-only flow/application branch. | Upstream: ranked retrieval evidences. Downstream: `KnowledgeAnswer` including `source_ids`, `permission_decision`, `knowledge_version`, `answered_at`, and citations. |
| app/services/single_chat.py | Integrates A-08 answer service into chat routing while preserving existing card paths for document-request/reimbursement/leave. | Runs for every valid single-chat text input. | Upstream: intent classifier + knowledge answer service. Downstream: enriched `ChatHandleResult` traceability fields to API/Stream. |
| app/api/dingtalk.py | Persists A-08 traceability fields into request state and callback response payload for auditing. | On `/dingtalk/stream/events` processing success path. | Upstream: `ChatHandleResult` metadata. Downstream: middleware observability logs and callback consumer evidence. |
| app/integrations/dingtalk/stream_runtime.py | Propagates A-08 metadata in stream runtime outcomes and structured callback logs. | During active Stream callback handling. | Upstream: `SingleChatService` outcomes. Downstream: runtime audit visibility aligned with HTTP callback behavior. |
| app/core/trace_middleware.py + app/core/structured_logging.py | Extends request observability schema with knowledge trace fields (`source_ids`, `permission_decision`, `knowledge_version`, `answered_at`). | Applied to API request completion/exception logging. | Upstream: route-populated request state. Downstream: machine-readable audit records for retrieval answer traceability. |
| tests/rag/test_knowledge_retriever.py + tests/rag/test_retrieval_evaluation.py + tests/services/test_knowledge_answering.py | Adds A-08 acceptance coverage for retrieval strategy and threshold gates (`FAQ Top3`, `doc QA accuracy`, `citation validity`) plus non-fabrication behavior. | Executed in A-08 regression pipeline before full test suite. | Upstream: retrieval and answer-composition logic. Downstream: quantified pass/fail evidence for FR-04 gate readiness. |

Operational Note (A-06):
- Local API startup must load runtime credentials (`uvicorn app.api.main:app --env-file .env` or equivalent exported env vars). If `DINGTALK_CLIENT_ID` / `DINGTALK_CLIENT_SECRET` are absent at process start, resolver cannot create OpenAPI client and will intentionally degrade to `identity_source=event_fallback`.
