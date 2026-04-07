from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TravelApplication:
    process_instance_id: str
    start_date: str
    destination: str
    purpose: str = ""

    def display_text(self) -> str:
        purpose = self.purpose.strip()
        if purpose:
            return f"{self.start_date} {self.destination}（{purpose}）"
        return f"{self.start_date} {self.destination}"


@dataclass(frozen=True)
class ReimbursementAttachmentProcessResult:
    success: bool
    reason: str
    department: str = ""
    amount: str = ""
    attachment_media_id: str = ""
    table_amount: str = ""
    uppercase_amount_text: str = ""
    uppercase_amount_raw: str = ""
    uppercase_amount_numeric: str = ""
    amount_conflict: bool = False
    amount_conflict_note: str = ""
    amount_source: str = "table"
    amount_source_note: str = ""
    extraction_evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReimbursementApprovalSubmission:
    originator_user_id: str
    travel_process_instance_id: str
    department: str
    fixed_company: str
    cost_company: str
    date: str
    amount: str
    over_five_thousand: str
    attachment_media_id: str


@dataclass(frozen=True)
class ReimbursementApprovalResult:
    success: bool
    reason: str
    process_instance_id: str = ""
    failure_category: str = ""
    raw_errcode: int | None = None
    raw_errmsg: str = ""
