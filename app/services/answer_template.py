from __future__ import annotations

from dataclasses import dataclass

from app.schemas.dingtalk_chat import IntentType
from app.schemas.knowledge import KnowledgeCitation, RetrievedEvidence
from app.schemas.tone import ToneProfile


@dataclass(frozen=True)
class UnifiedAnswerTemplate:
    """Canonical A-09 answer template for FAQ/document knowledge hits."""

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


def build_unified_answer_template(
    *,
    evidences: tuple[RetrievedEvidence, ...],
    citations: tuple[KnowledgeCitation, ...],
    intent: IntentType,
    tone_profile: ToneProfile,
) -> UnifiedAnswerTemplate:
    primary = evidences[0].entry
    steps = [_build_applicability_step(applicability=primary.applicability, tone_profile=tone_profile)]

    if intent == "fixed_quote":
        steps.append(_build_fixed_quote_step(tone_profile=tone_profile))
    elif intent in {"policy_process", "other"}:
        steps.append(_build_policy_step(tone_profile=tone_profile))
    else:
        steps.append(_build_generic_step(tone_profile=tone_profile))

    related_titles = [item.entry.title for item in evidences[1:3] if item.entry.source_id != primary.source_id]
    if related_titles:
        steps.append(_build_related_step(related_titles=related_titles, tone_profile=tone_profile))

    return UnifiedAnswerTemplate(
        conclusion=primary.summary,
        steps=tuple(steps),
        sources=citations,
        next_step=primary.next_step,
    )


def _build_applicability_step(*, applicability: str, tone_profile: ToneProfile) -> str:
    normalized = _normalize_applicability(applicability)
    if tone_profile == "formal":
        return f"请先确认适用范围：{normalized}"
    if tone_profile == "neutral":
        return f"先确认适用范围：{normalized}"
    return f"这个规则一般适用于：{normalized}"


def _build_fixed_quote_step(*, tone_profile: ToneProfile) -> str:
    if tone_profile == "formal":
        return "如型号、税率或数量与实际需求不一致，请先联系商务确认后再下单。"
    if tone_profile == "neutral":
        return "如果型号、税率或数量和实际情况不同，建议先联系商务确认后再下单。"
    return "型号、税率、数量只要有一项不一样，都先和商务对一下再下单。"


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
