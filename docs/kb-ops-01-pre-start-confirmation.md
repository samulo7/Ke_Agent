# KB-OPS-01 开工前确认单

## 1. 文档目的

本文档用于在正式进入 `KB-OPS-01` 开发前，锁定最后几个会影响实现路径的关键口径。

只要本确认单中的项目确认无误，就不再继续扩方案，直接进入开发。

---

## 2. 需要确认的 4 个关键口径

## 确认项 1：后台登录与角色来源

### 推荐口径
**复用现有用户体系 / 钉钉身份体系**，后台角色通过业务侧或后台表做映射。

### 不推荐
再独立做一套完全脱离钉钉的后台用户体系。

### 原因
- 产品本来就是钉钉体系
- 后台也是给内部人使用
- 复用身份体系更自然，后续权限校验也更统一

### 本项目建议结论
- 默认按“复用现有身份体系”执行
- 若后续确有必要，再补独立后台账号模型

---

## 确认项 2：正式数据库目标

### 推荐口径
- **正式运行目标：PostgreSQL**
- **测试 / 本地 bootstrap：继续兼容 SQLite**

### 原因
- 当前项目技术基线就是 PostgreSQL
- 现有测试和 bootstrap 已使用 SQLite
- 这样既不影响实现方向，也不破坏现有测试便利性

### 本项目建议结论
- 表结构设计按 PostgreSQL 思维落
- 测试仍允许 SQLite 跑通最小 schema

---

## 确认项 3：`发布到机器人` 的语义

### 推荐口径
**发布到机器人 = 将知识状态切到 `published`，并进入机器人可检索/可回答范围。**

### 明确不包含
- 不做额外外部同步中心
- 不发额外钉钉消息
- 不额外推送第三方知识服务

### 原因
这是最符合当前项目现状、最容易落地、最不容易引入额外耦合的定义。

### 本项目建议结论
- 后端落地上，发布动作本质上是状态流转 + 发布日志记录

---

## 确认项 4：`KB-OPS-01` 的实现范围

### 推荐口径
`KB-OPS-01` 只做两类知识的最小闭环：

1. FAQ
2. 固定报价

### 明确后置
- 制度文档上传解析
- 受控文档完整链路
- 批量导入
- 待确认复杂工作流
- 首页大盘 / 我的待办
- 权限管理大页

### 原因
如果第一步同时做 4 类知识和全部运营能力，最容易把最小闭环打散。

### 本项目建议结论
- 先用 FAQ 和固定报价把链路跑通
- 后续再进入 `KB-OPS-02`

---

## 3. 当前建议的默认结论

如果没有新的反对意见，建议直接按以下默认值开工：

1. **后台身份来源**：复用现有用户/钉钉身份体系
2. **正式数据库目标**：PostgreSQL
3. **测试数据库**：SQLite 兼容
4. **发布到机器人**：状态切换为 `published` + 写发布日志
5. **`KB-OPS-01` 范围**：只做 FAQ + 固定报价闭环

---

## 4. 确认后立即进入的开发文档

本确认单通过后，直接按以下文档开工：

### 后端优先看
- [docs/kb-ops-01-backend-first-batch-tasklist.md](docs/kb-ops-01-backend-first-batch-tasklist.md)

### 前端同步看
- [docs/kb-ops-01-frontend-first-batch-tasklist.md](docs/kb-ops-01-frontend-first-batch-tasklist.md)

### 需要查接口细节时看
- [docs/dingtalk-robot-knowledge-ops-backend-api-schema-v1.md](docs/dingtalk-robot-knowledge-ops-backend-api-schema-v1.md)

### 需要查表结构时看
- [docs/dingtalk-robot-knowledge-ops-backend-db-design-v1.md](docs/dingtalk-robot-knowledge-ops-backend-db-design-v1.md)

---

## 5. 确认后的第一批实际动作

### 后端第一批
1. 补 `knowledge_docs` 字段
2. 新建 `knowledge_quote_fields`
3. 新建 `knowledge_validation_runs`
4. 新建 `knowledge_publish_logs`
5. 起 6 个核心接口

### 前端第一批
1. 起后台框架
2. 起知识管理页
3. 起 FAQ 录入页
4. 起固定报价录入页
5. 起钉钉对话验证页
6. 起发布页

---

## 6. 进度记录要求

从开工开始，后续进度统一记录到：
- [memory-bank/progress.md](memory-bank/progress.md)

### 建议 Step ID
- `KB-OPS-01`
- `KB-OPS-02`
- `KB-OPS-HF1`
- `OPS-CHECKPOINT`

---

## 7. 一句话结论

如果你接受本文档中的默认结论，那么：

> **现在就停止继续扩方案，直接按 `KB-OPS-01` 的前后端任务单开工。**
