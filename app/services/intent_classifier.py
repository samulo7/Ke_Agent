from __future__ import annotations

from dataclasses import dataclass

from app.schemas.dingtalk_chat import IntentType

DOCUMENT_ACTION_KEYWORDS = ("申请", "调阅", "查看", "访问", "开通", "授权")
DOCUMENT_PERMISSION_KEYWORDS = (
    "权限",
    "开通",
    "授权",
    "调阅权限",
    "访问权限",
    "查看权限",
    "阅读权限",
)
DOCUMENT_TARGET_KEYWORDS = ("文档", "文件", "资料", "手册", "模板", "制度", "正文", "合同", "协议")
FILE_ACTION_KEYWORDS = (
    "找",
    "查",
    "检索",
    "申请",
    "发我",
    "给我",
    "下载",
    "链接",
    "我要",
    "我想要",
    "我需要",
    "需要",
    "在哪",
    "哪里看",
    "在哪里看",
    "在哪下载",
    "下载地址",
    "扫描版",
    "纸质版",
)
FILE_TARGET_KEYWORDS = ("合同", "协议", "附件", "文件", "扫描件", "纸质版", "原件", "资料")
REIMBURSEMENT_KEYWORDS = ("报销", "报账", "差旅", "出差", "发票", "费用报销", "报销单")
LEAVE_KEYWORDS = ("请假", "休假", "年假", "病假", "事假", "调休", "婚假", "产假")
FIXED_QUOTE_KEYWORDS = ("报价", "多少钱", "价格", "单价", "价目", "价目表", "定影器")
POLICY_PROCESS_KEYWORDS = ("制度", "规则", "规范", "标准", "流程", "入口", "步骤", "怎么走", "怎么办", "指引", "内容", "是什么")


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
        if self._is_file_request(question):
            return IntentClassification(intent="file_request", confidence=0.96)
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
        has_permission = self._contains_any(question, DOCUMENT_PERMISSION_KEYWORDS)
        if not has_permission:
            return False
        has_target = self._contains_any(question, DOCUMENT_TARGET_KEYWORDS)
        if not has_target:
            return False
        has_action = self._contains_any(question, DOCUMENT_ACTION_KEYWORDS)
        return has_action

    def _is_file_request(self, question: str) -> bool:
        has_target = self._contains_any(question, FILE_TARGET_KEYWORDS)
        if not has_target:
            return False
        return self._contains_any(question, FILE_ACTION_KEYWORDS)
