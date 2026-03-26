from __future__ import annotations

from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.in_memory_knowledge_repository import InMemoryKnowledgeRepository
from app.repos.knowledge_repository import KnowledgeRepository
from app.schemas.dingtalk_chat import IntentType
from app.schemas.knowledge import KnowledgeAnswer, KnowledgeCitation, RetrievedEvidence, utc_now_iso


class KnowledgeAnswerService:
    """Compose deterministic A-08 answers from retrieved knowledge evidence."""

    def __init__(
        self,
        *,
        retriever: KnowledgeRetriever,
        repository: KnowledgeRepository,
    ) -> None:
        self._retriever = retriever
        self._repository = repository

    def answer(self, *, question: str, intent: IntentType) -> KnowledgeAnswer:
        evidences = self._retriever.retrieve(question=question, intent=intent)
        knowledge_version = self._repository.knowledge_version()

        if not evidences:
            return KnowledgeAnswer.not_found(
                text=self._build_no_hit_text(intent),
                knowledge_version=knowledge_version,
            )

        primary = evidences[0].entry
        text = self._build_hit_text(evidences)
        citations = tuple(self._build_citation(item) for item in evidences)
        source_ids = tuple(citation.source_id for citation in citations)
        return KnowledgeAnswer(
            found=True,
            text=text,
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

    def _build_hit_text(self, evidences: tuple[RetrievedEvidence, ...]) -> str:
        primary = evidences[0].entry
        lines = [
            f"结论：{primary.summary}",
            f"适用说明：{primary.applicability}",
            "来源：",
            f"1. [{primary.source_id}] {primary.title}（{self._source_label(primary.source_type)}，更新于 {primary.updated_at}）",
            f"入口：{primary.source_uri}",
        ]
        related = [
            item.entry
            for item in evidences[1:3]
            if item.entry.source_id != primary.source_id
        ]
        if related:
            lines.append("你可能还想看：")
            for index, entry in enumerate(related, start=1):
                lines.append(
                    f"{index}. [{entry.source_id}] {entry.title}（{self._source_label(entry.source_type)}，更新于 {entry.updated_at}）"
                )
        lines.append(f"下一步：{primary.next_step}")
        return "\n".join(lines)

    @staticmethod
    def _build_no_hit_text(intent: IntentType) -> str:
        if intent == "fixed_quote":
            return (
                "未在固定报价 FAQ 中找到匹配条目，当前不提供推测价格。\n"
                "建议联系商务同事确认非标准报价。"
            )
        return (
            "未找到与问题直接匹配的制度或 FAQ。\n"
            "请补充更具体的制度名称或流程关键词，或联系对应岗位（人事/财务/商务）协助。"
        )

    @staticmethod
    def _source_label(source_type: str) -> str:
        return "文档" if source_type == "document" else "FAQ"


def build_default_knowledge_answer_service() -> KnowledgeAnswerService:
    repository = InMemoryKnowledgeRepository()
    retriever = KnowledgeRetriever(repository=repository)
    return KnowledgeAnswerService(retriever=retriever, repository=repository)
