from __future__ import annotations

from typing import Any

from app.schemas.dingtalk_chat import AgentReply, ChatHandleResult, IncomingChatMessage

FLOW_GUIDANCE_KEYWORDS = (
    "报销",
    "请假",
    "流程",
    "入口",
    "步骤",
    "怎么走",
    "怎么弄",
)
APPLICATION_DRAFT_KEYWORDS = (
    "申请",
    "文档",
    "文件",
    "资料",
)


class SingleChatService:
    """MVP A-05 single-chat responder with text/card output channels."""

    def handle(self, message: IncomingChatMessage) -> ChatHandleResult:
        if message.conversation_type != "single":
            return ChatHandleResult(
                handled=False,
                reason="non_single_chat",
                reply=AgentReply(
                    channel="text",
                    text="MVP currently supports only 1:1 chat messages.",
                ),
            )

        if message.message_type != "text":
            return ChatHandleResult(
                handled=False,
                reason="unsupported_message_type",
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
                reply=AgentReply(
                    channel="text",
                    text="I received an empty input. Please send your question in text.",
                ),
            )

        if self._is_application_request(question):
            return ChatHandleResult(
                handled=True,
                reason="application_draft_card",
                reply=AgentReply(
                    channel="interactive_card",
                    interactive_card=self._build_application_draft_card(question),
                ),
            )

        if self._is_flow_guidance_request(question):
            return ChatHandleResult(
                handled=True,
                reason="flow_guidance_card",
                reply=AgentReply(
                    channel="interactive_card",
                    interactive_card=self._build_flow_guidance_card(question),
                ),
            )

        return ChatHandleResult(
            handled=True,
            reason="text_answer",
            reply=AgentReply(
                channel="text",
                text=(
                    "Message received. A-05 now supports DingTalk single-chat loop. "
                    "For process guidance or document requests, ask with concrete keywords."
                ),
            ),
        )

    @staticmethod
    def _is_flow_guidance_request(question: str) -> bool:
        return any(keyword in question for keyword in FLOW_GUIDANCE_KEYWORDS)

    @staticmethod
    def _is_application_request(question: str) -> bool:
        return any(keyword in question for keyword in APPLICATION_DRAFT_KEYWORDS)

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
