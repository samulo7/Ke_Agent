# 钉钉机器人知识运营后台后端接口任务单（V1）

## 1. 文档目的

本文档用于把“钉钉机器人知识运营后台（V1）”拆成可执行的后端接口任务单，方便：

- 排期
- 分工
- 接口评审
- 前后端联调

---

## 2. 当前后端现状

当前项目的 API 仍以机器人运行时为主：

- 应用入口： [app/api/main.py](app/api/main.py)
- 已有路由：
  - 健康检查： [app/api/health.py](app/api/health.py)
  - 钉钉事件入口： [app/api/dingtalk.py](app/api/dingtalk.py)
- 当前 `FastAPI` app 只挂了健康检查和钉钉相关路由：
  - [app/api/main.py:17-33](app/api/main.py#L17-L33)
- 当前钉钉消息入口为：
  - [app/api/dingtalk.py:536](app/api/dingtalk.py#L536)

这意味着：

> **知识运营后台接口基本是绿地新增能力**。

建议不要把后台接口继续塞进现有 [app/api/dingtalk.py](app/api/dingtalk.py)，而是新增独立后台路由模块。

---

## 3. V1 后端接口设计原则

1. **后台接口与钉钉运行时接口分离**
2. **先打通主链路，不追求完美抽象**
3. **先支持业务可用，再补高级能力**
4. **所有发布动作都必须围绕“发布到机器人”**
5. **验证接口必须返回“钉钉对话预览数据”**

---

## 4. 建议新增的后端模块

## 4.1 API 路由层
建议新增：

- `app/api/admin_knowledge.py`
- `app/api/admin_validation.py`
- `app/api/admin_publish.py`
- `app/api/admin_roles.py`
- `app/api/admin_dashboard.py`

## 4.2 服务层
建议新增：

- `app/services/admin_knowledge_service.py`
- `app/services/admin_import_service.py`
- `app/services/admin_review_service.py`
- `app/services/admin_validation_service.py`
- `app/services/admin_publish_service.py`
- `app/services/admin_role_service.py`
- `app/services/admin_dashboard_service.py`

## 4.3 Schema 层
建议新增：

- `app/schemas/admin_knowledge.py`
- `app/schemas/admin_import.py`
- `app/schemas/admin_validation.py`
- `app/schemas/admin_publish.py`
- `app/schemas/admin_role.py`
- `app/schemas/admin_dashboard.py`

---

## 5. V1 接口分组

V1 后端接口分 6 组：

1. 仪表盘与首页接口
2. 知识管理接口
3. 上传与导入接口
4. 待确认接口
5. 钉钉对话验证接口
6. 发布到机器人接口
7. 角色权限接口

---

# 6. 接口任务单

---

## API-G1 仪表盘与首页接口

### 目标
支持“机器人首页 / 我的待办”页面。

### 接口 1：获取首页摘要数据
**建议路径**
- `GET /admin/dashboard/summary`

**返回**
- 今日提问量
- 今日未命中量
- 今日待确认量
- 今日待发布量
- 本周高频问题
- 最近活动

**任务点**
- 聚合机器人运行时日志/命中记录
- 聚合待确认与待发布数量
- 返回角色视角数据

---

### 接口 2：获取我的待办
**建议路径**
- `GET /admin/dashboard/todos`

**返回**
- 待确认内容
- 导入失败项
- 长期未更新知识
- 缺字段阻塞项

**任务点**
- 支持按角色过滤
- 支持优先级字段

---

## API-G1 开发优先级
P1

---

## API-G2 知识管理接口

### 目标
支撑“机器人知识管理”页。

### 接口 1：获取知识列表
**建议路径**
- `GET /admin/knowledge`

**查询参数**
- `type`
- `status`
- `owner`
- `department`
- `keyword`
- `updated_after`
- `page`
- `page_size`

**返回**
- 标题/名称
- 类型
- 状态
- 负责人
- 更新时间
- 最近命中趋势
- 是否已验证

---

### 接口 2：获取知识详情
**建议路径**
- `GET /admin/knowledge/{knowledge_id}`

**返回**
- 基础字段
- 类型专属字段
- 状态
- 负责人
- 最近版本信息

---

### 接口 3：新建知识
**建议路径**
- `POST /admin/knowledge`

**支持类型**
- 制度文档
- FAQ
- 固定报价
- 受控文档

**任务点**
- 根据类型走不同校验逻辑
- 初始状态默认草稿

---

### 接口 4：编辑知识
**建议路径**
- `PUT /admin/knowledge/{knowledge_id}`

**任务点**
- 已发布知识修改后建议进入草稿/待发布状态
- 按角色校验编辑权限

---

### 接口 5：停用知识
**建议路径**
- `POST /admin/knowledge/{knowledge_id}/disable`

---

### 接口 6：恢复知识
**建议路径**
- `POST /admin/knowledge/{knowledge_id}/restore`

---

### 接口 7：批量操作知识
**建议路径**
- `POST /admin/knowledge/batch`

**支持动作**
- 批量停用
- 批量发布
- 批量导出

---

## API-G2 开发优先级
P0

---

## API-G3 上传与导入接口

### 目标
支撑“上传与导入”页以及 4 类知识的进入链路。

### 接口 1：上传制度文档文件
**建议路径**
- `POST /admin/import/document/upload`

**输入**
- 文件
- 类型（制度文档）
- 所属部门
- 负责人

**输出**
- 上传文件 ID
- 原始文件信息
- 识别任务状态

---

### 接口 2：获取制度文档识别结果
**建议路径**
- `GET /admin/import/document/{upload_id}/draft`

**返回**
- 标题
- 摘要
- 适用范围
- 关键词建议
- 建议问题
- 原始来源信息

---

### 接口 3：FAQ 批量导入
**建议路径**
- `POST /admin/import/faq`

**输出**
- 成功条数
- 待确认条数
- 失败条数
- 错误明细

---

### 接口 4：固定报价批量导入
**建议路径**
- `POST /admin/import/quote`

**输出**
- 成功条数
- 待确认条数
- 失败条数
- 冲突明细

---

### 接口 5：下载导入模板（可后端生成或静态提供）
**建议路径**
- `GET /admin/import/templates/{template_type}`

**支持模板**
- faq
- fixed_quote
- restricted_doc

---

## API-G3 开发优先级
P0

---

## API-G4 待确认接口

### 目标
支撑“待确认内容”页。

### 接口 1：获取待确认列表
**建议路径**
- `GET /admin/review/pending`

**返回**
- 标题
- 类型建议
- 风险等级
- 上传时间
- 上传人
- 当前状态

---

### 接口 2：获取待确认详情
**建议路径**
- `GET /admin/review/pending/{item_id}`

**返回**
- 系统摘要建议
- 关键词建议
- 建议问题
- 风险提示
- 原始来源

---

### 接口 3：接受待确认内容
**建议路径**
- `POST /admin/review/pending/{item_id}/accept`

**效果**
- 转为草稿或待发布

---

### 接口 4：修改后接受
**建议路径**
- `POST /admin/review/pending/{item_id}/accept-with-edit`

---

### 接口 5：拆分为多条知识
**建议路径**
- `POST /admin/review/pending/{item_id}/split`

---

### 接口 6：忽略/删除待确认内容
**建议路径**
- `POST /admin/review/pending/{item_id}/dismiss`

---

## API-G4 开发优先级
P1

---

## API-G5 钉钉对话验证接口

### 目标
支撑“钉钉对话验证”页，是 V1 最关键接口组之一。

### 接口 1：验证一个问题
**建议路径**
- `POST /admin/validation/dingtalk-preview`

**输入**
- 问题文本
- 可选：指定知识 ID
- 可选：角色上下文 / 部门上下文

**返回**
- 命中知识列表
- 命中顺位
- 回复类型（text / interactive_card）
- 文本回复预览
- 卡片回复预览（如适用）
- 来源
- 下一步
- 权限决策

---

### 接口 2：获取建议测试问法
**建议路径**
- `GET /admin/validation/suggested-questions/{knowledge_id}`

**返回**
- 适合拿来测试的推荐问法

---

### 接口 3：记录一次验证结果（可选但建议）
**建议路径**
- `POST /admin/validation/log`

**返回**
- 验证日志 ID

---

## API-G5 开发优先级
P0

---

## API-G6 发布到机器人接口

### 目标
支撑“发布到机器人”页。

### 接口 1：获取待发布列表
**建议路径**
- `GET /admin/publish/pending`

**返回**
- 标题
- 类型
- 负责人
- 是否已通过钉钉验证
- 当前状态

---

### 接口 2：发布前校验
**建议路径**
- `POST /admin/publish/precheck`

**校验项**
- 必填字段完整性
- 角色权限
- 是否已验证
- 报价字段完整性
- 受控文档联系人/申请路径完整性

---

### 接口 3：单条发布到机器人
**建议路径**
- `POST /admin/publish/{knowledge_id}`

**效果**
- 状态切换为已发布
- 进入机器人可用知识范围

---

### 接口 4：批量发布到机器人
**建议路径**
- `POST /admin/publish/batch`

---

### 接口 5：获取发布记录
**建议路径**
- `GET /admin/publish/logs`

---

## API-G6 开发优先级
P0

---

## API-G7 角色权限接口

### 目标
支撑“权限与角色”页。

### 接口 1：获取角色列表
**建议路径**
- `GET /admin/roles`

---

### 接口 2：获取角色权限详情
**建议路径**
- `GET /admin/roles/{role_id}`

---

### 接口 3：更新角色权限
**建议路径**
- `PUT /admin/roles/{role_id}`

---

### 接口 4：获取当前登录用户权限
**建议路径**
- `GET /admin/me/permissions`

**前端用途**
- 控制菜单显隐
- 控制按钮显隐

---

## API-G7 开发优先级
P0

---

# 7. 数据与状态任务

## DATA-01 统一知识主表
支持：
- 标题
- 类型
- 状态
- 负责人
- 来源
- 更新时间
- 可见范围
- 最近验证状态
- 最近发布时间

## DATA-02 类型扩展字段
### 制度文档
- 摘要
- 适用范围
- 关键词

### FAQ
- 问题
- 答案
- 下一步
- 关键词

### 固定报价
- 品名
- 型号
- 价格
- 单位
- 是否含税
- 生效日期
- 版本号
- 非标准项处理方式

### 受控文档
- 联系人
- 申请路径
- 可见范围
- 是否允许摘要展示

## DATA-03 状态流转
建议最少支持：
- 草稿
- 待确认
- 待发布
- 已发布
- 已停用

---

# 8. 权限任务

## AUTH-01 角色固化
V1 先固定：
- 人事：员工手册、FAQ、受控文档
- 商务：固定报价
- 财务：只读验证
- 管理员：全权限

## AUTH-02 菜单显隐
根据角色返回菜单权限

## AUTH-03 按钮显隐
根据角色返回按钮权限

## AUTH-04 服务端强校验
即便前端隐藏按钮，后端也必须校验：
- 人事不能改报价
- 商务不能改 FAQ
- 财务不能发布知识

---

# 9. 联调顺序建议

## 第一批联调（P0）
1. 获取当前用户权限
2. 知识列表
3. FAQ 新增/编辑
4. 固定报价新增/编辑
5. 制度文档上传 + 草稿生成
6. 钉钉对话验证
7. 发布到机器人

## 第二批联调（P1）
1. 待确认内容
2. FAQ 批量导入
3. 报价批量导入
4. 首页数据
5. 我的待办

---

# 10. 验收重点

## 功能验收
- 可新增 FAQ
- 可新增固定报价
- 可上传制度文档并生成草稿
- 可验证钉钉回复
- 可发布到机器人

## 权限验收
- 人事无法编辑固定报价
- 商务无法编辑 FAQ
- 财务无法发布
- 管理员全权限有效

## 数据验收
- 缺关键字段不可发布
- 报价多价格冲突可进入待确认
- 受控文档缺联系人不可发布

---

# 11. 建议的接口开发顺序

## 第一阶段：主链路最小闭环
- `/admin/me/permissions`
- `/admin/knowledge`
- `/admin/knowledge/{id}`
- `/admin/knowledge` (POST)
- `/admin/knowledge/{id}` (PUT)
- `/admin/import/document/upload`
- `/admin/validation/dingtalk-preview`
- `/admin/publish/precheck`
- `/admin/publish/{knowledge_id}`

## 第二阶段：运营增强
- `/admin/review/pending`
- `/admin/review/pending/{id}`
- `/admin/import/faq`
- `/admin/import/quote`
- `/admin/dashboard/summary`
- `/admin/dashboard/todos`

## 第三阶段：管理增强
- `/admin/roles`
- `/admin/roles/{id}`
- `/admin/publish/logs`
- `/admin/knowledge/batch`

---

# 12. 一句话结论

V1 后端最重要的不是把所有后台页都做完，而是先打通：

> **录入知识 -> 钉钉对话验证 -> 发布到机器人**

这是整个运营后台成立的核心。
