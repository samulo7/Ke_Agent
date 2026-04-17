from __future__ import annotations

from typing import Any

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
from app.services.llm_constants import NON_LLM_GUARDRAILS, SUMMARY_ONLY_ALLOWLIST
from app.services.llm_content_generation import (
    LLMContentGenerationService,
    build_default_llm_content_generation_service,
)
from app.services.tone_resolver import ToneResolver, build_tone_resolver_from_env


class KnowledgeAnswerService:
    """Compose A-08/A-09/B-13 answers with LLM language generation + deterministic fallback."""

    def __init__(
        self,
        *,
        retriever: KnowledgeRetriever,
        repository: KnowledgeRepository,
        tone_resolver: ToneResolver | None = None,
        content_generation_service: LLMContentGenerationService | None = None,
    ) -> None:
        self._retriever = retriever
        self._repository = repository
        self._tone_resolver = tone_resolver or build_tone_resolver_from_env()
        self._content_generation_service = content_generation_service or build_default_llm_content_generation_service()

    def answer(
        self,
        *,
        question: str,
        intent: IntentType,
        access_context: KnowledgeAccessContext | None = None,
        conversation_id: str = "",
        sender_id: str = "",
    ) -> KnowledgeAnswer:
        evidences = self._retriever.retrieve(
            question=question,
            intent=intent,
            access_context=access_context,
        )
        knowledge_version = self._repository.knowledge_version()
        tone_profile = self._tone_resolver.resolve(intent=intent)

        if evidences:
            all_citations = tuple(self._build_citation(item) for item in evidences)
            template = build_unified_answer_template(
                evidences=evidences,
                citations=all_citations,
                intent=intent,
                tone_profile=tone_profile,
            )
            citations = template.sources
            deterministic_text = template.to_text()
            generated = self._content_generation_service.generate(
                mode="allow",
                question=question,
                prompt_fields=self._build_allow_fields(evidences=evidences, citations=citations),
                fallback_text=deterministic_text,
                conversation_id=conversation_id,
                sender_id=sender_id,
            )
            source_ids = tuple(citation.source_id for citation in citations)
            return KnowledgeAnswer(
                found=True,
                text=generated.text,
                source_ids=source_ids,
                permission_decision="allow",
                knowledge_version=knowledge_version,
                answered_at=utc_now_iso(),
                citations=citations,
                llm_trace=generated.to_trace(),
            )

        restricted_evidences = self._retriever.retrieve_restricted(
            question=question,
            intent=intent,
            access_context=access_context,
        )
        if restricted_evidences:
            top_hit = restricted_evidences[0].entry
            contact = self._resolve_contact(top_hit)
            if top_hit.permission_scope == "sensitive":
                deterministic_text = self._build_deny_text(top_hit, contact=contact)
                generated = self._content_generation_service.generate(
                    mode="deny",
                    question=question,
                    prompt_fields={
                        "next_step": top_hit.next_step,
                        "contact": contact,
                    },
                    fallback_text=deterministic_text,
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                    disallowed_values=(top_hit.summary,),
                )
                return KnowledgeAnswer.restricted(
                    text=generated.text,
                    knowledge_version=knowledge_version,
                    permission_decision="deny",
                    source_ids=(top_hit.source_id,),
                    llm_trace=generated.to_trace(),
                )

            deterministic_text = self._build_summary_only_text(top_hit, contact=contact)
            generated = self._content_generation_service.generate(
                mode="summary_only",
                question=question,
                prompt_fields={
                    "summary": top_hit.summary,
                    "next_step": top_hit.next_step,
                    "contact": contact,
                },
                fallback_text=deterministic_text,
                conversation_id=conversation_id,
                sender_id=sender_id,
            )
            return KnowledgeAnswer.restricted(
                text=generated.text,
                knowledge_version=knowledge_version,
                permission_decision="summary_only",
                source_ids=(top_hit.source_id,),
                llm_trace=generated.to_trace(),
            )

        deterministic_text = self._build_no_hit_text(intent=intent, tone_profile=tone_profile)
        generated = self._content_generation_service.generate(
            mode="no_hit",
            question=question,
            prompt_fields={
                "intent": intent,
                "fallback_text": deterministic_text,
            },
            fallback_text=deterministic_text,
            conversation_id=conversation_id,
            sender_id=sender_id,
        )
        return KnowledgeAnswer.not_found(
            text=generated.text,
            knowledge_version=knowledge_version,
            llm_trace=generated.to_trace(),
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

    @staticmethod
    def _build_allow_fields(
        *,
        evidences: tuple[RetrievedEvidence, ...],
        citations: tuple[KnowledgeCitation, ...],
    ) -> dict[str, str]:
        top = evidences[0].entry
        source_lines = [f"{item.title}({item.source_id})" for item in citations]
        return {
            "summary": top.summary,
            "applicability": top.applicability,
            "next_step": top.next_step,
            "sources": "; ".join(source_lines),
            "guardrails": ", ".join(sorted(NON_LLM_GUARDRAILS)),
        }

    def _build_summary_only_text(self, entry: RestrictedKnowledgeEntry, *, contact: str) -> str:
        return (
            "该资料属于受控内容，当前不可直接查看正文。\n"
            f"脱敏摘要：{entry.summary}\n"
            f"申请路径：{entry.next_step}\n"
            f"建议联系人：{contact}\n"
            "如需我继续协助，可直接回复“帮我生成申请草稿”。"
        )

    def _build_deny_text(self, entry: RestrictedKnowledgeEntry, *, contact: str) -> str:
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
                    "请补充型号或规格后重试；如仍属于非标准项，请联系商务确认。"
                )
            if tone_profile == "neutral":
                return (
                    "暂未查到对应固定报价，因此不提供推测价格。\n"
                    "建议补充型号或规格后重试；如仍属于非标准项，请联系商务确认。"
                )
            return (
                "我这边没查到对应的固定报价，所以不提供推测价格。\n"
                "你把型号或规格补全，我再帮你查一轮；如果还是非标准项，就直接联系商务确认。"
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


assert SUMMARY_ONLY_ALLOWLIST == {"summary", "next_step", "contact"}


def build_default_knowledge_answer_service() -> KnowledgeAnswerService:
    repository = InMemoryKnowledgeRepository()
    retriever = KnowledgeRetriever(repository=repository)
    tone_resolver = build_tone_resolver_from_env()
    content_generation_service = build_default_llm_content_generation_service()
    return KnowledgeAnswerService(
        retriever=retriever,
        repository=repository,
        tone_resolver=tone_resolver,
        content_generation_service=content_generation_service,
    )
