# Changelog

## [0.3.0] — 2026-06-02

### Phase 5 — 前端增强与运营
- **Admin Dashboard** (`/admin`): Agent 健康看板、告警列表（可确认）、备份管理
- **分组确认拖拽**: Step 3 支持 HTML5 drag-and-drop 调整 Agent 分组
- **辩论剧场 V2**: 四步时间线（statement → challenge → new_perspective → consensus）、用户插话 UI、共识评分进度条

### Phase 4 — 商业化基础设施
- **计费配额硬拦截**: 每次 API 调用前检查 `monthly_quota`，超额返回 429
- **套餐模型**: free (3次/月) / pro (¥29) / team (¥99)，含导出权限和批量评估
- **PDF 报告导出**: `POST /session/{id}/export` 支持 PDF（fpdf + CJK）和 Markdown
- **支付预留接口**: 微信/支付宝回调占位，订单状态机 pending → paid → activated

### Phase 3 — 哨兵机制
- **熔断器**: 每 Agent CLOSED/OPEN/HALF_OPEN 状态机，失败率 >50% 自动降级
- **幻觉检测**: 6 维度检测（无来源数字、低置信度、重复、断言词、禁止话题、矛盾）
- **告警 API**: `/system/alerts` + `/system/agent-health` 管理面板

### Phase 2 — 结构化辩论 V2
- **4 步辩论引擎**: statement → challenge → new_perspective → consensus
- **用户插话系统**: `POST /session/{id}/interrupt`，Agent 必须回应
- **事件回放**: `GET /session/{id}/events` 完整时间线
- **共识快照**: 维度评分 + agreement_level 判定

### Phase 1 — Agent 注册表 V2
- **动态匹配**: Jaccard 相似度 + 方法论关键词加分
- **可配置注册表**: `config/agents/registry.json` + `profiles/*.md`
- **分组确认 API**: `/agents/match` → `/agents/confirm-group`
