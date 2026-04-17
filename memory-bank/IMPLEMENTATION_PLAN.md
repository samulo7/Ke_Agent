# AI 开发实施计划（MVP 到里程碑 B，决策完成版）

## 0. 计划目标与执行原则
本计划用于指导 AI 开发者分阶段交付钉钉企业内部 Agent MVP。
当前交付边界锁定为里程碑 A + 里程碑 B；里程碑 C 仅保留后置规划，不在本轮实施。

### 0.1 强制阅读与门禁
1. 任何编码前，必须完整阅读：
   - `memory-bank/architecture.md`
   - `memory-bank/PRD-钉钉企业内部Agent-MVP.md`
   - `memory-bank/IMPLEMENTATION_PLAN.md`
2. 如任一必需文件缺失，立即停止开发并补齐文档后继续。
3. 严格模块化，多文件分层实现；禁止单体巨文件。
4. 每完成一个重大里程碑，更新 `memory-bank/architecture.md`。
5. 每完成一个实施步骤，更新 `memory-bank/progress.md`（Step ID、状态、日期、验证结果）。

### 0.2 冲突优先级
文档冲突时按以下优先级执行：
`PRD > AGENTS > tech-stack > IMPLEMENTATION_PLAN`

### 0.3 默认技术基线
`Stream + OpenAPI + 互动卡片 + PostgreSQL + pgvector + Redis`

### 0.3.1 LLM 落地强约束（补充）
1. 本项目产品定位为“LLM 辅助企业 Agent”，`Qwen` 运行时接入为必须项，不得以纯规则引擎形态作为最终交付。
2. 当前 A/B 阶段已交付能力中，若存在“配置了 `QWEN_*` 但未实际调用模型”的情况，必须在后续补充里程碑中闭环修正。
3. LLM 接入后仍需保留强约束边界：
   - 权限决策仍以系统规则与数据权限为准，不可由模型越权判定；
   - 文件发放审批链路仍以业务流程为准，不可由模型绕过审批；
   - 模型故障时必须可降级到可执行兜底回复。

### 0.4 文档层接口契约
1. 输出通道契约：`text | interactive_card`
2. 权限决策契约：`allow | summary_only | deny`
3. 追溯字段契约：`trace_id`、`user_id`、`dept_id`、`source_ids`、`permission_decision`、`knowledge_version`、`answered_at`
4. 检索结果契约：
   - 可见文档：返回内容 + 来源
   - 受控文档：仅返回来源元数据 + 申请路径（不返回正文分片）

### 0.5 FR 到主步骤映射（唯一映射）
| FR | 主步骤 |
| --- | --- |
| FR-01 钉钉单聊接入 | A-05 |
| FR-02 身份识别与上下文 | A-06 |
| FR-03 意图识别 | A-07 |
| FR-04 文档与 FAQ 检索问答 | A-08 |
| FR-05 权限控制 | B-13 |
| FR-06 文档申请草稿生成 | B-14 |
| FR-07 流程规则说明与入口指引 | B-15 |
| FR-08 固定报价 FAQ | B-16 |
| FR-09 兜底与转人工 | A-11 |
| FR-10 知识维护与运营基础能力 | B-17 |

### 0.6 步骤级技能执行链（强制）
除文档门禁步骤外，所有实现步骤默认使用 `systematic-debugging` 作为失败时强制介入流程。每个步骤完成后，`progress.md` 对应行 `Notes` 必须包含 `Skills:` 记录。

| Step ID | Mandatory Skill Chain |
| --- | --- |
| A-01 | `brainstorming -> writing-plans -> verification-before-completion` |
| A-02 | `brainstorming -> writing-plans -> verification-before-completion` |
| A-03 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion` |
| A-04 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion` |
| A-05 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion` |
| A-06 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion` |
| A-07 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion` |
| A-08 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion` |
| A-09 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion` |
| A-10 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion` |
| A-11 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion` |
| A-12 | `brainstorming -> writing-plans -> verification-before-completion -> requesting-code-review` |
| B-13 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion -> requesting-code-review` |
| B-14 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion -> requesting-code-review` |
| B-15 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion -> requesting-code-review` |
| B-16 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion -> requesting-code-review` |
| B-17 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion -> requesting-code-review` |
| B-18 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion -> requesting-code-review` |
| B-19 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion -> requesting-code-review` |
| B-20 | `brainstorming -> writing-plans -> verification-before-completion -> requesting-code-review` |
| LLM-01 | `brainstorming -> writing-plans -> verification-before-completion` |
| LLM-02 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion -> requesting-code-review` |
| LLM-03 | `brainstorming -> writing-plans -> test-driven-development -> verification-before-completion -> requesting-code-review` |
| LLM-04 | `brainstorming -> writing-plans -> verification-before-completion -> requesting-code-review` |

### 0.7 技能追溯门禁升级检查点（GOV-SKILL-02）
为避免在里程碑后段遗忘升级，设定以下强制门禁：

1. 在记录 `B-17`、`B-18`、`B-19`、`B-20` 任一实施步骤前，`progress.md` 必须先出现 `GOV-SKILL-02` 且状态为 `DONE`。
2. `GOV-SKILL-02` 定义：将 `validate-memory-bank.ps1` 从“仅最新行强制 `Skills:`”升级为“`GOV-SKILL-02` 之后新增行全部强制 `Skills:`”。
3. 升级前保留低摩擦策略（仅最新行强制），不追溯历史记录；升级后按分界点向后强制。

---

## 1. 里程碑 A：基础功能（必须先完成）
范围：FR-01、FR-02、FR-03、FR-04、FR-09（最小可用版本）

### A-01 前置文档门禁
- 输入：`architecture.md`、PRD、本计划文档
- 输出：开发前检查清单（核心实体、流程边界、验收口径）
- 验证方式：评审问答抽查清单字段与边界
- 通过阈值：抽查关键项准确率 `100%`

### A-02 仓库结构初始化
- 输入：AGENTS 规定目录结构
- 输出：固定分层目录（`app/api`、`app/agents`、`app/rag`、`app/integrations`、`app/services`、`app/repos`、`app/schemas`、`tests`、`docs`、`infra`）
- 验证方式：目录审查与职责检查
- 通过阈值：必需目录存在率 `100%`，跨层混合职责文件 `0`

### A-03 配置与密钥规范
- 输入：钉钉、模型、PostgreSQL、pgvector、Redis、日志配置项
- 输出：配置清单（必填/可选/默认值/生产禁用项）与缺失报错规则
- 验证方式：配置缺失场景测试
- 通过阈值：关键配置缺失识别率 `100%`，错误提示可操作率 `100%`

### A-04 健康检查与可观测性骨架
- 输入：服务健康指标、日志字段规范、追踪规则
- 输出：健康检查接口、结构化日志、`trace_id` 贯穿机制
- 验证方式：正常请求与失败请求演练
- 通过阈值：`trace_id` 覆盖率 `100%`，错误定位到模块级成功率 `100%`

### A-05 钉钉单聊接入最小链路（FR-01）
- 输入：钉钉 Stream 事件、用户单聊文本
- 输出：单聊闭环响应，支持 `text` 与 `interactive_card`
- 验证方式：真实或沙箱账号测试（普通问候、业务问题、空输入）+ 卡片场景测试（流程指引、申请草稿）
- 通过阈值：关键用例通过率 `100%`，异常崩溃 `0`

### A-06 身份识别与用户上下文（FR-02）
- 输入：钉钉用户标识、OpenAPI 组织信息
- 输出：用户上下文（`user_id`、姓名、部门）并写入请求上下文
- 验证方式：跨部门同题测试 + 审计字段检查
- 通过阈值：身份字段完整率 `100%`，上下文错配率 `0%`

### A-07 意图识别最小版（FR-03）
- 输入：用户自然语言问题样本集
- 输出：六类意图路由：制度/流程查询、文档申请、报销指引、请假指引、固定报价、其他
- 验证方式：离线分类评测（每类不少于 10 条）
- 通过阈值：总体准确率 `>=85%`

### A-08 文档优先的统一检索问答（FR-04）
- 输入：内部文档知识源 + FAQ（补充）
- 输出：文档优先检索结果，返回答案 + 来源 + 适用说明
- 验证方式：FAQ 命中测试 + 文档问答端到端评测
- 通过阈值：FAQ Top3 命中率 `>=80%`，文档问答正确率 `>=80%`，引用有效率 `>=95%`

### A-09 回答模板统一编排
- 输入：检索候选结果与来源信息
- 输出：统一回答模板（结论、步骤、来源、下一步）
- 验证方式：FAQ 与文档双场景一致性检查
- 通过阈值：结构化模板符合率 `>=95%`

### A-10 同库分表与 SQL 权限过滤边界
- 输入：知识建模设计
- 输出：
  - `knowledge_docs`：业务属性（权限、分类、版本、责任人、状态等）
  - `doc_chunks`：分片文本、向量、`doc_id` 外键
  - 检索时通过 SQL JOIN 执行权限过滤
- 验证方式：结构审查 + 检索 SQL 用例验证
- 通过阈值：权限相关业务属性冗余到 `doc_chunks` 的字段数 `0`，权限过滤命中准确率 `100%`

### A-11 最小兜底与转人工（FR-09）
- 输入：未命中、低置信度、系统异常、问题模糊场景
- 输出：差异化兜底文案与转人工引导（问题模糊仅追问 `<=1` 轮）
- 验证方式：失败路径注入测试
- 通过阈值：空白回复 `0`，编造答案 `0`，兜底路径可执行率 `100%`

### A-12 基础回归与冻结
- 输入：A 阶段全链路测试清单
- 输出：回归报告（通过率、缺陷分级、阻断项）
- 验证方式：端到端回归执行
- 通过阈值：关键路径通过率 `100%`，整体通过率 `>=95%`，`P0/P1=0`

---

## 2. 里程碑 B：完整 MVP 功能
范围：FR-05、FR-06、FR-07、FR-08、FR-10 与非功能要求

### B-13 权限控制（FR-05）
- 输入：用户上下文、文档权限标签、访问规则
- 输出：三挡权限决策：`allow / summary_only / deny`
- 验证方式：同题多权限身份对照测试
- 通过阈值：正文越权泄露 `0`，策略命中准确率 `100%`

### B-14 文档申请草稿生成（FR-06）
- 输入：申请场景与用户补充字段
- 输出：结构化申请草稿（卡片格式）
- 验证方式：5 个申请场景测试（字段缺失、追问、超时）
- 通过阈值：
  - 缺字段追问轮次 `<=2`
  - 问题模糊追问轮次 `<=1`
  - 无响应 `5` 分钟触发兜底成功率 `100%`
  - 草稿字段完整率 `100%`

### B-15 流程规则说明与入口指引（FR-07）
- 输入：报销、请假流程知识
- 输出：规则摘要、材料清单、操作步骤、常见错误、入口路径
- 验证方式：首次提问 + 追问一致性测试
- 通过阈值：首次回复完整度 `>=95%`，入口准确率 `100%`，追问自相矛盾率 `0%`

### B-16 固定报价 FAQ（FR-08）
- 输入：标准价目表知识项
- 输出：报价结果（含适用范围与版本日期）或转人工提示
- 验证方式：范围内/范围外双路径测试
- 通过阈值：范围内报价准确率 `100%`，范围外臆测报价 `0`

### B-17 知识维护与运营基础能力（FR-10）
- 输入：FAQ 更新、文档更新、问答日志
- 输出：命中日志、未命中统计、高频问题统计
- 验证方式：一次完整知识更新演练 + 日志抽样
- 通过阈值：知识更新生效时间 `<=30 分钟`，统计记录完整率 `100%`

#### B-17 设计补充：正式知识源上传与维护方案（锁定）
1. 运营侧采用**统一上传入口**（知识库管理），但上传时必须先选择知识类型，不允许把所有内容按单一模板混传。
2. 正式知识源固定分为 4 类：
   - 制度/规范文档
   - 普通 FAQ
   - 固定报价 FAQ
   - 受控文档元数据
3. 存储策略固定为**统一知识底座 + 分类建模**：
   - 所有正式知识项统一落入 `knowledge_docs` 作为元数据主表；
   - 长文档类知识写入 `doc_chunks`，用于分片检索/向量检索；
   - FAQ 与固定报价 FAQ 默认作为单知识单元维护，不强制拆分为多 chunk；
   - 固定报价 FAQ 必须保留结构化价格字段（至少含型号/品名、价格、单位、是否含税、生效日期/版本、非标准项处理方式），不可仅把价格写进自由文本摘要。
4. 上传方式固定为**统一入口、分类模板、半自动入库**：
   - 制度/规范文档：文件上传 -> 文本抽取 -> 自动生成标题/摘要/关键词/意图建议 -> 运营确认 -> 入库/分片；
   - 普通 FAQ：表单录入或批量导入，必须显式维护问题、标准答案、适用范围、下一步、关键词、来源、更新时间；
   - 固定报价 FAQ：走强结构化表单或专用批量模板，不走“上传原始报价文档后直接生效”的黑盒模式；
   - 受控文档：优先维护元数据与申请路径，不以无权限正文上传替代权限设计。
5. 以下内容**不纳入正式知识文档上传**：
   - 报销/请假 `flow_guidance` 固定指引块
   - 审批流配置、卡片模板、按钮动作映射
   - 运行态业务数据（报销附件、审批状态、会话状态、回调载荷）
6. 检索兼容约束：
   - `source_type` 继续保持 `document | faq` 两类；
   - 具体业务分类通过 `category/knowledge_kind/intents` 承载，不为每个知识类型新增独立检索主链路；
   - 固定报价 FAQ 统一走 `fixed_quote -> knowledge_answer` 主链路。
7. B-17 交付时必须至少完成以下运营能力：
   - 单条新增/编辑/停用
   - 批量导入 FAQ/固定报价 FAQ
   - 文档上传后人工确认再发布
   - 命中/未命中日志留存
   - 知识版本可追溯（`knowledge_version` / `updated_at` / 来源入口）

#### B-17 V2 后台工作流与权限决策（锁定）
1. B-17 后台定位不是传统台账页，而是**知识运营工作流后台**；核心链路固定为：`上传/录入 -> 待确认 -> 测试问答 -> 发布`。
2. 后台一级菜单范围锁定为：
   - 首页
   - 我的待办
   - 知识管理
   - 上传与导入
   - 待确认内容
   - 测试问答
   - 发布中心
   - 权限与角色
3. `测试问答` 为 B-17 必做页，不再作为可有可无的辅助工具；所有知识类型在发布前都必须可预览机器人回答效果。
4. `上传与导入` 为统一入口页，必须承接：制度文档上传、FAQ 批量导入、固定报价批量导入、受控文档录入；避免让非技术同事在多个分散入口之间跳转。
5. `我的待办` 为业务角色首页核心模块，必须展示：待确认内容、待发布内容、导入失败、长期未更新知识，确保业务同事知道“今天该处理什么”。
6. `发布中心` 必须明确区分草稿/待确认/已发布/已停用状态，并显式提示“未测试不建议发布”。
7. V1 角色权限固定为：
   - 人事：全权维护员工手册与 FAQ；
   - 商务：全权维护固定报价；
   - 财务：默认只读与测试验证，不承担后台知识维护责任；
   - 管理员：拥有所有知识类型与所有操作权限。
8. 细化权限边界固定为：
   - 人事不能修改固定报价；
   - 只有商务或管理员可以新增/编辑/发布/停用固定报价；
   - 只有人事或管理员可以新增/编辑/发布/停用员工手册；
   - FAQ 只有人事或管理员可以操作；
   - 受控文档 V1 默认由人事或管理员维护。
9. 业务界面命名必须使用业务语言，不暴露 `chunk`、向量、检索参数、模型参数等技术细节；高级配置若存在，必须收纳在管理员视角或高级设置内。

### B-18 审计与可追溯性
- 输入：问答请求、检索结果、权限决策、输出结果
- 输出：可追溯审计记录（必须含契约字段）
- 验证方式：20 条记录抽样审计
- 通过阈值：关键字段完整率 `100%`

### B-19 性能与稳定性基线
- 输入：常规负载与依赖故障演练场景
- 输出：性能报告与降级恢复记录
- 验证方式：20 并发、5 分钟稳态 + 依赖故障注入
- 通过阈值：`P95<=10s`，`P99<=15s`，错误率 `<1%`，故障后可恢复率 `100%`

### B-20 完整 MVP 验收
- 输入：PRD 验收场景（制度查询、文档申请、报销、请假、报价、无权限、未命中）
- 输出：验收报告（通过/失败、证据、责任人、修复计划）
- 验证方式：按场景逐项验收
- 通过阈值：7 类关键场景通过率 `100%`，阻断缺陷 `0`

---

## 3. 关键实现约束（全程适用）

### 3.1 检索与权限约束
1. 检索策略：文档优先，FAQ 补充。
2. 受控内容不返回正文分片，仅返回元数据与申请路径。
3. 权限过滤优先在 SQL 层通过 JOIN 完成；应用层只做结果编排与兜底。

### 3.2 OpenAPI 优先策略
1. 用户身份与组织信息以 OpenAPI 为优先事实源。
2. 缓存策略：默认 TTL `5 分钟`。
3. OpenAPI 故障时，允许回退到 `30 分钟`内缓存数据并记录降级日志。

### 3.3 卡片使用边界
1. 里程碑 A 的互动卡片仅覆盖：流程指引、申请草稿。
2. 普通问答默认走文本消息。

---

## 4. 全程质量闸门（每一步都适用）
1. 需求一致性：实现必须映射到 PRD 条目。
2. 架构一致性：实现必须符合 AGENTS 与 architecture 约束。
3. 模块化：禁止单体巨文件与跨层耦合。
4. 回归约束：新功能不得破坏已通过能力。
5. 文档同步：每个重大里程碑后，更新 `architecture.md` 与 `progress.md`。
6. 文档门禁：里程碑交付前运行 `./infra/scripts/validate-memory-bank.ps1` 必须 PASS。

---

## 5. 里程碑 C（后置，不纳入当前交付）
二期扩展能力（如请假智能辅助增强、招聘筛选辅助）保留为后续规划。
当前版本不进入实现、不纳入 MVP 验收。

---

## 6. LLM 补充里程碑（必须完成）
说明：该里程碑用于修正“当前运行时仍以规则编排为主、尚未真实接入 Qwen”的实现偏差，属于 MVP 目标对齐必做项。

### LLM-01 现状校准与边界锁定
- 输入：PRD 的 LLM 定位、当前代码实现、配置项清单
- 输出：现状差异清单（已配置未接入、规则替代点位、降级边界）
- 验证方式：代码检索 + 架构评审
- 通过阈值：差异项覆盖率 `100%`，风险分级与改造优先级明确

### LLM-02 Qwen 推理接入（意图识别 + 答案生成）
- 输入：`QWEN_API_KEY`、模型名配置、现有 `single_chat` 编排入口
- 输出：
  - 统一 LLM 客户端（provider 适配层）
  - 意图识别支持模型判定（并保留规则回退）
  - 知识答案生成支持模型重写/归纳（并保留无命中不编造约束）
- 验证方式：单元测试 + 集成测试 + 人工对话抽样
- 通过阈值：主链路模型调用成功率 `>=99%`，模型超时/失败回退成功率 `100%`

### LLM-03 检索增强与可追溯一致性
- 输入：RAG 检索结果、来源引用、权限决策结果
- 输出：模型回答在“结论/来源/下一步”模板下的稳定输出，且保持 `source_ids`、`permission_decision`、`knowledge_version` 可追溯
- 验证方式：离线评测 + 端到端回归
- 通过阈值：引用有效率 `>=95%`，越权泄露 `0`，无来源编造 `0`

### LLM-04 DingTalk 人工验收闭环
- 输入：钉钉真实单聊场景（知识问答、文件申请、审批回执）
- 输出：LLM 版本验收报告（命中质量、降级表现、失败样例）
- 验证方式：DingTalkManual 实测 + 回归报告
- 通过阈值：关键场景通过率 `100%`，阻断缺陷 `0`
