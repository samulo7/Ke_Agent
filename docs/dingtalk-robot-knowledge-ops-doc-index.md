# 钉钉机器人知识运营后台文档索引

## 1. 文档目的

本文档用于收口当前已产出的“钉钉机器人知识运营后台”相关方案文档，帮助后续：

- 快速定位文档
- 明确每份文档给谁看
- 确定阅读顺序
- 支撑评审、设计、开发和落地

---

## 2. 当前文档清单

### 2.1 完整方案文档
- [docs/dingtalk-robot-knowledge-ops-backend-v1.md](docs/dingtalk-robot-knowledge-ops-backend-v1.md)

**用途**
- 后台整体方案总文档
- 适合做完整方案沉淀与长期维护

**适合谁看**
- 产品
- 设计
- 前端
- 后端
- 业务负责人

---

### 2.2 评审摘要版
- [docs/dingtalk-robot-knowledge-ops-backend-review-summary-v1.md](docs/dingtalk-robot-knowledge-ops-backend-review-summary-v1.md)

**用途**
- 一页式评审摘要
- 适合开会、过方案、快速共识

**适合谁看**
- 业务负责人
- 管理者
- 产品
- 评审参与人

---

### 2.3 后端接口任务单
- [docs/dingtalk-robot-knowledge-ops-backend-api-tasklist-v1.md](docs/dingtalk-robot-knowledge-ops-backend-api-tasklist-v1.md)

**用途**
- 后端接口级任务拆分
- 适合排期和分工

**适合谁看**
- 后端开发
- 产品
- 技术负责人

---

### 2.4 前端开发任务单
- [docs/dingtalk-robot-knowledge-ops-backend-frontend-tasklist-v1.md](docs/dingtalk-robot-knowledge-ops-backend-frontend-tasklist-v1.md)

**用途**
- 前端页面、组件、状态、权限显隐任务拆分
- 适合前端排期与实现

**适合谁看**
- 前端开发
- 设计
- 产品

---

### 2.5 数据库表设计
- [docs/dingtalk-robot-knowledge-ops-backend-db-design-v1.md](docs/dingtalk-robot-knowledge-ops-backend-db-design-v1.md)

**用途**
- 明确 V1 数据表设计
- 支撑接口开发与后端落地

**适合谁看**
- 后端开发
- 技术负责人
- 产品

---

### 2.6 接口入参 / 出参结构
- [docs/dingtalk-robot-knowledge-ops-backend-api-schema-v1.md](docs/dingtalk-robot-knowledge-ops-backend-api-schema-v1.md)

**用途**
- 统一 request / response schema
- 支撑联调与前后端对齐

**适合谁看**
- 前端开发
- 后端开发
- 测试
- 产品

---

## 3. 推荐阅读顺序

### 3.1 如果你是产品
建议顺序：
1. [评审摘要版](docs/dingtalk-robot-knowledge-ops-backend-review-summary-v1.md)
2. [完整方案文档](docs/dingtalk-robot-knowledge-ops-backend-v1.md)
3. [前端开发任务单](docs/dingtalk-robot-knowledge-ops-backend-frontend-tasklist-v1.md)
4. [后端接口任务单](docs/dingtalk-robot-knowledge-ops-backend-api-tasklist-v1.md)

### 3.2 如果你是前端
建议顺序：
1. [完整方案文档](docs/dingtalk-robot-knowledge-ops-backend-v1.md)
2. [前端开发任务单](docs/dingtalk-robot-knowledge-ops-backend-frontend-tasklist-v1.md)
3. [接口入参 / 出参结构](docs/dingtalk-robot-knowledge-ops-backend-api-schema-v1.md)

### 3.3 如果你是后端
建议顺序：
1. [完整方案文档](docs/dingtalk-robot-knowledge-ops-backend-v1.md)
2. [数据库表设计](docs/dingtalk-robot-knowledge-ops-backend-db-design-v1.md)
3. [后端接口任务单](docs/dingtalk-robot-knowledge-ops-backend-api-tasklist-v1.md)
4. [接口入参 / 出参结构](docs/dingtalk-robot-knowledge-ops-backend-api-schema-v1.md)

### 3.4 如果你是业务负责人
建议顺序：
1. [评审摘要版](docs/dingtalk-robot-knowledge-ops-backend-review-summary-v1.md)
2. [完整方案文档](docs/dingtalk-robot-knowledge-ops-backend-v1.md)

---

## 4. 实施阶段如何使用这些文档

## 4.1 方案评审阶段
优先看：
- [评审摘要版](docs/dingtalk-robot-knowledge-ops-backend-review-summary-v1.md)
- [完整方案文档](docs/dingtalk-robot-knowledge-ops-backend-v1.md)

## 4.2 设计出图阶段
优先看：
- [完整方案文档](docs/dingtalk-robot-knowledge-ops-backend-v1.md)
- [前端开发任务单](docs/dingtalk-robot-knowledge-ops-backend-frontend-tasklist-v1.md)

## 4.3 后端设计与开发阶段
优先看：
- [数据库表设计](docs/dingtalk-robot-knowledge-ops-backend-db-design-v1.md)
- [后端接口任务单](docs/dingtalk-robot-knowledge-ops-backend-api-tasklist-v1.md)
- [接口入参 / 出参结构](docs/dingtalk-robot-knowledge-ops-backend-api-schema-v1.md)

## 4.4 前后端联调阶段
优先看：
- [前端开发任务单](docs/dingtalk-robot-knowledge-ops-backend-frontend-tasklist-v1.md)
- [接口入参 / 出参结构](docs/dingtalk-robot-knowledge-ops-backend-api-schema-v1.md)
- [后端接口任务单](docs/dingtalk-robot-knowledge-ops-backend-api-tasklist-v1.md)

---

## 5. 当前这套文档已经覆盖了什么

当前已基本覆盖：
- 产品定位
- 页面方案
- 角色权限
- 后台交互主链路
- 前端任务拆分
- 后端任务拆分
- 数据库设计
- API schema 设计

换句话说：

> **“钉钉机器人知识运营后台 V1”已经具备从方案到落地的完整文档闭环。**

---

## 6. 后续新增文档建议

如果继续往下推进，建议下一批新增文档按这个顺序补：

1. 测试用例文档
2. 页面交互状态清单
3. 上线计划/实施计划
4. 评审结论记录
5. 版本迭代路线图

---

## 7. 一句话使用建议

如果你现在要快速推进：

- 开会先发： [评审摘要版](docs/dingtalk-robot-knowledge-ops-backend-review-summary-v1.md)
- 产品对齐看： [完整方案文档](docs/dingtalk-robot-knowledge-ops-backend-v1.md)
- 后端开工看： [数据库表设计](docs/dingtalk-robot-knowledge-ops-backend-db-design-v1.md) + [后端接口任务单](docs/dingtalk-robot-knowledge-ops-backend-api-tasklist-v1.md)
- 前端开工看： [前端开发任务单](docs/dingtalk-robot-knowledge-ops-backend-frontend-tasklist-v1.md) + [接口入参 / 出参结构](docs/dingtalk-robot-knowledge-ops-backend-api-schema-v1.md)
