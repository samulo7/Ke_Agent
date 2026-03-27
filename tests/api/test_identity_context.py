from __future__ import annotations

import json
import sqlite3
import unittest
from io import StringIO

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.sql_knowledge_repository import SQLKnowledgeRepository, bootstrap_sqlite_schema
from app.schemas.user_context import UserContext
from app.services.knowledge_answering import KnowledgeAnswerService
from app.services.single_chat import SingleChatService
from app.services.tone_resolver import ToneResolver


def _load_logs(log_stream: StringIO) -> list[dict[str, object]]:
    rows = [line.strip() for line in log_stream.getvalue().splitlines() if line.strip()]
    return [json.loads(row) for row in rows]


class _StubResolver:
    def __init__(self) -> None:
        self._contexts = {
            "staff-a": UserContext(
                user_id="staff-a",
                user_name="Alice",
                dept_id="dept-finance",
                dept_name="Finance",
                identity_source="openapi",
                is_degraded=False,
                resolved_at="2026-03-26T00:00:00+00:00",
            ),
            "staff-b": UserContext(
                user_id="staff-b",
                user_name="Bob",
                dept_id="dept-hr",
                dept_name="HR",
                identity_source="openapi",
                is_degraded=False,
                resolved_at="2026-03-26T00:00:01+00:00",
            ),
        }

    def resolve(self, message):  # type: ignore[no-untyped-def]
        key = message.sender_staff_id or message.sender_id
        return self._contexts.get(
            key,
            UserContext(
                user_id=key or "unknown",
                user_name=message.sender_nick or "unknown",
                dept_id="unknown",
                dept_name="unknown",
                identity_source="event_fallback",
                is_degraded=True,
                resolved_at="2026-03-26T00:00:59+00:00",
            ),
        )


def _build_permission_single_chat_service() -> tuple[SingleChatService, sqlite3.Connection]:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    bootstrap_sqlite_schema(connection)
    connection.executemany(
        """
        INSERT INTO knowledge_docs (
            doc_id,
            source_type,
            title,
            summary,
            applicability,
            next_step,
            source_uri,
            updated_at,
            status,
            owner,
            category,
            version_tag,
            keywords_csv,
            intents_csv,
            permission_scope,
            permitted_depts_csv
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                "doc-public-policy",
                "document",
                "报销流程入口说明",
                "公共流程说明，员工可查看报销入口和步骤。",
                "适用于全员",
                "打开钉钉审批-报销入口",
                "https://example.local/docs/public-policy",
                "2026-03-20",
                "active",
                "hr-team",
                "policy",
                "v1",
                "报销,流程,入口",
                "policy_process",
                "public",
                "",
            ),
            (
                "doc-finance-policy",
                "document",
                "财务报销制度细则",
                "含票据规范与财务专项口径。",
                "适用于财务部门",
                "联系财务专员确认口径",
                "https://example.local/docs/finance-policy",
                "2026-03-20",
                "active",
                "finance-team",
                "policy",
                "v3",
                "财务,发票,口径",
                "policy_process",
                "department",
                "dept-finance",
            ),
            (
                "doc-sensitive-budget",
                "document",
                "高管预算审批规则",
                "高管预算审批阈值与审批链路说明。",
                "适用于财务预算审批岗",
                "通过钉钉“预算审批”流程提交并抄送财务负责人",
                "https://example.local/docs/sensitive-budget",
                "2026-03-21",
                "active",
                "finance-owner",
                "budget",
                "v2",
                "高管,预算,审批,敏感",
                "policy_process",
                "sensitive",
                "dept-finance",
            ),
        ),
    )
    connection.executemany(
        """
        INSERT INTO doc_chunks (
            chunk_id,
            doc_id,
            chunk_index,
            chunk_text,
            chunk_vector
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            ("chunk-public-1", "doc-public-policy", 0, "公共报销流程入口说明", "[0.1,0.2]"),
            ("chunk-finance-1", "doc-finance-policy", 0, "财务制度细则中的发票口径", "[0.4,0.6]"),
            ("chunk-sensitive-1", "doc-sensitive-budget", 0, "敏感预算审批流程说明", "[0.8,0.6]"),
        ),
    )
    connection.commit()

    repository = SQLKnowledgeRepository(connection=connection, version="b13-sql-v1")
    answer_service = KnowledgeAnswerService(
        retriever=KnowledgeRetriever(repository=repository, top_k=5),
        repository=repository,
        tone_resolver=ToneResolver(default_tone="neutral"),
    )
    return SingleChatService(knowledge_answer_service=answer_service), connection


def _payload(*, sender_staff_id: str, text: str) -> dict[str, object]:
    return {
        "event_id": "evt-a06-001",
        "conversation_id": "conv-a06-001",
        "conversation_type": "single",
        "senderStaffId": sender_staff_id,
        "senderNick": "tester",
        "message_type": "text",
        "text": text,
    }


class DingTalkIdentityContextApiTests(unittest.TestCase):
    def test_response_contains_user_context_and_log_matches(self) -> None:
        trace_id = "trace-a06-identity"
        log_stream = StringIO()
        app = create_app(log_stream=log_stream, user_context_resolver=_StubResolver())
        client = TestClient(app)

        response = client.post(
            "/dingtalk/stream/events",
            headers={"X-Trace-Id": trace_id},
            json=_payload(sender_staff_id="staff-a", text="你好"),
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("other", body["intent"])
        self.assertEqual("staff-a", body["user_context"]["user_id"])
        self.assertEqual("dept-finance", body["user_context"]["dept_id"])
        self.assertEqual("openapi", body["user_context"]["identity_source"])

        request_logs = [
            item
            for item in _load_logs(log_stream)
            if item.get("event") == "request_completed" and item.get("path") == "/dingtalk/stream/events"
        ]
        self.assertGreaterEqual(len(request_logs), 1)
        log = request_logs[-1]
        self.assertEqual(trace_id, log["trace_id"])
        self.assertEqual(body["user_context"]["user_id"], log["user_id"])
        self.assertEqual(body["user_context"]["dept_id"], log["dept_id"])
        self.assertEqual(body["intent"], log["intent"])
        self.assertEqual(body["user_context"]["identity_source"], log["identity_source"])
        self.assertEqual(body["user_context"]["is_degraded"], log["is_degraded"])
        self.assertEqual(body["source_ids"], log["source_ids"])
        self.assertEqual(body["permission_decision"], log["permission_decision"])
        self.assertEqual(body["knowledge_version"], log["knowledge_version"])
        self.assertEqual(body["answered_at"], log["answered_at"])

    def test_same_question_cross_department_has_no_context_mismatch(self) -> None:
        log_stream = StringIO()
        app = create_app(log_stream=log_stream, user_context_resolver=_StubResolver())
        client = TestClient(app)

        response_a = client.post(
            "/dingtalk/stream/events",
            headers={"X-Trace-Id": "trace-a06-a"},
            json=_payload(sender_staff_id="staff-a", text="报销流程是什么"),
        )
        response_b = client.post(
            "/dingtalk/stream/events",
            headers={"X-Trace-Id": "trace-a06-b"},
            json=_payload(sender_staff_id="staff-b", text="报销流程是什么"),
        )

        self.assertEqual(200, response_a.status_code)
        self.assertEqual(200, response_b.status_code)
        body_a = response_a.json()
        body_b = response_b.json()
        self.assertEqual("reimbursement", body_a["intent"])
        self.assertEqual("reimbursement", body_b["intent"])
        self.assertEqual("dept-finance", body_a["user_context"]["dept_id"])
        self.assertEqual("dept-hr", body_b["user_context"]["dept_id"])
        self.assertNotEqual(body_a["user_context"]["dept_id"], body_b["user_context"]["dept_id"])

    def test_same_question_cross_department_has_permission_decision_diff_and_log_consistency(self) -> None:
        service, connection = _build_permission_single_chat_service()
        try:
            log_stream = StringIO()
            app = create_app(
                log_stream=log_stream,
                user_context_resolver=_StubResolver(),
                single_chat_service=service,
            )
            client = TestClient(app)

            response_finance = client.post(
                "/dingtalk/stream/events",
                headers={"X-Trace-Id": "trace-b13-finance"},
                json=_payload(sender_staff_id="staff-a", text="财务制度细则规则是什么"),
            )
            response_hr = client.post(
                "/dingtalk/stream/events",
                headers={"X-Trace-Id": "trace-b13-hr"},
                json=_payload(sender_staff_id="staff-b", text="财务制度细则规则是什么"),
            )

            self.assertEqual(200, response_finance.status_code)
            self.assertEqual(200, response_hr.status_code)

            body_finance = response_finance.json()
            body_hr = response_hr.json()

            self.assertEqual("allow", body_finance["permission_decision"])
            self.assertEqual("knowledge_answer", body_finance["reason"])
            self.assertEqual("summary_only", body_hr["permission_decision"])
            self.assertEqual("permission_restricted", body_hr["reason"])

            completed_logs = {
                item.get("trace_id"): item
                for item in _load_logs(log_stream)
                if item.get("event") == "request_completed" and item.get("path") == "/dingtalk/stream/events"
            }
            self.assertEqual("allow", completed_logs["trace-b13-finance"]["permission_decision"])
            self.assertEqual("summary_only", completed_logs["trace-b13-hr"]["permission_decision"])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
