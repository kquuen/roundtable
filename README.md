# 圆桌会议 Roundtable Meeting

**AI 专家圆桌工作台后端 v0.1.0**

## 产品定位

圆桌会议不是普通会议纪要工具，而是一个 AI 专家圆桌工作台。用户可以像组队一样召集不同职业 Agent（产品经理、架构师、商业分析等），让它们围绕一场会议或一段个人想法进行多视角分析，最终输出结构化报告。

## 项目结构

```
roundtable/
  models.py          # Pydantic v2 协议模型（8 类）
  evidence.py        # 证据底座（TranscriptChunker + EvidenceBuilder）
  skills.py          # Skill Registry（5 个内置专家）
  agents.py          # Agent 基类 + 5 个专家实现
  orchestrator.py    # 同步多 Agent 编排
  supervisor.py      # 主审查官（4 级审查）
  report.py          # 报告合成器（10 区模板）
  team.py            # Team Builder（会话分类 + 队伍推荐）
  main.py            # CLI 流水线入口
  app.py             # FastAPI REST API
  providers.py       # LLM Provider Adapter
```

## 快速开始

```bash
# 安装
pip install -e .

# 运行 CLI
python -m roundtable.main

# 启动 API
uvicorn roundtable.app:app --reload

# 测试
python -m pytest tests/ -v
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/session/create` | 创建会话 |
| GET | `/session/{id}` | 查询会话 |
| POST | `/evidence/upload` | 上传文本证据 |
| POST | `/roundtable/run` | 执行圆桌分析 |
| POST | `/team/recommend` | 推荐专家队伍 |
| GET | `/health` | 健康检查 |

## 技术栈

- Python 3.11+
- Pydantic v2 + FastAPI
- PostgreSQL（规划中）
- Redis（规划中，当前同步调度）

## 开发路线

- [x] Phase 0: 协议模型
- [x] Phase 1: 证据底座
- [x] Phase 2: Skill Registry + Agent
- [x] Phase 3: Supervisor + Report
- [x] Phase 4: Team Builder
- [x] Phase 5: FastAPI + Provider
- [ ] Phase 6+: ASR 接入、真实 LLM、记忆系统

## License

MIT
