from __future__ import annotations

from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.in_memory_knowledge_repository import InMemoryKnowledgeRepository
from app.repos.knowledge_repository import KnowledgeRepository
from app.schemas.dingtalk_chat import IntentType
from app.schemas.knowledge import KnowledgeAnswer, KnowledgeCitation, RetrievedEvidence, utc_now_iso
from app.schemas.tone import ToneProfile
from app.services.answer_template import build_unified_answer_template
from app.services.tone_resolver import ToneResolver, build_tone_resolver_from_env


class KnowledgeAnswerService:
    """Compose deterministic A-08/A-09 answers from retrieved knowledge evidence."""

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

    def answer(self, *, question: str, intent: IntentType) -> KnowledgeAnswer:
        evidences = self._retriever.retrieve(question=question, intent=intent)
        knowledge_version = self._repository.knowledge_version()
        tone_profile = self._tone_resolver.resolve(intent=intent)

        if not evidences:
            return KnowledgeAnswer.not_found(
                text=self._build_no_hit_text(intent=intent, tone_profile=tone_profile),
                knowledge_version=knowledge_version,
            )

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
