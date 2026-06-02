# 四子项补全实施计划

> **给执行者的说明：** 使用 executing-plans 技能按任务执行本计划。步骤使用复选框（`- [ ]`）语法追踪。

**目标：** 补全用户数据隔离、API Key 管理（混合模式）、历史记录查询、备份恢复四个子项。

**方案：** 渐进式实施：先强制登录隔离数据，再扩展 users 表支持自定义 API Key 和用量记录，最后补充历史查询和运维备份端点。每步可独立验证。

**技术栈：** Python 3.11, FastAPI, Pydantic v2, SQLite, bcrypt, PyJWT, 原生 JS

---

## 前置状态确认

- [ ] **P0: 确认当前数据库已就绪**

运行验证：
```bash
cd /c/Users/Administrator/roundtable-backend-review
python3 -c "from roundtable.store import SessionStore; SessionStore(); print('DB OK')"
```
预期输出：`DB OK`

---

## 任务 1：用户数据隔离（强制登录 + created_by 校验）

**涉及文件：**
- 修改：`roundtable/app.py`（多处端点）
- 修改：`roundtable/auth.py`（UserInDB 添加 username 小写存储）
- 修改：`roundtable/store.py`（create 方法签名不变，但内部使用传入的 created_by）

### 步骤 1.1：统一 username 为小写存储

修改 `roundtable/auth.py` 中 `UserStore.create`：
```python
def create(self, username: str, email: str, password: str) -> User:
    username = username.lower().strip()
    if username in self._by_username:
        raise ValueError("Username already exists")
    # ... 后续不变
```

修改 `UserStore.authenticate`：
```python
def authenticate(self, username: str, password: str) -> Optional[User]:
    user = self.get_by_username(username.lower().strip())
    # ... 后续不变
```

### 步骤 1.2：将所有敏感端点改为 require_user

在 `roundtable/app.py` 中，找到以下端点并修改依赖：

```python
# 修改前
async def create_session(req: CreateSessionRequest, user: User = Depends(get_current_user)):
# 修改后
async def create_session(req: CreateSessionRequest, user: User = Depends(require_user)):
```

需要修改的端点列表（将 `get_current_user` 替换为 `require_user`）：
1. `POST /session/create`
2. `GET /session/{session_id}` — 同时加 `created_by` 校验
3. `GET /session/{session_id}/reports` — 同时加 `created_by` 校验
4. `POST /evidence/upload` — 同时加 `created_by` 校验
5. `POST /evidence/text` — 同时加 `created_by` 校验
6. `POST /speak` — 同时加 `created_by` 校验
7. `POST /roundtable/run` — 同时加 `created_by` 校验
8. `POST /roundtable/debate` — 同时加 `created_by` 校验
9. `POST /team/recommend` — 同时加 `created_by` 校验
10. `GET /session/{session_id}/pending` — 同时加 `created_by` 校验
11. `POST /review/confirm` — 同时加 `created_by` 校验
12. `POST /session/{session_id}/feedback` — 同时加 `created_by` 校验
13. `POST /memory/confirm` — 同时加 `created_by` 校验

每个端点增加统一校验函数：

```python
def _require_session_owner(session_id: str, user: User) -> Session:
    s = _store.get(session_id)
    if not s or s.created_by != user.username:
        raise HTTPException(404, "Session not found")
    return s
```

示例（`GET /session/{session_id}`）：
```python
@app.get("/session/{session_id}")
async def get_session(session_id: str, user: User = Depends(require_user)):
    s = _require_session_owner(session_id, user)
    return s.model_dump()
```

示例（`POST /evidence/upload`）：
```python
@app.post("/evidence/upload")
async def upload_evidence(req: UploadEvidenceRequest, user: User = Depends(require_user)):
    _require_session_owner(req.session_id, user)
    # ... 后续不变
```

### 步骤 1.3：验证语法

```bash
cd /c/Users/Administrator/roundtable-backend-review
python3 -c "import ast; ast.parse(open('roundtable/app.py', encoding='utf-8').read()); print('app.py OK')"
python3 -c "import ast; ast.parse(open('roundtable/auth.py', encoding='utf-8').read()); print('auth.py OK')"
```

---

## 任务 2：扩展 users 表结构（custom_keys + quota + used）

**涉及文件：**
- 修改：`roundtable/db.py`（_create_tables 函数）
- 修改：`roundtable/auth.py`（User, UserInDB, UserStore）
- 修改：`roundtable/models.py`（新增 UserProfile 相关模型或扩展 User）

### 步骤 2.1：修改 db.py 建表语句

在 `roundtable/db.py` 的 `_create_tables` 中，修改 `users` 表定义：

```python
CREATE TABLE IF NOT EXISTS users (
    user_id   TEXT PRIMARY KEY,
    username  TEXT NOT NULL UNIQUE,
    email     TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    created_at TEXT NOT NULL,
    custom_keys TEXT NOT NULL DEFAULT '{}',
    monthly_quota INTEGER NOT NULL DEFAULT 50000,
    monthly_used INTEGER NOT NULL DEFAULT 0
);
```

注意：由于 `users` 表可能已存在，直接改 `CREATE TABLE` 不会生效。需要在 `init_db` 中增加 `ALTER TABLE`：

```python
def init_db() -> None:
    conn = _get_conn()
    try:
        _create_tables(conn)
        # 迁移已有表：添加新列
        _add_column_if_missing(conn, "users", "custom_keys", "TEXT NOT NULL DEFAULT '{}'")
        _add_column_if_missing(conn, "users", "monthly_quota", "INTEGER NOT NULL DEFAULT 50000")
        _add_column_if_missing(conn, "users", "monthly_used", "INTEGER NOT NULL DEFAULT 0")
    finally:
        conn.close()

def _add_column_if_missing(conn: sqlite3.Connection, table: str, col: str, defn: str) -> None:
    cur = conn.execute(f"PRAGMA table_info({table})")
    existing = {r["name"] for r in cur.fetchall()}
    if col not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
        conn.commit()
```

### 步骤 2.2：修改 auth.py 模型和存储

修改 `User` 模型：
```python
class User(BaseModel):
    user_id: str
    username: str
    email: str
    created_at: datetime
    custom_keys: dict = Field(default_factory=dict)
    monthly_quota: int = 50000
    monthly_used: int = 0
```

修改 `UserStore._load` 中恢复 User 的逻辑：
```python
user = UserInDB(
    user_id=u["user_id"],
    username=u["username"],
    email=u["email"],
    created_at=datetime.fromisoformat(u["created_at"]),
    hashed_password=u["hashed_password"],
    custom_keys=_from_json(u.get("custom_keys"), {}),
    monthly_quota=u.get("monthly_quota", 50000),
    monthly_used=u.get("monthly_used", 0),
)
```

修改 `UserStore._save`：
```python
{
    "user_id": u.user_id,
    "username": u.username,
    "email": u.email,
    "created_at": u.created_at.isoformat(),
    "hashed_password": u.hashed_password,
    "custom_keys": _to_json(u.custom_keys),
    "monthly_quota": u.monthly_quota,
    "monthly_used": u.monthly_used,
}
```

修改 `UserStore.create` 初始化默认值：
```python
user = UserInDB(
    user_id=user_id,
    username=username,
    email=email,
    created_at=now,
    hashed_password=_hash_password(password),
    custom_keys={},
    monthly_quota=50000,
    monthly_used=0,
)
```

### 步骤 2.3：验证

```bash
python3 -c "
from roundtable.db import init_db
init_db()
print('init_db OK with ALTER TABLE')
"
```

---

## 任务 3：API Key 管理端点

**涉及文件：**
- 创建：`roundtable/api_keys.py`
- 修改：`roundtable/app.py`（引入新端点）

### 步骤 3.1：创建 api_keys.py

```python
"""User API Key management — custom provider keys + usage tracking."""

from __future__ import annotations

from fastapi import HTTPException, Depends
from pydantic import BaseModel, Field

from roundtable.auth import User, require_user, get_user_store


class ApiKeyUpdateRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    key: str = Field(min_length=10, max_length=256)


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "••••"
    return key[:4] + "••••" + key[-4:]


def get_user_api_keys(user: User) -> dict:
    store = get_user_store()
    db_user = store.get_by_id(user.user_id)
    if not db_user:
        return {}
    return {
        k: _mask_key(v)
        for k, v in (db_user.custom_keys or {}).items()
        if v
    }


def set_user_api_key(user: User, provider: str, key: str) -> None:
    store = get_user_store()
    db_user = store.get_by_id(user.user_id)
    if not db_user:
        raise HTTPException(404, "User not found")
    db_user.custom_keys[provider] = key
    store._save()


def delete_user_api_key(user: User, provider: str) -> None:
    store = get_user_store()
    db_user = store.get_by_id(user.user_id)
    if not db_user:
        raise HTTPException(404, "User not found")
    db_user.custom_keys.pop(provider, None)
    store._save()
```

### 步骤 3.2：在 app.py 注册端点

在 app.py 中 `auth_me` 端点之后添加：

```python
@app.get("/user/api-keys")
async def list_api_keys(user: User = Depends(require_user)):
    from roundtable.api_keys import get_user_api_keys
    return {"keys": get_user_api_keys(user)}

@app.post("/user/api-keys")
async def update_api_key(req: ApiKeyUpdateRequest, user: User = Depends(require_user)):
    from roundtable.api_keys import set_user_api_key
    set_user_api_key(user, req.provider, req.key)
    return {"status": "updated", "provider": req.provider}

@app.delete("/user/api-keys/{provider}")
async def delete_api_key(provider: str, user: User = Depends(require_user)):
    from roundtable.api_keys import delete_user_api_key
    delete_user_api_key(user, provider)
    return {"status": "deleted", "provider": provider}

@app.get("/user/usage")
async def get_usage(user: User = Depends(require_user)):
    store = get_user_store()
    db_user = store.get_by_id(user.user_id)
    if not db_user:
        raise HTTPException(404, "User not found")
    return {
        "monthly_quota": db_user.monthly_quota,
        "monthly_used": db_user.monthly_used,
        "remaining": max(0, db_user.monthly_quota - db_user.monthly_used),
    }
```

注意：`ApiKeyUpdateRequest` 模型也要在 app.py 的 Request models 区域定义。

### 步骤 3.3：验证

```bash
python3 -c "import ast; ast.parse(open('roundtable/api_keys.py', encoding='utf-8').read()); print('api_keys.py OK')"
python3 -c "import ast; ast.parse(open('roundtable/app.py', encoding='utf-8').read()); print('app.py OK')"
```

---

## 任务 4：ProviderRouter 改造（get_for_user + fallback）

**涉及文件：**
- 修改：`roundtable/providers.py`（ProviderRouter 类）

### 步骤 4.1：添加 get_for_user 方法

在 `roundtable/providers.py` 的 `ProviderRouter` 类中添加：

```python
def get_for_user(self, model_ref: str, user) -> BaseProvider:
    """Resolve provider for a specific user.

    Priority:
      1. User's custom API key for the provider
      2. Platform default key
      3. Raise if neither available
    """
    # Parse model_ref like "deepseek/deepseek-chat"
    provider_id, model_id = self._parse_ref(model_ref)

    # Check if user has custom key for this provider
    custom_key = None
    if user and hasattr(user, "custom_keys"):
        custom_key = (user.custom_keys or {}).get(provider_id)

    # Try user's custom key first
    if custom_key:
        try:
            return self._build_provider(provider_id, model_id, override_key=custom_key)
        except Exception as e:
            logger.warning("User custom key for %s failed: %s", provider_id, e)
            # Fall through to platform default

    # Fallback to platform default
    return self.get(model_ref)
```

同时需要添加 `_build_provider` 辅助方法（如果 ProviderRouter 已有类似私有方法，则复用）。如果 `ProviderRouter.get` 内部已经调用 `_get_provider_config` 之类的方法，只需在 `_build_provider` 中支持 `override_key` 参数。

如果 `ProviderRouter` 的实现不支持 `override_key`，则采用更简单的方案：

```python
def get_for_user(self, model_ref: str, user) -> BaseProvider:
    provider_id, model_id = self._parse_ref(model_ref)

    if user and hasattr(user, "custom_keys"):
        custom_key = (user.custom_keys or {}).get(provider_id)
        if custom_key:
            # Temporarily patch provider config
            cfg = ConfigManager.get().get_provider(provider_id)
            if cfg:
                original_key = cfg.api_key
                cfg.api_key = custom_key
                try:
                    return self.get(model_ref)
                except Exception:
                    pass
                finally:
                    cfg.api_key = original_key

    return self.get(model_ref)
```

### 步骤 4.2：修改调用点

全局搜索所有调用 `ProviderRouter.get_instance().get(...)` 的地方，替换为 `ProviderRouter.get_instance().get_for_user(..., user)`。

关键调用点（在 app.py 和 services.py 中）：
- `roundtable/app.py` 中 `_get_debate_provider()`
- `roundtable/services.py` 中 RoundtableService 的 provider 解析逻辑

由于调用点分散且涉及 async/await 上下文，**当前阶段简化处理**：
- 不全局替换所有调用点
- 仅在新增功能中支持用户 key（如个人圆桌模式的 `/roundtable/quick`）
- 标准审查管线 `/roundtable/run` 继续走平台 key（因为涉及多 Agent 并发，改造量大）

**简化方案实现**：

```python
def _get_debate_provider_for_user(user=None):
    router = ProviderRouter.get_instance()
    for ref in ("deepseek/deepseek-chat", "anthropic/claude-sonnet-4-20250514", "openai/gpt-4o"):
        try:
            if user:
                return router.get_for_user(ref, user)
            return router.get(ref)
        except Exception:
            continue
    return None
```

修改 `app.py` 中 `/roundtable/quick`、`/roundtable/quick/stream-start`、`/roundtable/interview` 的调用，传入 `user`。

### 步骤 4.3：验证

```bash
python3 -c "import ast; ast.parse(open('roundtable/providers.py', encoding='utf-8').read()); print('providers.py OK')"
```

---

## 任务 5：用户历史记录查询

**涉及文件：**
- 修改：`roundtable/store.py`（新增 list_by_user 方法）
- 修改：`roundtable/app.py`（新增 /user/sessions 端点）
- 修改：`frontend/js/auth.js`（下拉菜单新增"我的会话"）
- 创建：`frontend/js/ui/history.js`（历史记录弹窗）

### 步骤 5.1：后端

在 `roundtable/store.py` 的 `SessionStore` 中添加：

```python
def list_by_user(self, username: str, limit: int = 20, offset: int = 0) -> list[Session]:
    conn = _get_conn()
    try:
        cur = conn.execute(
            """SELECT session_id, mode, title, status, started_at, ended_at, created_by
               FROM sessions WHERE created_by = ? ORDER BY started_at DESC LIMIT ? OFFSET ?""",
            (username, limit, offset),
        )
        sessions = []
        for row in cur.fetchall():
            sessions.append(Session(
                session_id=row["session_id"],
                mode=SessionMode(row["mode"]),
                title=row["title"] or "",
                status=SessionStatus(row["status"]),
                started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
                ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
                created_by=row["created_by"] or "anonymous",
            ))
        return sessions
    finally:
        conn.close()
```

在 `roundtable/app.py` 中添加端点：

```python
@app.get("/user/sessions")
async def list_user_sessions(
    user: User = Depends(require_user),
    limit: int = 20,
    offset: int = 0,
):
    sessions = _store.list_by_user(user.username, limit=limit, offset=offset)
    return {
        "total": len(sessions),
        "limit": limit,
        "offset": offset,
        "sessions": [s.model_dump() for s in sessions],
    }
```

### 步骤 5.2：前端

在 `frontend/js/auth.js` 的 `updateAuthHeader` 中，用户头像下拉菜单新增：

```javascript
// 在 updateAuthHeader 中
if (user) {
    authBtn.textContent = (user.username || '?')[0].toUpperCase();
    authBtn.title = (user.username || '用户') + ' (点击退出)';
    authBtn.onclick = function(e) {
        // 显示下拉菜单：我的会话 / 退出
        showUserDropdown(e.target);
    };
}
```

新增 `showUserDropdown` 函数（内联在 auth.js 或新建 history.js）：

```javascript
function showUserDropdown(trigger) {
    var existing = document.getElementById('user-dropdown');
    if (existing) { existing.remove(); return; }
    var dd = document.createElement('div');
    dd.id = 'user-dropdown';
    dd.style.cssText = 'position:absolute;top:40px;right:0;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-md);padding:0.5rem 0;min-width:140px;z-index:9999;box-shadow:var(--shadow-lg);';
    dd.innerHTML = '<div style="padding:0.5rem 1rem;cursor:pointer;color:var(--text-primary);" onclick="loadUserHistory();document.getElementById(\'user-dropdown\').remove();">我的会话</div>' +
                   '<div style="padding:0.5rem 1rem;cursor:pointer;color:var(--danger);" onclick="if(confirm(\'确认退出？\')){setToken(null,null);updateAuthHeader();showToast(\'已退出\',\'info\');}document.getElementById(\'user-dropdown\').remove();">退出登录</div>';
    trigger.parentElement.style.position = 'relative';
    trigger.parentElement.appendChild(dd);
    document.addEventListener('click', function close(e) {
        if (!dd.contains(e.target) && e.target !== trigger) { dd.remove(); document.removeEventListener('click', close); }
    });
}
```

新增 `loadUserHistory` 函数：

```javascript
async function loadUserHistory() {
    try {
        var r = await apiFetch(API + '/user/sessions?limit=50');
        var d = await r.json();
        renderHistoryModal(d.sessions || []);
    } catch (e) {
        showToast('加载历史记录失败', 'error');
    }
}

function renderHistoryModal(sessions) {
    var existing = document.getElementById('history-modal');
    if (existing) existing.remove();
    var modal = document.createElement('div');
    modal.id = 'history-modal';
    modal.style.cssText = 'position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;';
    var content = '<div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);max-width:480px;width:90%;max-height:70vh;overflow:auto;padding:1.5rem;">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">' +
        '<h3 style="margin:0;">我的会话</h3><button onclick="document.getElementById(\'history-modal\').remove()" style="background:none;border:none;font-size:1.25rem;cursor:pointer;">✕</button></div>';
    if (!sessions.length) {
        content += '<div style="color:var(--text-secondary);text-align:center;padding:2rem;">暂无会话</div>';
    } else {
        content += '<div style="display:flex;flex-direction:gap:0.5rem;">';
        sessions.forEach(function(s) {
            content += '<div style="padding:0.75rem;border:1px solid var(--border);border-radius:var(--radius-md);margin-bottom:0.5rem;cursor:pointer;" onclick="state.sessionId=\''+s.session_id+'\';goStep(5);document.getElementById(\'history-modal\').remove();">' +
                '<div style="font-weight:500;">' + escHtml(s.title || '未命名会话') + '</div>' +
                '<div style="font-size:0.75rem;color:var(--text-secondary);margin-top:0.25rem;">' + s.session_id + ' · ' + (s.status || 'unknown') + '</div>' +
                '</div>';
        });
        content += '</div>';
    }
    content += '</div></div>';
    modal.innerHTML = content;
    document.body.appendChild(modal);
}
```

注意：如果项目中已有 `escHtml` 函数则复用，否则内联实现：
```javascript
function escHtml(s) {
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}
```

### 步骤 5.3：验证

```bash
python3 -c "import ast; ast.parse(open('roundtable/store.py', encoding='utf-8').read()); print('store.py OK')"
python3 -c "import ast; ast.parse(open('roundtable/app.py', encoding='utf-8').read()); print('app.py OK')"
```

---

## 任务 6：备份/恢复端点

**涉及文件：**
- 创建：`roundtable/admin.py`
- 修改：`roundtable/app.py`（注册管理端点）

### 步骤 6.1：环境变量

在 `roundtable/app.py` 顶部添加：
```python
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
```

### 步骤 6.2：创建 admin.py

```python
"""Admin endpoints — backup and restore SQLite database."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from fastapi import HTTPException, Header
from fastapi.responses import FileResponse

from roundtable.db import DB_PATH


def _require_admin(x_admin_token: str | None) -> None:
    expected = os.getenv("ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(403, "Admin endpoints are disabled (ADMIN_TOKEN not set)")
    if x_admin_token != expected:
        raise HTTPException(403, "Invalid admin token")


def backup_database(x_admin_token: str | None) -> dict:
    _require_admin(x_admin_token)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"roundtable_{ts}.db"
    shutil.copy2(str(DB_PATH), str(backup_path))
    return {
        "filename": backup_path.name,
        "path": str(backup_path),
        "size_bytes": backup_path.stat().st_size,
    }


def list_backups(x_admin_token: str | None) -> list[dict]:
    _require_admin(x_admin_token)
    backup_dir = DB_PATH.parent / "backups"
    if not backup_dir.exists():
        return []
    files = sorted(backup_dir.glob("roundtable_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {"filename": f.name, "size_bytes": f.stat().st_size, "created_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()}
        for f in files
    ]


def restore_database(x_admin_token: str | None, file_path: str) -> dict:
    _require_admin(x_admin_token)
    backup_dir = DB_PATH.parent / "backups"
    target = backup_dir / file_path
    if ".." in file_path or "/" in file_path or "\\" in file_path:
        raise HTTPException(400, "Invalid file path")
    if not target.exists():
        raise HTTPException(404, "Backup file not found")
    # Validate SQLite magic header
    with open(target, "rb") as f:
        header = f.read(16)
    if not header.startswith(b"SQLite format 3"):
        raise HTTPException(400, "Invalid SQLite database file")
    # Replace current DB
    shutil.copy2(str(target), str(DB_PATH))
    return {"status": "restored", "from": file_path}
```

### 步骤 6.3：在 app.py 注册端点

```python
@app.post("/system/backup")
async def system_backup(x_admin_token: str = Header(default="")):
    from roundtable.admin import backup_database
    return backup_database(x_admin_token)

@app.get("/system/backups")
async def list_backups_endpoint(x_admin_token: str = Header(default="")):
    from roundtable.admin import list_backups
    return {"backups": list_backups(x_admin_token)}

@app.post("/system/restore")
async def system_restore(req: dict, x_admin_token: str = Header(default="")):
    from roundtable.admin import restore_database
    filename = req.get("filename", "")
    return restore_database(x_admin_token, filename)
```

### 步骤 6.4：验证

```bash
python3 -c "import ast; ast.parse(open('roundtable/admin.py', encoding='utf-8').read()); print('admin.py OK')"
python3 -c "import ast; ast.parse(open('roundtable/app.py', encoding='utf-8').read()); print('app.py OK')"
```

---

## 任务 7：端到端验证

### 步骤 7.1：启动服务

```bash
cd /c/Users/Administrator/roundtable-backend-review
uvicorn roundtable.app:app --reload
```

### 步骤 7.2：API 验证（在另一个终端执行）

```bash
# 注册
 curl -X POST http://127.0.0.1:8000/auth/register \
   -H "Content-Type: application/json" \
   -d '{"username":"test","email":"test@example.com","password":"123456"}'

# 登录获取 token
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"test","password":"123456"}' | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

# 创建 session
curl -X POST http://127.0.0.1:8000/session/create \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"测试","mode":"meeting"}'

# 查询历史
curl http://127.0.0.1:8000/user/sessions \
  -H "Authorization: Bearer $TOKEN"

# 设置 API Key
curl -X POST http://127.0.0.1:8000/user/api-keys \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider":"deepseek","key":"sk-test1234"}'

# 查询 API Keys
curl http://127.0.0.1:8000/user/api-keys \
  -H "Authorization: Bearer $TOKEN"

# 备份（需 ADMIN_TOKEN）
ADMIN_TOKEN=your-secret-here curl -X POST http://127.0.0.1:8000/system/backup \
  -H "X-Admin-Token: your-secret-here"
```

### 步骤 7.3：前端验证

1. 打开浏览器访问前端页面
2. 点击右上角登录按钮，注册/登录
3. 创建 session，确认需要登录后才能操作
4. 点击头像下拉菜单 → "我的会话"，确认历史列表弹出
5. 确认未登录时无法创建 session（返回 401）

---

## 自检清单

写完后检查：

- [ ] 规格覆盖：四个子项（隔离、API Key、历史查询、备份恢复）都有对应任务
- [ ] 占位符扫描：无 "TBD"、无 "TODO"、无 "稍后实现"
- [ ] 类型一致性：`User` 模型在 auth.py、api_keys.py、app.py 中字段名一致
- [ ] 依赖顺序：任务 1 必须在任务 2 之前（因为任务 2 的端点需要登录）
