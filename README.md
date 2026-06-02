# 圆桌会议 Roundtable Meeting

面向会议内容与个人决策的 AI 专家圆桌后端。系统支持文本与语音输入、多专家并发分析、结构化辩论、审查校验、人在回路确认与报告生成。

## 核心功能

- **多 Provider 模型路由**：通过 `config/providers.yaml` 配置多个 LLM/ASR provider，并按 `agent_models` 映射到不同专家角色。
- **Agent 动态匹配 V2**：基于 Jaccard 相似度 + 方法论关键词加分，自动从 `config/agents/registry.json` 匹配最合适的专家并分组。
- **多 Agent 并发分析**：编排层并发调度多个专家，支持超时与异常降级，保证流程可完成。
- **结构化辩论 V2**：4 步辩论引擎（statement → challenge → new_perspective → consensus），支持用户插话与事件回放。
- **Supervisor 审查**：对 claim 做证据绑定、边界与冲突审查，输出可确认待办项。
- **哨兵机制**：Agent 级熔断器（CLOSED/OPEN/HALF_OPEN）、6 维度幻觉检测、系统告警面板。
- **计费与配额**：套餐模型（free/pro/team），服务端硬拦截配额，支持 PDF/Word 报告导出。
- **支付预留**：微信支付/支付宝统一下单接口占位，订单状态机 + 回调激活。
- **记忆与持久化**：会话、证据、审查结果、报告与记忆条目可持久化，支持重启后继续处理。
- **实时语音会话**：`/ws/voice` 支持流式音频输入、实时识别与 AI 回答。
- **个人圆桌模式**：支持一问即辩（同步）、流式过程推送（SSE）与决策模板。
- **管理后台**：Admin 用户可查看 Agent 健康、系统告警与备份状态。

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
- `JWT_SECRET`（必填）
- `ADMIN_USERS`（可选，逗号分隔 admin 用户名）

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
- `POST /session/{session_id}/export`（PDF / Markdown）

### Agent 与分组

- `GET /agents`
- `POST /agents/match`（动态匹配）
- `POST /agents/confirm-group`（确认分组）
- `POST /agents/adjust-group`（调整分组）

### 分析与辩论

- `POST /roundtable/run`（标准分析管线）
- `POST /roundtable/debate`（两轮辩论管线）
- `POST /roundtable/debate-v2`（结构化 4 步辩论，SSE 支持）
- `POST /roundtable/interview`（个人圆桌追问）
- `POST /roundtable/quick`（个人圆桌同步结果）
- `GET /session/{session_id}/events`（辩论事件回放）
- `POST /session/{session_id}/interrupt`（用户插话）

### 人在回路与记忆

- `GET /session/{session_id}/pending`
- `POST /review/confirm`
- `POST /session/{session_id}/feedback`
- `POST /memory/confirm`
- `GET /memory/{session_id}`
- `GET /memory/search`

### 计费与支付

- `GET /user/usage`
- `GET /user/plan`
- `POST /payment/create-order`
- `POST /payment/callback`
- `GET /payment/orders`

### 系统与哨兵

- `GET /system/agent-health`
- `POST /system/agent-health/{agent_id}/reset`
- `GET /system/alerts`
- `POST /system/alerts/{alert_id}/acknowledge`
- `GET /system/backups`
- `POST /system/backup`
- `POST /system/restore`
- `GET /health`

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
  app.py              # FastAPI 入口
  services.py         # 统一业务编排入口
  orchestrator.py     # 多 Agent 并发调度
  agent_matcher.py    # Agent 动态匹配引擎
  debate_v2.py        # 结构化 4 步辩论引擎
  sentinel/           # 熔断器 + 幻觉检测
  billing.py          # 配额与套餐模型
  export.py           # Markdown → PDF
  payment.py          # 支付订单与回调
  providers.py        # Provider 抽象、工厂、路由
  config.py           # 配置加载与热重载
  supervisor.py       # 审查与共识计算
  db.py               # SQLite 持久层
  voice/              # 实时语音协议、会话与 ASR

frontend/
  index.html          # SPA 主页面
  js/ui/step3.js      # 分组确认（含拖拽）
  js/ui/step5.js      # 报告 + 辩论剧场 V2
  js/ui/admin.js      # 管理后台
  css/pages.css       # 页面样式
```

## 数据库迁移

Schema 变更记录位于 `migrations/`，按 Phase 编号：

- `001_initial_schema.sql` — 初始表结构
- `002_phase1_agent_registry.sql` — Agent 注册表
- `003_phase2_structured_debate.sql` — 辩论 V2
- `004_phase3_sentinel.sql` — 哨兵机制
- `005_phase4_billing.sql` — 计费与支付

## 测试

```bash
# 全部测试
python -m pytest -q

# 分模块测试
python -m pytest tests/test_agent_matcher.py tests/test_debate_v2.py tests/test_sentinel.py tests/test_billing.py -v
```

## License

MIT
