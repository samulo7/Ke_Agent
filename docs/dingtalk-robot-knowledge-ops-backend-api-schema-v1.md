# 钉钉机器人知识运营后台接口入参/出参结构（V1）

## 1. 文档目的

本文档用于把“钉钉机器人知识运营后台（V1）”的后端接口进一步收紧为：

- request schema
- response schema
- 字段命名规则
- 列表返回结构
- 错误返回结构

为后续：
- schema 定义
- 接口开发
- 前后端联调
- 测试用例编写

提供统一依据。

---

## 2. 命名与风格约定

结合当前项目已有 schema 风格，建议后台接口统一采用：

### 2.1 命名风格
- **全部使用 snake_case**
- 与当前项目保持一致

参考：
- [app/schemas/dingtalk_chat.py:68-80](app/schemas/dingtalk_chat.py#L68-L80)

### 2.2 时间字段
- 统一使用 ISO8601 字符串
- 字段名建议：
  - `created_at`
  - `updated_at`
  - `published_at`
  - `validated_at`

### 2.3 ID 字段
- 统一使用字符串
- 示例：
  - `doc_id`
  - `upload_id`
  - `job_id`
  - `review_item_id`
  - `validation_id`
  - `publish_log_id`

### 2.4 布尔字段
- 统一用 `true/false`
- 示例：
  - `tax_included`
  - `allow_summary`
  - `has_price_conflict`
  - `can_edit`

### 2.5 列表字段
- 统一返回数组
- 命名建议：
  - `items`
  - `matched_doc_ids`
  - `suggested_questions`
  - `keywords`

---

## 3. 通用返回结构约定

## 3.1 成功返回（对象型）

```json
{
  "ok": true,
  "data": {}
}
```

## 3.2 成功返回（列表型）

```json
{
  "ok": true,
  "data": {
    "items": [],
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total": 86
    }
  }
}
```

## 3.3 失败返回

```json
{
  "ok": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "price_amount is required",
    "details": {
      "field": "price_amount"
    }
  }
}
```

### 推荐错误码
- `VALIDATION_ERROR`
- `NOT_FOUND`
- `FORBIDDEN`
- `CONFLICT`
- `IMPORT_PARSE_ERROR`
- `PRECHECK_FAILED`
- `INTERNAL_ERROR`

---

## 4. 通用字段结构

## 4.1 知识列表项 `knowledge_list_item`

```json
{
  "doc_id": "doc-001",
  "title": "员工手册",
  "knowledge_kind": "policy_doc",
  "source_type": "document",
  "review_status": "published",
  "owner": "hr",
  "department": "hr",
  "updated_at": "2026-04-17T09:12:00+08:00",
  "published_at": "2026-04-17T09:20:00+08:00",
  "last_validated_at": "2026-04-17T09:18:00+08:00",
  "hit_trend": "high",
  "can_view": true,
  "can_edit": true,
  "can_publish": true,
  "can_disable": true
}
```

---

## 4.2 知识详情 `knowledge_detail`

```json
{
  "doc_id": "doc-001",
  "title": "员工手册",
  "knowledge_kind": "policy_doc",
  "source_type": "document",
  "summary": "覆盖考勤、请假、福利等规则。",
  "applicability": "全体员工",
  "next_step": "如需入口说明，可继续提问。",
  "source_uri": "dingtalk://drive/employee-handbook-v3",
  "updated_at": "2026-04-17T09:12:00+08:00",
  "version_tag": "v3",
  "owner": "hr",
  "department": "hr",
  "review_status": "draft",
  "permission_scope": "public",
  "permitted_depts": [],
  "keywords": ["请假", "考勤", "福利", "试用期"],
  "intents": ["policy_process", "leave"],
  "created_by": "u-hr-01",
  "updated_by": "u-hr-01",
  "published_by": "",
  "published_at": "",
  "last_validated_at": ""
}
```

---

## 4.3 固定报价扩展 `quote_fields`

```json
{
  "quote_item_name": "黑色墨粉",
  "spec_model": "7788",
  "quote_category": "consumable",
  "price_amount": 1050,
  "price_currency": "CNY",
  "unit": "元/支",
  "tax_included": true,
  "effective_date": "2026-04-17",
  "expire_date": null,
  "quote_version": "V2026.04",
  "non_standard_action": "如数量或折扣条件不同，请联系商务确认。",
  "source_note": "来源于 2026Q2 商务标准价目表",
  "has_price_conflict": false,
  "price_conflict_note": ""
}
```

---

## 5. 接口分组结构

---

# 6. 首页与待办接口

## 6.1 `GET /admin/dashboard/summary`

### request
无 body，可带角色上下文（由登录态决定）

### response

```json
{
  "ok": true,
  "data": {
    "today_question_count": 186,
    "today_miss_count": 32,
    "pending_review_count": 18,
    "ready_to_publish_count": 7,
    "recent_publish_count": 4,
    "hot_questions": [
      {
        "question": "试用期员工可以请假吗？",
        "count": 21
      }
    ],
    "recent_activities": [
      {
        "activity_type": "publish",
        "title": "员工手册 FAQ（试用期）",
        "operator": "人事",
        "created_at": "2026-04-17T09:20:00+08:00"
      }
    ]
  }
}
```

---

## 6.2 `GET /admin/dashboard/todos`

### response

```json
{
  "ok": true,
  "data": {
    "items": [
      {
        "todo_type": "pending_review",
        "priority": "high",
        "title": "员工手册V3 待确认摘要",
        "target_id": "review-001",
        "target_path": "/admin/review/pending/review-001"
      }
    ]
  }
}
```

---

# 7. 机器人知识管理接口

## 7.1 `GET /admin/knowledge`

### query
- `knowledge_kind`
- `review_status`
- `owner`
- `department`
- `keyword`
- `page`
- `page_size`

### response

```json
{
  "ok": true,
  "data": {
    "items": [
      {
        "doc_id": "doc-001",
        "title": "员工手册",
        "knowledge_kind": "policy_doc",
        "source_type": "document",
        "review_status": "published",
        "owner": "hr",
        "department": "hr",
        "updated_at": "2026-04-17T09:12:00+08:00",
        "published_at": "2026-04-17T09:20:00+08:00",
        "last_validated_at": "2026-04-17T09:18:00+08:00",
        "hit_trend": "high",
        "can_view": true,
        "can_edit": true,
        "can_publish": true,
        "can_disable": true
      }
    ],
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total": 86
    }
  }
}
```

---

## 7.2 `GET /admin/knowledge/{doc_id}`

### response

```json
{
  "ok": true,
  "data": {
    "knowledge": {
      "doc_id": "doc-001",
      "title": "员工手册",
      "knowledge_kind": "policy_doc",
      "source_type": "document",
      "summary": "覆盖考勤、请假、福利等规则。",
      "applicability": "全体员工",
      "next_step": "如需入口说明，可继续提问。",
      "source_uri": "dingtalk://drive/employee-handbook-v3",
      "updated_at": "2026-04-17T09:12:00+08:00",
      "version_tag": "v3",
      "owner": "hr",
      "department": "hr",
      "review_status": "draft",
      "permission_scope": "public",
      "permitted_depts": [],
      "keywords": ["请假", "考勤"],
      "intents": ["policy_process", "leave"]
    },
    "quote_fields": null,
    "permissions": {
      "can_view": true,
      "can_edit": true,
      "can_publish": true,
      "can_disable": true
    }
  }
}
```

---

## 7.3 `POST /admin/knowledge`

### request

```json
{
  "knowledge_kind": "faq",
  "title": "试用期员工可以请假吗",
  "summary": "试用期员工可以按公司制度申请事假/病假。",
  "applicability": "全体员工",
  "next_step": "如为病假，请补充证明材料。",
  "source_uri": "employee-handbook-v3",
  "updated_at": "2026-04-17T09:12:00+08:00",
  "owner": "hr",
  "department": "hr",
  "permission_scope": "public",
  "permitted_depts": [],
  "keywords": ["试用期", "请假", "病假"],
  "intents": ["policy_process", "leave"]
}
```

### response

```json
{
  "ok": true,
  "data": {
    "doc_id": "doc-101",
    "review_status": "draft"
  }
}
```

---

## 7.4 `PUT /admin/knowledge/{doc_id}`

### request
与创建结构相同，允许部分字段更新

### response

```json
{
  "ok": true,
  "data": {
    "doc_id": "doc-101",
    "review_status": "draft",
    "updated_at": "2026-04-17T11:22:00+08:00"
  }
}
```

---

# 8. 上传与导入接口

## 8.1 `POST /admin/import/document/upload`

### request
`multipart/form-data`
- `file`
- `owner`
- `department`
- `permission_scope`

### response

```json
{
  "ok": true,
  "data": {
    "upload_id": "upload-001",
    "file_name": "员工手册V3.pdf",
    "parse_status": "uploaded"
  }
}
```

---

## 8.2 `GET /admin/import/document/{upload_id}/draft`

### response

```json
{
  "ok": true,
  "data": {
    "upload_id": "upload-001",
    "parse_status": "parsed",
    "suggested_title": "员工手册",
    "suggested_summary": "覆盖考勤、请假、福利等规则。",
    "suggested_keywords": ["请假", "考勤", "福利", "试用期"],
    "suggested_questions": [
      "试用期员工可以请假吗？",
      "员工手册在哪里看？"
    ]
  }
}
```

---

## 8.3 `POST /admin/import/faq`

### request
`multipart/form-data`
- `file`

### response

```json
{
  "ok": true,
  "data": {
    "job_id": "job-faq-001",
    "job_status": "partial_success",
    "success_count": 28,
    "pending_review_count": 4,
    "failed_count": 2,
    "errors": [
      {
        "row_no": 12,
        "field": "updated_at",
        "message": "updated_at is required"
      }
    ]
  }
}
```

---

## 8.4 `POST /admin/import/quote`

### request
`multipart/form-data`
- `file`

### response

```json
{
  "ok": true,
  "data": {
    "job_id": "job-quote-001",
    "job_status": "partial_success",
    "success_count": 19,
    "pending_review_count": 9,
    "failed_count": 3,
    "conflicts": [
      {
        "row_no": 8,
        "quote_item_name": "定影器组件",
        "message": "multiple prices detected"
      }
    ]
  }
}
```

---

# 9. 待确认接口

## 9.1 `GET /admin/review/pending`

### response

```json
{
  "ok": true,
  "data": {
    "items": [
      {
        "review_item_id": "review-001",
        "suggested_type": "fixed_quote",
        "risk_level": "medium",
        "suggested_title": "7788 黑色墨粉",
        "created_at": "2026-04-17T10:00:00+08:00"
      }
    ]
  }
}
```

---

## 9.2 `GET /admin/review/pending/{review_item_id}`

### response

```json
{
  "ok": true,
  "data": {
    "review_item_id": "review-001",
    "suggested_type": "fixed_quote",
    "risk_level": "medium",
    "suggested_title": "7788 黑色墨粉",
    "suggested_summary": "标准报价 1050 元/支（含税）",
    "suggested_keywords": ["7788", "黑色墨粉", "报价"],
    "suggested_questions": ["7788 黑色墨粉多少钱？"],
    "risk_note": "price conflict resolved manually required"
  }
}
```

---

## 9.3 `POST /admin/review/pending/{review_item_id}/accept`

### request

```json
{
  "review_note": "accepted as draft"
}
```

### response

```json
{
  "ok": true,
  "data": {
    "doc_id": "doc-301",
    "review_status": "draft"
  }
}
```

---

# 10. 钉钉对话验证接口

## 10.1 `POST /admin/validation/dingtalk-preview`

### request

```json
{
  "question": "试用期员工可以请假吗？",
  "doc_id": "doc-001",
  "role_context": "hr",
  "dept_context": "hr"
}
```

### response

```json
{
  "ok": true,
  "data": {
    "matched_knowledge": [
      {
        "doc_id": "doc-001",
        "title": "员工手册",
        "rank": 1
      }
    ],
    "reply_preview": {
      "channel": "text",
      "text": "结论：试用期员工可以按制度申请事假/病假。\n步骤：1. 先确认假种...",
      "interactive_card": null
    },
    "permission_decision": "allow",
    "citations": [
      {
        "source_id": "doc-001",
        "title": "员工手册",
        "source_uri": "employee-handbook-v3",
        "updated_at": "2026-04-17T09:12:00+08:00"
      }
    ],
    "next_step": "如需入口说明，可继续提问。",
    "validation_result": "passed"
  }
}
```

### 说明
`reply_preview` 建议尽量贴近现有 [app/schemas/dingtalk_chat.py](app/schemas/dingtalk_chat.py) 中 `AgentReply.to_dict()` 的结构。

---

## 10.2 `GET /admin/validation/suggested-questions/{doc_id}`

### response

```json
{
  "ok": true,
  "data": {
    "doc_id": "doc-001",
    "suggested_questions": [
      "试用期员工可以请假吗？",
      "病假需要什么材料？"
    ]
  }
}
```

---

# 11. 发布到机器人接口

## 11.1 `GET /admin/publish/pending`

### response

```json
{
  "ok": true,
  "data": {
    "items": [
      {
        "doc_id": "doc-001",
        "title": "员工手册",
        "knowledge_kind": "policy_doc",
        "owner": "hr",
        "validated": true,
        "last_validated_at": "2026-04-17T09:18:00+08:00",
        "review_status": "ready_to_publish"
      }
    ]
  }
}
```

---

## 11.2 `POST /admin/publish/precheck`

### request

```json
{
  "doc_ids": ["doc-001", "doc-101"]
}
```

### response

```json
{
  "ok": true,
  "data": {
    "passed": false,
    "results": [
      {
        "doc_id": "doc-001",
        "passed": true,
        "issues": []
      },
      {
        "doc_id": "doc-101",
        "passed": false,
        "issues": [
          {
            "code": "MISSING_VALIDATION",
            "message": "knowledge has not been validated in dingtalk preview"
          }
        ]
      }
    ]
  }
}
```

---

## 11.3 `POST /admin/publish/{doc_id}`

### request

```json
{
  "publish_note": "ready for robot"
}
```

### response

```json
{
  "ok": true,
  "data": {
    "doc_id": "doc-001",
    "review_status": "published",
    "published_at": "2026-04-17T12:10:00+08:00",
    "published_by": "u-hr-01"
  }
}
```

---

## 11.4 `POST /admin/publish/batch`

### request

```json
{
  "doc_ids": ["doc-001", "doc-002"],
  "publish_note": "batch publish"
}
```

### response

```json
{
  "ok": true,
  "data": {
    "success_count": 2,
    "failed_count": 0,
    "results": [
      {
        "doc_id": "doc-001",
        "status": "published"
      },
      {
        "doc_id": "doc-002",
        "status": "published"
      }
    ]
  }
}
```

---

# 12. 角色权限接口

## 12.1 `GET /admin/me/permissions`

### response

```json
{
  "ok": true,
  "data": {
    "user_id": "u-hr-01",
    "role_code": "hr",
    "menus": {
      "dashboard": true,
      "todos": true,
      "knowledge": true,
      "import": true,
      "review": true,
      "validation": true,
      "publish": true,
      "roles": false
    },
    "knowledge_permissions": {
      "policy_doc": {
        "can_view": true,
        "can_create": true,
        "can_edit": true,
        "can_publish": true,
        "can_disable": true
      },
      "faq": {
        "can_view": true,
        "can_create": true,
        "can_edit": true,
        "can_publish": true,
        "can_disable": true
      },
      "fixed_quote": {
        "can_view": true,
        "can_create": false,
        "can_edit": false,
        "can_publish": false,
        "can_disable": false
      },
      "restricted_doc": {
        "can_view": true,
        "can_create": true,
        "can_edit": true,
        "can_publish": true,
        "can_disable": true
      }
    }
  }
}
```

### 说明
这是前端菜单显隐与按钮显隐的核心接口。

---

## 12.2 `GET /admin/roles`

### response

```json
{
  "ok": true,
  "data": {
    "items": [
      {
        "role_id": "role-hr",
        "role_code": "hr",
        "role_name": "人事"
      },
      {
        "role_id": "role-admin",
        "role_code": "admin",
        "role_name": "管理员"
      }
    ]
  }
}
```

---

## 12.3 `PUT /admin/roles/{role_id}`

### request

```json
{
  "menus": {
    "dashboard": true,
    "todos": true,
    "knowledge": true,
    "import": true,
    "review": true,
    "validation": true,
    "publish": true,
    "roles": false
  },
  "knowledge_permissions": {
    "faq": {
      "can_view": true,
      "can_create": true,
      "can_edit": true,
      "can_publish": true,
      "can_disable": true
    }
  }
}
```

### response

```json
{
  "ok": true,
  "data": {
    "role_id": "role-hr",
    "updated_at": "2026-04-17T12:30:00+08:00"
  }
}
```

---

# 13. 错误返回规范

## 13.1 字段校验错误

```json
{
  "ok": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "effective_date is required",
    "details": {
      "field": "effective_date"
    }
  }
}
```

## 13.2 权限错误

```json
{
  "ok": false,
  "error": {
    "code": "FORBIDDEN",
    "message": "current role cannot edit fixed_quote",
    "details": {
      "role_code": "hr",
      "knowledge_kind": "fixed_quote"
    }
  }
}
```

## 13.3 资源不存在

```json
{
  "ok": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "knowledge not found",
    "details": {
      "doc_id": "doc-999"
    }
  }
}
```

## 13.4 发布前校验失败

```json
{
  "ok": false,
  "error": {
    "code": "PRECHECK_FAILED",
    "message": "knowledge is not ready to publish",
    "details": {
      "doc_id": "doc-101",
      "issues": ["MISSING_VALIDATION", "MISSING_CONTACT"]
    }
  }
}
```

---

# 14. V1 最应该先定死的 schema

如果按优先级，最先需要在代码里真正落 schema 的是：

1. `knowledge_list_item`
2. `knowledge_detail`
3. `quote_fields`
4. `dingtalk_preview_response`
5. `publish_precheck_result`
6. `current_user_permissions`
7. `review_item`

---

# 15. 一句话结论

后台接口结构最重要的是保持：

> **snake_case、一致的 envelope、清晰的列表结构、统一的错误格式，并让“钉钉对话验证”的返回结构足够贴近机器人真实回复。**
