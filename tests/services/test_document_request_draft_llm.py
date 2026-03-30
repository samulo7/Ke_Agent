from __future__ import annotations

import unittest

from app.schemas.user_context import UserContext
from app.services.document_request_draft import DocumentRequestDraftOrchestrator
from app.services.llm_draft_generation import DraftLLMResult


class _StubDraftService:
    def __init__(self, *, initial_item: str = "", initial_purpose: str = "", followup_purpose: str = "", polished: str = "") -> None:
        self.initial_item = initial_item
        self.initial_purpose = initial_purpose
        self.followup_purpose = followup_purpose
        self.polished = polished

    def extract_initial(self, *, text: str, conversation_id: str, sender_id: str) -> DraftLLMResult:
        return DraftLLMResult(
            requested_item=self.initial_item,
            request_purpose=self.initial_purpose,
            fallback_used=False,
            validation_passed=True,
            model="qwen-plus",
        )

    def extract_followup(self, *, text: str, conversation_id: str, sender_id: str) -> DraftLLMResult:
        return DraftLLMResult(
            requested_item="",
            request_purpose=self.followup_purpose,
            fallback_used=False,
            validation_passed=True,
            model="qwen-plus",
        )

    def polish_purpose(self, *, purpose: str, conversation_id: str, sender_id: str) -> DraftLLMResult:
        return DraftLLMResult(
            requested_item="",
            request_purpose=self.polished or purpose,
            fallback_used=False,
            validation_passed=True,
            model="qwen-plus",
        )


def _user_context() -> UserContext:
    return UserContext(
        user_id="u-1",
        user_name="Alice",
        dept_id="finance",
        dept_name="Finance",
        identity_source="openapi",
        is_degraded=False,
        resolved_at="2026-03-27T00:00:00+00:00",
    )


class DocumentRequestDraftOrchestratorLLMTests(unittest.TestCase):
    def test_initial_turn_can_be_ready_when_llm_extracts_all_required_fields(self) -> None:
        orchestrator = DocumentRequestDraftOrchestrator(
            llm_draft_service=_StubDraftService(
                initial_item="定影器采购合同",
                initial_purpose="年度审计",
                polished="用于年度审计复盘",
            )
        )

        outcome = orchestrator.handle(
            conversation_id="conv-1",
            sender_id="u-1",
            text="我要申请定影器采购合同，用于年度审计",
            user_context=_user_context(),
            force_start=True,
        )

        self.assertIsNotNone(outcome)
        assert outcome is not None
        self.assertEqual("application_draft_ready", outcome.reason)
        self.assertEqual("interactive_card", outcome.reply.channel)
        self.assertEqual("用于年度审计复盘", outcome.reply.interactive_card["draft_fields"]["request_purpose"])

    def test_followup_turn_uses_llm_extracted_purpose(self) -> None:
        orchestrator = DocumentRequestDraftOrchestrator(
            llm_draft_service=_StubDraftService(
                initial_item="采购制度文件",
                followup_purpose="项目复盘材料准备",
                polished="用于项目复盘材料准备",
            )
        )
        first = orchestrator.handle(
            conversation_id="conv-2",
            sender_id="u-1",
            text="我要申请采购制度文件",
            user_context=_user_context(),
            force_start=True,
        )
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual("application_draft_collecting", first.reason)

        second = orchestrator.handle(
            conversation_id="conv-2",
            sender_id="u-1",
            text="用于项目复盘",
            user_context=_user_context(),
            force_start=False,
        )
        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual("application_draft_ready", second.reason)
        self.assertEqual("用于项目复盘材料准备", second.reply.interactive_card["draft_fields"]["request_purpose"])


if __name__ == "__main__":
    unittest.main()

