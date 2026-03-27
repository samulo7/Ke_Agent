from __future__ import annotations

import logging
from typing import Any

from app.schemas.dingtalk_chat import AgentReply, ChatHandleResult, IncomingChatMessage
from app.services.intent_classifier import IntentClassifier
from app.services.knowledge_answering import KnowledgeAnswerService, build_default_knowledge_answer_service

LOW_CONFIDENCE_THRESHOLD = 0.6
AMBIGUOUS_PHRASES = {
    "这个怎么弄",
    "这个怎么办",
    "那个怎么弄",
    "那个怎么办",
    "怎么弄",
    "怎么办",
    "请问怎么办",
}
LOW_CONFIDENCE_EXCLUSIONS = {
    "你好",
    "您好",
    "hello",
    "hi",
    "在吗",
    "谢谢",
}


class SingleChatService:
    """MVP A-05 single-chat responder with text/card output channels."""

    def __init__(
        self,
        *,
        intent_classifier: IntentClassifier | None = None,
        knowledge_answer_service: KnowledgeAnswerService | None = None,
    ) -> None:
        self._intent_classifier = intent_classifier or IntentClassifier()
        self._knowledge_answer_service = knowledge_answer_service or build_default_knowledge_answer_service()
        self._logger = logging.getLogger("keagent.observability")

    def handle(self, message: IncomingChatMessage) -> ChatHandleResult:
        if message.conversation_type != "single":
            return ChatHandleResult(
                handled=False,
                reason="non_single_chat",
                intent="other",
                reply=AgentReply(
                    channel="text",
                    text="MVP currently supports only 1:1 chat messages.",
                ),
            )

        if message.message_type != "text":
            return ChatHandleResult(
                handled=False,
                reason="unsupported_message_type",
                intent="other",
                reply=AgentReply(
                    channel="text",
                    text="MVP A-05 supports text input only. Please send a text message.",
                ),
            )

        question = message.text.strip()
        if not question:
            return ChatHandleResult(
                handled=False,
                reason="empty_input",
                intent="other",
                reply=AgentReply(
                    channel="text",
                    text="I received an empty input. Please send your question in text.",
                ),
            )

        if self._is_ambiguous_question(question):
            return ChatHandleResult(
                handled=False,
                reason="ambiguous_question",
                intent="other",
                reply=AgentReply(
                    channel="text",
                    text=(
                        "我还不能仅凭这句话准确判断你的诉求。为避免来回确认，本轮仅追问一次：\n"
                        "请补充具体事项（例如：制度名、流程名、报销类型、文档名称）。\n"
                        "如果你希望直接转人工，也可以联系人事/财务/商务。"
                    ),
                ),
            )

        intent_result = self._intent_classifier.classify(question)
        intent = intent_result.intent

        if (
            intent == "other"
            and intent_result.confidence < LOW_CONFIDENCE_THRESHOLD
            and self._should_use_low_confidence_fallback(question)
        ):
            return ChatHandleResult(
                handled=False,
                reason="low_confidence_fallback",
                intent=intent,
                reply=AgentReply(
                    channel="text",
                    text=(
                        "我暂时无法准确判断你的问题属于哪一类场景。\n"
                        "你可以直接说明目标（制度查询 / 文档申请 / 报销 / 请假 / 固定报价），"
                        "我会按对应流程给出下一步；也可以直接联系相关岗位处理。"
                    ),
                ),
            )

        if intent == "document_request":
            return ChatHandleResult(
                handled=True,
                reason="application_draft_card",
                intent=intent,
                reply=AgentReply(
                    channel="interactive_card",
                    interactive_card=self._build_application_draft_card(question),
                ),
            )

        if intent in {"reimbursement", "leave"}:
            return ChatHandleResult(
                handled=True,
                reason="flow_guidance_card",
                intent=intent,
                reply=AgentReply(
                    channel="interactive_card",
                    interactive_card=self._build_flow_guidance_card(question),
                ),
            )

        try:
            knowledge_answer = self._knowledge_answer_service.answer(question=question, intent=intent)
        except Exception:
            self._logger.exception("single_chat.answer_failed")
            return ChatHandleResult(
                handled=False,
                reason="system_fallback",
                intent=intent,
                reply=AgentReply(
                    channel="text",
                    text="系统当前处理异常，请稍后再试；如需紧急处理，请直接联系对应岗位。",
                ),
            )
        return ChatHandleResult(
            handled=knowledge_answer.found,
            reason="knowledge_answer" if knowledge_answer.found else "knowledge_no_hit",
            intent=intent,
            reply=AgentReply(
                channel="text",
                text=knowledge_answer.text,
            ),
            source_ids=knowledge_answer.source_ids,
            permission_decision=knowledge_answer.permission_decision,
            knowledge_version=knowledge_answer.knowledge_version,
            answered_at=knowledge_answer.answered_at,
            citations=tuple(citation.to_dict() for citation in knowledge_answer.citations),
        )

    @staticmethod
    def _build_flow_guidance_card(question: str) -> dict[str, Any]:
        return {
            "card_type": "flow_guidance",
            "title": "Process Guidance",
            "question": question,
            "summary": "Use the standard DingTalk approval process entry.",
            "steps": [
                "Open DingTalk > Workbench > Approvals.",
                "Select the matching process template.",
                "Prepare required materials and submit.",
            ],
            "next_action": "If you need rule details, ask the specific process name.",
        }

    @staticmethod
    def _build_application_draft_card(question: str) -> dict[str, Any]:
        return {
            "card_type": "application_draft",
            "title": "Document Request Draft",
            "requested_item": question,
            "draft_fields": {
                "applicant_name": "<to be filled>",
                "department": "<to be filled>",
                "request_purpose": "<required>",
                "expected_use_time": "<required>",
                "suggested_approver": "HR/Document Owner",
            },
            "note": "A-05 provides draft guidance only; auto-submit is out of scope.",
        }

    @staticmethod
    def _normalize(text: str) -> str:
        return "".join(text.strip().lower().split())

    def _is_ambiguous_question(self, question: str) -> bool:
        normalized = self._normalize(question)
        if not normalized:
            return False
        if normalized in AMBIGUOUS_PHRASES:
            return True
        if normalized.startswith(("这个", "那个")) and any(token in normalized for token in ("怎么", "咋", "如何", "处理")):
            return True
        return False

    def _should_use_low_confidence_fallback(self, question: str) -> bool:
        normalized = self._normalize(question)
        if normalized in LOW_CONFIDENCE_EXCLUSIONS:
            return False
        return self._contains_cjk(question)

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return any("\u4e00" <= char <= "\u9fff" for char in text)
