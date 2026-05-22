# Roundtable Backend — 全面规划审查与修复记录

> 审查日期：2026-05-23 · 审查者：Reasonix Code · 版本跨度：v0.1.0 → v0.3.0

---

## 审查条例类目

### 一级类目：功能完整性

| # | 检查项 | 判定标准 |
|---|--------|---------|
| 1 | 协议模型是否完整 | 所有核心实体有 Pydantic 定义，枚举值正确，字段约束有效 |
| 2 | 证据底座是否可用 | 文本→分块→EvidencePacket 链路通畅 |
| 3 | Agent 是否接入真实 LLM | analyze() 必须调用 LLM API，不能是关键词规则引擎 |
| 4 | 编排器是否支持并发 | 多 Agent 应并行调用，不能串行阻塞 |
| 5 | API 证据链路是否贯通 | upload → run 必须使用用户上传的证据，不能读固定文件 |
| 6 | 报告输出是否结构化 | 10 区模板，审查统计可读 |

### 二级类目：审查深度

| # | 检查项 | 判定标准 |
|---|--------|---------|
| 7 | 证据绑定审查 | fact 无证据 → REJECTED，confidence<0.5 → DOWNGRADED |
| 8 | forbidden 规则执行 | Agent 的 forbidden 列表被实际检查并执行 |
| 9 | 跨 Agent 矛盾检测 | 两个 Agent 冲突结论 → NEEDS_USER_CONFIRMATION |
| 10 | 审查统计汇总 | 通过/驳回/降级/待确认计数 |

### 三级类目：工程可靠性

| # | 检查项 | 判定标准 |
|---|--------|---------|
| 11 | 依赖完整性 | pyproject.toml 包含所有运行时依赖 |
| 12 | 容器化支持 | Dockerfile 存在且可构建 |
| 13 | 数据持久化 | Session/Evidence 重启不丢失 |
| 14 | 报告归档 | 每次分析报告可追溯 |
| 15 | 记忆系统 | 高置信度结论自动留存，可查询 |

---

## 审查发现与修复记录

### v0.1.0 审查结果（初始状态）

| # | 问题 | 严重度 | 状态 |
|---|------|--------|------|
| 1 | Agent 无 LLM 接入 — 全部关键词规则引擎 | 🔴 严重 | → v0.2.0 修复 |
| 2 | API `/roundtable/run` 忽略用户证据 — 读固定 sample_transcript.json | 🔴 严重 | → v0.2.0 修复 |
| 3 | pyproject.toml 缺少 fastapi/uvicorn/httpx/openai | 🔴 严重 | → v0.2.0 修复 |
| 4 | 无 Dockerfile | 🔴 严重 | → v0.2.0 修复 |
| 5 | Agent 同步串行 — 5 Agent 耗时 10-25s | 🟡 中等 | → v0.2.0 修复 |
| 6 | forbidden_claims / forbidden 规则无人读取执行 | 🟡 中等 | → v0.3.0 修复 |
| 7 | Supervisor 不检测跨 Agent 矛盾 | 🟡 中等 | → v0.3.0 修复 |
| 8 | 枚举与字符串混用 (ClaimType) | 🟡 中等 | → v0.2.0 修复 |
| 9 | Session 内存 dict → 重启丢失 | 🟡 中等 | → v0.3.0 修复 |
| 10 | MemoryWrite 模型定义但无实现 | 🟡 中等 | → v0.3.0 修复 |
| 11 | ProjectManager/BusinessAnalyst analyze() 硬编码 | 🟡 中等 | → v0.2.0 修复 |
| 12 | main.py 不支持命令行参数 | 🟢 轻微 | → v0.2.0 修复 |
| 13 | 无日志系统 | 🟢 轻微 | 待做 |
| 14 | 时间戳模拟 30s 固定间隔 | 🟢 轻微 | 待做 |

### v0.2.0 修复内容

| 文件 | 改动 |
|------|------|
| `providers.py` | DeepSeek API 适配器（AsyncOpenAI + 重试 + prompt 构建 + JSON 解析容错） |
| `agents.py` | 基类统一 LLM/mock 双路径，子类保留 _analyze_mock() 回退 |
| `orchestrator.py` | asyncio.gather() 并发 + 每 Agent 30s 超时 + 错误隔离 |
| `app.py` | 证据链路修复（存入 session 上下文），lifespan 管理 provider |
| `main.py` | --input/--mock/--mode/--agents/--output CLI 参数 |
| `supervisor.py` | 枚举统一 ClaimType.FACT/INFERENCE |
| `pyproject.toml` | 补全 fastapi/uvicorn/httpx/openai + 包发现 |
| `Dockerfile` | 新增 python:3.11-slim |

### v0.3.0 修复内容

| 文件 | 改动 |
|------|------|
| `supervisor.py` | forbidden 规则关键词检查 + LLM 跨 Agent 矛盾检测 |
| `store.py` (新) | SessionStore + ReportStore JSON 持久化 |
| `memory.py` (新) | MemoryStore — 高置信度 claims 自动写记忆 + 关键词搜索 |
| `app.py` | 集成持久化层 + /memory 端点 + 报告归档 |

---

## 测试覆盖

| 测试文件 | 测试数 | 覆盖范围 |
|---------|--------|---------|
| test_models.py | 12 | 8 个协议模型 |
| test_evidence.py | 8 | chunk_transcript + build_evidence_packet |
| test_agents.py | 9 | Skill Registry + 5 Agent + Orchestrator |
| test_supervisor.py | 6 | 审查规则 + 报告合成 |
| test_team.py | 9 | 会话分类 + 队伍推荐 |
| test_api.py | 10 | 6 个 API 端点 |
| **总计** | **54** | **全部通过** |

---

## 待办（P2 及后续）

- [ ] Agent evidence_text → chunk_id 的语义匹配优化
- [ ] PostgreSQL 替代 JSON 文件存储
- [ ] 日志系统
- [ ] 用户认证
- [ ] ASR 音频接入
- [ ] 全量 131 Skill 仓库
