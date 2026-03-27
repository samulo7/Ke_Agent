from __future__ import annotations

from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.in_memory_knowledge_repository import InMemoryKnowledgeRepository
from app.repos.knowledge_repository import KnowledgeRepository
from app.schemas.dingtalk_chat import IntentType
from app.schemas.knowledge import (
    KnowledgeAccessContext,
    KnowledgeAnswer,
    KnowledgeCitation,
    RestrictedKnowledgeEntry,
    RetrievedEvidence,
    utc_now_iso,
)
from app.schemas.tone import ToneProfile
from app.services.answer_template import build_unified_answer_template
from app.services.tone_resolver import ToneResolver, build_tone_resolver_from_env


class KnowledgeAnswerService:
    """Compose deterministic A-08/A-09/B-13 answers from retrieved knowledge evidence."""

    def __init__(
        self,
        *,
        retriever: KnowledgeRetriever,
        repository: KnowledgeRepository,
        tone_resolver: ToneResolver | None = None,
    ) -> None:
        self._retriever = retriever
        self._repository = repository
        self._tone_resolver = tone_resolver or build_tone_resolver_from_env()

    def answer(
        self,
        *,
        question: str,
        intent: IntentType,
        access_context: KnowledgeAccessContext | None = None,
    ) -> KnowledgeAnswer:
        evidences = self._retriever.retrieve(
            question=question,
            intent=intent,
            access_context=access_context,
        )
        knowledge_version = self._repository.knowledge_version()
        tone_profile = self._tone_resolver.resolve(intent=intent)

        if evidences:
            citations = tuple(self._build_citation(item) for item in evidences)
            template = build_unified_answer_template(
                evidences=evidences,
                citations=citations,
                intent=intent,
                tone_profile=tone_profile,
            )
            source_ids = tuple(citation.source_id for citation in citations)
            return KnowledgeAnswer(
                found=True,
                text=template.to_text(),
                source_ids=source_ids,
                permission_decision="allow",
                knowledge_version=knowledge_version,
                answered_at=utc_now_iso(),
                citations=citations,
            )

        restricted_evidences = self._retriever.retrieve_restricted(
            question=question,
            intent=intent,
            access_context=access_context,
        )
        if restricted_evidences:
            top_hit = restricted_evidences[0].entry
            if top_hit.permission_scope == "sensitive":
                return KnowledgeAnswer.restricted(
                    text=self._build_deny_text(top_hit),
                    knowledge_version=knowledge_version,
                    permission_decision="deny",
                    source_ids=(top_hit.source_id,),
                )
            return KnowledgeAnswer.restricted(
                text=self._build_summary_only_text(top_hit),
                knowledge_version=knowledge_version,
                permission_decision="summary_only",
                source_ids=(top_hit.source_id,),
            )

        return KnowledgeAnswer.not_found(
            text=self._build_no_hit_text(intent=intent, tone_profile=tone_profile),
            knowledge_version=knowledge_version,
        )

    @staticmethod
    def _build_citation(evidence: RetrievedEvidence) -> KnowledgeCitation:
        return KnowledgeCitation(
            source_id=evidence.entry.source_id,
            source_type=evidence.entry.source_type,
            title=evidence.entry.title,
            source_uri=evidence.entry.source_uri,
            updated_at=evidence.entry.updated_at,
        )

    @staticmethod
    def _resolve_contact(entry: RestrictedKnowledgeEntry) -> str:
        owner = (entry.owner or "").strip()
        return owner if owner else "人事/财务/商务"

    def _build_summary_only_text(self, entry: RestrictedKnowledgeEntry) -> str:
        contact = self._resolve_contact(entry)
        return (
            "该资料属于受控内容，当前不可直接查看正文。\n"
            f"脱敏摘要：{entry.summary}\n"
            f"申请路径：{entry.next_step}\n"
            f"建议联系人：{contact}\n"
            "如需我继续协助，可直接回复“帮我生成申请草稿”。"
        )

    def _build_deny_text(self, entry: RestrictedKnowledgeEntry) -> str:
        contact = self._resolve_contact(entry)
        return (
            "该资料属于敏感受控内容，当前权限下不可查看，且无法提供摘要。\n"
            f"申请路径：{entry.next_step}\n"
            f"建议联系人：{contact}\n"
            "如需我继续协助，可直接回复“帮我生成申请草稿”。"
        )

    @staticmethod
    def _build_no_hit_text(*, intent: IntentType, tone_profile: ToneProfile) -> str:
        if intent == "fixed_quote":
            if tone_profile == "formal":
                return (
                    "当前未检索到对应固定报价，故不提供推测价格。\n"
                    "请补充型号或规格后重试，或直接联系商务确认。"
                )
            if tone_profile == "neutral":
                return (
                    "暂未查到对应固定报价，因此不提供推测价格。\n"
                    "建议补充型号或规格后重试，或直接联系商务确认。"
                )
            return (
                "我这边没查到对应的固定报价，所以不提供推测价格。\n"
                "你把型号或规格补全，我再帮你查一轮；或者直接找商务确认也行。"
            )
        if tone_profile == "formal":
            return (
                "当前未检索到可直接回答该问题的制度或 FAQ。\n"
                "请补充制度名称或流程关键词后重试，或联系人事/财务/商务协助。"
            )
        if tone_profile == "neutral":
            return (
                "暂未找到可直接回答该问题的制度或 FAQ。\n"
                "建议补充制度名称或流程关键词后重试，或联系人事/财务/商务协助。"
            )
        return (
            "这句我现在还匹配不到对应的制度或 FAQ。\n"
            "你补几个关键词（制度名、流程名、报销类型）我再查；也可以直接找人事/财务/商务。"
        )


def build_default_knowledge_answer_service() -> KnowledgeAnswerService:
    repository = InMemoryKnowledgeRepository()
    retriever = KnowledgeRetriever(repository=repository)
    tone_resolver = build_tone_resolver_from_env()
    return KnowledgeAnswerService(retriever=retriever, repository=repository, tone_resolver=tone_resolver)
