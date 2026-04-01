from __future__ import annotations

from dataclasses import replace
import logging
import re
from typing import Any

from app.schemas.dingtalk_chat import AgentReply, ChatHandleResult, IncomingChatMessage
from app.schemas.knowledge import KnowledgeAccessContext
from app.schemas.llm import OrchestratorAction
from app.schemas.user_context import UserContext
from app.services.document_request_draft import DocumentRequestDraftOrchestrator
from app.services.file_request import FileApprovalActionResult, FileRequestService
from app.services.flow_guidance import (
    build_flow_guidance_card,
    build_reimbursement_guidance_fallback_text,
    build_reimbursement_guidance_prompt_fields,
)
from app.services.intent_classifier import IntentClassifier
from app.services.knowledge_answering import KnowledgeAnswerService, build_default_knowledge_answer_service
from app.services.leave_request import LeaveRequestOrchestrator, build_default_leave_request_orchestrator
from app.services.reimbursement_request import (
    ReimbursementRequestOrchestrator,
    build_default_reimbursement_request_orchestrator,
)
from app.services.llm_content_generation import LLMContentGenerationService, build_default_llm_content_generation_service
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
_LEAVE_ACTION_PHRASE_PATTERN = re.compile(r"请[^，。！？,.!?]{0,8}假")


class SingleChatService:
    """Single-chat orchestrator for knowledge QA, draft guidance, and file delivery."""

    def __init__(
        self,
        *,
        intent_classifier: IntentClassifier | None = None,
        knowledge_answer_service: KnowledgeAnswerService | None = None,
        document_request_orchestrator: DocumentRequestDraftOrchestrator | None = None,
        file_request_service: FileRequestService | None = None,
        leave_request_orchestrator: LeaveRequestOrchestrator | None = None,
        reimbursement_request_orchestrator: ReimbursementRequestOrchestrator | None = None,
        content_generation_service: LLMContentGenerationService | None = None,
        llm_intent_service: LLMIntentService | None = None,
        orchestrator_shadow_service: LLMOrchestratorShadowService | None = None,
    ) -> None:
        self._intent_classifier = intent_classifier or IntentClassifier()
        self._knowledge_answer_service = knowledge_answer_service or build_default_knowledge_answer_service()
        self._document_request_orchestrator = document_request_orchestrator or DocumentRequestDraftOrchestrator()
        self._file_request_service = file_request_service or FileRequestService()
        self._leave_request_orchestrator = leave_request_orchestrator or build_default_leave_request_orchestrator()
        self._reimbursement_request_orchestrator = (
            reimbursement_request_orchestrator or build_default_reimbursement_request_orchestrator()
        )
        self._content_generation_service = content_generation_service or build_default_llm_content_generation_service()
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

        question = message.text.strip() if message.message_type == "text" else ""
        reimbursement_continuation = self._reimbursement_request_orchestrator.handle(
            conversation_id=message.conversation_id,
            sender_id=message.sender_id,
            message=message,
            user_context=user_context,
            force_start=(
                message.message_type == "text"
                and question != ""
                and self._should_start_reimbursement_workflow(question=question)
            ),
        )
        if reimbursement_continuation is not None:
            llm_trace["orchestrator_shadow"] = self._orchestrator_shadow_service.suggest(
                question=question,
                intent="reimbursement",
                rule_action="flow_guidance",
                conversation_id=message.conversation_id,
                sender_id=message.sender_id,
            ).to_trace()
            return self._apply_llm_trace(result=reimbursement_continuation, llm_trace=llm_trace)

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

        rule_hint_intent = self._intent_classifier.classify(question).intent
        if rule_hint_intent != "file_request":
            draft_continuation = self._document_request_orchestrator.handle(
                conversation_id=message.conversation_id,
                sender_id=message.sender_id,
                text=question,
                user_context=user_context,
                force_start=False,
            )
            if draft_continuation is not None:
                return draft_continuation

        has_pending_selection = getattr(self._file_request_service, "has_pending_selection", None)
        is_selection_reply = getattr(self._file_request_service, "is_selection_reply", None)
        pending_selection = False
        if callable(has_pending_selection):
            pending_selection = bool(
                has_pending_selection(
                    conversation_id=message.conversation_id,
                    sender_id=message.sender_id,
                )
            )
            if pending_selection and callable(is_selection_reply):
                if is_selection_reply(
                    conversation_id=message.conversation_id,
                    sender_id=message.sender_id,
                    text=question,
                ):
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

        leave_continuation = self._leave_request_orchestrator.handle(
            conversation_id=message.conversation_id,
            sender_id=message.sender_id,
            text=question,
            user_context=user_context,
            force_start=False,
        )
        if leave_continuation is not None:
            llm_trace["orchestrator_shadow"] = self._orchestrator_shadow_service.suggest(
                question=question,
                intent="leave",
                rule_action="flow_guidance",
                conversation_id=message.conversation_id,
                sender_id=message.sender_id,
            ).to_trace()
            return self._apply_llm_trace(result=leave_continuation, llm_trace=llm_trace)

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

        if pending_selection and intent != "file_request":
            clear_pending_selection = getattr(self._file_request_service, "clear_pending_selection", None)
            if callable(clear_pending_selection):
                clear_pending_selection(
                    conversation_id=message.conversation_id,
                    sender_id=message.sender_id,
                )

        if intent == "other" and self._should_start_leave_workflow(question=question):
            intent = "leave"
            llm_trace["intent"] = {**llm_trace["intent"], "intent": intent, "reason": "leave_workflow_heuristic"}
        if intent == "other" and self._should_start_reimbursement_workflow(question=question):
            intent = "reimbursement"
            llm_trace["intent"] = {**llm_trace["intent"], "intent": intent, "reason": "reimbursement_workflow_heuristic"}

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

        if intent == "leave":
            if self._should_start_leave_workflow(question=question):
                result = self._leave_request_orchestrator.handle(
                    conversation_id=message.conversation_id,
                    sender_id=message.sender_id,
                    text=question,
                    user_context=user_context,
                    force_start=True,
                ) or ChatHandleResult(
                    handled=False,
                    reason="leave_workflow_incomplete",
                    intent="leave",
                    reply=AgentReply(channel="text", text="请假信息暂未收集成功，请重试。"),
                )
                return self._apply_llm_trace(result=result, llm_trace=llm_trace)
            return ChatHandleResult(
                handled=True,
                reason="flow_guidance_card",
                intent=intent,
                reply=AgentReply(
                    channel="interactive_card",
                    interactive_card=build_flow_guidance_card(intent="leave", question=question),
                ),
                llm_trace=llm_trace,
            )

        if intent == "reimbursement":
            if self._should_start_reimbursement_workflow(question=question):
                result = self._reimbursement_request_orchestrator.handle(
                    conversation_id=message.conversation_id,
                    sender_id=message.sender_id,
                    message=message,
                    user_context=user_context,
                    force_start=True,
                ) or ChatHandleResult(
                    handled=False,
                    reason="reimbursement_workflow_incomplete",
                    intent="reimbursement",
                    reply=AgentReply(channel="text", text="报销信息暂未收集成功，请重试。"),
                )
                return self._apply_llm_trace(result=result, llm_trace=llm_trace)
            generated = self._content_generation_service.generate(
                mode="flow_guidance_reimbursement",
                question=question,
                prompt_fields=build_reimbursement_guidance_prompt_fields(user_input=question),
                fallback_text=build_reimbursement_guidance_fallback_text(user_input=question),
                conversation_id=message.conversation_id,
                sender_id=message.sender_id,
            )
            llm_trace["content"] = generated.to_trace()
            return ChatHandleResult(
                handled=True,
                reason="flow_guidance_text",
                intent=intent,
                reply=AgentReply(
                    channel="text",
                    text=generated.text,
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

    def handle_leave_confirmation_action_by_session(
        self,
        *,
        action: str,
        conversation_id: str,
        sender_id: str,
    ) -> ChatHandleResult:
        return self._leave_request_orchestrator.handle_confirmation_action_by_session(
            action=action,
            conversation_id=conversation_id,
            sender_id=sender_id,
        )

    def handle_reimbursement_confirmation_action_by_session(
        self,
        *,
        action: str,
        conversation_id: str,
        sender_id: str,
    ) -> ChatHandleResult:
        return self._reimbursement_request_orchestrator.handle_confirmation_action_by_session(
            action=action,
            conversation_id=conversation_id,
            sender_id=sender_id,
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

    def _should_start_leave_workflow(self, *, question: str) -> bool:
        normalized = self._normalize(question)
        if not normalized:
            return False
        if self._is_leave_information_query(question):
            return False
        leave_tokens = (
            "请假",
            "我要请假",
            "我想请假",
            "帮我请假",
            "请帮我请假",
            "发起请假",
            "提交请假",
            "请一天",
            "请一天假",
            "请一天的假",
            "请两天",
            "请两天假",
            "请两天的假",
            "请半天",
            "年假",
            "病假",
            "事假",
            "调休",
        )
        if any(token in normalized for token in leave_tokens):
            return True
        leave_action_hints = (
            "我要",
            "我想",
            "帮我",
            "发起",
            "提交",
            "年假",
            "病假",
            "事假",
            "调休",
            "婚假",
            "产假",
            "陪产假",
            "丧假",
            "今天",
            "明天",
            "后天",
            "月",
            "号",
            "日",
        )
        return bool(_LEAVE_ACTION_PHRASE_PATTERN.search(normalized)) and any(
            token in normalized for token in leave_action_hints
        )

    def _is_leave_information_query(self, question: str) -> bool:
        normalized = self._normalize(question)
        leave_scope_tokens = ("请假", "年假", "病假", "事假", "调休", "婚假", "产假", "陪产假", "丧假", "假期", "休假")
        if not any(token in normalized for token in leave_scope_tokens):
            return False
        info_tokens = ("流程", "入口", "在哪", "怎么", "如何", "规则", "制度", "说明")
        action_tokens = (
            "我要",
            "我想",
            "帮我",
            "发起",
            "提交",
            "请一天",
            "请两天",
            "请半天",
            "今天",
            "明天",
            "后天",
            "月",
            "号",
            "日",
            "到",
            "至",
        )
        return any(token in normalized for token in info_tokens) and not any(token in normalized for token in action_tokens)

    def _should_start_reimbursement_workflow(self, *, question: str) -> bool:
        normalized = self._normalize(question)
        if not normalized:
            return False
        if self._is_reimbursement_information_query(question):
            return False
        explicit_tokens = (
            "我要报销差旅费",
            "我要差旅报销",
            "我要报销差旅",
            "我想报销差旅费",
            "发起差旅报销",
            "提交差旅报销",
        )
        if any(token in normalized for token in explicit_tokens):
            return True
        action_tokens = ("我要", "我想", "帮我", "发起", "提交", "申请")
        return (
            "报销" in normalized
            and any(scope in normalized for scope in ("差旅", "出差"))
            and any(token in normalized for token in action_tokens)
        )

    def _is_reimbursement_information_query(self, question: str) -> bool:
        normalized = self._normalize(question)
        reimbursement_scope_tokens = ("报销", "差旅", "出差")
        if not any(token in normalized for token in reimbursement_scope_tokens):
            return False
        info_tokens = ("流程", "入口", "在哪", "怎么", "如何", "规则", "制度", "说明")
        action_tokens = ("我要", "我想", "帮我", "发起", "提交", "申请")
        return any(token in normalized for token in info_tokens) and not any(token in normalized for token in action_tokens)

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
