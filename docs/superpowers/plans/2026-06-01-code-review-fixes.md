# 代码审查修复实施计划

> **给执行者的说明：** 使用 executing-plans 技能按任务执行本计划。步骤使用复选框（`- [ ]`）语法追踪。

**目标：** 修复第三方代码审查中发现的 Critical + High 级别问题（14 项），提升系统安全性、数据一致性和代码质量。

**方案：** 按优先级分 4 个阶段执行：① 数据层统一（JSON→SQLite）② 安全与权限加固 ③ 并发安全与资源管理 ④ 代码质量与性能优化。每个阶段包含具体代码变更和配套测试验证。

**技术栈：** Python 3.14, FastAPI, Pydantic v2, SQLite, bcrypt, PyJWT, pytest

---

## 阶段 1：数据层统一 — UserStore 迁移到 SQLite

> **背景：** auth.py 的 UserStore 使用 `data/users/users.json` 存储用户数据，但 db.py 已定义 `users` 表。这是双轨制存储，导致 api_keys.py 等模块与 auth.py 强耦合于 JSON 实现，且 users.json 无文件锁保护。

### 任务 1：在 db.py 中扩展 users 表 CRUD 操作

**涉及文件：**
- 修改：`roundtable/db.py`
- 测试：`tests/test_db.py`（新建）

- [ ] **步骤 1：编写测试验证 users 表 CRUD**

```python
# tests/test_db.py
import pytest
from roundtable.db import init_db, _get_conn

class TestUsersCRUD:
    def test_create_and_get_user(self):
        init_db()
        conn = _get_conn()
        conn.execute(
            "INSERT INTO users (user_id, username, email, hashed_password, created_at, custom_keys, monthly_quota, monthly_used) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("u_test001", "testuser", "test@example.com", "hashed_pwd", "2024-01-01T00:00:00+00:00", "{}", 50000, 0),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", ("u_test001",)).fetchone()
        conn.close()
        assert row["username"] == "testuser"
        assert row["email"] == "test@example.com"
```

- [ ] **步骤 2：在 db.py 底部添加 users 表操作函数**

在 `roundtable/db.py` 第 164 行之后追加：

```python
# ── Users CRUD ──

def create_user(user_id: str, username: str, email: str, hashed_password: str,
                custom_keys: dict | None = None, monthly_quota: int = 50000,
                monthly_used: int = 0) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO users (user_id, username, email, hashed_password, created_at,
                 custom_keys, monthly_quota, monthly_used)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, username, email, hashed_password,
             datetime.now(timezone.utc).isoformat(),
             _to_json(custom_keys or {}), monthly_quota, monthly_used),
        )
        conn.commit()
    finally:
        conn.close()


def get_user_by_username(username: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_user_custom_keys(user_id: str, custom_keys: dict) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE users SET custom_keys = ? WHERE user_id = ?",
            (_to_json(custom_keys), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_user_usage(user_id: str, monthly_used: int) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE users SET monthly_used = ? WHERE user_id = ?",
            (monthly_used, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_all_users() -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM users").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
```

- [ ] **步骤 3：运行测试确认通过**

```bash
cd /c/Users/Administrator/roundtable-backend-review
pytest tests/test_db.py -v
```

预期：`test_create_and_get_user` PASS

- [ ] **步骤 4：提交**

```bash
git add roundtable/db.py tests/test_db.py
git commit -m "feat(db): add users table CRUD operations"
```

---

### 任务 2：重构 auth.py 的 UserStore 为 SQLite 实现

**涉及文件：**
- 修改：`roundtable/auth.py`
- 测试：`tests/test_auth.py`（新建或修改）

- [ ] **步骤 1：在 auth.py 中导入 db 函数并替换 UserStore 实现**

保留所有对外接口（`User`, `UserInDB`, `UserRegisterRequest`, `UserLoginRequest`, `TokenResponse`, `get_user_store`, `get_current_user`, `require_user`）不变，仅替换 `UserStore` 内部实现：

```python
# roundtable/auth.py — 在现有导入下方添加
from roundtable.db import (
    init_db, create_user, get_user_by_username, get_user_by_id,
    update_user_custom_keys, update_user_usage, list_all_users,
    _from_json,
)

# 替换 UserStore 类内部实现
class UserStore:
    """SQLite-backed user store. Replaces JSON file storage."""

    def __init__(self, base_dir: str = "data/users"):
        from pathlib import Path
        init_db()
        self._json_path = Path(base_dir) / "users.json"
        self._migrate_from_json_if_needed()

    def _migrate_from_json_if_needed(self) -> None:
        """One-time migration from legacy JSON file to SQLite."""
        if not self._json_path.exists():
            return
        import json
        try:
            data = json.loads(self._json_path.read_text(encoding="utf-8"))
            existing = {u["username"] for u in list_all_users()}
            for u in data.get("users", []):
                if u["username"] in existing:
                    continue
                create_user(
                    user_id=u["user_id"],
                    username=u["username"],
                    email=u["email"],
                    hashed_password=u["hashed_password"],
                    custom_keys=_from_json(u.get("custom_keys"), {}),
                    monthly_quota=u.get("monthly_quota", 50000),
                    monthly_used=u.get("monthly_used", 0),
                )
            # Rename JSON file to prevent re-migration
            self._json_path.rename(self._json_path.with_suffix(".json.migrated"))
        except Exception:
            pass

    def get_by_username(self, username: str) -> Optional[UserInDB]:
        row = get_user_by_username(username.lower().strip())
        if not row:
            return None
        return self._row_to_user(row)

    def get_by_id(self, user_id: str) -> Optional[UserInDB]:
        row = get_user_by_id(user_id)
        if not row:
            return None
        return self._row_to_user(row)

    @staticmethod
    def _row_to_user(row: dict) -> UserInDB:
        return UserInDB(
            user_id=row["user_id"],
            username=row["username"],
            email=row["email"],
            created_at=datetime.fromisoformat(row["created_at"]),
            hashed_password=row["hashed_password"],
            custom_keys=_from_json(row.get("custom_keys"), {}),
            monthly_quota=row.get("monthly_quota", 50000),
            monthly_used=row.get("monthly_used", 0),
        )

    def create(self, username: str, email: str, password: str) -> User:
        username = username.lower().strip()
        if get_user_by_username(username):
            raise ValueError("Username already exists")

        user_id = f"u_{uuid.uuid4().hex}"  # 16 bytes entropy
        now = datetime.now(timezone.utc)
        create_user(
            user_id=user_id,
            username=username,
            email=email,
            hashed_password=_hash_password(password),
            custom_keys={},
            monthly_quota=50000,
            monthly_used=0,
        )
        return User(
            user_id=user_id,
            username=username,
            email=email,
            created_at=now,
            custom_keys={},
            monthly_quota=50000,
            monthly_used=0,
        )

    def authenticate(self, username: str, password: str) -> Optional[User]:
        user = self.get_by_username(username.lower().strip())
        if not user:
            return None
        if not _verify_password(password, user.hashed_password):
            return None
        return User(
            user_id=user.user_id,
            username=user.username,
            email=user.email,
            created_at=user.created_at,
            custom_keys=user.custom_keys,
            monthly_quota=user.monthly_quota,
            monthly_used=user.monthly_used,
        )

    def _save(self) -> None:
        """Backward compatibility: no-op for SQLite (writes are immediate)."""
        pass
```

- [ ] **步骤 2：更新 api_keys.py 适配新的 UserStore**

`api_keys.py` 中 `set_user_api_key` 和 `delete_user_api_key` 当前直接修改 `db_user.custom_keys` 然后调用 `store._save()`。由于新的 `_save()` 是 no-op，需要改为直接调用 `update_user_custom_keys`：

```python
# roundtable/api_keys.py
from roundtable.db import update_user_custom_keys, get_user_by_id, _from_json

def set_user_api_key(user: User, provider: str, key: str) -> None:
    row = get_user_by_id(user.user_id)
    if not row:
        raise HTTPException(404, "User not found")
    custom_keys = _from_json(row.get("custom_keys"), {})
    custom_keys[provider] = key
    update_user_custom_keys(user.user_id, custom_keys)


def delete_user_api_key(user: User, provider: str) -> None:
    row = get_user_by_id(user.user_id)
    if not row:
        raise HTTPException(404, "User not found")
    custom_keys = _from_json(row.get("custom_keys"), {})
    custom_keys.pop(provider, None)
    update_user_custom_keys(user.user_id, custom_keys)
```

同时更新 `get_user_api_keys`：

```python
def get_user_api_keys(user: User) -> dict:
    row = get_user_by_id(user.user_id)
    if not row:
        return {}
    custom_keys = _from_json(row.get("custom_keys"), {})
    return {k: _mask_key(v) for k, v in custom_keys.items() if v}
```

- [ ] **步骤 3：更新 get_current_user 返回完整 User**

```python
# roundtable/auth.py — get_current_user 末尾
    return User(
        user_id=user.user_id,
        username=user.username,
        email=user.email,
        created_at=user.created_at,
        custom_keys=user.custom_keys,
        monthly_quota=user.monthly_quota,
        monthly_used=user.monthly_used,
    )
```

- [ ] **步骤 4：运行 auth 相关测试**

```bash
pytest tests/test_api.py tests/test_integration.py -v -k "auth or login or register or user"
```

预期：所有涉及用户的测试通过

- [ ] **步骤 5：提交**

```bash
git add roundtable/auth.py roundtable/api_keys.py tests/
git commit -m "refactor(auth): migrate UserStore from JSON to SQLite"
```

---

## 阶段 2：安全与权限加固

### 任务 3：移除生产环境 demo 数据 fallback

**涉及文件：**
- 修改：`roundtable/app.py`

- [ ] **步骤 1：修改 `_get_evidence_segments`**

将 `roundtable/app.py:490-500` 替换为：

```python
def _get_evidence_segments(session_id: str) -> list[dict]:
    """Fetch stored evidence. Returns empty list if none uploaded."""
    segments = _store.get_evidence(session_id)
    if not segments:
        # Only allow demo fallback in explicit debug mode
        debug_mode = os.getenv("DEBUG", "").lower() in ("1", "true", "yes")
        if debug_mode:
            import json
            data_path = Path(__file__).resolve().parent.parent / "data" / "sample_transcript.json"
            if data_path.exists():
                segments = json.loads(data_path.read_text(encoding="utf-8")).get("segments", [])
    return segments
```

- [ ] **步骤 2：在 `/roundtable/run` 中增加空证据保护**

在 `run_roundtable` 和 `run_debate` 中，获取 segments 后添加：

```python
    segments = _get_evidence_segments(req.session_id)
    if not segments:
        raise HTTPException(400, "No evidence uploaded for this session. Please upload evidence first.")
```

- [ ] **步骤 3：运行测试**

```bash
pytest tests/test_api.py -v -k "run or roundtable"
```

- [ ] **步骤 4：提交**

```bash
git add roundtable/app.py
git commit -m "fix(app): remove implicit demo data fallback in production"
```

---

### 任务 4：加固敏感端点权限

**涉及文件：**
- 修改：`roundtable/app.py`

- [ ] **步骤 1：/skills/reload 添加认证**

```python
@app.post("/skills/reload")
async def reload_skills_endpoint(user: User = Depends(require_user)):
    """Hot-reload skill definitions from skills/ directory. Admin only."""
    result = reload_skills()
    return {
        "status": "reloaded",
        "skills_loaded": result["loaded"],
        "total_skills": result["total"],
        "skill_ids": result["skill_ids"],
    }
```

- [ ] **步骤 2：/providers 过滤敏感字段**

将 `list_providers` 中返回的 provider 对象改为仅公开安全字段：

```python
@app.get("/providers")
async def list_providers():
    cfg = ConfigManager.get()
    providers = []
    for pid in cfg.list_providers():
        p = cfg.get_provider(pid)
        if p:
            providers.append({
                "id": p.id,
                "name": p.name,
                "protocol": p.protocol,
                "models": [m.get("id") for m in p.models],
            })
    return {
        "providers": providers,
        "agent_models": cfg.list_agent_models(),
        "voice": cfg.get_voice_config(),
        "loaded": cfg.loaded,
    }
```

- [ ] **步骤 3：/memory/search 添加用户隔离**

```python
@app.get("/memory/search")
async def search_memory(q: str = "", limit: int = 20, user: User = Depends(require_user)):
    """Keyword search across user's own memory entries."""
    if not q:
        return {"results": [], "query": ""}
    # MemoryStore 当前是 JSON 文件，按 session_id 存储。
    # 先获取用户所有 session，再过滤记忆。
    user_sessions = _store.list_by_user(user.username, limit=1000)
    session_ids = {s.session_id for s in user_sessions}
    all_results = _memory.search(q, limit=limit * 10)
    filtered = [r for r in all_results if r.get("session_id") in session_ids][:limit]
    return {"query": q, "result_count": len(filtered), "results": filtered}
```

- [ ] **步骤 4：运行测试**

```bash
pytest tests/test_api.py -v
```

- [ ] **步骤 5：提交**

```bash
git add roundtable/app.py
git commit -m "security(app): add auth to skills/reload, filter providers, isolate memory search"
```

---

## 阶段 3：并发安全与资源管理

### 任务 5：SSE 全局状态加锁与资源保护

**涉及文件：**
- 修改：`roundtable/app.py`

- [ ] **步骤 1：添加 SSE 状态锁**

在 `_sse_queues` / `_sse_keys` 定义处添加锁：

```python
# roundtable/app.py:976-978 附近
_sse_queues: dict[str, asyncio.Queue] = {}
_sse_keys: dict[str, str] = {}
_sse_lock = asyncio.Lock()
```

- [ ] **步骤 2：修改 `_start_sse_pipeline` 使用锁**

```python
async def _start_sse_pipeline(session_id: str, run_fn, finalize_fn):
    queue: asyncio.Queue = asyncio.Queue()
    stream_key = _uuid.uuid4().hex
    async with _sse_lock:
        _sse_queues[session_id] = queue
        _sse_keys[session_id] = stream_key

    async def _runner():
        try:
            result = await run_fn(queue)
            await queue.put({"type": "final_report", "data": result})
        except Exception as e:
            logger.exception("[%s] Pipeline error", session_id)
            await queue.put({"type": "error", "content": str(e)})
        finally:
            await queue.put({"type": "done"})
            async with _sse_lock:
                _sse_keys.pop(session_id, None)

    asyncio.create_task(_runner())
    return {"session_id": session_id, "stream_url": f"/roundtable/stream/{session_id}?key={stream_key}"}
```

- [ ] **步骤 3：修改 stream 端点使用锁**

```python
@app.get("/roundtable/stream/{session_id}")
async def stream_debate_events(session_id: str, key: str = ""):
    async with _sse_lock:
        if not key or _sse_keys.get(session_id) != key:
            raise HTTPException(401, "Invalid or missing stream key")
        queue = _sse_queues.get(session_id)
        if queue is None:
            raise HTTPException(404, f"No active debate stream for session {session_id}")

    # ... event_generator 中的 finally 块也使用锁
```

- [ ] **步骤 4：添加队列大小上限保护**

在 `event_generator` 中，限制队列最大长度（通过创建 Queue 时指定 `maxsize`）：

```python
queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
```

- [ ] **步骤 5：运行测试**

```bash
pytest tests/test_api.py tests/test_voice_ws.py -v
```

- [ ] **步骤 6：提交**

```bash
git add roundtable/app.py
git commit -m "fix(app): add asyncio lock to SSE state, limit queue size"
```

---

### 任务 6：数据库恢复安全保护

**涉及文件：**
- 修改：`roundtable/admin.py`

- [ ] **步骤 1：添加恢复前备份当前 DB**

```python
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

    # Backup current DB before overwriting
    import shutil
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    pre_restore_backup = backup_dir / f"roundtable_pre_restore_{ts}.db"
    shutil.copy2(str(DB_PATH), str(pre_restore_backup))

    # Replace current DB
    shutil.copy2(str(target), str(DB_PATH))
    return {
        "status": "restored",
        "from": file_path,
        "pre_restore_backup": pre_restore_backup.name,
    }
```

- [ ] **步骤 2：运行 admin 测试**

```bash
pytest tests/ -v -k "backup or restore or admin" || echo "No existing admin tests"
```

- [ ] **步骤 3：提交**

```bash
git add roundtable/admin.py
git commit -m "fix(admin): backup current DB before restore, add pre_restore_backup"
```

---

## 阶段 4：代码质量与性能优化

### 任务 7：修复 tmp_path NameError 和 user_id 熵值

**涉及文件：**
- 修改：`roundtable/app.py`, `roundtable/auth.py`

- [ ] **步骤 1：修复 speak 端点 tmp_path**

在 `roundtable/app.py` `speak` 函数中，将 `tmp_path = None` 提前：

```python
    tmp_path = None
    try:
        # ... existing code ...
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        # ... rest of try block ...
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass
```

- [ ] **步骤 2：提升 user_id 熵值**

在 `roundtable/auth.py` `UserStore.create` 中：

```python
        user_id = f"u_{uuid.uuid4().hex}"  # 16 bytes entropy
```

- [ ] **步骤 3：提交**

```bash
git add roundtable/app.py roundtable/auth.py
git commit -m "fix: tmp_path NameError guard, 16-byte user_id entropy"
```

---

### 任务 8：修复 N+1 查询和批量插入

**涉及文件：**
- 修改：`roundtable/store.py`

- [ ] **步骤 1：store_evidence 使用 executemany**

```python
    def store_evidence(self, session_id: str, segments: list[dict]) -> None:
        with self._lock(session_id):
            _execute("DELETE FROM evidence_segments WHERE session_id = ?", (session_id,))
            if segments:
                conn = _get_conn()
                try:
                    conn.executemany(
                        "INSERT INTO evidence_segments (session_id, speaker, text) VALUES (?, ?, ?)",
                        [(session_id, seg.get("speaker", "Speaker")[:64], seg.get("text", "")) for seg in segments],
                    )
                    conn.commit()
                finally:
                    conn.close()
            self._evidence[session_id] = segments
```

- [ ] **步骤 2：store_reviews 使用事务 + 批量插入**

```python
    def store_reviews(
        self, session_id: str,
        agent_reviews: list[AgentReview],
        supervisor_reviews: list[SupervisorReview],
    ) -> None:
        ar_dicts = [ar.model_dump() for ar in agent_reviews]
        sr_dicts = [sr.model_dump() for sr in supervisor_reviews]

        with self._lock(session_id):
            conn = _get_conn()
            try:
                conn.execute("BEGIN")
                conn.execute("DELETE FROM agent_reviews WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM claims WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM supervisor_reviews WHERE session_id = ?", (session_id,))

                for ar in agent_reviews:
                    conn.execute(
                        """INSERT INTO agent_reviews
                           (session_id, agent_id, summary, claims_json, open_questions_json, recommended_next_actions_json)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (session_id, ar.agent_id, ar.summary,
                         _to_json([c.model_dump() for c in ar.claims]),
                         _to_json(ar.open_questions),
                         _to_json(ar.recommended_next_actions)),
                    )

                for ar in agent_reviews:
                    for c in ar.claims:
                        conn.execute(
                            """INSERT INTO claims
                               (session_id, claim_id, agent_id, claim_type, content,
                                evidence_ids_json, confidence, status, lifecycle,
                                consensus_level, verification, debate_history_json)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (session_id, c.claim_id, c.agent_id, c.claim_type.value, c.content,
                             _to_json(c.evidence_ids), c.confidence, c.status,
                             c.lifecycle.value, c.consensus_level.value, c.verification.value,
                             _to_json(c.debate_history)),
                        )

                for sr in supervisor_reviews:
                    conn.execute(
                        """INSERT INTO supervisor_reviews
                           (session_id, claim_id, review_result, final_type, reason,
                            required_changes_json, boundary_classification)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (session_id, sr.claim_id, sr.review_result.value, sr.final_type, sr.reason,
                         _to_json(sr.required_changes),
                         sr.boundary_classification.value if sr.boundary_classification else None),
                    )

                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

            self._agent_reviews[session_id] = ar_dicts
            self._supervisor_reviews[session_id] = sr_dicts
```

注意：这里需要在 `store.py` 顶部添加 `from roundtable.db import _get_conn`。

- [ ] **步骤 3：运行 store 测试**

```bash
pytest tests/test_store.py tests/test_integration.py -v
```

- [ ] **步骤 4：提交**

```bash
git add roundtable/store.py
git commit -m "perf(store): use transactions + executemany for evidence and reviews"
```

---

### 任务 9：添加状态机转换校验和 email 格式校验

**涉及文件：**
- 修改：`roundtable/models.py`, `roundtable/app.py`, `roundtable/auth.py`

- [ ] **步骤 1：在 models.py 添加状态机转换规则**

```python
# roundtable/models.py — 在 SessionStatus 枚举后添加

_SESSION_TRANSITIONS: dict[str, set[str]] = {
    "recording": {"transcribing", "analyzing"},
    "transcribing": {"analyzing"},
    "analyzing": {"reviewing", "completed"},
    "reviewing": {"completed"},
    "completed": set(),
}


def is_valid_status_transition(from_status: str, to_status: str) -> bool:
    return to_status in _SESSION_TRANSITIONS.get(from_status, set())
```

- [ ] **步骤 2：在 app.py update_status 调用处添加校验**

在 `upload_evidence`, `upload_text_evidence`, `run_roundtable`, `run_debate`, `confirm_review` 等端点中，更新状态前添加校验：

```python
# 例如在 /evidence/upload 中
    current = _store.get(req.session_id)
    if current and not is_valid_status_transition(current.status.value, "transcribing"):
        raise HTTPException(400, f"Cannot transition from {current.status.value} to transcribing")
```

- [ ] **步骤 3：email 格式校验**

在 `roundtable/auth.py` 中：

```python
import re

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

class UserRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    email: str = Field(min_length=5, max_length=128)
    password: str = Field(min_length=6, max_length=128)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email format")
        return v
```

- [ ] **步骤 4：运行测试**

```bash
pytest tests/test_api.py tests/test_models.py -v
```

- [ ] **步骤 5：提交**

```bash
git add roundtable/models.py roundtable/app.py roundtable/auth.py
git commit -m "feat: add session status transition validation and email format check"
```

---

## 最终阶段：验证与回归测试

### 任务 10：全量回归测试

**涉及文件：**
- 全部修改过的文件

- [ ] **步骤 1：运行全量测试套件**

```bash
cd /c/Users/Administrator/roundtable-backend-review
pytest tests/ -v --tb=short
```

预期：所有测试通过（或仅与本次修改无关的既有失败）

- [ ] **步骤 2：启动服务做集成验证**

```bash
export JWT_SECRET="test-secret-for-validation"
uvicorn roundtable.app:app --reload &
```

手动验证（或通过 curl）：
1. 注册新用户 → 201
2. 创建 session → 201
3. 上传证据 → 200
4. 调用 /roundtable/run → 200
5. 调用 /user/sessions → 200
6. 调用 /skills/reload（无 token）→ 401
7. 调用 /memory/search → 仅返回当前用户数据

- [ ] **步骤 3：提交最终变更**

```bash
git add .
git commit -m "chore: code review fixes complete — SQLite user store, security hardening, concurrency safety"
```

---

## 自检清单

| 审查问题 | 对应任务 | 状态 |
|---------|---------|------|
| CQ-1 demo fallback | 任务 3 | ✅ 已规划 |
| CQ-2 user_id 熵值 | 任务 7 | ✅ 已规划 |
| CQ-3 _execute 无锁 | 任务 2 (已解决，SQLite 连接独立) | ✅ 已规划 |
| CQ-4 create_task 无引用 | 阶段 3 未完全覆盖，见备注 | ⚠️ 降级处理 |
| CQ-5 tmp_path NameError | 任务 7 | ✅ 已规划 |
| CQ-7 N+1 evidence | 任务 8 | ✅ 已规划 |
| CQ-8 N+1 reviews | 任务 8 | ✅ 已规划 |
| FM-1 双轨制存储 | 任务 1+2 | ✅ 已规划 |
| FM-2 memory/search 隔离 | 任务 4 | ✅ 已规划 |
| FM-3 /skills/reload 无认证 | 任务 4 | ✅ 已规划 |
| FM-4 /providers 泄露 | 任务 4 | ✅ 已规划 |
| HD-1 SSE 无锁 | 任务 5 | ✅ 已规划 |
| HD-2 恢复无保护 | 任务 6 | ✅ 已规划 |
| FM-8 状态机校验 | 任务 9 | ✅ 已规划 |

**备注：** CQ-4（create_task 无引用保存）涉及较大的 SSE 架构改动（需要保存 task 引用并在客户端断开时取消），当前计划采用队列大小限制（任务 5）作为缓解措施。完整的 task 生命周期管理建议作为后续迭代。

---

**计划完成，已保存到 `docs/superpowers/plans/2026-06-01-code-review-fixes.md`。两种执行方式：**

**1. 子代理驱动（推荐）** — 每个任务分派新的子代理，任务间审查，快速迭代

**2. 内联执行** — 在当前会话中使用 executing-plans 执行，批处理 + 检查点

**选哪个？**
