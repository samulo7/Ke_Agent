from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.dingtalk_chat import IntentType
from app.schemas.knowledge import KnowledgeCitation, RetrievedEvidence
from app.schemas.tone import ToneProfile


@dataclass(frozen=True)
class UnifiedAnswerTemplate:
    """Canonical A-09 answer template for document knowledge hits."""

    conclusion: str
    steps: tuple[str, ...]
    sources: tuple[KnowledgeCitation, ...]
    next_step: str

    def to_text(self) -> str:
        lines: list[str] = [f"结论：{self.conclusion}", "步骤："]
        for index, step in enumerate(self.steps, start=1):
            lines.append(f"{index}. {step}")

        lines.append("来源：")
        for index, source in enumerate(self.sources, start=1):
            lines.append(
                f"{index}. [{source.source_id}] {source.title}"
                f"（{self._source_label(source.source_type)}，更新于 {source.updated_at}，入口：{source.source_uri}）"
            )

        lines.append(f"下一步：{self.next_step}")
        return "\n".join(lines)

    @staticmethod
    def _source_label(source_type: str) -> str:
        return "文档" if source_type == "document" else "FAQ"


@dataclass(frozen=True)
class FAQAnswerTemplate:
    """Answer template for non-quote FAQ hits."""

    conclusion: str
    applicability: str
    sources: tuple[KnowledgeCitation, ...]
    suggestion: str

    def to_text(self) -> str:
        lines: list[str] = [
            f"结论：{self.conclusion}",
            f"适用范围：{_normalize_applicability(self.applicability)}",
        ]
        if self.suggestion.strip():
            lines.append(f"建议：{self.suggestion.strip()}")
        lines.append("来源：")
        for index, source in enumerate(self.sources, start=1):
            lines.append(
                f"{index}. [{source.source_id}] {source.title}"
                f"（FAQ，更新于 {source.updated_at}）"
            )
        return "\n".join(lines)


def build_unified_answer_template(
    *,
    evidences: tuple[RetrievedEvidence, ...],
    citations: tuple[KnowledgeCitation, ...],
    intent: IntentType,
    tone_profile: ToneProfile,
    question: str,
) -> UnifiedAnswerTemplate | FAQAnswerTemplate:
    selected_evidences, selected_citations = _select_display_evidences_and_citations(
        evidences=evidences,
        citations=citations,
    )
    primary = selected_evidences[0].entry
    if primary.source_type == "faq" and intent != "fixed_quote":
        return FAQAnswerTemplate(
            conclusion=primary.summary,
            applicability=primary.applicability,
            sources=selected_citations,
            suggestion=primary.next_step,
        )

    steps = [_build_applicability_step(applicability=primary.applicability, tone_profile=tone_profile)]

    if intent == "fixed_quote":
        steps.append(_build_fixed_quote_version_step(updated_at=primary.updated_at, tone_profile=tone_profile))
        steps.append(_build_fixed_quote_step(tone_profile=tone_profile))
    elif intent in {"policy_process", "other"}:
        steps.append(_build_policy_step(tone_profile=tone_profile))
    else:
        steps.append(_build_generic_step(tone_profile=tone_profile))

    related_titles = [
        item.entry.title
        for item in selected_evidences[1:3]
        if item.entry.source_id != primary.source_id
    ]
    if related_titles:
        steps.append(_build_related_step(related_titles=related_titles, tone_profile=tone_profile))

    return UnifiedAnswerTemplate(
        conclusion=(
            _build_document_conclusion(search_text=primary.search_text, summary=primary.summary, question=question)
            if primary.source_type == "document"
            else primary.summary
        ),
        steps=tuple(steps),
        sources=selected_citations,
        next_step=primary.next_step,
    )


def _select_display_evidences_and_citations(
    *,
    evidences: tuple[RetrievedEvidence, ...],
    citations: tuple[KnowledgeCitation, ...],
) -> tuple[tuple[RetrievedEvidence, ...], tuple[KnowledgeCitation, ...]]:
    primary = evidences[0]
    threshold = _supporting_score_threshold(primary_score=primary.score)
    selected_evidences: list[RetrievedEvidence] = [primary]
    for item in evidences[1:]:
        if item.score >= threshold:
            selected_evidences.append(item)
        if len(selected_evidences) >= 3:
            break

    citation_map = {item.source_id: item for item in citations}
    selected_citations = tuple(
        citation_map[item.entry.source_id]
        for item in selected_evidences
        if item.entry.source_id in citation_map
    )
    return tuple(selected_evidences), selected_citations


def _build_document_conclusion(*, search_text: str, summary: str, question: str) -> str:
    terms = _extract_match_terms(question)
    best_segment = ""
    best_score = 0
    for raw_segment in re.split(r"[\n。！？!?；;]", search_text):
        segment = raw_segment.strip()
        if not segment:
            continue
        normalized_segment = _normalize_for_match(segment)
        matched_terms = [term for term in terms if term in normalized_segment]
        if not matched_terms:
            continue
        score = sum(max(len(term), 2) for term in matched_terms) - (segment.count(".") + segment.count("…") + segment.count("·")) * 3
        if "目录" in segment:
            score -= 20
        if score > best_score:
            best_segment = segment
            best_score = score
    return best_segment or summary


def _extract_match_terms(question: str) -> tuple[str, ...]:
    terms: list[str] = []
    for raw in re.findall(r"[\u4e00-\u9fff]{2,16}|[A-Za-z0-9][A-Za-z0-9_-]{1,31}", question):
        normalized = _normalize_for_match(raw)
        if len(normalized) < 2:
            continue
        candidates = [normalized]
        if all("\u4e00" <= char <= "\u9fff" for char in normalized) and len(normalized) > 4:
            for size in range(2, min(4, len(normalized)) + 1):
                for index in range(len(normalized) - size + 1):
                    candidates.append(normalized[index : index + size])
        for candidate in candidates:
            if len(candidate) < 2 or candidate in terms:
                continue
            terms.append(candidate)
    return tuple(terms)


def _normalize_for_match(text: str) -> str:
    return "".join(text.strip().lower().split())


def _supporting_score_threshold(*, primary_score: int) -> int:
    if primary_score >= 30:
        return max(15, int(primary_score * 0.6))
    if primary_score >= 20:
        return 12
    return 10


def _build_applicability_step(*, applicability: str, tone_profile: ToneProfile) -> str:
    normalized = _normalize_applicability(applicability)
    if tone_profile == "formal":
        return f"请先确认适用范围：{normalized}"
    if tone_profile == "neutral":
        return f"先确认适用范围：{normalized}"
    return f"这个规则一般适用于：{normalized}"


def _build_fixed_quote_version_step(*, updated_at: str, tone_profile: ToneProfile) -> str:
    if tone_profile == "formal":
        return f"本报价版本日期：{updated_at}，请以下方来源更新时间为准。"
    if tone_profile == "neutral":
        return f"本报价版本日期：{updated_at}，请以下方来源更新时间为准。"
    return f"这条报价当前版本日期是 {updated_at}，以来源更新时间为准。"


def _build_fixed_quote_step(*, tone_profile: ToneProfile) -> str:
    if tone_profile == "formal":
        return "如型号、税率、数量或折扣条件与实际需求不一致，请先联系商务确认后再下单。"
    if tone_profile == "neutral":
        return "如果型号、税率、数量或折扣条件和实际情况不同，建议先联系商务确认后再下单。"
    return "型号、税率、数量、折扣条件只要有一项不一样，都先联系商务确认再下单。"


def _build_policy_step(*, tone_profile: ToneProfile) -> str:
    if tone_profile == "formal":
        return "请按来源要求准备材料并执行流程，以减少退回风险。"
    if tone_profile == "neutral":
        return "按来源要求准备材料并走流程，通常更容易一次通过。"
    return "照来源里的要求准备材料再提交流程，基本就不容易被退回。"


def _build_generic_step(*, tone_profile: ToneProfile) -> str:
    if tone_profile == "formal":
        return "请先按来源说明执行，如遇阻塞再补充细节继续查询。"
    if tone_profile == "neutral":
        return "先按来源说明执行，遇到卡点再补充细节继续查询。"
    return "你先按这里的说明走一遍，卡住了把具体环节告诉我，我再接着帮你。"


def _build_related_step(*, related_titles: list[str], tone_profile: ToneProfile) -> str:
    joined = "；".join(related_titles)
    if tone_profile == "formal":
        return f"如需补充细则，可继续参考：{joined}。"
    if tone_profile == "neutral":
        return f"如需补充细节，可继续查看：{joined}。"
    return f"要是你还想看细则，这几份也有用：{joined}。"


def _normalize_applicability(value: str) -> str:
    stripped = value.strip()
    for prefix in ("适用于", "适用范围为", "适用范围：", "适用范围"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :].strip()
            break
    return stripped.lstrip("：:，,。 ")
