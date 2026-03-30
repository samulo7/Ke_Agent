from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.schemas.tone import parse_intent_tone_overrides, parse_tone_profile

ConfigParser = Callable[[str], Any]


def parse_str(value: str) -> str:
    return value.strip()


def parse_positive_int(value: str) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ValueError("must be an integer") from exc
    if parsed <= 0:
        raise ValueError("must be > 0")
    return parsed


def parse_non_negative_int(value: str) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ValueError("must be an integer") from exc
    if parsed < 0:
        raise ValueError("must be >= 0")
    return parsed


def parse_percentage(value: str) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ValueError("must be an integer") from exc
    if parsed < 0 or parsed > 100:
        raise ValueError("must be between 0 and 100")
    return parsed


def parse_unit_float(value: str) -> float:
    try:
        parsed = float(value.strip())
    except ValueError as exc:
        raise ValueError("must be a float") from exc
    if parsed < 0 or parsed > 1:
        raise ValueError("must be between 0 and 1")
    return parsed


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    truthy = {"1", "true", "yes", "y", "on"}
    falsy = {"0", "false", "no", "n", "off", ""}
    if normalized in truthy:
        return True
    if normalized in falsy:
        return False
    raise ValueError("must be boolean: true/false")


def parse_env(value: str) -> str:
    normalized = value.strip().lower()
    allowed = {"dev", "test", "staging", "prod"}
    if normalized not in allowed:
        raise ValueError(f"must be one of: {', '.join(sorted(allowed))}")
    return normalized


def parse_log_level(value: str) -> str:
    normalized = value.strip().upper()
    allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if normalized not in allowed:
        raise ValueError(f"must be one of: {', '.join(sorted(allowed))}")
    return normalized


def parse_log_format(value: str) -> str:
    normalized = value.strip().lower()
    allowed = {"json", "text"}
    if normalized not in allowed:
        raise ValueError(f"must be one of: {', '.join(sorted(allowed))}")
    return normalized


@dataclass(frozen=True)
class ConfigRule:
    key: str
    category: str
    required: bool
    parser: ConfigParser
    description: str
    remediation: str
    default: str | None = None
    production_forbidden: bool = False


CONFIG_RULES: tuple[ConfigRule, ...] = (
    ConfigRule(
        key="APP_ENV",
        category="app",
        required=False,
        default="dev",
        parser=parse_env,
        description="Runtime environment.",
        remediation="Set APP_ENV to dev/test/staging/prod.",
    ),
    ConfigRule(
        key="APP_PORT",
        category="app",
        required=False,
        default="8000",
        parser=parse_positive_int,
        description="HTTP listen port.",
        remediation="Set APP_PORT to a positive integer, e.g. 8000.",
    ),
    ConfigRule(
        key="APP_DEBUG",
        category="app",
        required=False,
        default="false",
        parser=parse_bool,
        description="Debug mode switch.",
        remediation="Set APP_DEBUG to true/false.",
    ),
    ConfigRule(
        key="DINGTALK_CLIENT_ID",
        category="dingtalk",
        required=True,
        parser=parse_str,
        description="DingTalk app client id.",
        remediation="Set DINGTALK_CLIENT_ID from DingTalk app credentials.",
    ),
    ConfigRule(
        key="DINGTALK_CLIENT_SECRET",
        category="dingtalk",
        required=True,
        parser=parse_str,
        description="DingTalk app client secret.",
        remediation="Set DINGTALK_CLIENT_SECRET from DingTalk app credentials.",
    ),
    ConfigRule(
        key="DINGTALK_AGENT_ID",
        category="dingtalk",
        required=True,
        parser=parse_str,
        description="DingTalk agent id.",
        remediation="Set DINGTALK_AGENT_ID from DingTalk app configuration.",
    ),
    ConfigRule(
        key="DINGTALK_STREAM_ENDPOINT",
        category="dingtalk",
        required=False,
        default="https://api.dingtalk.com/v1.0/gateway/connections/open",
        parser=parse_str,
        description="DingTalk stream endpoint.",
        remediation="Set DINGTALK_STREAM_ENDPOINT to a valid DingTalk stream URL.",
    ),
    ConfigRule(
        key="DINGTALK_OPENAPI_ENDPOINT",
        category="dingtalk",
        required=False,
        default="https://api.dingtalk.com",
        parser=parse_str,
        description="DingTalk OpenAPI endpoint.",
        remediation="Set DINGTALK_OPENAPI_ENDPOINT to a valid DingTalk OpenAPI base URL.",
    ),
    ConfigRule(
        key="DINGTALK_CARD_TEMPLATE_ID",
        category="dingtalk",
        required=False,
        default="",
        parser=parse_str,
        description="Template id for interactive file-request confirmation card.",
        remediation=(
            "Set DINGTALK_CARD_TEMPLATE_ID to an interactive card template id with fixed "
            "`confirm_request` / `cancel_request` request buttons."
        ),
    ),
    ConfigRule(
        key="DINGTALK_HR_APPROVER_USER_ID",
        category="dingtalk",
        required=False,
        default="",
        parser=parse_str,
        description="Target DingTalk user id for HR approval card delivery.",
        remediation=(
            "Set DINGTALK_HR_APPROVER_USER_ID to the approver user id when enabling "
            "HR approval card delivery."
        ),
    ),
    ConfigRule(
        key="DINGTALK_HR_CARD_TEMPLATE_ID",
        category="dingtalk",
        required=False,
        default="",
        parser=parse_str,
        description="Template id for HR-side approval action card.",
        remediation=(
            "Set DINGTALK_HR_CARD_TEMPLATE_ID to an approval card template id with fixed "
            "`approve` / `reject` callback actions."
        ),
    ),
    ConfigRule(
        key="DINGTALK_CARD_CALLBACK_DEBUG",
        category="dingtalk",
        required=False,
        default="false",
        parser=parse_bool,
        description="Enable verbose card callback diagnostics for stream callback topic and payload parsing.",
        remediation="Set DINGTALK_CARD_CALLBACK_DEBUG to true/false.",
    ),
    ConfigRule(
        key="DINGTALK_AI_CARD_STREAMING_ENABLED",
        category="dingtalk",
        required=False,
        default="false",
        parser=parse_bool,
        description="Enable streaming AI card typewriter effect for long text replies in stream mode.",
        remediation="Set DINGTALK_AI_CARD_STREAMING_ENABLED to true/false.",
    ),
    ConfigRule(
        key="DINGTALK_AI_CARD_TEMPLATE_ID",
        category="dingtalk",
        required=False,
        default="",
        parser=parse_str,
        description="Template id for streaming AI card.",
        remediation=(
            "Set DINGTALK_AI_CARD_TEMPLATE_ID to your streaming-enabled card template id "
            "when DINGTALK_AI_CARD_STREAMING_ENABLED=true."
        ),
    ),
    ConfigRule(
        key="DINGTALK_AI_CARD_CONTENT_KEY",
        category="dingtalk",
        required=False,
        default="content",
        parser=parse_str,
        description="Bound variable key for markdown content streaming updates.",
        remediation="Set DINGTALK_AI_CARD_CONTENT_KEY to the markdown variable key in your card template.",
    ),
    ConfigRule(
        key="DINGTALK_AI_CARD_TITLE_KEY",
        category="dingtalk",
        required=False,
        default="",
        parser=parse_str,
        description="Optional bound variable key for card title.",
        remediation="Set DINGTALK_AI_CARD_TITLE_KEY if your template has a title variable.",
    ),
    ConfigRule(
        key="DINGTALK_AI_CARD_TITLE",
        category="dingtalk",
        required=False,
        default="企业 Agent",
        parser=parse_str,
        description="Optional card title value when title key is configured.",
        remediation="Set DINGTALK_AI_CARD_TITLE to the desired card title text.",
    ),
    ConfigRule(
        key="DINGTALK_AI_CARD_CHUNK_CHARS",
        category="dingtalk",
        required=False,
        default="20",
        parser=parse_positive_int,
        description="Character interval for each streaming update batch.",
        remediation="Set DINGTALK_AI_CARD_CHUNK_CHARS to a positive integer.",
    ),
    ConfigRule(
        key="DINGTALK_AI_CARD_INTERVAL_MS",
        category="dingtalk",
        required=False,
        default="120",
        parser=parse_non_negative_int,
        description="Delay between streaming updates in milliseconds.",
        remediation="Set DINGTALK_AI_CARD_INTERVAL_MS to a non-negative integer.",
    ),
    ConfigRule(
        key="DINGTALK_AI_CARD_MIN_CHARS",
        category="dingtalk",
        required=False,
        default="80",
        parser=parse_positive_int,
        description="Minimum reply length to trigger streaming AI card.",
        remediation="Set DINGTALK_AI_CARD_MIN_CHARS to a positive integer.",
    ),
    ConfigRule(
        key="LLM_API_KEY",
        category="model",
        required=False,
        parser=parse_str,
        description="Primary LLM API key.",
        remediation="Set LLM_API_KEY for your OpenAI-compatible model gateway.",
    ),
    ConfigRule(
        key="LLM_CHAT_MODEL",
        category="model",
        required=False,
        default="qwen-plus",
        parser=parse_str,
        description="Primary chat model name.",
        remediation="Set LLM_CHAT_MODEL, e.g. qwen-plus.",
    ),
    ConfigRule(
        key="LLM_INTENT_MODEL",
        category="model",
        required=False,
        default="qwen-plus",
        parser=parse_str,
        description="Primary intent model name.",
        remediation="Set LLM_INTENT_MODEL, e.g. qwen-plus.",
    ),
    ConfigRule(
        key="LLM_EMBEDDING_MODEL",
        category="model",
        required=False,
        default="text-embedding-v4",
        parser=parse_str,
        description="Primary embedding model name.",
        remediation="Set LLM_EMBEDDING_MODEL, e.g. text-embedding-v4.",
    ),
    ConfigRule(
        key="LLM_BASE_URL",
        category="model",
        required=False,
        default="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        parser=parse_str,
        description="Primary OpenAI-compatible chat completion endpoint.",
        remediation="Set LLM_BASE_URL to a valid OpenAI-compatible endpoint URL.",
    ),
    ConfigRule(
        key="LLM_TIMEOUT_SECONDS",
        category="model",
        required=False,
        default="10",
        parser=parse_positive_int,
        description="Primary LLM timeout per request in seconds.",
        remediation="Set LLM_TIMEOUT_SECONDS to a positive integer.",
    ),
    ConfigRule(
        key="LLM_MAX_RETRIES",
        category="model",
        required=False,
        default="2",
        parser=parse_non_negative_int,
        description="Primary LLM max retries per call.",
        remediation="Set LLM_MAX_RETRIES to a non-negative integer not greater than 2.",
    ),
    ConfigRule(
        key="LLM_MAX_TOKENS",
        category="model",
        required=False,
        parser=parse_positive_int,
        description="Primary LLM max output tokens for chat completion.",
        remediation="Set LLM_MAX_TOKENS to a positive integer, e.g. 8192.",
    ),
    ConfigRule(
        key="QWEN_API_KEY",
        category="model",
        required=False,
        parser=parse_str,
        description="Legacy API key alias for compatibility.",
        remediation="Prefer LLM_API_KEY; keep QWEN_API_KEY only for backward compatibility.",
    ),
    ConfigRule(
        key="QWEN_CHAT_MODEL",
        category="model",
        required=False,
        parser=parse_str,
        description="Legacy chat model alias for compatibility.",
        remediation="Prefer LLM_CHAT_MODEL; keep QWEN_CHAT_MODEL only for backward compatibility.",
    ),
    ConfigRule(
        key="QWEN_INTENT_MODEL",
        category="model",
        required=False,
        parser=parse_str,
        description="Legacy intent model alias for compatibility.",
        remediation="Prefer LLM_INTENT_MODEL; keep QWEN_INTENT_MODEL only for backward compatibility.",
    ),
    ConfigRule(
        key="QWEN_EMBEDDING_MODEL",
        category="model",
        required=False,
        parser=parse_str,
        description="Legacy embedding model alias for compatibility.",
        remediation="Prefer LLM_EMBEDDING_MODEL; keep QWEN_EMBEDDING_MODEL only for backward compatibility.",
    ),
    ConfigRule(
        key="QWEN_BASE_URL",
        category="model",
        required=False,
        parser=parse_str,
        description="Legacy endpoint alias for compatibility.",
        remediation="Prefer LLM_BASE_URL; keep QWEN_BASE_URL only for backward compatibility.",
    ),
    ConfigRule(
        key="QWEN_TIMEOUT_SECONDS",
        category="model",
        required=False,
        parser=parse_positive_int,
        description="Legacy timeout alias for compatibility.",
        remediation="Prefer LLM_TIMEOUT_SECONDS; keep QWEN_TIMEOUT_SECONDS only for backward compatibility.",
    ),
    ConfigRule(
        key="QWEN_MAX_RETRIES",
        category="model",
        required=False,
        parser=parse_non_negative_int,
        description="Legacy retry alias for compatibility.",
        remediation="Prefer LLM_MAX_RETRIES; keep QWEN_MAX_RETRIES only for backward compatibility.",
    ),
    ConfigRule(
        key="LLM_INTENT_ENABLED",
        category="model",
        required=False,
        default="false",
        parser=parse_bool,
        description="Enable LLM-based intent inference.",
        remediation="Set LLM_INTENT_ENABLED to true/false.",
    ),
    ConfigRule(
        key="LLM_INTENT_ROLLOUT_PERCENT",
        category="model",
        required=False,
        default="10",
        parser=parse_percentage,
        description="Percent of traffic routed to LLM intent inference.",
        remediation="Set LLM_INTENT_ROLLOUT_PERCENT between 0 and 100.",
    ),
    ConfigRule(
        key="LLM_INTENT_CONFIDENCE_THRESHOLD",
        category="model",
        required=False,
        default="0.75",
        parser=parse_unit_float,
        description="Minimum accepted confidence for LLM intent output.",
        remediation="Set LLM_INTENT_CONFIDENCE_THRESHOLD between 0 and 1.",
    ),
    ConfigRule(
        key="LLM_CONTENT_ENABLED",
        category="model",
        required=False,
        default="false",
        parser=parse_bool,
        description="Enable LLM-based response wording generation.",
        remediation="Set LLM_CONTENT_ENABLED to true/false.",
    ),
    ConfigRule(
        key="LLM_CONTENT_ROLLOUT_PERCENT",
        category="model",
        required=False,
        default="10",
        parser=parse_percentage,
        description="Percent of traffic routed to LLM content generation.",
        remediation="Set LLM_CONTENT_ROLLOUT_PERCENT between 0 and 100.",
    ),
    ConfigRule(
        key="LLM_DRAFT_ENABLED",
        category="model",
        required=False,
        default="false",
        parser=parse_bool,
        description="Enable LLM extraction/polish in document draft flow.",
        remediation="Set LLM_DRAFT_ENABLED to true/false.",
    ),
    ConfigRule(
        key="LLM_DRAFT_ROLLOUT_PERCENT",
        category="model",
        required=False,
        default="10",
        parser=parse_percentage,
        description="Percent of draft conversations routed to LLM extraction.",
        remediation="Set LLM_DRAFT_ROLLOUT_PERCENT between 0 and 100.",
    ),
    ConfigRule(
        key="LLM_ORCHESTRATOR_SHADOW_ENABLED",
        category="model",
        required=False,
        default="false",
        parser=parse_bool,
        description="Enable shadow-only LLM orchestrator suggestions.",
        remediation="Set LLM_ORCHESTRATOR_SHADOW_ENABLED to true/false.",
    ),
    ConfigRule(
        key="LLM_ORCHESTRATOR_SHADOW_ROLLOUT_PERCENT",
        category="model",
        required=False,
        default="10",
        parser=parse_percentage,
        description="Percent of traffic for shadow orchestrator inference.",
        remediation="Set LLM_ORCHESTRATOR_SHADOW_ROLLOUT_PERCENT between 0 and 100.",
    ),
    ConfigRule(
        key="PG_HOST",
        category="database",
        required=True,
        parser=parse_str,
        description="PostgreSQL host.",
        remediation="Set PG_HOST to PostgreSQL service host.",
    ),
    ConfigRule(
        key="PG_PORT",
        category="database",
        required=False,
        default="5432",
        parser=parse_positive_int,
        description="PostgreSQL port.",
        remediation="Set PG_PORT to a positive integer, e.g. 5432.",
    ),
    ConfigRule(
        key="PG_DATABASE",
        category="database",
        required=True,
        parser=parse_str,
        description="PostgreSQL database name.",
        remediation="Set PG_DATABASE to the target database name.",
    ),
    ConfigRule(
        key="PG_USER",
        category="database",
        required=True,
        parser=parse_str,
        description="PostgreSQL username.",
        remediation="Set PG_USER to the database login user.",
    ),
    ConfigRule(
        key="PG_PASSWORD",
        category="database",
        required=True,
        parser=parse_str,
        description="PostgreSQL password.",
        remediation="Set PG_PASSWORD to the database login password.",
    ),
    ConfigRule(
        key="PGVECTOR_TABLE_DOCS",
        category="vector",
        required=False,
        default="knowledge_docs",
        parser=parse_str,
        description="Metadata table name for knowledge documents.",
        remediation="Set PGVECTOR_TABLE_DOCS to metadata table name.",
    ),
    ConfigRule(
        key="PGVECTOR_TABLE_CHUNKS",
        category="vector",
        required=False,
        default="doc_chunks",
        parser=parse_str,
        description="Chunk table name for vectorized segments.",
        remediation="Set PGVECTOR_TABLE_CHUNKS to chunk table name.",
    ),
    ConfigRule(
        key="PGVECTOR_TOP_K",
        category="vector",
        required=False,
        default="5",
        parser=parse_positive_int,
        description="Top-k retrieval size.",
        remediation="Set PGVECTOR_TOP_K to a positive integer, e.g. 5.",
    ),
    ConfigRule(
        key="REDIS_HOST",
        category="cache",
        required=True,
        parser=parse_str,
        description="Redis host.",
        remediation="Set REDIS_HOST to Redis service host.",
    ),
    ConfigRule(
        key="REDIS_PORT",
        category="cache",
        required=False,
        default="6379",
        parser=parse_positive_int,
        description="Redis port.",
        remediation="Set REDIS_PORT to a positive integer, e.g. 6379.",
    ),
    ConfigRule(
        key="REDIS_DB",
        category="cache",
        required=False,
        default="0",
        parser=parse_non_negative_int,
        description="Redis logical database index.",
        remediation="Set REDIS_DB to a non-negative integer, e.g. 0.",
    ),
    ConfigRule(
        key="REDIS_PASSWORD",
        category="cache",
        required=False,
        default="",
        parser=parse_str,
        description="Redis password.",
        remediation="Set REDIS_PASSWORD if Redis requires authentication.",
    ),
    ConfigRule(
        key="LOG_LEVEL",
        category="logging",
        required=False,
        default="INFO",
        parser=parse_log_level,
        description="Application log level.",
        remediation="Set LOG_LEVEL to DEBUG/INFO/WARNING/ERROR/CRITICAL.",
    ),
    ConfigRule(
        key="LOG_FORMAT",
        category="logging",
        required=False,
        default="json",
        parser=parse_log_format,
        description="Application log format.",
        remediation="Set LOG_FORMAT to json or text.",
    ),
    ConfigRule(
        key="LOG_MASK_SECRETS",
        category="logging",
        required=False,
        default="true",
        parser=parse_bool,
        description="Mask secrets in logs.",
        remediation="Set LOG_MASK_SECRETS to true/false.",
    ),
    ConfigRule(
        key="RESPONSE_TONE_DEFAULT",
        category="reply",
        required=False,
        default="conversational",
        parser=parse_tone_profile,
        description="Default reply tone profile.",
        remediation="Set RESPONSE_TONE_DEFAULT to conversational/formal/neutral.",
    ),
    ConfigRule(
        key="RESPONSE_TONE_BY_INTENT",
        category="reply",
        required=False,
        default="",
        parser=parse_intent_tone_overrides,
        description="Per-intent tone overrides, format intent:tone,intent:tone.",
        remediation=(
            "Set RESPONSE_TONE_BY_INTENT using intent:tone pairs, "
            "e.g. policy_process:formal,fixed_quote:neutral."
        ),
    ),
    ConfigRule(
        key="DEV_BYPASS_AUTH",
        category="safety",
        required=False,
        default="false",
        parser=parse_bool,
        production_forbidden=True,
        description="Development-only auth bypass switch.",
        remediation="Unset DEV_BYPASS_AUTH in production, or set to false.",
    ),
    ConfigRule(
        key="LOCAL_FAKE_DINGTALK_USER",
        category="safety",
        required=False,
        default="",
        parser=parse_str,
        production_forbidden=True,
        description="Development-only fake DingTalk user id.",
        remediation="Unset LOCAL_FAKE_DINGTALK_USER in production.",
    ),
    ConfigRule(
        key="USE_MOCK_QWEN",
        category="safety",
        required=False,
        default="false",
        parser=parse_bool,
        production_forbidden=True,
        description="Development-only mock model switch.",
        remediation="Unset USE_MOCK_QWEN in production, or set to false.",
    ),
)


REQUIRED_KEYS: tuple[str, ...] = tuple(rule.key for rule in CONFIG_RULES if rule.required)
