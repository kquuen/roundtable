# 四子项补全设计方案

> **日期**: 2026-06-01  
> **主题**: 用户数据隔离、API Key 管理（混合模式）、历史记录查询、备份恢复  
> **策略**: 渐进式（推荐）— 功能完整但不过度设计，OPC 阶段够用且可平滑升级

---

## 1. 用户数据隔离

### 目标
未登录用户无法创建/访问 session，用户只能看到自己的数据。

### 实现
- `POST /session/create` 从 `get_current_user` 改为 `require_user`（强制登录）
- `GET /session/{id}`、`POST /evidence/*`、`POST /roundtable/*`、`GET /session/{id}/pending`、`POST /review/confirm`、`POST /session/{id}/feedback` 全部改为 `require_user`
- 每个端点内部增加：
  ```python
  if session.created_by != user.username:
      raise HTTPException(404, "Session not found")
  ```
- 匿名 session 创建能力关闭（所有 `created_by="anonymous"` 路径改为必须传 user）

### 错误处理
返回 404 而非 403，避免暴露"该 session 存在但不属于你"的信息。

### 安全原则
- 任何涉及 session 数据读写的端点都必须鉴权
- 公开端点仅保留：`/health`、`/providers`、`/skills`、`/roundtable/templates`、`/auth/*`

---

## 2. API Key 管理（混合模式）

### 目标
用户可填自己的 LLM Key，平台默认兜底；Key 失效时自动回退。

### 数据模型（users 表扩展）
```sql
ALTER TABLE users ADD COLUMN custom_keys TEXT DEFAULT '{}';
  -- {"deepseek":"sk-xxx", "openai":"sk-yyy"}

ALTER TABLE users ADD COLUMN monthly_quota INTEGER DEFAULT 50000;
  -- 平台代付配额（Token数），当前阶段记录不限制

ALTER TABLE users ADD COLUMN monthly_used INTEGER DEFAULT 0;
  -- 本月已用Token数
```

### 新增端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/user/api-keys` | GET | 返回用户配置的 key（脱敏，只显示前4后4位） |
| `/user/api-keys` | POST | 设置/更新 `{provider, key}` |
| `/user/api-keys/{provider}` | DELETE | 删除某个 provider 的 key |
| `/user/usage` | GET | 返回本月用量和配额 |

### ProviderRouter 改造

新增 `get_for_user(provider_id, user)` 方法：

```
调用链:
  ProviderRouter.get_for_user("deepseek", user)
    → 查 user.custom_keys["deepseek"]
      → 有: 用用户 key 构建 Provider
      → 无: fallback 到平台 config/providers.yaml 中的 key
    → 用户 key 调用失败（401/403）
      → 自动 fallback 到平台 key
      → 记录 user_key_failed 标志
      → 下次调用前 toast 提示用户"个人 Key 已失效，已切换至平台 Key"
```

### 配额策略（记录不限制）

- 每次 LLM 调用后，估算 token 数并 `UPDATE users SET monthly_used = monthly_used + ?`
- **超出配额时不阻断**，但返回响应头 `X-Quota-Exceeded: true`
- 前端检测到后显示温和提示："本月平台配额已用完，建议切换到个人 API Key 继续使用"
- 用户填了自己的 key 后，不再走平台配额，无限使用

### 设计理由

OPC 阶段核心诉求是"功能完整、能演示"，而非"计费系统闭环"。记录不限制的策略既保留了未来升级空间，又避免了复杂的 Token 计费逻辑。

---

## 3. 用户历史记录查询

### 目标
用户可查看自己的所有 session。

### 新增端点

```python
@app.get("/user/sessions")
async def list_user_sessions(
    user: User = Depends(require_user),
    limit: int = 20,
    offset: int = 0,
):
    # 从 SQLite 查询 created_by = user.username 的 sessions
    # 按 started_at DESC
    # 返回 [{session_id, title, mode, status, started_at, ended_at}]
```

### 前端交互
- Header 用户头像下拉菜单中新增"我的会话"
- 弹窗/侧边栏展示历史列表
- 点击直接跳转对应 session 的 step5 报告页

---

## 4. 备份/恢复端点

### 目标
运维人员可手动备份和恢复 SQLite 数据库。

### 新增端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/system/backup` | POST | 复制 `data/roundtable.db` 到 `data/backups/roundtable_YYYYMMDD_HHMMSS.db`，返回文件名 |
| `/system/restore` | POST | 上传 `.db` 文件，校验 SQLite magic header → 关闭连接 → 替换文件 → 重启加载 |
| `/system/backups` | GET | 列出所有备份文件 |

### 权限控制

当前阶段简化处理：
- 环境变量 `ADMIN_TOKEN`（如未设置则禁用管理端点）
- 请求头 `X-Admin-Token` 匹配时才允许访问
- 返回 403 如果 Token 不匹配或环境变量未配置

### 为什么不建完整 admin 角色系统？

OPC 阶段通常只有 1 个运维人员（就是创始人自己），复杂的 RBAC 是过度设计。`X-Admin-Token` 足够安全且零维护成本。

---

## 5. 依赖关系与执行顺序

```
Step 1: 用户数据隔离
  └─ 改所有 session 端点为 require_user
  └─ 加 created_by 校验

Step 2: API Key 管理
  └─ 改 users 表结构（custom_keys, monthly_quota, monthly_used）
  └─ 改 ProviderRouter（get_for_user 方法）
  └─ 新增 /user/api-keys 和 /user/usage 端点
  └─ 改调用链路（所有 LLM 调用处替换为 get_for_user）

Step 3: 用户历史记录查询
  └─ 新增 /user/sessions 端点
  └─ 前端 header 下拉菜单

Step 4: 备份/恢复端点
  └─ 新增 /system/backup, /system/restore, /system/backups
  └─ 加 ADMIN_TOKEN 环境变量校验
```

**关键约束**：Step 1 必须在 Step 2 之前完成，因为 API Key 管理端点本身也需要登录才能访问。

---

## 6. 风险与回退方案

| 风险 | 影响 | 回退方案 |
|------|------|---------|
| 强制登录后老用户（已有 anonymous session）无法访问 | 高 | Step 1 执行时同时运行迁移脚本：将 anonymous session 关联到首次登录的用户 |
| ProviderRouter fallback 逻辑出错导致所有调用失败 | 高 | 保留平台默认 Provider 作为最终兜底，fallback 链：用户 key → 平台 key → mock |
| 备份恢复时数据库被占用 | 中 | 恢复前停止 uvicorn，或采用 VACUUM INTO 导出方式避免文件锁 |
| monthly_used 统计不准确 | 低 | 当前阶段仅作参考，不用于计费阻断 |
