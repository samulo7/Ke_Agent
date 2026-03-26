# 钉钉企业内部 Agent 技术栈方案

## 1. 结论
整体方向没有大问题，但为了做到“最简单且最健壮”，建议做两处收敛：

1. `卡片消息默认用互动卡片，不把 ActionCard 作为主方案`
   - 如果只是静态结果展示，`ActionCard` 可以继续作为兜底消息类型。
   - 只要涉及按钮回调、状态更新、申请单确认，默认使用 `互动卡片`，因为钉钉 Stream 模式明确支持卡片回调。
2. `向量库首版默认用 Qdrant 单机，不用 Milvus Lite 作为生产默认`
   - `Milvus Lite` 更适合本地验证或极轻量场景。
   - 首版生产环境更推荐 `Qdrant` 单机 Docker 部署；后续规模上来再升级到 `Milvus Standalone / Distributed` 或托管方案。

在这个前提下，推荐的 MVP 生产技术栈如下：

| 层级 | 默认选型 | 备注 |
|---|---|---|
| 接入层 | 钉钉 Stream SDK + 钉钉 OpenAPI | 机器人消息、卡片回调、身份权限查询 |
| 消息展示 | 文本消息 + 互动卡片 | ActionCard 仅作静态兜底 |
| 应用层 | Python 3.11 + FastAPI | 单语言、异步、维护成本低 |
| AI 推理层 | `qwen-plus` + `text-embedding-v4` | 中文稳定，成本可控，向量模型选官方更新版本 |
| RAG 编排 | `LlamaIndex` | 仅用于 RAG pipeline，不做深度绑定 |
| 向量检索 | `Qdrant` 单机 | 首版足够轻，迁移成本低 |
| 结构化数据 | `MySQL 8` | 用户、权限、FAQ、日志元数据 |
| 缓存 / 会话 | `Redis 7` | 多轮上下文、热点缓存 |
| 部署 | Docker + 阿里云 ECS | 首版单机部署 |
| 最小生产规格 | 1 台 ECS `4C8G` | 文本型内部工具足够起步 |

## 2. 分层设计与选型理由

### 2.1 接入层
**默认选型**
- 钉钉 Stream SDK（Python）
- 钉钉 OpenAPI
- 互动卡片

**选型理由**
- `Stream 模式` 不需要公网 IP、域名、证书和 Webhook 签名校验，对 20-40 人公司的运维最友好。
- Stream 模式适合机器人收消息、事件订阅、卡片回调这类“钉钉调用你的服务”的场景。
- `OpenAPI` 负责补充组织信息，例如用户身份、部门、权限等，作为权限控制和个性化回复的基础。
- `互动卡片` 比 `ActionCard` 更适合申请单草稿确认、按钮回调、状态更新等交互型场景。

**设计原则**
- 机器人接收统一走 Stream。
- 所有需要“点击后回调后端”的消息，统一走互动卡片。
- 普通问答结果优先用文本消息；结构化结果再升级为卡片。

### 2.2 应用层
**默认选型**
- Python 3.11
- FastAPI
- Pydantic v2
- SQLAlchemy 2 + Alembic
- httpx

**选型理由**
- `FastAPI` 对异步 I/O、Webhook/回调、接口定义和 JSON API 非常合适。
- Python 与 LLM/RAG 生态兼容最好，减少语言切换和桥接代码。
- 小团队一套语言维护成本最低。
- `SQLAlchemy + Alembic` 足够成熟，适合做 FAQ、权限、日志、文档元数据这类结构化数据管理。

**设计原则**
- `Agent 编排逻辑自己写`，不要把核心业务流程全绑死在框架里。
- 服务端只保留三类核心模块：
  - 消息接入与响应
  - Agent 编排与权限判断
  - RAG 检索与知识命中

### 2.3 AI 推理层
**默认选型**
- 主模型：`qwen-plus`
- 可选低成本分类模型：`qwen-turbo`
- 向量模型：`text-embedding-v4`

**选型理由**
- `qwen-plus` 在中文问答、归纳、申请单生成、规则解释这类场景上更稳，成本也显著低于更高规格模型，适合内部工具长期使用。
- `qwen-turbo` 可以作为后续优化项，用于意图分类、简单改写、低价值问题分流，降低成本。
- `text-embedding-v4` 是当前阿里云官方更新的文本向量模型，官方说明其相较 `text-embedding-v3` 在语种支持、代码片段向量化效果和向量维度自定义方面有升级，更适合新项目默认选型。
- 若已有历史索引或兼容性要求，`text-embedding-v3` 仍可作为保守回退方案。

**设计原则**
- 模型名必须配置化，不要硬编码。
- 首版只做三种 AI 能力：
  - 意图识别
  - RAG 问答生成
  - 结构化内容生成
- 不做“复杂自主 Agent”，先把检索问答和流程辅助做稳。

### 2.4 RAG 编排层
**默认选型**
- `LlamaIndex`

**为什么不是 LangChain**
- 当前 MVP 的核心是“文档导入、分片、索引、检索、答案生成”。
- 对这个问题，`LlamaIndex` 的抽象更贴近 RAG 本身，集成更直接。
- `LangChain` 适合更复杂的工具调用、多工具 Agent 编排；首版如果只是做知识问答，会引入不必要复杂度。

**使用边界**
- 只使用它做：
  - 文档切分
  - 向量写入
  - 检索器封装
  - 基础问答链
- 不让框架接管你的权限逻辑、业务流程和会话状态。

### 2.5 知识层
**默认选型**
- 向量库：`Qdrant`
- 分片策略：`512 token chunk + 128 token overlap`
- Embedding 维度：默认使用 `1024`

**选型理由**
- 对 20-40 人公司来说，文档量、FAQ 量和价目表规模都不大，`Qdrant` 单机部署足够。
- `Qdrant` 本地 Docker 启动和迁移都很轻，适合首版快速上线。
- `Milvus Lite` 适合本地验证，但不建议作为首版 Linux 生产默认。
- `512 / 128` 的中文分片策略在制度、流程、FAQ 这类文档上通常更平衡，既能保留语义完整度，也不会让 chunk 过长。

**后续升级路径**
- 文档规模或并发明显增长：
  - 先从 `Qdrant 单机` 升到 `Qdrant Cloud / 多节点`
  - 或迁移到 `Milvus Standalone / Distributed`
- 如果后续要做更复杂检索：
  - 增加 metadata filter
  - 增加 hybrid search
  - 增加 rerank

### 2.6 数据层
**默认选型**
- `MySQL 8`
- `Redis 7`

**MySQL 用途**
- 用户基础信息缓存
- 部门与权限映射
- FAQ 结构化数据
- 文档元数据
- 问答日志索引
- 未命中问题记录
- 管理后台配置

**Redis 用途**
- 多轮对话上下文窗口
- 高频问题缓存
- 短期查询结果缓存
- 去重和幂等控制

**默认配置**
- 多轮上下文 TTL：`30 分钟`
- 高频问题缓存 TTL：`1-6 小时`
- 幂等键 TTL：`5-10 分钟`

**选型理由**
- `MySQL` 适合当前结构化数据模型，团队普遍更熟。
- `Redis` 可直接降低大模型重复调用次数，尤其适合内部高频重复问答。

## 3. 最小生产部署方案

### 3.1 部署形态
**默认选型**
- Docker Compose
- 阿里云 ECS

**推荐最小生产拓扑**
- 1 台 `ECS 4C8G`
  - `app`：FastAPI 服务
  - `worker`：文档导入 / 重建索引任务
  - `qdrant`：向量库
  - `redis`：会话与缓存
- `MySQL`
  - 优先：阿里云 `RDS MySQL`
  - 次选：与应用同机容器化部署

### 3.2 为什么 MySQL 优先放 RDS
- 这是“最简单但最健壮”里最值的一点。
- 即使应用仍部署在单台 ECS，把 MySQL 放到 RDS 也能显著降低：
  - 数据损坏风险
  - ECS 宕机导致的全量不可恢复风险
  - 备份恢复复杂度

### 3.3 初期资源建议
- ECS：`4C8G`
- 系统盘：`100GB SSD`
- Qdrant 数据盘：`按文档量预留 20-50GB`
- Redis：单实例即可
- RDS：入门规格即可

### 3.4 不建议的首版方案
- 一开始上 ACK / Kubernetes
- 一开始做多服务拆分
- 一开始做多模型路由平台
- 一开始接太多业务系统

## 4. 默认运行参数

### 4.1 模型参数
- `chat_model = qwen-plus`
- `intent_model = qwen-plus`
  - 如后续控制成本，可切换到 `qwen-turbo`
- `embedding_model = text-embedding-v4`
- embedding 维度：默认 `1024`

### 4.2 检索参数
- chunk size：`512 token`
- overlap：`128 token`
- top_k：`5`
- 命中阈值：首版建议先通过离线验证调优，不在业务代码硬写死

### 4.3 会话参数
- 会话上下文保留：`30 分钟`
- 单轮对话最大保留消息数：`10-20 条`
- 命中缓存：按问题标准化后做 key

## 5. 组件边界

### 5.1 可以晚一点再加的能力
- OCR / 文件抽取服务
- rerank 模型
- hybrid search
- 后台运营大屏
- 审批流自动提交
- 浏览器插件场景

### 5.2 一开始就要有的能力
- 身份识别
- 权限过滤
- 文档 / FAQ 检索
- 问答日志
- 未命中统计
- 知识更新流程

## 6. 推荐目录结构

```text
app/
  api/
  core/
  agents/
  rag/
  integrations/
    dingtalk/
    dashscope/
  models/
  repos/
  services/
  workers/
  schemas/
infra/
  docker/
  scripts/
docs/
```

## 7. 最终建议版本

### 7.1 MVP 默认技术栈
- 接入层：钉钉 Stream SDK + OpenAPI
- 卡片：互动卡片为主，ActionCard 为辅
- 后端：Python 3.11 + FastAPI
- 模型：`qwen-plus`
- 向量：`text-embedding-v4`
- RAG：`LlamaIndex`
- 向量库：`Qdrant`
- 数据库：`MySQL 8`
- 缓存：`Redis 7`
- 部署：Docker + 阿里云 ECS

### 7.2 升级路线
- 成本优化：`qwen-plus -> qwen-turbo` 分流部分请求
- 检索优化：加入 rerank / hybrid search
- 数据规模增长：`Qdrant -> Qdrant Cloud / Milvus`
- 可用性增强：`单 ECS -> ECS + RDS + SLB / ACK`

## 8. 官方参考
- 钉钉 Stream 模式概述：https://opensource.dingtalk.com/developerpedia/docs/learn/stream/overview/
- 钉钉 Stream 教程概述：https://opensource.dingtalk.com/developerpedia/docs/explore/tutorials/stream/overview/
- 钉钉 Stream 协议补充：https://opensource.dingtalk.com/developerpedia/docs/learn/stream/protocol/
- 钉钉互动卡片概述：https://opensource.dingtalk.com/developerpedia/docs/learn/card/intro
- 钉钉权限概述：https://opensource.dingtalk.com/developerpedia/docs/learn/permission/intro/overview
- 阿里云 Qwen API 参考：https://help.aliyun.com/zh/model-studio/developer-reference/use-qwen-by-calling-api
- 阿里云文本向量 API 参考：https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api
- 阿里云知识库向量模型说明：https://help.aliyun.com/zh/model-studio/rag-knowledge-base
- Qdrant Local Quickstart：https://qdrant.tech/documentation/quick-start/
- Qdrant 文档主页：https://qdrant.tech/documentation/
- Milvus Lite 官方说明：https://milvus.io/docs/milvus_lite.md
