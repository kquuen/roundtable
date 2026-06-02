# Roundtable V2 改进计划

> 基于技术设计方案（v1.0-draft）与 OPC 审查报告（OPC-2026-RT-001）的对比分析
> 作者：AI 辅助规划 | 日期：2026-06-02

---

## 1. 计划概述

### 1.1 一句话目标
把 Roundtable 从"技术 demo"升级成"可收费产品"——补齐 Agent 注册表、辩论分组、哨兵机制等核心体验缺口，接入支付和配额系统。

### 1.2 基线状态
当前代码库（`kquuen/roundtable`，commit `d6b1d43`）已实现：
- FastAPI + 多 Provider LLM + 并发 Agent 调度
- 两轮辩论 + 锚点式个人圆桌
- Supervisor 三层审查 + 语音实时交互
- JWT 认证 + SQLite 持久化 + 24 个测试文件

### 1.3 差距总结
| 维度 | 当前 | 目标 | 差距等级 |
|------|------|------|---------|
| Agent 体系 | 10 个硬编码 YAML skill | registry.json + profiles/*.md + 动态匹配 | 架构级 |
| 辩论体验 | 所有 Agent 一起分析 | 分组辩论 + 用户插话 + 步骤回放 | UX 级 |
| 稳定性 | Supervisor 审查 | 哨兵机制 + 幻觉控制 + 熔断降级 | 基础设施级 |
| 商业化 | 无支付 | 微信/支付宝 + 配额硬拦截 + 报告导出 | 变现级 |
| 前端 | 纯原生 JS | 保持现状（OPC 阶段够用） | 暂缓 |

---

## 2. 执行阶段

### Phase 1：Agent 注册表与动态匹配（Week 1-2）
**目标**：从硬编码 Agent 升级到可配置、可动态匹配的注册表体系。

#### Task 1.1 创建 Agent 注册表
- **文件**：`config/agents/registry.json`
- **格式**：按设计文档 6.1 节，每个 Agent 包含 id/name/emoji/role/domains/keywords/methodology/score_dimension/can_challenge/must_yield_to/max_words/min_words/forbidden_topics/required_output_fields/is_active
- **验收**：`GET /agents` 从 registry.json 读取，不是硬编码

#### Task 1.2 创建人格文件体系
- **目录**：`config/agents/profiles/*.md`
- **格式**：按设计文档 6.2 节，包含身份/方法论/专长领域/辩论风格/输出格式/边界约束
- **验收**：每个 profile 能被独立读取和渲染

#### Task 1.3 实现动态匹配算法
- **文件**：`roundtable/agent_matcher.py`
- **算法**：Jaccard 相似度 + 方法论加分（设计文档 7.2 节）
- **流程**：关键词提取 → 匹配度计算 → 人格文件验证 → 分组 → 宁缺毋滥过滤
- **验收**：输入"帮双非学生写简历的 AI 工具"，输出匹配度 ≥0.5 的 Agent 列表

#### Task 1.4 数据库表：agents
- **表结构**：按设计文档 4.1 节 `agents` 表
- **同步**：registry.json 启动时同步到 SQLite，运行时读缓存
- **验收**：`SELECT * FROM agents WHERE 'AI' = ANY(keywords)`

#### Task 1.5 用户确认分组 API
- **端点**：`POST /session/{id}/confirm-group` + `POST /session/{id}/adjust-group`
- **流程**：展示分组方案 → 用户确认/调整 → 锁定后开始辩论
- **验收**：前端 Step 3 从"雷达图"升级为"分组确认卡片"

---

### Phase 2：结构化辩论与回放（Week 2-3）
**目标**：实现设计文档中的 4 步结构化辩论 + 完整回放能力。

#### Task 2.1 数据库表：debate_groups, debate_steps, debate_events
- **表结构**：按设计文档 4.1 节
- **关系**：debates 1-N debate_groups 1-N debate_steps + debate_events
- **验收**：ER 图与设计文档一致

#### Task 2.2 四步辩论引擎
- **文件**：`roundtable/debate_v2.py`
- **步骤**：
  - Step 1：开场陈述（方法论 + 问题清单 + 数据来源）
  - Step 2：强制质疑（必须指出遗漏/错误/优先级，禁止"我同意"）
  - Step 3：补充新视角（引入前两人没想到的角度）
  - Step 4：回应 → 修正 → 共识（致命缺陷 / 高危缺陷 / 改进建议）
- **验收**：每个 step 的输出是结构化 JSON，不是自由文本

#### Task 2.3 用户插话系统
- **表**：`user_interrupts`
- **端点**：`POST /session/{id}/interrupt`
- **类型**：question / rebuttal / clarify / deep_dive
- **验收**：插话后目标 Agent 必须回应，并记录到 debate_steps

#### Task 2.4 事件回放 API
- **端点**：`GET /session/{id}/events`
- **排序**：sequence_num ASC
- **验收**：前端可完整回放一场辩论的时间线

#### Task 2.5 共识判定与快照
- **表**：`consensus_snapshots`
- **逻辑**：每组每轮输出维度评分 + agreement_level（strong_consensus/partial_consensus/divided/irreconcilable）
- **验收**：共识变化曲线可可视化

---

### Phase 3：哨兵机制与幻觉控制（Week 3-4）
**目标**：建立系统级稳定性保障，让 AI 输出可信、可量化。

#### Task 3.1 Agent 健康监控表
- **表**：`agent_health`
- **字段**：status/failure_count/success_count/circuit_state/total_hallucinations/avg_confidence
- **更新**：每次 Agent 调用后更新
- **验收**：`GET /system/agent-health` 返回所有 Agent 健康状态

#### Task 3.2 熔断器实现
- **文件**：`roundtable/sentinel/circuit_breaker.py`
- **状态**：closed → open（失败率 >50% 连续 5 次）→ half_open（60s 后）
- **行为**：open 时自动降级到 MockProvider
- **验收**：人为制造失败，观察熔断触发

#### Task 3.3 幻觉检测体系
- **文件**：`roundtable/sentinel/hallucination.py`
- **检测维度**：
  - 无来源数字（含数字但无 data_sources）
  - 矛盾声明（同一 Agent 前后矛盾）
  - 越界发言（评价了 forbidden_topics）
  - 低置信度（confidence < 0.5 但未标注"推测"）
- **标记**：hallucination_flags JSONB 写入 debate_steps
- **验收**：幻觉率从"未知"变成"可统计"

#### Task 3.4 哨兵告警 API
- **表**：`sentinel_alerts`
- **端点**：`GET /system/alerts`
- **类型**：boundary_violation / hallucination / timeout / repetition
- **分级**：low/medium/high/critical
- **验收**：告警可配置 Webhook 推送

---

### Phase 4：商业化基础设施（Week 4-5）
**目标**：让产品能收费、能控制成本、能交付正式报告。

#### Task 4.1 计费配额硬拦截
- **文件**：`roundtable/billing.py`
- **逻辑**：
  - 每次 API 调用前检查 `users.monthly_used < users.monthly_quota`
  - Token 用量实时统计（按 Provider 返回的 usage）
  - 超额时返回 429 "Quota exceeded"
- **验收**：免费用户用完配额后无法创建新会话

#### Task 4.2 套餐模型
- **表字段**：`users.plan`（free/pro/team）、`users.trial_expires_at`
- **配额**：
  - free：3 次/月，无导出
  - pro：无限次 + 报告导出 + 自定义 Agent，¥29/月
  - team：Pro + 批量评估 + 协作，¥99/月
- **验收**：`GET /user/usage` 返回剩余配额

#### Task 4.3 报告导出
- **端点**：`POST /session/{id}/export`
- **格式**：PDF（首选）、Word
- **方案**：后端生成 Markdown → 调用 make-pdf skill / pandoc / weasyprint
- **验收**：导出的 PDF 包含报告标题、时间戳、各 Agent 评分、共识结论

#### Task 4.4 支付集成（预留接口）
- **文件**：`roundtable/payment.py`
- **接口**：微信支付 / 支付宝统一下单
- **状态机**：pending → paid → activated
- **验收**：支付回调能正确更新用户套餐和配额

---

### Phase 5：前端增强与运营（Week 5-6）
**目标**：不重构前端框架，但在现有 SPA 上补齐关键交互。

#### Task 5.1 分组确认页面
- **文件**：`frontend/js/ui/step3-v2.js`
- **设计**：卡片式分组展示，每个 Agent 显示匹配度和专长，用户可拖拽调整
- **验收**：与设计文档 8.2 节的 mockup 一致

#### Task 5.2 辩论剧场升级
- **文件**：`frontend/js/ui/debate-theater.js`
- **功能**：
  - 步骤时间线（可点击跳转到任意 step）
  - 用户插话按钮（质疑 / 追问 / 补充）
  - 共识评分可视化（雷达图或进度条）
- **验收**：辩论过程像"聊天记录"一样可读

#### Task 5.3 管理后台（极简）
- **路由**：`/admin`
- **功能**：Agent 健康看板、用户列表、告警列表
- **权限**：`require_admin`
- **验收**：admin 用户可查看系统状态

---

## 3. 技术决策

### 3.1 数据库：继续 SQLite 还是迁移 PostgreSQL？
- **决策**：Phase 1-3 继续使用 SQLite（WAL 模式），Phase 4 评估迁移
- **理由**：当前用户量=1（你自己），PostgreSQL 增加运维负担。SQLite 在 OPC 阶段足够。
- **迁移时机**：日活 >100 或需要多实例部署时

### 3.2 前端：保持原生 JS 还是迁移框架？
- **决策**：保持原生 JS，但引入 Vite 做构建工具
- **理由**：OPC 阶段"轻量优先"，Next.js 增加 30KB+  bundle 和 node_modules 复杂度
- **引入**：Vite（dev server + 压缩）+ 原生 Web Components（可复用组件）

### 3.3 支付：先做国内还是预留国际？
- **决策**：先做微信支付 + 支付宝，Stripe 预留接口
- **理由**：目标客户是国内 OPC 创业者

### 3.4 哨兵：自研还是引入成熟库？
- **决策**：自研熔断器（<100 行），幻觉检测用 LLM 自审
- **理由**：pybreaker 等库过重，且我们需要 LLM-specific 的检测逻辑

---

## 4. 风险与对策

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| Agent 动态匹配效果差 | 中 | 高 | 先上线 5 个固定领域模板，逐步扩展 |
| 四步辩论输出质量不稳定 | 高 | 高 | 增加 Supervisor 审查点，每步都审 |
| 计费系统安全漏洞 | 低 | 高 | 支付回调做签名验证，配额检查服务端强校验 |
| 前端重构拖慢进度 | 低 | 中 | 明确"不重构"原则，只做增量 |
| 技术文档与实现脱节 | 中 | 中 | 每个 Task 都附验收标准，完成后打勾 |

---

## 5. 验收标准

### 5.1 整体完成标准
- [ ] 输入一个想法，系统能自动匹配 Agent、分组、等待用户确认
- [ ] 辩论过程有 4 个明确步骤，每步输出结构化 JSON
- [ ] 用户可在辩论中插话，Agent 必须回应
- [ ] 辩论结束后有共识评分 + 回放时间线
- [ ] 免费用户用完 3 次后无法继续
- [ ] 报告可导出 PDF
- [ ] 系统有 Agent 健康监控面板

### 5.2 代码质量标准
- [ ] 每个新模块都有对应测试文件
- [ ] 新增 API 都有 Pydantic 模型校验
- [ ] 数据库变更有 schema 版本记录（手动记录到 `migrations/`）
- [ ] 前端新增功能不破坏现有 5 步流程

---

## 6. 资源估算

| 阶段 | 人天 | 产出物 |
|------|------|--------|
| Phase 1：Agent 注册表 | 5 | registry.json + matcher + 确认页面 |
| Phase 2：结构化辩论 | 7 | 3 张新表 + 四步引擎 + 回放 |
| Phase 3：哨兵机制 | 5 | 熔断器 + 幻觉检测 + 告警 |
| Phase 4：商业化 | 5 | 配额拦截 + 导出 + 支付接口 |
| Phase 5：前端增强 | 3 | 分组确认 + 剧场升级 + 管理后台 |
| **总计** | **~25 人天** | **V2 可收费版本** |

---

## 7. NOT in scope（明确不做）

- ❌ 微信小程序（Taro 3）— 等 Web 端验证后再做
- ❌ PostgreSQL 迁移 — 日活 <100 时不需要
- ❌ Next.js 重构 — OPC 阶段原生 JS 够用
- ❌ 多语言 Prompt（日韩）— 中文市场优先
- ❌ 实时协作（多人同一会话）— 单用户模式先跑通
- ❌ AI Agent 准确率追踪仪表板 — 运营期再补

---

## 8. 下一步行动

1. **本周**：我帮你实现 Phase 1 Task 1.1-1.3（Agent 注册表 + 人格文件 + 匹配算法）
2. **验证**：跑一遍"帮双非学生写简历"的匹配流程
3. **迭代**：根据匹配效果调整算法参数
4. **推进**：按周执行后续 Phase

要我现在开始实现 Phase 1 吗？
