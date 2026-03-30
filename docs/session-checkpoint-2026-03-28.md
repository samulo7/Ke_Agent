# 会话检查点（2026-03-28）

## 当前结论

1. 打字机效果闭环已完成。  
   - Node 独立链路已验证：`createAndDeliver -> streaming -> finalize(success)`。
   - 主链路（`run-dingtalk-stream.py`）已人工验证：开启流式开关后，钉钉端消息按增量逐步展示；关闭开关后退回普通文本回复。

2. 文档问答链路可用。  
   - `policy_process` 场景可返回知识答案与来源信息。
   - LLM 链路已在运行（可从日志 `llm_trace.intent/content` 观察是否命中或回退）。

3. 文件取出链路可用（审批闭环保留）。  
   - 命中文件请求后进入审批待处理。
   - 审批同意后按约定序列发送文件信息与链接。
   - 默认扫描件策略、扫描件缺失回退纸质版、拒绝兜底路径均在测试覆盖内。

## 本次关键验证

1. `python -m unittest tests.integrations.test_stream_runtime -v`（20/20 通过）
2. `python -m unittest tests.services.test_file_request_service -v`（6/6 通过）
3. `python -m unittest tests.services.test_single_chat_service -v`（21/21 通过）
4. 钉钉人工验证：
   - 流式开启：逐步增长显示。
   - 流式关闭：普通文本回复。

## 关机后恢复建议

1. 启动主链路：
   - `python .\infra\scripts\run-dingtalk-stream.py --env-file .env`
2. 如需继续观察回调原文：
   - 设置 `DINGTALK_STREAM_EVENT_DUMP_DIR`，并查看 `tmp/dingtalk-events/event-*.json`。
3. 如需继续独立验证卡片流式：
   - `node .\infra\scripts\run-dingtalk-card-streaming-demo.js`
