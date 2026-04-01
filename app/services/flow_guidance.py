from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

FlowGuidanceIntent = Literal["reimbursement", "leave"]


@dataclass(frozen=True)
class FlowGuidanceCanonicalBlock:
    title: str
    summary: str
    primary_action: str
    entry_point: str
    required_materials: str
    process_path: tuple[str, ...]
    common_errors: tuple[str, ...]
    next_action: str


FLOW_GUIDANCE_CANONICAL_BLOCKS: dict[FlowGuidanceIntent, FlowGuidanceCanonicalBlock] = {
    "reimbursement": FlowGuidanceCanonicalBlock(
        title="报销办理指引",
        summary="我先帮你收拢报销规则和入口，避免你来回补材料影响时效。",
        primary_action="先到钉钉工作台进入“审批”，选择对应的报销模板发起办理。",
        entry_point="钉钉 > 工作台 > 审批 > 报销",
        required_materials="提前准备发票、行程单、金额与事由说明，按模板补全必填信息。",
        process_path=(
            "选择报销模板",
            "填写金额与事由",
            "上传票据并提交",
            "财务复核",
        ),
        common_errors=(
            "超过报销时限（出差后30天内）",
            "金额与发票不符",
        ),
        next_action="如果你不确定该选哪种报销类型，我可以继续帮你缩小到对应入口。",
    ),
    "leave": FlowGuidanceCanonicalBlock(
        title="请假申请指引",
        summary="我先把请假规则、材料和入口整理给你，避免提交后被退回。",
        primary_action="先到钉钉工作台进入 OA审批，选择“请假”后发起提交。",
        entry_point="钉钉 > 工作台 > OA审批 > 请假",
        required_materials="提前确认假种、请假时间和必要说明；病假等场景按制度补充证明材料。",
        process_path=(
            "进入请假模板",
            "选择假种",
            "填写时间与事由",
            "提交审批",
        ),
        common_errors=(
            "未提前申请（需提前1天）",
            "假种选择错误",
        ),
        next_action="如果你已经确定请假类型和时间，也可以直接说“我要请假 + 时间 + 假种”，我按流程帮你发起。",
    ),
}


def build_flow_guidance_card(*, intent: FlowGuidanceIntent, question: str) -> dict[str, Any]:
    block = FLOW_GUIDANCE_CANONICAL_BLOCKS[intent]
    return {
        "card_type": "flow_guidance",
        "title": block.title,
        "summary": block.summary,
        "primary_action": block.primary_action,
        "context": question.strip(),
        "entry_point": block.entry_point,
        "required_materials": block.required_materials,
        "process_path": list(block.process_path),
        "common_errors": list(block.common_errors),
        "next_action": block.next_action,
    }


def build_reimbursement_guidance_prompt_fields(*, user_input: str) -> dict[str, str]:
    block = FLOW_GUIDANCE_CANONICAL_BLOCKS["reimbursement"]
    return {
        "user_input": user_input.strip(),
        "canonical_block": _serialize_reimbursement_block(block),
    }


def build_reimbursement_guidance_fallback_text(*, user_input: str) -> str:
    del user_input
    block = FLOW_GUIDANCE_CANONICAL_BLOCKS["reimbursement"]
    process_hint = "，".join(block.process_path[:2])
    return (
        f"出差报销一般先准备好发票、行程单和金额与事由说明，然后在{block.entry_point}按“{process_hint}”提交就可以。"
        "注意出差后30天内要完成报销，另外金额一定要和发票保持一致，不然很容易被财务退回。"
        "如果你不确定该选哪种报销类型，可以告诉我你的场景，我帮你快速定位。"
    )


def _serialize_reimbursement_block(block: FlowGuidanceCanonicalBlock) -> str:
    process_path = " > ".join(block.process_path)
    common_errors = "；".join(block.common_errors)
    return (
        f"summary={block.summary}; "
        f"entry_point={block.entry_point}; "
        f"required_materials={block.required_materials}; "
        f"process_path={process_path}; "
        f"common_errors={common_errors}; "
        f"next_action={block.next_action}"
    )
