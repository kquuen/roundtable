# 圆桌会议 Roundtable Meeting

面向会议内容与个人决策的 AI 专家圆桌后端。系统支持文本与语音输入、多专家并发分析、两轮辩论、审查校验、人在回路确认与报告生成。

## 核心功能

- 多 Provider 模型路由：通过 `config/providers.yaml` 配置多个 LLM/ASR provider，并按 `agent_models` 映射到不同专家角色。
- Agent 自管理 Provider：默认自动按角色解析模型；也支持显式注入 provider 或显式 mock 模式。
- 多 Agent 并发分析：编排层并发调度多个专家，支持超时与异常降级，保证流程可完成。
- 两轮专家辩论：第一轮独立分析，第二轮基于其他专家观点进行交叉回应，并做引用完整性校验。
- Supervisor 审查：对 claim 做证据绑定、边界与冲突审查，输出可确认待办项。
- 搜索校验：对待确认 claim 可接入搜索校验（有 `SERPAPI_API_KEY` 时使用 SerpAPI，否则 mock）。
- 记忆与持久化：会话、证据、审查结果、报告与记忆条目可持久化，支持重启后继续处理。
- 实时语音会话：`/ws/voice` 支持流式音频输入、实时识别与 AI 回答。
- 个人圆桌模式：支持一问即辩（同步）、流式过程推送（SSE）与决策模板。

## 运行模式

- 自动模式：有可用配置时走 LLM 分析；无配置或不可用时自动降级 mock。
- 显式 mock：CLI 使用 `--mock`，或在服务中显式传 `provider=None`。
- 显式 provider：可在服务层或会话层注入指定 provider 实例。

## 配置说明

配置文件：`config/providers.yaml`

- `providers`：定义 provider（如 `deepseek`、`anthropic`、`openai`、`dashscope_asr`）。
- `agent_models`：定义专家角色到模型的映射，例如 `product_manager: deepseek/deepseek-chat`。
- `voice`：定义语音会话默认 ASR 与 LLM。

环境变量通过 `${ENV_VAR}` 注入，常用项：

- `DEEPSEEK_API_KEY`
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `DASHSCOPE_API_KEY`
- `SERPAPI_API_KEY`（可选）

## 安装与启动

```bash
pip install -e .
uvicorn roundtable.app:app --reload
```

CLI：

```bash
python -m roundtable.main
python -m roundtable.main --input ./data/sample_transcript.json --mode meeting
python -m roundtable.main --mock --lang en
```

## API 能力

### 会话与证据

- `POST /session/create`
- `GET /session/{session_id}`
- `GET /session/{session_id}/reports`
- `POST /evidence/upload`
- `POST /speak`（音频上传转写）

### 分析与辩论

- `POST /roundtable/run`（标准分析管线）
- `POST /roundtable/debate`（两轮辩论管线）
- `POST /roundtable/interview`（个人圆桌追问）
- `POST /roundtable/quick`（个人圆桌同步结果）
- `POST /roundtable/quick/stream-start` + `GET /roundtable/stream/{session_id}`（SSE 流式过程）
- `GET /roundtable/templates`

### 人在回路与记忆

- `GET /session/{session_id}/pending`
- `POST /review/confirm`
- `POST /session/{session_id}/feedback`
- `POST /memory/confirm`
- `GET /memory/{session_id}`
- `GET /memory/search`

### 配置与系统

- `GET /providers`
- `GET /skills`
- `POST /skills/reload`
- `GET /health`
- `GET /`

### 实时语音

- `WebSocket /ws/voice`

消息类型见 `roundtable/voice/protocol.py`，典型流程：

1. 建立连接，接收 `ready`
2. 发送 `init`
3. 持续发送 `audio`（base64 PCM）
4. 接收 `transcript_final` 与 `ai_response`
5. 发送 `close` 或断开连接

## 目录概览

```text
roundtable/
  app.py           # FastAPI 入口与全部端点
  services.py      # 统一业务编排入口（CLI/API 共用）
  orchestrator.py  # 多 Agent 并发调度
  agents.py        # Agent 基类与角色实现
  providers.py     # Provider 抽象、工厂、路由与缓存
  config.py        # 配置加载、环境变量替换与热重载
  debate.py        # 两轮辩论与个人圆桌引擎
  supervisor.py    # 审查与共识计算
  voice/           # 实时语音协议、会话与 ASR 客户端
```

## 测试

```bash
python -m pytest -q
```

## License

MIT
