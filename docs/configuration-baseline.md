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
| model | `QWEN_API_KEY` | Yes | - | No | Qwen API key. |
| model | `QWEN_CHAT_MODEL` | No | `qwen-plus` | No | Chat model name. |
| model | `QWEN_INTENT_MODEL` | No | `qwen-plus` | No | Intent model name. |
| model | `QWEN_EMBEDDING_MODEL` | No | `text-embedding-v4` | No | Embedding model name. |
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

## Missing-key scenario expectations

For every required key, the validator must produce:
- code: `MISSING_REQUIRED`
- exact key name in message
- actionable remediation text

This is the acceptance baseline for A-03 "configuration missing scenario" testing.
