from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

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
        key="QWEN_API_KEY",
        category="model",
        required=True,
        parser=parse_str,
        description="Qwen API key.",
        remediation="Set QWEN_API_KEY from Model Studio credentials.",
    ),
    ConfigRule(
        key="QWEN_CHAT_MODEL",
        category="model",
        required=False,
        default="qwen-plus",
        parser=parse_str,
        description="Chat model name.",
        remediation="Set QWEN_CHAT_MODEL, e.g. qwen-plus.",
    ),
    ConfigRule(
        key="QWEN_INTENT_MODEL",
        category="model",
        required=False,
        default="qwen-plus",
        parser=parse_str,
        description="Intent model name.",
        remediation="Set QWEN_INTENT_MODEL, e.g. qwen-plus.",
    ),
    ConfigRule(
        key="QWEN_EMBEDDING_MODEL",
        category="model",
        required=False,
        default="text-embedding-v4",
        parser=parse_str,
        description="Embedding model name.",
        remediation="Set QWEN_EMBEDDING_MODEL, e.g. text-embedding-v4.",
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
