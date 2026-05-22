# Session State — 续接点

**时间：** 2026-05-21
**仓库：** kquuen/roundtable-backend
**最新 commit：** 97ffe90 [Phase 6]

## 已完成（6/6 Phase）

| Phase | 内容 | 单测 |
|-------|------|------|
| 0 | 8 个 Pydantic 协议模型 | 12 ✅ |
| 1 | 证据底座 (Chunker + Builder) | 20 ✅ |
| 2 | Skill Registry + 5 Agent + Orchestrator | 29 ✅ |
| 3 | Supervisor + Report Composer + main.py | 35 ✅ |
| 4 | Team Builder (分类 + 4 队伍) | 45 ✅ |
| 5 | FastAPI + Provider Adapter | 54 ✅ |
| 6 | README + Docker + Release v0.1.0 | 54 ✅ |

## 项目结构

```
roundtable-backend/
├── roundtable/
│   ├── models.py, evidence.py, skills.py
│   ├── agents.py, orchestrator.py
│   ├── supervisor.py, report.py
│   ├── team.py, main.py, app.py, providers.py
├── tests/ (6 files, 54 tests)
├── data/sample_transcript.json
├── README.md, docker-compose.yml, SESSION_STATE.md
```

## 下次从哪继续

### 立即可做的
- [ ] Agent 接入真实 LLM（替换规则引擎为 DeepSeek API）
- [ ] Provider Adapter 实现 httpx 真实调用
- [ ] main.py 支持自定义文本输入

### 中期
- [ ] PostgreSQL schema + Alembic migrations
- [ ] ASR API 接入
- [ ] Redis 任务队列

### 远期
- [ ] Tauri 前端
- [ ] 全量 131 Skill 仓库
- [ ] 知识图谱 + 向量库

## 运行命令

```bash
cd Desktop\会议助手\roundtable-backend
$env:PYTHONPATH = pwd
python -m pytest tests -v
python -m roundtable.main
uvicorn roundtable.app:app --reload
```
