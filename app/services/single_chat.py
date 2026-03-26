from __future__ import annotations

from typing import Any

from app.schemas.dingtalk_chat import AgentReply, ChatHandleResult, IncomingChatMessage
from app.services.intent_classifier import IntentClassifier
from app.services.knowledge_answering import KnowledgeAnswerService, build_default_knowledge_answer_service


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

        intent_result = self._intent_classifier.classify(question)
        intent = intent_result.intent

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

        knowledge_answer = self._knowledge_answer_service.answer(question=question, intent=intent)
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
