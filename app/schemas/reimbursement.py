from __future__ import annotations

from dataclasses import dataclass


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
