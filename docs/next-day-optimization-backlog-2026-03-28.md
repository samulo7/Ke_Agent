# 次日改造清单（2026-03-28）

用途：记录“当前已跑通但尚未优化完成”的事项，供 2026-03-29 继续开发。

## 1. 当前状态（已闭环）

1. 打字机效果：
   - Node 独立脚本链路已通：`createAndDeliver -> streaming -> finalize`。
   - 主链路（`run-dingtalk-stream.py`）已人工验证：开启流式开关后可逐步增长，关闭后回退普通文本。
2. 文件申请/文件发放：
   - `file_request` 分流、审批待处理、审批同意后三条文本发放、拒绝兜底均可执行。
3. 知识库问答：
   - `policy_process` 等知识问答路径可返回答案、来源、权限结果。

## 2. 尚未优化完成（必须继续做）

### 2.1 打字机效果（需优化）

1. 触发策略还偏粗：
   - 目前主要按 `DINGTALK_AI_CARD_MIN_CHARS` 长度阈值触发，缺少“按意图/场景”的精细策略。
2. 流式参数未产品化：
   - `chunk_chars/interval_ms/min_chars` 仅基础配置，缺少按场景预设（知识问答、文件通知、审批提示）。
3. 观测项不足：
   - 目前缺少独立的“流式成功率/失败率/回退率/耗时分位”统计视图。
4. Markdown 约束未做专门守护：
   - 现实现是全量内容推送，尚未增加 Markdown 语法完整性校验器（避免复杂内容渲染抖动）。

### 2.2 文件申请/文件取出（需优化）

1. 首轮体验文案还不够“会话化”：
   - 当前主文案偏系统提示，尚未完全对齐你要求的“检索中 -> 命中 -> 发送中 -> 已发送”更自然连贯体验。
2. 审批人仍为写死策略：
   - 当前 `DEFAULT_APPROVER_USER_ID = 人事行政`，尚未做按文件类型/组织配置动态路由。
3. 文件源对接深度不足：
   - 现阶段依赖 file repository 数据与 `file_url`，还未与真实钉盘检索/权限元数据完全打通（若你要做生产化需补）。
4. 审批状态通知策略仍保守：
   - 当前是员工追问才提示待审批，无后台主动催办/超时升级策略。

### 2.3 知识库问答（需优化）

1. 回复风格仍可继续收敛：
   - 模板结构稳定，但在“更像同事口吻、减少模板痕迹”上还有可优化空间。
2. 时延优化未完成：
   - 目前问答在 LLM 命中时有明显耗时波动，尚未做更系统的 timeout/降级分层策略调优。
3. 非知识类问题策略可再明确：
   - 如天气等超范围问题，当前主要走 no-hit 兜底；若产品需要可增加外部工具能力或更清晰路由文案。

### 2.4 人事审批落地（待决策）

1. 口径固定为“人事审批”（不是“领导审批”）。
2. 当前实现仍为状态机 + 日志通知（`_NoopApprovalNotifier`），未真正触达人事侧。
3. 次日先定方案再开发：
   - 方案 A：给人事单聊投放审批卡（轻量、实现快，推荐）。
   - 方案 B：接入钉钉审批实例 API（标准审批流，接入成本更高）。

## 3. LLM 应用现状（明天改造前必须统一认知）

## 3.1 已接入 LLM 的位置

1. 意图识别（主链路）：
   - `app/services/llm_intent.py`
   - 由 `SingleChatService` 调用，失败/低置信回退规则分类器。
2. 内容生成（知识问答）：
   - `app/services/llm_content_generation.py`
   - `KnowledgeAnswerService` 在 `allow/summary_only/deny/no_hit` 场景调用，失败回退模板。
3. 草稿提取/润色（申请草稿）：
   - `app/services/llm_draft_generation.py`
4. 编排影子评估（不驱动执行）：
   - `app/services/llm_orchestrator_shadow.py`

## 3.2 仍由规则控制（按设计不交给 LLM）

1. 权限判定：`allow/summary_only/deny` 决策边界
2. 审批状态机：pending/delivered/rejected
3. 数据库读写
4. 敏感操作硬兜底

## 3.3 当前未充分利用 LLM 的区域（可评估）

1. 文件申请“会话文案优化”当前主要是模板文本，尚未引入受控 LLM 改写层。
2. 打字机内容分段策略当前为规则分段，尚未做语义级分段优化。

## 4. 次日建议执行顺序（2026-03-29）

1. 先做“文件申请会话化文案 + 多回复顺序优化”（低风险，高感知收益）。
2. 再做“知识问答时延与文案收敛优化”（保持权限边界不变）。
3. 最后做“打字机触发策略与观测增强”（避免影响主链路稳定性）。

## 5. 参考文件

1. `app/integrations/dingtalk/stream_runtime.py`
2. `app/services/file_request.py`
3. `app/services/single_chat.py`
4. `app/services/knowledge_answering.py`
5. `app/services/llm_intent.py`
6. `app/services/llm_content_generation.py`
7. `app/services/llm_draft_generation.py`
