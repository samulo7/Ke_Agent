# A-03 Configuration Baseline

This document defines the configuration inventory for A-03 and classifies each key by:
- required vs optional
- default value
- production forbidden switch (if applicable)

## Validation command

```bash
python infra/scripts/validate-config.py --env-file .env.example
```

The command returns a non-zero exit code when required keys are missing, values are invalid, or production-forbidden switches are enabled in `APP_ENV=prod`.

## Configuration inventory

| Category | Key | Required | Default | Production Forbidden | Purpose |
| --- | --- | --- | --- | --- | --- |
| app | `APP_ENV` | No | `dev` | No | Runtime environment (`dev/test/staging/prod`). |
| app | `APP_PORT` | No | `8000` | No | HTTP listen port. |
| app | `APP_DEBUG` | No | `false` | No | Debug mode toggle. |
| dingtalk | `DINGTALK_CLIENT_ID` | Yes | - | No | DingTalk client id. |
| dingtalk | `DINGTALK_CLIENT_SECRET` | Yes | - | No | DingTalk client secret. |
| dingtalk | `DINGTALK_AGENT_ID` | Yes | - | No | DingTalk agent id. |
| dingtalk | `DINGTALK_STREAM_ENDPOINT` | No | `https://api.dingtalk.com/v1.0/gateway/connections/open` | No | Stream endpoint URL. |
| dingtalk | `DINGTALK_OPENAPI_ENDPOINT` | No | `https://api.dingtalk.com` | No | DingTalk OpenAPI base URL used by card createAndDeliver calls. |
| dingtalk | `DINGTALK_CARD_TEMPLATE_ID` | No | empty | No | Interactive request-confirm card template id (fixed `confirm_request/cancel_request` buttons). |
| dingtalk | `DINGTALK_HR_APPROVER_USER_ID` | No | empty | No | HR approver DingTalk user id for Stream-side approval card delivery. |
| dingtalk | `DINGTALK_HR_CARD_TEMPLATE_ID` | No | empty | No | HR approval action card template id (fixed `approve/reject` callbacks). |
| dingtalk | `DINGTALK_CARD_CALLBACK_DEBUG` | No | `false` | No | Enable card callback diagnostics (`topic` + payload parser logs) for Gate 0 checks. |
| dingtalk | `DINGTALK_AI_CARD_STREAMING_ENABLED` | No | `false` | No | Enable streaming AI card typewriter effect for long text replies. |
| dingtalk | `DINGTALK_AI_CARD_TEMPLATE_ID` | No | empty | No | Streaming AI card template id (required when streaming enabled). |
| dingtalk | `DINGTALK_AI_CARD_CONTENT_KEY` | No | `content` | No | Markdown variable key bound for streaming content updates. |
| dingtalk | `DINGTALK_AI_CARD_TITLE_KEY` | No | empty | No | Optional title variable key in card template. |
| dingtalk | `DINGTALK_AI_CARD_TITLE` | No | `企业 Agent` | No | Optional title value for streaming card. |
| dingtalk | `DINGTALK_AI_CARD_CHUNK_CHARS` | No | `20` | No | Characters pushed per streaming update batch. |
| dingtalk | `DINGTALK_AI_CARD_INTERVAL_MS` | No | `120` | No | Delay between streaming updates (ms). |
| dingtalk | `DINGTALK_AI_CARD_MIN_CHARS` | No | `80` | No | Minimum reply length to trigger streaming card. |
| model | `LLM_API_KEY` | Yes* | - | No | Primary LLM API key (OpenAI-compatible gateway). |
| model | `LLM_CHAT_MODEL` | No | `qwen-plus` | No | Primary chat model name. |
| model | `LLM_INTENT_MODEL` | No | `qwen-plus` | No | Primary intent model name. |
| model | `LLM_EMBEDDING_MODEL` | No | `text-embedding-v4` | No | Primary embedding model name. |
| model | `LLM_BASE_URL` | No | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` | No | Primary OpenAI-compatible endpoint. |
| model | `LLM_TIMEOUT_SECONDS` | No | `10` | No | Primary LLM timeout per request (seconds). |
| model | `LLM_MAX_RETRIES` | No | `2` | No | Primary LLM max retries per call. |
| model | `LLM_MAX_TOKENS` | No | empty | No | Optional max output tokens for completion requests. |
| model | `QWEN_API_KEY` | No | empty | No | Legacy alias of `LLM_API_KEY` (backward compatibility). |
| model | `QWEN_CHAT_MODEL` | No | `qwen-plus` | No | Legacy alias of `LLM_CHAT_MODEL`. |
| model | `QWEN_INTENT_MODEL` | No | `qwen-plus` | No | Legacy alias of `LLM_INTENT_MODEL`. |
| model | `QWEN_EMBEDDING_MODEL` | No | `text-embedding-v4` | No | Legacy alias of `LLM_EMBEDDING_MODEL`. |
| model | `QWEN_BASE_URL` | No | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` | No | Legacy alias of `LLM_BASE_URL`. |
| model | `QWEN_TIMEOUT_SECONDS` | No | `10` | No | Legacy alias of `LLM_TIMEOUT_SECONDS`. |
| model | `QWEN_MAX_RETRIES` | No | `2` | No | Legacy alias of `LLM_MAX_RETRIES`. |
| model | `LLM_INTENT_ENABLED` | No | `false` | No | Enable LLM intent inference (A-07). |
| model | `LLM_INTENT_ROLLOUT_PERCENT` | No | `10` | No | LLM intent rollout percentage. |
| model | `LLM_INTENT_CONFIDENCE_THRESHOLD` | No | `0.75` | No | LLM intent minimum accepted confidence. |
| model | `LLM_CONTENT_ENABLED` | No | `false` | No | Enable LLM content generation (A-08/A-09). |
| model | `LLM_CONTENT_ROLLOUT_PERCENT` | No | `10` | No | LLM content rollout percentage. |
| model | `LLM_DRAFT_ENABLED` | No | `false` | No | Enable LLM extraction/polish for draft (B-14). |
| model | `LLM_DRAFT_ROLLOUT_PERCENT` | No | `10` | No | LLM draft rollout percentage. |
| model | `LLM_ORCHESTRATOR_SHADOW_ENABLED` | No | `false` | No | Enable shadow-only LLM orchestrator inference. |
| model | `LLM_ORCHESTRATOR_SHADOW_ROLLOUT_PERCENT` | No | `10` | No | Shadow orchestrator rollout percentage. |
| database | `PG_HOST` | Yes | - | No | PostgreSQL host. |
| database | `PG_PORT` | No | `5432` | No | PostgreSQL port. |
| database | `PG_DATABASE` | Yes | - | No | PostgreSQL database name. |
| database | `PG_USER` | Yes | - | No | PostgreSQL username. |
| database | `PG_PASSWORD` | Yes | - | No | PostgreSQL password. |
| vector | `PGVECTOR_TABLE_DOCS` | No | `knowledge_docs` | No | Metadata table for documents. |
| vector | `PGVECTOR_TABLE_CHUNKS` | No | `doc_chunks` | No | Chunk table for vector segments. |
| vector | `PGVECTOR_TOP_K` | No | `5` | No | Retrieval top-k size. |
| cache | `REDIS_HOST` | Yes | - | No | Redis host. |
| cache | `REDIS_PORT` | No | `6379` | No | Redis port. |
| cache | `REDIS_DB` | No | `0` | No | Redis logical DB index. |
| cache | `REDIS_PASSWORD` | No | empty | No | Redis password if required. |
| logging | `LOG_LEVEL` | No | `INFO` | No | Log level. |
| logging | `LOG_FORMAT` | No | `json` | No | Log format (`json/text`). |
| logging | `LOG_MASK_SECRETS` | No | `true` | No | Secret masking in logs. |
| reply | `RESPONSE_TONE_DEFAULT` | No | `conversational` | No | Default reply tone (`conversational/formal/neutral`). |
| reply | `RESPONSE_TONE_BY_INTENT` | No | empty | No | Per-intent tone overrides, format `intent:tone,intent:tone`. |
| safety | `DEV_BYPASS_AUTH` | No | `false` | Yes | Dev-only bypass switch. |
| safety | `LOCAL_FAKE_DINGTALK_USER` | No | empty | Yes | Dev-only fake user id. |
| safety | `USE_MOCK_QWEN` | No | `false` | Yes | Dev-only mock model switch. |

`*` `LLM_API_KEY` is required for runtime unless legacy `QWEN_API_KEY` is provided.

## Runtime status note (2026-03-27)

- Main runtime has integrated LLM call points for `intent` / `content` / `draft` and keeps `orchestrator` in shadow mode.
- Feature toggles default to `false`; enable gradually with rollout percentage keys before production cutover.
- Guardrails remain hard-coded in service logic: permission checks, approval state transitions, DB writes, and sensitive fallback are non-LLM.
- `LLM_*` is the primary config namespace; legacy `QWEN_*` keys are still accepted as fallback aliases.
- DingTalk stream mode now supports optional AI card streaming updates for long text replies; when enabled, you must provide a streaming-capable card template id and bound markdown key.
- Keep `DINGTALK_CARD_TEMPLATE_ID` (interactive confirm/cancel card) and `DINGTALK_AI_CARD_TEMPLATE_ID` (AI streaming card) separated; reuse of the same template id is treated as configuration error.
- For HR approval-card delivery, `DINGTALK_HR_APPROVER_USER_ID` and `DINGTALK_HR_CARD_TEMPLATE_ID` must be configured together, and `DINGTALK_HR_CARD_TEMPLATE_ID` must not reuse requester/AI template ids.

## Missing-key scenario expectations

For every required key, the validator must produce:
- code: `MISSING_REQUIRED`
- exact key name in message
- actionable remediation text

This is the acceptance baseline for A-03 "configuration missing scenario" testing.
