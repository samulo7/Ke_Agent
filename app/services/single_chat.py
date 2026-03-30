from __future__ import annotations

from dataclasses import replace
import logging
from typing import Any

from app.schemas.dingtalk_chat import AgentReply, ChatHandleResult, IncomingChatMessage
from app.schemas.knowledge import KnowledgeAccessContext
from app.schemas.llm import OrchestratorAction
from app.schemas.user_context import UserContext
from app.services.document_request_draft import DocumentRequestDraftOrchestrator
from app.services.file_request import FileApprovalActionResult, FileRequestService
from app.services.intent_classifier import IntentClassifier
from app.services.knowledge_answering import KnowledgeAnswerService, build_default_knowledge_answer_service
from app.services.llm_intent import LLMIntentService, build_default_llm_intent_service
from app.services.llm_orchestrator_shadow import LLMOrchestratorShadowService, build_default_orchestrator_shadow_service

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
PENDING_FILE_CONTEXT_HINTS = {
    "审批",
    "进度",
    "通过",
    "驳回",
    "拒绝",
    "确认",
    "取消",
    "提交申请",
    "发起申请",
    "什么时候",
    "处理好",
    "好了没",
    "状态",
}


class SingleChatService:
    """Single-chat orchestrator for knowledge QA, draft guidance, and file delivery."""

    def __init__(
        self,
        *,
        intent_classifier: IntentClassifier | None = None,
        knowledge_answer_service: KnowledgeAnswerService | None = None,
        document_request_orchestrator: DocumentRequestDraftOrchestrator | None = None,
        file_request_service: FileRequestService | None = None,
        llm_intent_service: LLMIntentService | None = None,
        orchestrator_shadow_service: LLMOrchestratorShadowService | None = None,
    ) -> None:
        self._intent_classifier = intent_classifier or IntentClassifier()
        self._knowledge_answer_service = knowledge_answer_service or build_default_knowledge_answer_service()
        self._document_request_orchestrator = document_request_orchestrator or DocumentRequestDraftOrchestrator()
        self._file_request_service = file_request_service or FileRequestService()
        self._llm_intent_service = llm_intent_service or build_default_llm_intent_service(
            fallback_classifier=self._intent_classifier
        )
        self._orchestrator_shadow_service = orchestrator_shadow_service or build_default_orchestrator_shadow_service()
        self._logger = logging.getLogger("keagent.observability")

    def handle(
        self,
        message: IncomingChatMessage,
        *,
        user_context: UserContext | None = None,
    ) -> ChatHandleResult:
        llm_trace: dict[str, Any] = {
            "intent": {},
            "content": {},
            "orchestrator_shadow": {},
        }
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
                    text="MVP currently supports text input only. Please send a text message.",
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

        draft_continuation = self._document_request_orchestrator.handle(
            conversation_id=message.conversation_id,
            sender_id=message.sender_id,
            text=question,
            user_context=user_context,
            force_start=False,
        )
        if draft_continuation is not None:
            return draft_continuation

        has_pending_request = getattr(self._file_request_service, "has_pending_request", None)
        if callable(has_pending_request):
            if has_pending_request(conversation_id=message.conversation_id, sender_id=message.sender_id):
                if self._should_route_to_pending_file_request(question):
                    result = self._file_request_service.handle(
                        message=message,
                        query_text=question,
                        user_context=user_context,
                    )
                    llm_trace["orchestrator_shadow"] = self._orchestrator_shadow_service.suggest(
                        question=question,
                        intent="file_request",
                        rule_action="file_request",
                        conversation_id=message.conversation_id,
                        sender_id=message.sender_id,
                    ).to_trace()
                    return self._apply_llm_trace(result=result, llm_trace=llm_trace)

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

        intent_result = self._llm_intent_service.infer(
            text=question,
            conversation_id=message.conversation_id,
            sender_id=message.sender_id,
        )
        llm_trace["intent"] = intent_result.to_trace()
        intent = intent_result.intent

        if (
            intent == "other"
            and intent_result.confidence < LOW_CONFIDENCE_THRESHOLD
            and self._should_use_low_confidence_fallback(question)
        ):
            llm_trace["orchestrator_shadow"] = self._orchestrator_shadow_service.suggest(
                question=question,
                intent=intent,
                rule_action="fallback",
                conversation_id=message.conversation_id,
                sender_id=message.sender_id,
            ).to_trace()
            return ChatHandleResult(
                handled=False,
                reason="low_confidence_fallback",
                intent=intent,
                reply=AgentReply(
                    channel="text",
                    text=(
                        "我暂时无法准确判断你的问题属于哪一类场景。\n"
                        "你可以直接说明目标（制度查询 / 文件申请 / 报销 / 请假 / 固定报价），"
                        "我会按对应流程给出下一步；也可以直接联系相关岗位处理。"
                    ),
                ),
                llm_trace=llm_trace,
            )

        rule_action = self._rule_action_for_intent(intent)
        llm_trace["orchestrator_shadow"] = self._orchestrator_shadow_service.suggest(
            question=question,
            intent=intent,
            rule_action=rule_action,
            conversation_id=message.conversation_id,
            sender_id=message.sender_id,
        ).to_trace()

        if intent == "file_request":
            result = self._file_request_service.handle(
                message=message,
                query_text=question,
                user_context=user_context,
            )
            return self._apply_llm_trace(result=result, llm_trace=llm_trace)

        if intent == "document_request":
            result = self._document_request_orchestrator.handle(
                conversation_id=message.conversation_id,
                sender_id=message.sender_id,
                text=question,
                user_context=user_context,
                force_start=True,
            ) or ChatHandleResult(
                handled=False,
                reason="application_draft_incomplete",
                intent="document_request",
                reply=AgentReply(channel="text", text="申请信息暂未收集成功，请重试。"),
            )
            return self._apply_llm_trace(result=result, llm_trace=llm_trace)

        if intent in {"reimbursement", "leave"}:
            return ChatHandleResult(
                handled=True,
                reason="flow_guidance_card",
                intent=intent,
                reply=AgentReply(
                    channel="interactive_card",
                    interactive_card=self._build_flow_guidance_card(question),
                ),
                llm_trace=llm_trace,
            )

        try:
            knowledge_answer = self._knowledge_answer_service.answer(
                question=question,
                intent=intent,
                access_context=self._to_access_context(user_context),
                conversation_id=message.conversation_id,
                sender_id=message.sender_id,
            )
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
                llm_trace=llm_trace,
            )

        llm_trace["content"] = dict(knowledge_answer.llm_trace)
        reason = "knowledge_answer" if knowledge_answer.found else "knowledge_no_hit"
        handled = knowledge_answer.found
        if knowledge_answer.permission_decision in {"summary_only", "deny"}:
            reason = "permission_restricted"
            handled = False

        return ChatHandleResult(
            handled=handled,
            reason=reason,
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
            llm_trace=llm_trace,
        )

    def handle_file_approval_action(
        self,
        *,
        request_id: str,
        action: str,
        approver_user_id: str,
    ) -> FileApprovalActionResult:
        return self._file_request_service.handle_approval_action(
            request_id=request_id,
            action=action,
            approver_user_id=approver_user_id,
        )

    def handle_file_approval_action_by_session(
        self,
        *,
        action: str,
        approver_user_id: str,
        conversation_id: str,
        sender_id: str,
    ) -> FileApprovalActionResult:
        resolver = getattr(self._file_request_service, "resolve_active_request_id", None)
        request_id = ""
        if callable(resolver):
            request_id = str(
                resolver(
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                )
            ).strip()

        if not request_id:
            sender_only_resolver = getattr(self._file_request_service, "resolve_active_request_id_by_sender", None)
            if callable(sender_only_resolver):
                request_id = str(sender_only_resolver(sender_id=sender_id)).strip()

        if request_id:
            return self._file_request_service.handle_approval_action(
                request_id=request_id,
                action=action,
                approver_user_id=approver_user_id,
            )
        return FileApprovalActionResult(
            handled=False,
            reason="file_approval_not_found",
            request_id="",
            action=None,
            status=None,
        )

    @staticmethod
    def _to_access_context(user_context: UserContext | None) -> KnowledgeAccessContext | None:
        if user_context is None:
            return None
        return KnowledgeAccessContext(
            user_id=(user_context.user_id or "").strip(),
            dept_id=(user_context.dept_id or "").strip(),
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

    def _should_route_to_pending_file_request(self, question: str) -> bool:
        normalized = self._normalize(question)
        return any(token in normalized for token in PENDING_FILE_CONTEXT_HINTS)

    def _should_use_low_confidence_fallback(self, question: str) -> bool:
        normalized = self._normalize(question)
        if normalized in LOW_CONFIDENCE_EXCLUSIONS:
            return False
        return self._contains_cjk(question)

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return any("\u4e00" <= char <= "\u9fff" for char in text)

    @staticmethod
    def _rule_action_for_intent(intent: str) -> OrchestratorAction:
        if intent == "file_request":
            return "file_request"
        if intent == "document_request":
            return "document_request"
        if intent in {"reimbursement", "leave"}:
            return "flow_guidance"
        if intent in {"policy_process", "fixed_quote", "other"}:
            return "knowledge_answer"
        return "fallback"

    @staticmethod
    def _apply_llm_trace(*, result: ChatHandleResult, llm_trace: dict[str, Any]) -> ChatHandleResult:
        return replace(result, llm_trace=llm_trace)
