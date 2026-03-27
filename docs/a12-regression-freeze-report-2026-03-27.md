# A-12 基础回归与冻结报告

Date: 2026-03-27  
Scope: 里程碑 A（A-01 ~ A-11）能力回归与冻结门禁确认

## 1. 执行摘要

- 关键路径通过率：100%（41/41）
- 全量测试通过率：100%（70/70）
- 缺陷分级：P0=0，P1=0，P2=0，P3=0（本轮回归未发现新增缺陷）
- 阻断项：0
- 冻结结论：满足 A-12 阈值，可冻结里程碑 A

## 2. 回归命令与结果

### 2.1 关键路径回归

Command:

```powershell
& "C:\Users\13635\AppData\Local\Programs\Python\Python312\python.exe" -m unittest `
  tests.api.test_health_observability `
  tests.api.test_dingtalk_single_chat `
  tests.api.test_identity_context `
  tests.integrations.test_stream_runtime `
  tests.services.test_user_context `
  tests.services.test_intent_classifier `
  tests.rag.test_knowledge_retriever `
  tests.rag.test_retrieval_evaluation `
  tests.repos.test_sql_knowledge_repository -v
```

Result:

- Ran 41 tests
- OK
- 通过率 100%

### 2.2 全量回归

Command:

```powershell
& "C:\Users\13635\AppData\Local\Programs\Python\Python312\python.exe" -m unittest discover -s tests -v
```

Result:

- Ran 70 tests
- OK
- 通过率 100%

## 3. 阈值核对（A-12）

| 阈值项 | 目标 | 本次结果 | 结论 |
| --- | --- | --- | --- |
| 关键路径通过率 | 100% | 100% (41/41) | PASS |
| 整体通过率 | >=95% | 100% (70/70) | PASS |
| P0/P1 缺陷 | 0 | 0/0 | PASS |

## 4. 缺陷与阻断

- 新增缺陷：无
- 已知阻断：无
- 冻结建议：冻结 A 阶段基线，不做跨步变更

## 5. 下一步约束

- 已完成 A-12 回归与冻结
- 按用户要求，进入 B-13 前需等待用户明确确认
