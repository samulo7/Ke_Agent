# KB-OPS-01 后端第一批任务清单

## 1. 文档目的

本文档用于把 `KB-OPS-01` 进一步拆成**后端第一批可执行任务**，方便开发者直接按顺序开工。

目标不是一次性覆盖后台全部后端能力，而是先打通：

```text
录入知识
→ 钉钉对话验证
→ 发布到机器人
```

---

## 2. 本批任务目标

本批只保证两类知识跑通：
1. FAQ
2. 固定报价

输出结果应满足：
- 能新增 FAQ
- 能新增固定报价
- 能做钉钉对话验证
- 能单条发布到机器人
- 能记录验证与发布时间

---

## 3. 开发顺序（建议严格按顺序）

## BE-001 补齐知识主表字段

### 目标
让现有 `knowledge_docs` 可以承接后台运营流转。

### 要做的事
在现有 `knowledge_docs` 基础上补充：
- `knowledge_kind`
- `review_status`
- `created_by`
- `updated_by`
- `published_by`
- `published_at`
- `last_validated_at`

### 依赖
- 当前已有 `knowledge_docs` 基础表

### 完成标准
- 本地 schema 可创建/迁移成功
- FAQ 和固定报价都能在主表中存储

---

## BE-002 新建固定报价扩展表

### 目标
支持结构化报价，不把所有报价字段塞进主表。

### 要做的事
创建 `knowledge_quote_fields`，至少包含：
- `doc_id`
- `quote_item_name`
- `spec_model`
- `quote_category`
- `price_amount`
- `unit`
- `tax_included`
- `effective_date`
- `quote_version`
- `non_standard_action`
- `has_price_conflict`
- `price_conflict_note`

### 依赖
- `knowledge_docs` 已存在

### 完成标准
- 报价表和主表能通过 `doc_id` 关联
- 能插入一条 7788 黑色墨粉报价样本

---

## BE-003 新建验证与发布日志表

### 目标
支持验证和发布可追溯。

### 要做的事
创建：
- `knowledge_validation_runs`
- `knowledge_publish_logs`

### `knowledge_validation_runs` 最少字段
- `validation_id`
- `doc_id`
- `question`
- `reply_preview_json`
- `validation_result`
- `validated_by`
- `validated_at`

### `knowledge_publish_logs` 最少字段
- `publish_log_id`
- `doc_id`
- `publish_action`
- `publish_status`
- `published_by`
- `published_at`

### 完成标准
- 能记录一次验证日志
- 能记录一次发布日志

---

## BE-004 当前用户权限接口

### 目标
让前端知道当前角色能看什么、改什么。

### 接口
- `GET /admin/me/permissions`

### 最少返回
- `role_code`
- 菜单权限
- 知识类型权限
- 动作权限

### V1 固定规则
- 人事：FAQ / 员工手册 / 受控文档
- 商务：固定报价
- 财务：只读验证
- 管理员：全权限

### 完成标准
- 前端可直接依赖此接口做菜单显隐和按钮显隐

---

## BE-005 知识列表接口

### 目标
支撑“机器人知识管理页”。

### 接口
- `GET /admin/knowledge`

### 本批先支持
- 按 `knowledge_kind` 筛选
- 按 `review_status` 筛选
- 按关键字搜索

### 返回最少字段
- `doc_id`
- `title`
- `knowledge_kind`
- `review_status`
- `owner`
- `updated_at`
- `can_view`
- `can_edit`
- `can_publish`
- `can_disable`

### 完成标准
- 可以列出 FAQ 和固定报价
- 权限字段随角色变化

---

## BE-006 知识详情接口

### 目标
支撑 FAQ/报价编辑页回显。

### 接口
- `GET /admin/knowledge/{doc_id}`

### FAQ 返回
- 主表字段

### 固定报价返回
- 主表字段 + `quote_fields`

### 完成标准
- 前端可拿此接口完成“编辑回填”

---

## BE-007 新增知识接口

### 目标
先支持两类知识新增。

### 接口
- `POST /admin/knowledge`

### 本批只支持类型
- `faq`
- `fixed_quote`

### 关键规则
#### FAQ
必填：
- `title`
- `summary`
- `applicability`
- `next_step`
- `owner`
- `updated_at`

#### 固定报价
必填：
- `title`
- `summary`
- `owner`
- `updated_at`
- `quote_fields.price_amount`
- `quote_fields.unit`
- `quote_fields.effective_date`
- `quote_fields.non_standard_action`

### 完成标准
- 可新增 FAQ
- 可新增报价
- 新增后状态为 `draft`

---

## BE-008 编辑知识接口

### 目标
支持 FAQ / 报价更新。

### 接口
- `PUT /admin/knowledge/{doc_id}`

### 关键规则
- 已发布知识修改后，建议回到 `draft` 或 `ready_to_publish`
- 按角色校验：
  - 人事不能改报价
  - 商务不能改 FAQ
  - 财务不能改任何知识

### 完成标准
- 编辑后数据可回显
- 权限校验生效

---

## BE-009 钉钉对话验证接口

### 目标
这是 `KB-OPS-01` 最关键接口。

### 接口
- `POST /admin/validation/dingtalk-preview`

### 输入
- `question`
- `doc_id`（可选）
- `role_context`（可选）
- `dept_context`（可选）

### 输出最少包含
- `matched_knowledge`
- `reply_preview.channel`
- `reply_preview.text`
- `reply_preview.interactive_card`
- `citations`
- `permission_decision`
- `validation_result`

### 完成标准
- FAQ 样本问题可返回文本型钉钉预览
- 固定报价问题可返回文本型钉钉预览
- 可写入 `knowledge_validation_runs`

---

## BE-010 发布前校验接口

### 目标
保证坏数据不会直接发布到机器人。

### 接口
- `POST /admin/publish/precheck`

### 本批校验项
#### FAQ
- 必填字段是否完整
- 是否至少验证过一次

#### 固定报价
- 价格是否存在
- 单位是否存在
- 生效日期是否存在
- 是否至少验证过一次
- 是否存在明显价格冲突

### 完成标准
- 对于缺字段或未验证知识，能返回明确错误原因

---

## BE-011 单条发布到机器人接口

### 目标
让单条知识正式生效。

### 接口
- `POST /admin/publish/{doc_id}`

### 动作
- 更新 `review_status=published`
- 写入 `published_by`
- 写入 `published_at`
- 写入发布日志

### 完成标准
- FAQ 可发布
- 固定报价可发布
- 发布后列表中状态可见

---

## BE-012 样本数据验证

### 目标
保证这批接口不是空壳。

### 必须跑通的 2 条样本
#### FAQ
- `试用期员工可以请假吗？`

#### 固定报价
- `7788 黑色墨粉多少钱？`

### 完成标准
- 两条都能：
  - 新增
  - 查询
  - 验证
  - 发布

---

# 4. 本批推荐实现顺序（真正开工顺序）

### 第一天建议顺序
1. `BE-001` 补主表字段
2. `BE-002` 建报价扩展表
3. `BE-003` 建验证/发布日志表
4. `BE-004` 起当前用户权限接口
5. `BE-005` 起知识列表接口
6. `BE-006` 起知识详情接口

### 第二天建议顺序
7. `BE-007` 起新增知识接口
8. `BE-008` 起编辑知识接口
9. `BE-009` 起钉钉对话验证接口
10. `BE-010` 起发布前校验接口
11. `BE-011` 起单条发布接口
12. `BE-012` 跑样本闭环

---

# 5. 本批不做

为了避免目标发散，本批不要同时去做：

- 制度文档上传解析
- 批量导入
- 待确认内容工作流
- 权限管理页接口
- 首页数据汇总
- 我的待办数据聚合
- 批量发布

这些统一放到 `KB-OPS-02` 之后。

---

# 6. 本批完成后的 progress 记录建议

记录到：
- [memory-bank/progress.md](memory-bank/progress.md)

### 建议 Step ID
- `KB-OPS-01`

### 建议 Notes 内容至少写明
- 落了哪些表 / 字段
- 起了哪些接口
- FAQ 样本是否跑通
- 固定报价样本是否跑通
- 验证接口是否写日志
- 发布接口是否写日志
- Skills 追溯

---

# 7. 一句话结论

`KB-OPS-01` 后端第一批最重要的不是接口数量，而是：

> **用最少的表和最少的接口，把 FAQ 与固定报价两条链路真的跑通。**
