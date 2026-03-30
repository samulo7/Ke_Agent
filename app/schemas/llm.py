from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.schemas.dingtalk_chat import IntentType

OrchestratorAction = Literal[
    "knowledge_answer",
    "file_request",
    "document_request",
    "flow_guidance",
    "fallback",
]


@dataclass(frozen=True)
class IntentInferenceResult:
    intent: IntentType
    confidence: float
    reason: str
    model: str
    fallback_used: bool
    validation_passed: bool
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def to_trace(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "reason": self.reason,
            "model": self.model,
            "fallback_used": self.fallback_used,
            "validation_passed": self.validation_passed,
        }


@dataclass(frozen=True)
class ContentGenerationResult:
    text: str
    safety_flags: tuple[str, ...]
    validation_passed: bool
    fallback_used: bool
    model: str
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def to_trace(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "safety_flags": list(self.safety_flags),
            "validation_passed": self.validation_passed,
            "fallback_used": self.fallback_used,
        }


@dataclass(frozen=True)
class OrchestratorShadowResult:
    suggested_action: OrchestratorAction
    rule_action: OrchestratorAction
    reason: str
    model: str
    fallback_used: bool
    validation_passed: bool
    risk_tags: tuple[str, ...] = ()
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def to_trace(self) -> dict[str, Any]:
        return {
            "suggested_action": self.suggested_action,
            "rule_action": self.rule_action,
            "matched": self.suggested_action == self.rule_action,
            "reason": self.reason,
            "model": self.model,
            "fallback_used": self.fallback_used,
            "validation_passed": self.validation_passed,
            "risk_tags": list(self.risk_tags),
        }

