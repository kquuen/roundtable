# 圆桌会议 Roundtable Meeting

**AI 专家圆桌工作台 v0.3.0 — 搜索 / 语音 / 领域三柱就位**

> 不是会议纪要工具。是一个 AI 专家圆桌工作台——用户召集多职业 Agent，围绕会议或想法进行多视角分析，输出通过 Supervisor 审查 + 搜索引擎校验的结构化报告。

---

## 架构

```
POST /speak (语音)                     POST /evidence/upload (文本)
       │                                        │
       ▼                                        ▼
  ┌─────────┐                            ┌──────────┐
  │ ASR     │                            │ Evidence │
  │ Whisper │                            │ Chunker  │
  └────┬────┘                            └────┬─────┘
       │                                      │
       └──────────────┬───────────────────────┘
                      ▼
              ┌──────────────┐
              │ Domain       │ ← 领域分类 (4 领域 YAML 驱动)
              │ Classifier   │
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │ Orchestrator │ ← 5 Agent 并发分析 (PM / 架构 / 项目管理 / 商业 / Supervisor)
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │ Supervisor   │ ← 证据绑定 + 越界检测 + 矛盾检测 + 共识分层
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │ Search +     │ ← 仅 NEEDS_USER claims → SerpAPI/mock 搜索 → 校验
              │ Verify       │
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │ Report       │ ← 中英双语 10-section 结构化报告 → JSON 持久化
              └──────────────┘
                     │
                     ▼
              ┌──────────────┐
              │ Feedback     │ ← 人在回路：/pending → /review/confirm (确认/驳回/修正/回答)
              │ Loop         │
              └──────────────┘
```

---

## 项目结构 (25 模块)

```
roundtable/
  models.py          # 零依赖核心：所有 Pydantic v2 模型 + 10 枚举
  evidence.py        # 证据底座：transcript 分块 + EvidencePacket 构建
  skills.py          # Skill 注册表：5 内置专家 + YAML 热加载
  agents.py          # Agent 基类 + 5 专家 (LLM/mock 双模式)
  registry.py        # Agent 工厂：单例注册 + 插件式扩展
  orchestrator.py    # 编排器：多 Agent 并发调度 + 超时 fallback
  providers.py       # LLM 适配器：DeepSeek API + 重试 + Prompt 构建
  supervisor.py      # 审查官：证据绑定 / 越界 / 矛盾 / 共识 (同步+异步)
  linker.py          # 语义链接：Agent evidence_text → chunk_id (LLM+关键词)
  feedback.py        # 反馈引擎：确认/驳回/修正/回答 4 种交互
  debate.py          # 辩论引擎：Round 1 独立分析 + Round 2 交叉辩论
  report.py          # 报告合成：中英双语 10-section Markdown
  store.py           # 持久化：Session/Evidence/Reviews + UUID 会话 ID
  memory.py          # 记忆系统：高置信度 claims 自动写入 JSON
  services.py        # 中央编排器：pipeline 唯一入口 (CLI/API 共用)
  domain.py          # 领域适配：YAML 驱动 4 领域 + LLM/关键词分类
  search.py          # 搜索适配器：Mock + SerpAPI 双后端，top-3 × 200 chars
  verify.py          # 搜索校验：claim vs 搜索结果 (LLM+字符级匹配)
  asr.py             # 语音转写：Whisper API + 20min 分片 + ffprobe
  team.py            # 队伍推荐：会议分类 + 专家组合评分
  utils.py           # 工具：async/sync 安全桥接
  logging_config.py  # 日志：结构化 stdout
  app.py             # FastAPI：16 端点 + CORS + UTF-8 JSON
  main.py            # CLI：argparse → pipeline → report
```

---

## 快速开始

```bash
# 安装
pip install -e .

# 环境变量 (可选，不设则走 mock 模式)
export DEEPSEEK_API_KEY="sk-..."      # LLM 分析
export OPENAI_API_KEY="sk-..."        # Whisper 语音转写
export SERPAPI_API_KEY="..."          # 搜索校验（需 pip install -e .[search]）

# CLI
python -m roundtable.main --transcript meeting.txt

# API
uvicorn roundtable.app:app --reload

# 测试
python -m pytest tests/ -v
```

---

## API 端点 (16 个)

### 会话 & 证据

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/session/create` | 创建会话 |
| `GET` | `/session/{id}` | 查询会话 |
| `GET` | `/sessions` | 列出所有会话 |
| `DELETE` | `/session/{id}` | 删除会话 |
| `POST` | `/evidence/upload` | 上传文本 evidence |

### 分析 & 辩论

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/roundtable/run` | 执行完整分析 pipeline |
| `POST` | `/roundtable/debate` | 执行辩论 pipeline |
| `POST` | `/speak` | 音频上传 → 转写 → 自动分析 |

### 人在回路

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/session/{id}/pending` | 获取待裁决项 |
| `POST` | `/review/confirm` | 提交用户裁决 |

### 队伍 & 领域

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/team/recommend` | 根据 transcript 推荐 Agent 组合 |
| `GET` | `/team/list` | 列出所有可用的 Agent 模板 |
| `GET` | `/domains` | 列出所有领域配置 |

### 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `GET` | `/report/{session_id}` | 获取已生成的报告 |

---

## 技术栈

| 组件 | 选型 | 理由 |
|------|------|------|
| 框架 | FastAPI | 异步原生 + Pydantic 深度集成 |
| 模型 | Pydantic v2 | 零 v1 遗留，enum 原生序列化 |
| LLM | DeepSeek (OpenAI SDK) | 兼容接口，3 重试 |
| 语音 | Whisper API | 复用 openai 依赖 |
| 搜索 | SerpAPI + Mock | 环境变量自动切换 |
| 存储 | JSON 文件 (`data/`) | 零依赖持久化 + UUID 会话 ID |
| 测试 | pytest | 175 tests，mock 优先 |

---

## 开发路线

| Phase | 内容 | 测试 | 状态 |
|-------|------|------|------|
| 0 | 协议模型 (10 enums + 15 models) | — | ✅ |
| 1 | 证据底座 (chunk + packet) | — | ✅ |
| 2 | Agent + Skills (5 专家 + YAML) | — | ✅ |
| 3 | Supervisor + Report (4 级审查) | — | ✅ |
| 4 | Team Builder + Provider | — | ✅ |
| 5 | FastAPI + 人在回路 | 126 | ✅ |
| 6 | 辩论引擎 + Supervisor 补完 | 141 | ✅ |
| 6a | 报告补完 + 越界检测 | 141 | ✅ |
| 7C | 领域适配 (4 领域 YAML) | 156 | ✅ |
| 7B | 语音入口 (Whisper ASR) | 165 | ✅ |
| 7A | 搜索校验 (SerpAPI + Mock) | **175** | ✅ |

---

## 设计决策

| 决策 | 理由 |
|------|------|
| `models.py` 零内部导入 | 核心类型不依赖任何业务模块 |
| `services.py` 单编排入口 | CLI 和 API 共享同一 pipeline |
| mock 优先 + 环境变量切换 | 零配置启动，有 key 自动升级 |
| 中文 stopwords 过滤 | 字符级匹配避免"的/了/是"噪声 |
| 验证状态中性命名 | `supported_by_search` ≠ 事实确认 |
| Reviews 持久化到 session JSON | 重启后 `/pending` 不丢数据 |
| UUID session ID | 消除并发 ID 冲突 |
| 全量 `except Exception` 覆盖日志 | 0 处静默 pass |

## License

MIT
