# KB-OPS-01 开发启动单

## 1. 启动单目的

本文档用于作为“钉钉机器人知识运营后台（V1）”的第一个实际实施步骤启动单。

`KB-OPS-01` 的目标不是做完后台全部能力，而是先打通一条最小闭环：

```text
录入知识
→ 钉钉对话验证
→ 发布到机器人
```

这是 B-17（知识维护与运营基础能力）最合理的第一步。

---

## 2. 本步目标

### 2.1 业务目标
让业务人员能够：
- 录入一条 FAQ
- 录入一条固定报价
- 在后台验证机器人在钉钉里的回答
- 将知识发布到机器人

### 2.2 技术目标
完成后台最小闭环能力：
- 知识录入
- 钉钉对话验证
- 发布到机器人
- 最小权限控制

---

## 3. 本步范围

## 3.1 本步只覆盖的知识类型
1. FAQ
2. 固定报价

## 3.2 本步只覆盖的页面
1. 后台基础框架
2. 机器人知识管理页
3. FAQ 录入页
4. 固定报价录入页
5. 钉钉对话验证页
6. 发布到机器人页

## 3.3 本步只覆盖的后端能力
1. 知识表与报价扩展表
2. 当前用户权限接口
3. FAQ CRUD
4. 固定报价 CRUD
5. 钉钉对话验证接口
6. 单条发布到机器人接口

---

## 4. 本步暂不做

以下能力明确后置到 `KB-OPS-02` 或之后：

- 制度文档上传解析
- 批量导入 FAQ
- 批量导入固定报价
- 待确认内容页
- 首页大盘
- 我的待办
- 权限管理页
- 命中/未命中分析页
- 自动 FAQ 提炼
- 版本回滚

---

## 5. 数据库最小落地项

结合 [docs/dingtalk-robot-knowledge-ops-backend-db-design-v1.md](docs/dingtalk-robot-knowledge-ops-backend-db-design-v1.md)，本步至少需要：

### 必须落地
1. `knowledge_docs`
2. `knowledge_quote_fields`
3. `knowledge_validation_runs`
4. `knowledge_publish_logs`

### `knowledge_docs` 至少补齐这些字段
- `knowledge_kind`
- `review_status`
- `created_by`
- `updated_by`
- `published_by`
- `published_at`
- `last_validated_at`

---

## 6. 后端接口范围

结合 [docs/dingtalk-robot-knowledge-ops-backend-api-tasklist-v1.md](docs/dingtalk-robot-knowledge-ops-backend-api-tasklist-v1.md) 与 [docs/dingtalk-robot-knowledge-ops-backend-api-schema-v1.md](docs/dingtalk-robot-knowledge-ops-backend-api-schema-v1.md)，本步先实现：

### 必须接口
1. `GET /admin/me/permissions`
2. `GET /admin/knowledge`
3. `POST /admin/knowledge`
4. `PUT /admin/knowledge/{doc_id}`
5. `POST /admin/validation/dingtalk-preview`
6. `POST /admin/publish/{doc_id}`

### 可延后接口
- 批量发布
- 角色管理页接口
- 待确认接口
- 批量导入接口

---

## 7. 前端页面范围

结合 [docs/dingtalk-robot-knowledge-ops-backend-frontend-tasklist-v1.md](docs/dingtalk-robot-knowledge-ops-backend-frontend-tasklist-v1.md)，本步先实现：

### 必须页面
1. 后台框架页
2. 机器人知识管理页
3. FAQ 录入页
4. 固定报价录入页
5. 钉钉对话验证页
6. 发布到机器人页

### 页面要求
- 必须体现钉钉机器人运营后台定位
- 验证页必须提供钉钉聊天预览
- 发布按钮必须表达“发布到机器人”

---

## 8. 样本数据要求

为了确保第一轮联调可控，本步先只使用 2 条样本。

## 8.1 FAQ 样本
- 标题：`试用期员工可以请假吗`
- 知识类型：`faq`
- 摘要：`试用期员工可以按公司制度申请事假/病假。`
- 适用范围：`全体员工`
- 下一步：`如为病假，请补充证明材料。`
- 关键词：`试用期, 请假, 病假`

## 8.2 固定报价样本
- 品名：`黑色墨粉`
- 型号：`7788`
- 价格：`1050`
- 单位：`元/支`
- 含税：`true`
- 生效日期：`2026-04-17`
- 版本：`V2026.04`
- 非标准项处理方式：`如数量或折扣条件不同，请联系商务确认。`

---

## 9. 第一轮联调验证项

## 9.1 FAQ 验证
提问：
- `试用期员工可以请假吗？`

预期：
- 命中 FAQ
- 后台返回钉钉文本回复预览
- 可以执行发布到机器人

## 9.2 固定报价验证
提问：
- `7788 黑色墨粉多少钱？`

预期：
- 命中固定报价
- 回复中出现：
  - 价格
  - 单位
  - 生效日期/版本
  - 非标准项提示
- 可以执行发布到机器人

---

## 10. Done 标准

## 10.1 后端 Done
- 能创建 FAQ
- 能创建固定报价
- 能查询知识列表
- 能执行钉钉对话验证
- 能单条发布到机器人
- 能记录验证时间与发布时间

## 10.2 前端 Done
- 能录 FAQ
- 能录固定报价
- 能在验证页看到钉钉回复预览
- 能触发发布到机器人
- 发布后状态变化可见

## 10.3 联调 Done
- FAQ 主链路跑通一次
- 固定报价主链路跑通一次

---

## 11. 本步之后的下一步

`KB-OPS-01` 完成后，下一步进入：

## `KB-OPS-02`
补齐：
1. 制度文档上传
2. 待确认内容
3. 批量导入 FAQ
4. 批量导入固定报价

---

## 12. 本步不应扩展的内容

本步过程中，不要中途扩展去做：

- 首页大盘
- 我的待办
- 权限管理 UI
- 命中分析
- 自动 FAQ 提炼
- 复杂审核流

否则很容易把最小闭环打散。

---

## 13. 建议分工

### 后端
- 建表/补字段
- 起 6 个核心接口
- 做最小权限校验

### 前端
- 起后台框架
- 起 5~6 个主链路页面
- 接上权限接口
- 接上验证接口

### 产品/负责人
- 提供 1 条 FAQ 样本
- 提供 1 条固定报价样本
- 盯第一轮联调结果

---

## 14. progress.md 记录要求

本步完成后，进度统一记录到：
- [memory-bank/progress.md](memory-bank/progress.md)

### 建议记录 ID
- `KB-OPS-01`

### 记录内容至少包括
- 日期
- 状态（`IN_PROGRESS` / `DONE`）
- 建了哪些表/字段
- 起了哪些接口
- 起了哪些页面
- FAQ / 报价样本是否跑通
- 验证结果
- `Skills:` 追溯

---

## 15. 一句话结论

`KB-OPS-01` 的目标很简单：

> **先用一条 FAQ 和一条固定报价，把“录入 -> 验证 -> 发布到机器人”跑通。**

只要这一步跑通，后台就从“方案阶段”真正进入“可执行阶段”。
