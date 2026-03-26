from __future__ import annotations

import unittest
from collections import Counter

from app.schemas.dingtalk_chat import IntentType
from app.services.intent_classifier import IntentClassifier


LABELED_SAMPLES: list[tuple[str, IntentType]] = [
    ("宴请制度是什么", "policy_process"),
    ("财务制度在哪里看", "policy_process"),
    ("办公用品采购流程入口在哪", "policy_process"),
    ("访客管理规范有哪些", "policy_process"),
    ("公司会议管理标准是什么", "policy_process"),
    ("印章使用流程步骤", "policy_process"),
    ("门禁管理规则", "policy_process"),
    ("OA流程办理入口在哪", "policy_process"),
    ("固定资产借用规范", "policy_process"),
    ("人事制度更新要求", "policy_process"),
    ("我要申请采购制度文件", "document_request"),
    ("需要查看员工手册文档", "document_request"),
    ("请帮我开通合同资料权限", "document_request"),
    ("我想调阅财务模板文件", "document_request"),
    ("我要获取报销制度文档原文", "document_request"),
    ("帮我申请人事资料", "document_request"),
    ("需要访问内部制度文档", "document_request"),
    ("我想查看请假制度文件", "document_request"),
    ("请申请项目流程手册", "document_request"),
    ("我要获取行政管理模板", "document_request"),
    ("出差报销怎么弄", "reimbursement"),
    ("报销流程是什么", "reimbursement"),
    ("发票报销要准备什么", "reimbursement"),
    ("差旅费用报销入口在哪", "reimbursement"),
    ("报账步骤有哪些", "reimbursement"),
    ("报销单怎么填", "reimbursement"),
    ("费用报销规则是什么", "reimbursement"),
    ("出差回来如何报销", "reimbursement"),
    ("发票丢了还能报销吗", "reimbursement"),
    ("报销审批多久", "reimbursement"),
    ("我要请假", "leave"),
    ("病假怎么申请", "leave"),
    ("年假规则是什么", "leave"),
    ("事假流程入口在哪", "leave"),
    ("调休怎么走", "leave"),
    ("婚假需要什么材料", "leave"),
    ("产假天数是多少", "leave"),
    ("请假审批多久", "leave"),
    ("休假需要提前几天", "leave"),
    ("请假单在哪里提交", "leave"),
    ("XX定影器多少钱", "fixed_quote"),
    ("A型号配件报价是多少", "fixed_quote"),
    ("这个耗材价格多少", "fixed_quote"),
    ("标准单价怎么查", "fixed_quote"),
    ("价目表有更新吗", "fixed_quote"),
    ("B配件报价", "fixed_quote"),
    ("C组件多少钱一套", "fixed_quote"),
    ("这个型号价格", "fixed_quote"),
    ("是否有固定报价", "fixed_quote"),
    ("报价单里的价格是多少", "fixed_quote"),
    ("你好", "other"),
    ("在吗", "other"),
    ("今天天气怎么样", "other"),
    ("讲个笑话", "other"),
    ("帮我写一首诗", "other"),
    ("你是谁", "other"),
    ("谢谢", "other"),
    ("我想聊电影", "other"),
    ("午饭吃什么", "other"),
    ("你会画图吗", "other"),
]


class IntentClassifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.classifier = IntentClassifier()

    def test_priority_rules_for_overlapping_keywords(self) -> None:
        self.assertEqual("document_request", self.classifier.classify("我要申请报销制度文件").intent)
        self.assertEqual("reimbursement", self.classifier.classify("报销流程怎么走").intent)
        self.assertEqual("leave", self.classifier.classify("请假流程怎么走").intent)
        self.assertEqual("fixed_quote", self.classifier.classify("定影器报价流程").intent)

    def test_confidence_is_reported_in_valid_range(self) -> None:
        outcome = self.classifier.classify("我要申请采购制度文件")
        self.assertGreaterEqual(outcome.confidence, 0.0)
        self.assertLessEqual(outcome.confidence, 1.0)

    def test_offline_dataset_meets_sample_and_accuracy_threshold(self) -> None:
        self.assertGreaterEqual(len(LABELED_SAMPLES), 60)
        per_intent = Counter(intent for _, intent in LABELED_SAMPLES)
        for intent in ("policy_process", "document_request", "reimbursement", "leave", "fixed_quote", "other"):
            self.assertGreaterEqual(per_intent[intent], 10, msg=f"insufficient samples for {intent}")

        predictions = [self.classifier.classify(text).intent for text, _ in LABELED_SAMPLES]
        expected = [intent for _, intent in LABELED_SAMPLES]
        correct = sum(1 for pred, exp in zip(predictions, expected, strict=True) if pred == exp)
        accuracy = correct / len(LABELED_SAMPLES)
        self.assertGreaterEqual(accuracy, 0.85, msg=f"offline accuracy below threshold: {accuracy:.2%}")


if __name__ == "__main__":
    unittest.main()
