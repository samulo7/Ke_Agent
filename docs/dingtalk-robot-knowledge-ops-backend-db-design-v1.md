# 钉钉机器人知识运营后台数据库表设计（V1）

## 1. 文档目的

本文档用于给“钉钉机器人知识运营后台（V1）”提供数据库表设计方案，目标是：

- 能支撑知识录入、待确认、钉钉验证、发布到机器人
- 尽量复用当前项目已有知识库方向
- 不和现有 `knowledge_docs` / `doc_chunks` 设计冲突
- 给后续接口设计和前后端开发一个稳定的数据底座

---

## 2. 现有项目基础

当前项目已经有明确的知识库 SQL 方向：
- [app/repos/sql_knowledge_repository.py](app/repos/sql_knowledge_repository.py)

现有核心表为：
- `knowledge_docs`
- `doc_chunks`

当前字段设计已经覆盖：
- 文档标题
- 摘要
- 适用范围
- 下一步
- 来源
- 更新时间
- 状态
- 负责人
- 分类
- 关键词
- intents
- 权限范围
- 允许访问部门

见：
- [app/repos/sql_knowledge_repository.py:47-73](app/repos/sql_knowledge_repository.py#L47-L73)

因此，V1 数据库设计建议：

> **保留现有 `knowledge_docs` / `doc_chunks` 作为知识主底座，在其上补后台运营所需的辅助表。**

而不是推翻重来。

---

## 3. 设计原则

### 3.1 知识主表不拆散
`knowledge_docs` 继续作为所有正式知识项的统一元数据主表。

### 3.2 长文档与短知识分层
- 制度文档正文走 `doc_chunks`
- FAQ / 固定报价 / 受控文档默认以结构化单元为主

### 3.3 后台工作流单独落表
“上传、待确认、验证、发布”属于运营过程数据，不应直接污染知识主表。

### 3.4 角色权限独立建模
角色和后台权限不要散落在前端或配置文件里，应具备表结构支撑。

### 3.5 发布到机器人要可追溯
至少要能追踪：
- 谁发布的
- 何时发布的
- 发布了哪些知识
- 是否验证过

---

## 4. V1 核心表概览

V1 建议至少包含以下表：

### 知识主表层
1. `knowledge_docs`
2. `doc_chunks`
3. `knowledge_quote_fields`（固定报价扩展表）

### 运营过程层
4. `knowledge_uploads`
5. `knowledge_import_jobs`
6. `knowledge_review_items`
7. `knowledge_validation_runs`
8. `knowledge_publish_logs`

### 权限与角色层
9. `admin_roles`
10. `admin_role_permissions`
11. `admin_users`（如系统内需要单独映射）

---

# 5. 主表设计

---

## 5.1 `knowledge_docs`

### 作用
所有正式知识项的统一主表。

### 建议保留现有字段
- `doc_id`
- `source_type`
- `title`
- `summary`
- `applicability`
- `next_step`
- `source_uri`
- `updated_at`
- `status`
- `owner`
- `category`
- `version_tag`
- `keywords_csv`
- `intents_csv`
- `permission_scope`
- `permitted_depts_csv`

### 建议补充字段
- `knowledge_kind`
  - `policy_doc`
  - `faq`
  - `fixed_quote`
  - `restricted_doc`
- `review_status`
  - `draft`
  - `pending_review`
  - `ready_to_publish`
  - `published`
  - `disabled`
- `created_by`
- `updated_by`
- `published_by`
- `published_at`
- `last_validated_at`
- `is_deleted`

### 说明
- `status` 可以保留现有数据库兼容含义
- `review_status` 用来支撑后台工作流
- `knowledge_kind` 用来区分后台知识类型

---

## 5.2 `doc_chunks`

### 作用
长文档分片表，用于检索/向量化。

### 保留现有字段
- `chunk_id`
- `doc_id`
- `chunk_index`
- `chunk_text`
- `chunk_vector`

### 建议补充字段
- `chunk_summary`（可选）
- `token_count`（可选）
- `embedding_model`（可选）
- `embedding_version`（可选）

### 使用建议
- 制度文档写入本表
- FAQ/固定报价默认不强制拆 chunk
- 受控文档按权限策略决定是否写入 chunk

---

## 5.3 `knowledge_quote_fields`

### 作用
固定报价的结构化扩展表。

### 为什么单独建表
固定报价字段明显比 FAQ/制度文档多，强行塞进 `knowledge_docs` 会导致主表污染。

### 字段建议
- `doc_id`（FK -> `knowledge_docs.doc_id`）
- `quote_item_name`
- `quote_item_code`（可选）
- `spec_model`
- `quote_category`
- `price_amount`
- `price_currency`
- `unit`
- `tax_included`（bool）
- `effective_date`
- `expire_date`（可选）
- `quote_version`
- `non_standard_action`
- `source_note`
- `has_price_conflict`（bool）
- `price_conflict_note`（可选）

### 说明
- 只有 `knowledge_kind=fixed_quote` 的知识需要写这张表
- 未来报价相关回答应优先从这里拿结构化字段

---

# 6. 运营过程表设计

---

## 6.1 `knowledge_uploads`

### 作用
记录原始上传文件及解析前状态。

### 字段建议
- `upload_id`
- `file_name`
- `file_type`
- `file_size`
- `storage_uri`
- `uploaded_by`
- `uploaded_at`
- `source_kind`（document/faq_import/quote_import/restricted_doc）
- `parse_status`
  - `uploaded`
  - `parsing`
  - `parsed`
  - `failed`
- `parse_error`

### 说明
- 这张表承接“上传文件”动作
- 不等于正式知识表

---

## 6.2 `knowledge_import_jobs`

### 作用
记录批量导入任务。

### 适用场景
- FAQ Excel 导入
- 固定报价 Excel 导入
- 受控文档目录导入

### 字段建议
- `job_id`
- `upload_id`
- `job_type`
- `started_by`
- `started_at`
- `finished_at`
- `job_status`
  - `running`
  - `success`
  - `partial_success`
  - `failed`
- `success_count`
- `pending_review_count`
- `failed_count`
- `error_summary`

---

## 6.3 `knowledge_review_items`

### 作用
承接“待确认内容”。

### 字段建议
- `review_item_id`
- `source_upload_id`（可选）
- `source_job_id`（可选）
- `candidate_doc_id`（可选）
- `suggested_type`
- `risk_level`
- `suggested_title`
- `suggested_summary`
- `suggested_keywords_json`
- `suggested_questions_json`
- `review_status`
  - `pending`
  - `accepted`
  - `edited_accepted`
  - `split`
  - `dismissed`
- `reviewed_by`
- `reviewed_at`
- `risk_note`

### 说明
- 这是 V1 “待确认内容页”的核心表
- 不应与正式知识表混用

---

## 6.4 `knowledge_validation_runs`

### 作用
记录一次钉钉对话验证行为。

### 字段建议
- `validation_id`
- `doc_id`（可选，若指定知识验证）
- `question`
- `role_context`
- `dept_context`
- `matched_doc_ids_json`
- `reply_channel`（text/interactive_card）
- `reply_preview_json`
- `permission_decision`
- `validation_result`
  - `passed`
  - `failed`
  - `partial`
- `validated_by`
- `validated_at`
- `note`

### 说明
- 这是“钉钉对话验证页”的核心追溯表
- 发布前可据此判断是否已验证

---

## 6.5 `knowledge_publish_logs`

### 作用
记录知识发布到机器人的动作。

### 字段建议
- `publish_log_id`
- `doc_id`
- `published_by`
- `published_at`
- `publish_action`
  - `publish`
  - `disable`
  - `restore`
- `publish_status`
  - `success`
  - `failed`
- `validation_id`（可选）
- `note`

### 说明
- 用于发布追溯
- 可支持最近发布记录

---

# 7. 角色权限表设计

---

## 7.1 `admin_roles`

### 作用
维护后台角色。

### 建议字段
- `role_id`
- `role_code`
  - `hr`
  - `finance`
  - `business`
  - `admin`
- `role_name`
- `is_system_role`
- `status`

---

## 7.2 `admin_role_permissions`

### 作用
维护角色权限。

### 建议字段
- `permission_id`
- `role_id`
- `resource_type`
  - `menu`
  - `page`
  - `action`
  - `knowledge_kind`
- `resource_code`
- `can_view`
- `can_create`
- `can_edit`
- `can_publish`
- `can_disable`
- `can_import`
- `can_validate`

### 说明
- 可支持前端菜单显隐和按钮显隐
- 服务端也可据此校验

---

## 7.3 `admin_users`（可选）

### 作用
如果后台不完全复用主业务用户体系，可增加一张后台用户映射表。

### 建议字段
- `admin_user_id`
- `user_id`
- `display_name`
- `dept_id`
- `role_id`
- `status`
- `last_login_at`

### 说明
- 若直接复用现有用户系统，也可不单独建此表

---

# 8. 表关系建议

```text
knowledge_docs (1) ───── (N) doc_chunks
knowledge_docs (1) ───── (0..1) knowledge_quote_fields
knowledge_uploads (1) ───── (N) knowledge_import_jobs
knowledge_uploads (1) ───── (N) knowledge_review_items
knowledge_import_jobs (1) ───── (N) knowledge_review_items
knowledge_docs (1) ───── (N) knowledge_validation_runs
knowledge_docs (1) ───── (N) knowledge_publish_logs
admin_roles (1) ───── (N) admin_role_permissions
admin_roles (1) ───── (N) admin_users
```

---

# 9. 状态流转建议

## 9.1 知识状态流转

```text
草稿(draft)
  ↓
待确认(pending_review)
  ↓
待发布(ready_to_publish)
  ↓
已发布(published)
  ↓
已停用(disabled)
```

## 9.2 上传任务状态流转

```text
uploaded
  ↓
parsing
  ↓
parsed / failed
```

## 9.3 导入任务状态流转

```text
running
  ↓
success / partial_success / failed
```

---

# 10. 与现有代码的兼容建议

## 10.1 兼容 `KnowledgeRepository`
继续复用：
- [app/repos/knowledge_repository.py](app/repos/knowledge_repository.py)

后台运营修改的最终结果，仍然应沉淀到 `knowledge_docs` / `doc_chunks` 可读取的结构中。

## 10.2 兼容 `SQLKnowledgeRepository`
继续复用：
- [app/repos/sql_knowledge_repository.py](app/repos/sql_knowledge_repository.py)

建议优先：
- 在现有表基础上补字段
- 为固定报价新增扩展表
- 为运营过程新增独立表

而不是重写全部检索逻辑。

---

# 11. V1 必须先落地的表

如果按最小闭环优先，我建议第一批先做：

1. `knowledge_docs`（补字段）
2. `doc_chunks`（保留）
3. `knowledge_quote_fields`
4. `knowledge_uploads`
5. `knowledge_review_items`
6. `knowledge_validation_runs`
7. `knowledge_publish_logs`
8. `admin_roles`
9. `admin_role_permissions`

### 可以稍后再做
- `knowledge_import_jobs`
- `admin_users`（如果当前身份体系可复用）

---

# 12. 一句话结论

V1 数据库设计最重要的不是把所有后台需求一次性塞进知识主表，而是：

> **保住 `knowledge_docs/doc_chunks` 这条正式知识底座，再用上传、待确认、验证、发布、权限表把运营流程补完整。**
