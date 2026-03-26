from __future__ import annotations

from dataclasses import dataclass

from app.schemas.dingtalk_chat import IntentType

DOCUMENT_ACTION_KEYWORDS = ("申请", "调阅", "获取", "查看", "访问", "开通")
DOCUMENT_TARGET_KEYWORDS = ("文档", "文件", "资料", "手册", "模板", "制度", "权限", "正文")
REIMBURSEMENT_KEYWORDS = ("报销", "报账", "差旅", "出差", "发票", "费用报销", "报销单")
LEAVE_KEYWORDS = ("请假", "休假", "年假", "病假", "事假", "调休", "婚假", "产假")
FIXED_QUOTE_KEYWORDS = ("报价", "多少钱", "价格", "单价", "价目", "价目表", "定影器")
POLICY_PROCESS_KEYWORDS = ("制度", "规则", "规范", "标准", "流程", "入口", "步骤", "怎么走", "怎么办", "指引")


@dataclass(frozen=True)
class IntentClassification:
    intent: IntentType
    confidence: float


class IntentClassifier:
    """A-07 rule-first intent classifier with deterministic priority."""

    def classify(self, text: str) -> IntentClassification:
        question = self._normalize(text)
        if not question:
            return IntentClassification(intent="other", confidence=0.0)

        if self._is_document_request(question):
            return IntentClassification(intent="document_request", confidence=0.97)
        if self._contains_any(question, REIMBURSEMENT_KEYWORDS):
            return IntentClassification(intent="reimbursement", confidence=0.95)
        if self._contains_any(question, LEAVE_KEYWORDS):
            return IntentClassification(intent="leave", confidence=0.95)
        if self._contains_any(question, FIXED_QUOTE_KEYWORDS):
            return IntentClassification(intent="fixed_quote", confidence=0.92)
        if self._contains_any(question, POLICY_PROCESS_KEYWORDS):
            return IntentClassification(intent="policy_process", confidence=0.88)
        return IntentClassification(intent="other", confidence=0.5)

    @staticmethod
    def _normalize(text: str) -> str:
        return "".join(text.strip().lower().split())

    @staticmethod
    def _contains_any(question: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword in question for keyword in keywords)

    def _is_document_request(self, question: str) -> bool:
        has_target = self._contains_any(question, DOCUMENT_TARGET_KEYWORDS)
        if not has_target:
            return False
        has_action = self._contains_any(question, DOCUMENT_ACTION_KEYWORDS)
        if has_action:
            return True
        return "我要" in question or "需要" in question
