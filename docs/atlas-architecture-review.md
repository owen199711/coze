# Atlas 架构评审：从“完整设计空间”到 Lean Atlas

> 评审对象：[Atlas：基于 AgentScope 的生产级 Coding Agent 技术方案](./atlas-coding-agent-design.md)
> 落地清单：[Lean Atlas v1（8 周实现）](./lean-atlas-v1.md)
> 评审日期：2026-11-17 · 结论：**架构方向合理，但当前版本过于臃肿，不适合直接作为 v1 实现清单。**
> 建议：保留核心思想，合并“记忆 / 知识库 / 经验”，把 17 个组件压到 6 个必选 + 1 个可选，把 27 张表压到约 15 张，先交付一个 8 周内可用的 Lean Atlas。

---

## 0. 结论先行

### 0.1 一句话判断

**这是一份合格的“参考架构”，但不是一份合格的“v1 构建计划”。**

它把 Coding Agent 可能需要的所有能力都写进去了：分层记忆、项目知识库、经验学习、留守线程、六级自我演进、多项目、多服务、上下文压缩、VSCode、评估、合规……概念上大部分是对的，但**名词太多、边界太细、每件事都有三套表示**。如果照着 5,700 行文档逐章实现，团队会在 6 个月后得到一个“什么都有、什么都不好用”的系统。

### 0.2 核心问题

| 问题 | 具体表现 | 后果 |
|---|---|---|
| **三套知识系统** | L0–L4 Memory、Project Knowledge Base、Lesson Store，各自有写入/检索/生命周期 | 同一事实存三份；检索冲突；用户不知道去哪看 |
| **把“会话日志”当记忆层** | L1 Session Journal 被列为记忆层 | 恢复状态与长期知识耦合，表结构和清理策略混乱 |
| **组件过多** | 17 个顶层组件、15 个中间件 | 每加一个功能要改 3–5 个地方；新人一周看不懂 |
| **表过多** | 27 张表，`memory_items`/`lessons`/`kb_documents` 语义重叠 | 迁移、备份、权限、删除级联都变复杂 |
| **配置过多** | 6 份配置：config/project/style/workspace/permissions/learning | 首次配置劝退；团队不知道改哪份 |
| **UI 过重** | 19 个界面功能 | 首版做不完；用户找不到主路径 |
| **演进过早** | 六级自我演进、EvolutionSandbox 在 v1 | 没有真实用户前就做“自改代码”，风险高、收益低 |
| **文档即实现** | 5,700 行、22 章 | 读者把“设计空间”误当“构建清单” |

### 0.3 建议目标（Lean Atlas）

| 维度 | 当前 | Lean Atlas |
|---|---|---|
| 顶层组件 | 17 | **6 个必选 + 1 个可选（Eval）** |
| 中间件 | 15 | **8** |
| 工具 | 20+ | **12** |
| 数据表 | 27 | **约 15** |
| 配置 | 6 份 | **2 份必需 + 1 份可选** |
| UI | 19 项 | **4 个主面板 + 1 个 Review 收件箱** |
| 知识层 | L0–L4 + KB + Lessons | **3 层 + 1 个 KnowledgeService** |
| 存储 | 事件 + 记忆 + 知识 + 经验 | **2 个存储：事件日志 + 知识库** |
| 演进级别 | L1–L6 | **v1 只做 L1（经验）+ L2（规则提案）** |
| MVP 周期 | 20–24 周 | **8 周可用，16 周完整** |

---

## 1. 评审范围与方法

### 1.1 评审维度

| 维度 | 问题 |
|---|---|
| **必要性** | 没有它，用户能否完成核心任务？ |
| **唯一性** | 是否与现有组件职责重叠？能否用现有组件的一个配置/插件替代？ |
| **耦合度** | 改动一个功能要动几个组件？ |
| **可验证性** | 能否用测试/评测证明它有效？ |
| **运维成本** | 多一个服务/表/配置，备份、监控、迁移成本多少？ |
| **认知负担** | 新用户/新工程师能否在一天内理解主路径？ |

### 1.2 评审规则

1. **每个新顶层组件，必须证明它不能被现有组件的一个模块替代**；
2. **每个新表，必须证明它不能表达为现有表的 `kind`/`status`/JSON 字段**；
3. **每个新中间件，必须对应一个现有钩子覆盖不了的行为**；
4. **每个新配置，必须有“零配置默认值”**；
5. **每个新 UI 面板，必须替换一个旧面板，或放在 feature flag 后**。

### 1.3 当前架构的量化画像

| 指标 | 数值 | 评价 |
|---|---|---|
| 文档 | 22 章 + 3 附录，5,700+ 行 | 参考架构可以，构建清单太长 |
| 顶层组件 | 17 | 偏多 |
| 中间件 | 15 | 明显偏多 |
| 数据表 | 27 | 明显偏多 |
| 工具 | 20+ | 偏多 |
| 配置 | 6 份 | 偏多 |
| UI 功能 | 19 项 | 首版做不完 |
| 知识系统 | Memory + KB + Lesson | **重复** |

---

## 2. 总体评分

| 维度 | 分数 | 评价 |
|---|---|---|
| 架构方向 | **8/10** | AgentScope-native、分层、证据驱动、Plan/Test 门禁都正确 |
| 概念完整性 | **9/10** | 该想的都想到了 |
| 简洁性 | **4/10** | 三套知识系统、15 个中间件、27 张表 |
| 可用性 | **6/10** | 19 个 UI 功能、6 份配置，首次体验偏重 |
| 可维护性 | **5/10** | 组件多、边界细，小团队维护成本高 |
| 可扩展性 | **9/10** | 扩展点清晰，但扩展过多 |
| 交付风险 | **7/10** | 按文档全做，大概率延期/臃肿 |

**结论**：方向保留，范围砍半，结构合并。不是“推翻重来”，而是“把同一件事的三种说法合成一种”。

---

## 3. 当前架构的 12 个臃肿点

| # | 臃肿点 | 证据 | 影响 |
|---|---|---|---|
| 1 | **三套知识系统** | 第 6 章 Memory、22.6 Lesson、22.8 PKB | 同一事实三份；检索冲突；生命周期三套 |
| 2 | **5 层记忆 + KB** | L0–L4 + K | 用户/工程师不知道某个信息该放哪层 |
| 3 | **会话日志被当记忆** | L1 Session Journal | 恢复状态与长期知识耦合 |
| 4 | **17 个顶层组件** | 4.2 组件表 | Planner/Approval/Budget/Context 过细 |
| 5 | **15 个中间件** | 5.2 表 | Memory/Lesson 召回、Writeback/Capture 重复 |
| 6 | **27 张表** | 17.2 + 22.6 | `memory_items`/`lessons`/`kb_documents` 重叠 |
| 7 | **20+ 工具** | 15.2 表 | write/edit/patch、glob/grep、memory/lesson 重复 |
| 8 | **6 份配置** | config/project/style/workspace/permissions/learning | 首次配置成本高 |
| 9 | **19 个 UI 功能** | 14.3 表 | 首版做不完；主路径不突出 |
| 10 | **6 级演进** | 22.9 L1–L6 | 没有用户前做自改代码，过早 |
| 11 | **三套召回/写入路径** | MemoryRecall/MemoryWriteback、LessonRecall/LearningCapture、KB builder | 行为不一致、调试困难 |
| 12 | **ProjectService + RepoIntelligence 重叠** | 8.x 项目契约、8.3 模块图、15.2 repo_graph | 项目配置、图、合同、影响分析分散 |

---

## 4. 核心合并：记忆 + 知识库 + 经验 → 一个 KnowledgeService

> **一句话回答：合并存储、索引、写入、检索与生命周期；保留 `kind/scope/status` 的语义差异。不要把“记忆/知识库/经验”做成三个服务，也不要把会话日志并进知识库。**

### 4.1 先澄清概念

| 名词 | 本质 | 是否长期存储 | 现有问题 |
|---|---|---|---|
| 工作上下文 | 每轮模型输入 | 否 | 不是“层”，是装配结果 |
| 会话日志 | 事件 + 检查点 | 是（运行态） | 不是知识，是恢复/回放 |
| 记忆 | 事实/偏好/决策 | 是 | 与 KB/经验重叠 |
| 知识库 | 文档/模块/API/命令 | 是 | 与记忆重叠 |
| 经验（Lesson） | 带验证的行为规则 | 是 | 与记忆/KB 重叠 |
| 代码索引 | AST/符号/引用 | 可重建 | 不是知识，是派生索引 |

**关键洞察**：

> “记忆 / 知识 / 经验”不是三个系统，而是**同一个知识存储的三种视图**。
>
> 区别不在存储，而在：
> - `scope`：user / project / org
> - `kind`：preference / fact / decision / lesson / doc / command / contract / assumption
> - `status`：candidate / active / stale / retired
> - `validation`：是否需要回放/测试/人审
> - `freshness`：TTL / 来源哈希 / 版本

会话日志**不合并**：它是 append-only 事件与检查点，用于恢复/回放，不是“我们相信的知识”。

### 4.2 目标模型：3 层 + 2 个存储

```
┌─────────────────────────────────────────────────────────────┐
│ L0 Working Context（不存储）                                 │
│ 每轮由 ContextGovernor 从任务、会话、知识库装配               │
└───────────────────────┬─────────────────────────────────────┘
                        │ 读取
┌───────────────────────▼─────────────────────────────────────┐
│ Knowledge Store（一个逻辑库，物理 2 张主表 + 对象存储）       │
│ knowledge_items  +  knowledge_chunks  (+ links 可选)         │
│                                                             │
│ 3 个 scope：project / user / org                             │
│ N 个 kind：preference/fact/decision/lesson/doc/command/...   │
│ 5 个 status：candidate/active/stale/retired/quarantined      │
└───────────────────────┬─────────────────────────────────────┘
                        │ 独立
┌───────────────────────▼─────────────────────────────────────┐
│ Session Journal（运行态，不叫记忆）                           │
│ sessions / turns / events / checkpoints                      │
│ 用于恢复、回放、审计、证据引用                                │
└─────────────────────────────────────────────────────────────┘
```

- **L0 Working Context**：每轮装配，不持久化；不是“记忆层”。
- **L1 Project Knowledge**：项目级知识（事实、决策、经验、文档、命令、合同、模块卡）。
- **L2 User/Org Knowledge**：跨项目偏好/组织规范；用 `scope` 区分，不单独建系统。
- **Session Journal**：运行态事件与检查点，独立存储。
- **代码索引**：`CodebaseService` 的派生索引，独立但通过统一检索 facade 暴露。

### 4.3 一个 `knowledge_items` 表覆盖所有知识

```sql
CREATE TABLE knowledge_items (
  id            TEXT PRIMARY KEY,
  tenant_id     TEXT NOT NULL,
  project_id    TEXT,                       -- org/user 级可为空
  owner_id      TEXT,
  scope         TEXT NOT NULL,              -- user | project | org
  kind          TEXT NOT NULL,              -- preference|fact|decision|lesson|doc|command|contract|assumption|glossary
  status        TEXT NOT NULL,              -- candidate|active|stale|retired|quarantined
  title         TEXT NOT NULL,
  body          TEXT NOT NULL,              -- Markdown 正文；doc 类可只存摘要 + 引用
  tags_json     TEXT,
  source_json   TEXT NOT NULL,              -- session/file/url/command/decision 及其 hash/时间
  confidence    REAL,
  severity      TEXT,
  validation_json TEXT,                     -- lesson 的回放/测试/人审结果
  freshness_at  TIMESTAMP,
  expires_at    TIMESTAMP,
  supersedes_json TEXT,
  sensitivity   TEXT,                       -- public|internal|secret
  created_at    TIMESTAMP NOT NULL,
  updated_at    TIMESTAMP NOT NULL
);

CREATE TABLE knowledge_chunks (
  id            TEXT PRIMARY KEY,
  item_id       TEXT NOT NULL REFERENCES knowledge_items(id),
  project_id    TEXT,
  ordinal       INTEGER,
  content       TEXT NOT NULL,
  symbol        TEXT,                        -- 函数/类/标题
  start_line    INTEGER,
  end_line      INTEGER,
  token_count   INTEGER,
  embedding_ref TEXT
);
-- 可选：关系图；v1 可不建，先用 tags + 结构字段
CREATE TABLE knowledge_links (
  from_id       TEXT,
  to_id         TEXT,
  relation      TEXT,                        -- depends_on|supersedes|related
  PRIMARY KEY (from_id, to_id, relation)
);
```

**一个 schema 如何覆盖原来的三套**：

| 原对象 | 新表示 |
|---|---|
| 用户偏好 | `scope=user, kind=preference` |
| 项目事实/约定 | `scope=project, kind=fact` |
| 架构决策 | `scope=project, kind=decision`，链接到 plan/commit |
| 经验（Lesson） | `scope=project, kind=lesson, status=active, validation_json` |
| 项目知识卡 | `scope=project, kind=doc` |
| 模块/API 卡 | `scope=project, kind=doc`（或 `module`/`api` 子类型） |
| 命令/坑点 | `kind=command` / `kind=fact` |
| 合同 | `kind=contract` |
| 假设 | `kind=assumption` + `expires_at`（也可放 plan JSON） |
| 组织规范 | `scope=org, kind=fact/preference` |
| 候选经验 | `status=candidate` |
| 淘汰经验 | `status=retired` |
| 可疑/投毒 | `status=quarantined` |

**原表的下场**：

| 原表 | 处理 |
|---|---|
| `memory_items` | 合并进 `knowledge_items` |
| `lessons` | 合并进 `knowledge_items`（kind=lesson） |
| `learning_signals` | 复用 `events`（type=`knowledge.signal`），不建新表 |
| `learning_candidates` | 用 `knowledge_items.status=candidate`，不建新表 |
| `learning_runs` | 用 `jobs` + `usage`，不建新表 |
| `kb_documents` | 合并进 `knowledge_items` |
| `kb_chunks` | 合并进 `knowledge_chunks` |
| `kb_entities` | v1 不做；需要时再加 `knowledge_links` |
| `unknowns` / `assumptions` | 放 `plans` JSON，或 `kind=assumption`；v1 不建表 |

### 4.4 一套写入管线

```
信号/显式写入
  → 归一化（title/body/source/scope/kind）
  → 去重（哈希 + 相似度）
  → 分类（kind 决定策略）
  → 校验（kind 特定）
  → 状态（candidate/active）
  → 分块 + 索引
  → 审计
```

按 `kind` 的写入策略：

| kind | 写入者 | 校验 | 默认状态 |
|---|---|---|---|
| preference | 用户/Agent（需确认） | 用户确认 | active |
| fact | Agent + 来源 | 来源可追溯 | active（低置信 candidate） |
| decision | 计划批准 | 人审 | active |
| lesson | Curator | 回放/合成测试/人审 | candidate → active |
| doc | Curator/人 | 来源 hash + 人审（架构/API/事故） | active |
| command | 验证通过 | 命令证据 | active |
| contract | 合同文件 | 合同校验 | active |
| assumption | Agent | 记录依据 + 过期时间 | candidate/active |

### 4.5 一套检索管线

```
query
  → 意图/范围（project/user/org；kind 过滤）
  → 混合召回（FTS5/BM25 + 可选向量）
  → 去重/重排（relevance × confidence × freshness × severity）
  → 预算（per-kind）
  → 摘要 + 引用
```

按 `kind` 的召回策略：

| kind | 何时召回 | 预算 |
|---|---|---|
| preference | 每轮常驻（核心偏好） | ≤ 300 tokens |
| fact/decision | 计划/探索时 | ≤ 800 tokens |
| lesson | 计划开始、工具前、评审 | ≤ 800 tokens |
| doc/module/api | 探索/按需 | ≤ 3000 tokens |
| command | 工具前 | ≤ 300 tokens |
| contract | 计划/影响分析 | ≤ 500 tokens |
| assumption | 计划/交付报告 | ≤ 300 tokens |

**一套 API**：

```python
knowledge.search(query, scope=None, kinds=None, limit=8)
knowledge.get(id)
knowledge.write(item)          # 需权限；candidate/active 由 kind 策略决定
knowledge.feedback(id, outcome)
knowledge.forget(id)
```

原来的 `memory_search`、`lesson_search`、`kb_search` 合并为 `knowledge.search`；`memory_save`、`lesson_save`、`kb_build` 合并为 `knowledge.write`/`knowledge.refresh`。

### 4.6 一套生命周期

```
candidate → active → stale → retired
                 └→ quarantined（冲突/投毒/验证失败）
```

按 `kind` 的 TTL/验证：

| kind | TTL/衰减 | 验证 |
|---|---|---|
| preference | 长期；用户可改 | 用户确认 |
| fact | 来源变更即 stale | 来源可追溯 |
| decision | 长期 | 计划批准 |
| lesson | 90 天未命中降级 | 回放/测试/人审 |
| doc | 来源 hash 变 → stale | 人审（关键卡） |
| command | 版本变更 → stale | 命令证据 |
| contract | 合同版本变更 → stale | 合同校验 |
| assumption | 到期自动 retired | 依据 + 过期 |

**Curator 不再是服务**：它是 `KnowledgeService` 的一个后台 Job：

```
KnowledgeService
├── Store（items/chunks）
├── Index（FTS/vector）
├── Writer（按 kind 的 extractor）
├── Retriever（按 kind 的 policy）
├── CuratorJob（巩固/验证/淘汰/建库）
└── ACL（scope/tenant）
```

“留守线程”保留，但它只是 `CuratorJob` 的调度器；不再有 `LearningService`、`LessonStore`、`PKB` 三个服务。

### 4.7 四个账本怎么办

“事实账本 / 假设账本 / 决策账本 / 验证账本”是好概念，但不需要四个系统：

| 账本 | 在新模型中的位置 |
|---|---|
| 事实账本 | `knowledge_items` + `source_json` |
| 假设账本 | `kind=assumption` 或 plan JSON |
| 决策账本 | `kind=decision` + plan/commit 链接 |
| 验证账本 | `verifications`（不可变证据，独立于知识） |

**验证证据不合并进知识库**：它是不可变的执行事实，知识是经过策展的解释。二者分开，审计才可信。

### 4.8 不要合并的四样东西

合并不是“把所有东西塞进一张表”。以下四样必须保持独立：

| 对象 | 为什么独立 |
|---|---|
| **Session Journal** | 运行态事件/检查点，append-only；用于恢复/回放/审计，不是“我们相信的知识” |
| **Codebase Index** | 从代码派生的 AST/符号/引用索引，可重建、高 churn；与策展知识生命周期不同 |
| **Verification Evidence** | 不可变的命令/退出码/输出哈希；知识可变，证据不可变 |
| **Evolution Proposal** | 对规则/技能/代码的变更提案，有审批/评测/回滚；不是“知识”，是“变更单” |

**统一的是入口，不是存储**：用户/Agent 通过一个 `knowledge` 工具和一个 `ContextGovernor` 访问；底层仍按对象职责分开。

### 4.9 迁移策略（如果已有旧实现）

```
1. 新建 knowledge_items/knowledge_chunks
2. 写 facade KnowledgeService，旧 API 全部转发
3. 双写：memory/lessons/kb 写入同时写 knowledge
4. 回填：把旧表按 kind/scope/status 映射导入
5. 读切换：先 10% 流量读 knowledge，对比结果
6. 下线旧表/旧服务；保留 30 天回滚
```

映射规则：

| 旧表 | 新 kind | 新 scope | 新 status |
|---|---|---|---|
| `memory_items`（user） | `preference/fact` | user | active |
| `memory_items`（project） | `fact/decision` | project | active |
| `lessons` | `lesson` | project | candidate/active |
| `kb_documents` | `doc` | project | active/stale |

如果是新项目，直接上 Lean Atlas，不做双写。

---

## 5. 其他合并

### 5.1 组件：17 → 6 必选 + 1 可选

| 当前 | Lean 归属 | 说明 |
|---|---|---|
| SessionActor | **Orchestrator** | 会话、轮次、队列、审批、预算、任务 DAG |
| DurableTaskGraph | **Orchestrator** | 任务持久化，内部模块 |
| Planner | **AgentRuntime**（角色） | “Planner”是模型角色，不是服务 |
| ApprovalBroker | **Orchestrator** | HITL 队列，不需要独立服务 |
| BudgetManager | **ContextGovernor** | 预算与装配合并 |
| ContextAssembler | **ContextGovernor** | 统一上下文装配 |
| MemoryService | **KnowledgeService** | 合并 |
| LearningService | **KnowledgeService** | 合并 |
| ProjectKnowledgeBase | **KnowledgeService** | 合并 |
| LessonStore | **KnowledgeService** | 合并 |
| EvolutionSandbox | **Phase 3** | v1 不做 |
| ProjectService | **CodebaseService** | 项目配置/合同/规则 |
| RepoIntelligence | **CodebaseService** | 图/LSP/索引 |
| ToolRuntime | **ExecutionService** | 工具 + 沙箱 |
| VerificationService | **ExecutionService** | 测试/lint/证据 |
| ModelGateway | **Platform** | 模型路由/计费 |
| EventBus | **Platform** | 事件流 |
| Observability | **Platform** | OTel/指标 |
| Storage | **Platform** | SQLite/Postgres |

**Lean 组件**：

1. **Orchestrator**：会话、轮次、审批、任务 DAG、预算、调度；
2. **AgentRuntime**：AgentScope `Agent` + 8 个中间件 + prompts；
3. **KnowledgeService**：知识存储、检索、写入、Curator；
4. **CodebaseService**：项目配置、模块图、合同、LSP/tree-sitter、影响分析；
5. **ExecutionService**：工具、沙箱、测试/lint/build、证据；
6. **Platform**：存储、模型、事件、遥测、协议（必选底座）；
7. （可选）**EvalService**：离线评测，可先做脚本，Phase 2 再服务化。

### 5.2 中间件：15 → 8

| Lean 中间件 | 合并来源 | 职责 |
|---|---|---|
| **Telemetry** | Audit + Tracing | 事件、trace、脱敏、成本 |
| **Context** | Budget + Compaction + Manifest | 预算、压缩、manifest |
| **Journal** | Journal | 检查点、恢复 |
| **Inbox** | Inbox | 插话、后台结果、审批结果 |
| **KnowledgeRecall** | MemoryRecall + LessonRecall | 一次检索，按 kind 过滤 |
| **ProjectContract** | ProjectContract | 项目契约/风格摘要 |
| **Policy** | PlanMode + ToolPolicy + UncertaintyGuard | 阶段/权限/未知/路径/命令规则 |
| **Delivery** | TestGate + MemoryWriteback + LearningCapture | 交付门禁 + 知识写回 |

说明：

- **Policy** 是一个“策略引擎”，内部是规则集，不是一坨代码；PlanMode 是其中一条 phase rule。
- **Delivery** 在回复结束前跑门禁，结束后写知识；失败不阻塞用户，但阻断“交付”。
- 如果你觉得 Policy 太大，可以在代码里拆成 3 个模块，但**对外仍是一个中间件**，减少装配复杂度。

### 5.3 工具：20+ → 12

| Lean 工具 | 合并来源 | 说明 |
|---|---|---|
| `read` | read_file | 支持行范围 |
| `edit` | write_file + edit_file + apply_patch | `mode=create/replace/patch` |
| `search` | glob + grep | `mode=files/content` |
| `shell` | run_shell + env_probe | 沙箱命令 |
| `git` | git | status/diff/log/commit（受限） |
| `check` | run_tests + lint + typecheck + project_validate | 统一“跑检查” |
| `lsp` | lsp | diagnostics/symbols/references/rename |
| `web` | web_search + web_fetch + docs_lookup | `action=search/fetch/docs` |
| `ask_user` | ask_user | 选项式提问 |
| `knowledge` | memory_* + lesson_* + kb_* | `action=search/get/write/feedback` |
| `plan` | plan_write + plan_exit + task_* | 计划/任务 |
| `subagent` | agent_spawn/send/list/task_output | 子 Agent |

12 个工具覆盖读写、搜索、执行、检查、知识与编排；其余（浏览器、数据库、Jira、K8s）通过 MCP/工具组按需启用。

### 5.4 表：27 → 约 15

**保留**：

`sessions`, `turns`, `events`, `checkpoints`, `tasks`, `plans`, `verifications`, `approvals`, `usage`, `artifacts`, `jobs`, `projects`, `knowledge_items`, `knowledge_chunks`, `knowledge_links`（可选）。

**合并/删除**：

- `memory_items`, `lessons`, `kb_documents`, `kb_chunks`, `kb_entities` → `knowledge_*`；
- `learning_signals`, `learning_candidates`, `learning_runs` → `events`/`status`/`jobs`；
- `unknowns`, `assumptions` → `plans` JSON / `kind=assumption`；
- `job_deps` → `jobs.depends_on_json`；
- `subagents`/`prs` → 可先由 `events`/git 派生，或 Phase 2 再加。

### 5.5 配置：6 → 2+1

| 配置 | 作用 | 必需 |
|---|---|---|
| `atlas.yaml` | 项目、模块、风格、学习、权限的 sections | 是 |
| `AGENTS.md` | 仓库级人话指令（标准） | 是 |
| `permissions.yaml` | 企业/安全覆盖（可选） | 否 |

- 风格默认用预设，`atlas.yaml` 只写 `style: google-ts` 这类一行；
- 学习默认开，`learning:` section 可选；
- 多项目 `workspace:` section 可选；
- 权限默认安全，企业才需要独立文件。

### 5.6 UI：19 → 4+1

| 面板 | 内容 | 优先级 |
|---|---|---|
| **Chat** | 对话、工具卡片、审批、上下文提及 | P0 |
| **Plan** | 计划评审、批准/修改 | P0 |
| **Diff** | 原生 diff、逐 hunk 接受/拒绝 | P0 |
| **Tasks** | 任务 DAG、状态、阻塞 | P0 |
| **Review** | 学习收件箱：候选经验/提案，批准/拒绝/编辑 | P1 |

**后置**：记忆浏览器、知识库浏览器、风格向导、上下文检查器、证据面板、检查点时间线、多仓库视图。它们可以后续以“Chat 内的 tab”或命令面板形式加，不占首版主路径。

---

## 6. Lean Atlas 目标架构

```
┌──────────────────────────────────────────────────────────────┐
│ VSCode：Chat / Plan / Diff / Tasks / Review                  │
└───────────────────────────┬──────────────────────────────────┘
                            │ JSON-RPC 2.0 (stdio/WS)
┌───────────────────────────▼──────────────────────────────────┐
│ Orchestrator                                                 │
│ sessions · turns · approvals · task DAG · budgets · schedule │
└───────────────────────────┬──────────────────────────────────┘
                            │ Agent.reply_stream
┌───────────────────────────▼──────────────────────────────────┐
│ AgentRuntime（AgentScope Agent + 8 middleware）               │
│ Telemetry · Context · Journal · Inbox                        │
│ KnowledgeRecall · ProjectContract · Policy · Delivery        │
└───────┬───────────────────────┬───────────────────────┬──────┘
        │                       │                       │
┌───────▼──────────┐ ┌──────────▼─────────┐ ┌───────────▼──────┐
│ KnowledgeService │ │ CodebaseService    │ │ ExecutionService │
│ items/chunks     │ │ project/graph/LSP  │ │ tools/sandbox    │
│ curator job      │ │ contracts/impact   │ │ tests/evidence   │
└───────┬──────────┘ └──────────┬─────────┘ └───────────┬──────┘
        │                       │                       │
┌───────▼───────────────────────▼───────────────────────▼──────┐
│ Platform：Storage · ModelGateway · EventBus · Telemetry       │
│ SQLite/Postgres + FTS/vector · OTel · JSON-RPC               │
└──────────────────────────────────────────────────────────────┘
```

**三条不变量**：

1. **一个知识入口**：所有“记住/学到/查到”都走 `KnowledgeService`；
2. **一个恢复入口**：所有“继续/回滚/审计”都走 Session Journal；
3. **一个装配入口**：所有“模型看到什么”都走 `ContextGovernor`。

---

## 7. 需求覆盖检查：瘦身之后还能满足 10 条需求吗？

| 需求 | Lean 机制 | 是否保留 |
|---|---|---|
| 1. 分层记忆、长期偏好 | KnowledgeService（scope=user/project/org，kind=preference/fact/decision） | ✅ |
| 2. 跨 Session 保存 | Session Journal（sessions/turns/events/checkpoints） | ✅ |
| 3. 约束项目结构与风格 | CodebaseService + Policy + `atlas.yaml` | ✅ |
| 4. 多前端/后端协作 | CodebaseService 的 workspace/contracts；Phase 2 | ✅（后置） |
| 5. 任务拆解、规划、测试先行 | Orchestrator + Plan 面板 + Delivery 门禁 | ✅ |
| 6. 不知道时不猜测 | Policy（unknown router）+ `ask_user` + 证据 | ✅ |
| 7. 长任务/复杂任务 | Orchestrator DAG + Journal + subagent | ✅ |
| 8. 上下文压缩/监控/限额 | ContextGovernor（预算/压缩/manifest） | ✅ |
| 9. 交互与 VSCode | Chat/Plan/Diff/Tasks + Review | ✅ |
| 10. 越用越聪明/项目知识库 | KnowledgeService + CuratorJob + lesson lifecycle | ✅ |

**瘦身没有砍掉能力，砍掉的是“同一能力的多套实现”。**

---

## 8. MVP 范围与阶段计划

> v1 的可执行清单（含代码结构、15 张表、8 中间件、12 工具、8 周里程碑）见 [Lean Atlas v1](./lean-atlas-v1.md)。

### 8.1 Phase 0：冻结与删除（1 周）

- 把本文档定为“参考架构”，另建 `Lean Atlas` 实现清单；
- 删除/合并：MemoryService、LearningService、PKB、LessonStore、EvolutionSandbox；
- 合并表：memory/lessons/kb → knowledge；
- 合并中间件/工具/UI/配置；
- 定复杂度预算（见第 10 节）。

### 8.2 Phase 1：MVP（6–8 周）

**必须做**：

- Orchestrator：会话、审批、任务 DAG、预算；
- AgentRuntime：8 个中间件；
- KnowledgeService：items/chunks、FTS5、写入/检索、lesson 验证；
- CodebaseService：`atlas.yaml` 解析、模块图基础版；
- ExecutionService：sandbox、read/edit/search/shell/git/check；
- VSCode：Chat/Plan/Diff/Tasks；
- Session Journal：恢复/检查点；
- 评测：10 个 golden tasks + 重复错误基线。

**验收**：

- 单仓库、单语言、单用户；
- 5 个真实任务端到端：计划 → 红-绿 → 证据 → 草案 PR；
- 杀掉进程可恢复；
- 知识写入/召回可用，且可删除；
- 新工程师 1 天能跑通主路径。

### 8.3 Phase 2：增强（3–4 周）

- CodebaseService：LSP/SCIP、合同、影响分析、多模块；
- KnowledgeService：CuratorJob、周报、知识卡、冲突/过期；
- 多仓库/workspace；
- 子 Agent；
- Review 面板。

### 8.4 Phase 3：可选（按需）

- 自我演进 L2–L6；
- 团队服务端/多租户；
- GraphRAG；
- 风格向导；
- 组织知识。

**原则**：Phase 3 的每一项都必须证明“没有它，用户完不成任务”，否则不做。

---

## 9. 合并风险与缓解

| 风险 | 表现 | 缓解 |
|---|---|---|
| **知识库变垃圾场** | 什么都往里塞，检索污染 | 按 kind 配额；写入校验；TTL；人工审计 |
| **语义丢失** | 把 lesson 当普通 fact，失去验证 | `kind` 驱动策略；lesson 必须 `validation_json` |
| **生命周期冲突** | doc 要新鲜、lesson 要验证、preference 要长期 | 按 kind 的 TTL/验证策略，不搞一刀切 |
| **权限混乱** | user/project/org 混读 | 服务端强制 scope 过滤；跨 scope 显式授权 |
| **单体化** | KnowledgeService 变成上帝服务 | 内部拆 Store/Index/Writer/Retriever/Curator/ACL，接口清晰 |
| **迁移复杂** | 旧表/旧 API | 先做 facade `KnowledgeService`，双写/ETL，再删旧表 |
| **检索性能** | 一个索引变大 | 按 project 分库/分区；chunk 级过滤；预算 |
| **学习剧场** | 存了不召回 | 强制“召回—度量—淘汰”；Review 面板 |

---

## 10. 复杂度预算（建议写进团队规范）

### 10.1 硬上限

| 维度 | 上限 | 超出时 |
|---|---|---|
| 顶层服务 | 6 必选 + 1 可选 | 必须删除/合并 ≥2 个，或 ADR |
| 中间件 | 8 | 必须证明现有钩子覆盖不了 |
| 工具 | 12 | 必须证明核心工具无法扩展 |
| 数据表 | 15 | 必须证明不能表达为 kind/JSON |
| 配置 | 2+1 | 必须提供零配置默认 |
| UI 主面板 | 4+1 | 必须替换旧面板或 feature flag |
| 知识层 | 3 | 不允许再加“第 5 层记忆” |

### 10.2 评审问题

每个新功能上线前问：

1. 它能否作为现有 `kind` 的一个值？
2. 它能否作为现有服务的一个模块？
3. 它能否作为现有中间件的一条规则？
4. 它能否作为现有工具的一个 `mode`？
5. 它能否默认关闭/零配置？
6. 删除它，用户会失去什么？如果答不上来，就不做。

---

## 11. 决策记录

| ID | 决策 | 理由 |
|---|---|---|
| R1 | **合并 Memory + KB + Lessons 为 KnowledgeService** | 同一存储的三种视图；减少三套写入/检索/生命周期 |
| R2 | **Session Journal 独立** | 运行态事件/检查点不是知识；恢复与知识分离 |
| R3 | **3 层而非 5 层** | working/project/user-org 足够；L0 不存储 |
| R4 | **6 个必选 + 1 个可选组件** | 降低装配/维护成本；内部模块化 |
| R5 | **8 个中间件** | 合并重复的 Recall/Writeback/Capture/Budget |
| R6 | **12 个工具** | 合并 write/edit/patch、glob/grep、memory/lesson/kb |
| R7 | **2+1 份配置** | 零配置默认；团队覆盖可选 |
| R8 | **4+1 个 UI 面板** | 首版主路径清晰；其余后置 |
| R9 | **v1 不做 L3–L6 自我演进** | 没有用户前，自改代码风险高、收益低 |
| R10 | **复杂度预算** | 防止架构再次膨胀 |

---

## 12. 附录：旧章节处置表

| 章节 | 处置 | 去向 |
|---|---|---|
| 1–3 背景/目标/原则 | 保留，压缩 | Lean Atlas 文档 |
| 4 总体架构 | 保留，替换为目标图 | 第 6 节 |
| 5 运行时 | 保留 | AgentRuntime |
| 6 记忆 L0–L4 | **合并** | KnowledgeService |
| 7 跨 Session | 保留，改名“Session Journal” | Orchestrator/Platform |
| 8 项目结构与风格 | **合并** | CodebaseService |
| 9 多项目 | 后置 Phase 2 | CodebaseService/workspace |
| 10 规划/测试 | 保留 | Orchestrator/AgentRuntime/Delivery |
| 11 不确定性 | 保留 | Policy |
| 12 长任务 | 保留 | Orchestrator/Journal |
| 13 上下文 | 保留，合并 Budget | ContextGovernor |
| 14 VSCode | **裁剪** | 4+1 面板 |
| 15 工具 | **裁剪** | 12 工具 |
| 16 安全 | 保留 | Policy/Execution |
| 17 数据模型 | **压缩** | ~15 表 |
| 18 评估 | 保留，缩小 | Eval |
| 19 性能/成本 | 保留 | Platform |
| 20 路线图 | **重写** | 8/16 周计划 |
| 21 风险 | 保留 | 本文第 9 节 |
| 22 学习/知识库 | **合并/裁剪** | KnowledgeService；Curator；只留 L1/L2 |
| 附录 A 提示词 | 保留 | AgentRuntime |
| 附录 B 参考 | 保留 | 研究依据 |
| 附录 C 术语 | 保留，合并术语 | Knowledge |

---

## 13. 最终建议

1. **不要按 5,700 行文档逐章实现**；把它当“设计空间”和“需求清单”。
2. **先做 Lean Atlas**：6 个必选 + 1 个可选服务、8 中间件、12 工具、4+1 UI、2+1 配置、约 15 表。
3. **记忆/知识库/经验合并**：这是最大、最值得做的简化；语义靠 `kind/scope/status/validation` 保留。
4. **会话日志独立**：它不是知识，是恢复与审计。
5. **代码索引独立但统一入口**：CodebaseService 负责派生索引，KnowledgeService 负责策展知识，二者通过 `ContextGovernor` 汇合。
6. **自我演进后置**：v1 只做“经验 + 规则提案”，不做 L3–L6。
7. **用复杂度预算约束后续扩展**：每加一个名词，先问能否用 `kind`/模块/规则/`mode` 表达。

> 一句话：**把“记忆、知识库、经验”合成一个知识服务，把“恢复”留给会话日志，把“执行”留给执行服务；少三个服务、少十二张表、少七个中间件，Agent 不会变笨，只会更好维护。**
