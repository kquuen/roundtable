# Session State — 续接点

**时间：** 2026-05-23
**仓库：** kquuen/roundtable-backend
**最新 commit：** (pending push)

## 已完成

| 版本 | 内容 | 单测 |
|------|------|------|
| v0.1.0 | 6 Phase POC：协议模型 + 证据底座 + Skill/Agent(关键词模拟) + Supervisor + Team + FastAPI | 54 ✅ |
| v0.2.0 | **LLM 驱动引擎**：DeepSeek API 适配器 + Agent 重构 + asyncio 并发 + 证据链路修复 + Dockerfile | 54 ✅ |
| v0.3.0 | **审查闭环 + 持久化 + 记忆**：forbidden 执行 + 跨 Agent 矛盾检测 + JSON 持久化 + Memory 系统 | 54 ✅ |

## 项目结构

```
roundtable-backend/
├── roundtable/
│   ├── models.py          # 8 个 Pydantic 协议模型
│   ├── evidence.py        # 证据底座 (Chunker + Builder)
│   ├── skills.py          # Skill Registry (5 内置专家)
│   ├── agents.py          # Agent 基类 + 5 专家 (LLM/mock 双路径)
│   ├── orchestrator.py    # 异步并发编排器
│   ├── supervisor.py      # 主审查官 (证据+forbidden+矛盾检测)
│   ├── report.py          # 报告合成器 (10 区模板)
│   ├── team.py            # Team Builder (分类 + 4 队伍)
│   ├── main.py            # CLI 流水线入口 (支持参数)
│   ├── app.py             # FastAPI REST API (持久化+记忆)
│   ├── providers.py       # LLM Provider Adapter (DeepSeek)
│   ├── store.py           # JSON 持久化层 (Session/Evidence/Report)
│   └── memory.py          # Memory 记忆系统
├── tests/ (6 files, 54 tests)
├── data/sample_transcript.json
├── AUDIT.md               # 审查条例与修复记录
├── README.md
├── Dockerfile
├── docker-compose.yml
└── SESSION_STATE.md
```

## API 端点 (v0.3.0)

```
GET  /                          → 服务信息
POST /session/create            → 创建会话 (持久化)
GET  /session/{id}              → 查询会话
GET  /session/{id}/reports      → 历史报告列表
POST /evidence/upload           → 上传证据 (持久化)
POST /roundtable/run            → 执行分析 (矛盾检测+记忆写入)
POST /team/recommend            → 推荐队伍
GET  /memory/{session_id}       → 查询记忆
GET  /memory/search?q=关键词    → 搜索记忆
GET  /health                    → 健康检查
```

## 下次从哪继续

### P2 推荐
- [ ] Agent evidence_text → chunk_id 语义匹配优化（减少 fact 误驳回）
- [ ] PostgreSQL + Alembic 替代 JSON 文件
- [ ] 用户认证 (JWT)
- [ ] 日志系统

### 中期
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
$env:DEEPSEEK_API_KEY = "sk-..."

# Mock 模式 (无需 API Key)
python -m roundtable.main --mock

# LLM 模式 (需要 API Key)
python -m roundtable.main --agents 3

# API 服务
uvicorn roundtable.app:app --reload

# 测试
python -m pytest tests -v
```
