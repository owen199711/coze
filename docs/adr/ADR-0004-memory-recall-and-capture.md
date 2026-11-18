# ADR-0004: Task-aware recall and candidate capture

- **状态**: Accepted
- **日期**: 2026-11-19
- **决策者**: Atlas 维护者
- **相关**: `src/atlas/knowledge.py`、`src/atlas/agent.py`、`src/atlas/tools.py`、`tests/test_memory.py`、ADR-0001

## 背景

M0 的知识库只有一个 `KnowledgeService`，但召回使用的是
`knowledge.list(limit=12)`：按 `updated_at` 倒序注入最近 12 条，不传当前用户问题，
没有来源/id、没有按 kind 的预算。12 条新噪声可以把真正相关的旧记忆挤出 prompt。

同时，自动写回（ADR-0001）没有落地；知识主要依靠模型主动调用 `knowledge`
工具或人手动 `atlas knowledge add`。另外：

- `feedback(effective)` 会把 quarantined 条目直接复活；
- agent 可以用一句自述 note 把 lesson 提升为 active；
- `LIKE` 回退没有转义 `%`/`_`，查询 `%` 会返回全部 active；
- 写入没有事务，FTS 失败会被静默吞掉。

## 决定

1. **query-aware recall**：新增 `RecallQuery`/`RecallHit` 和
   `KnowledgeService.recall()`。先通过 FTS5 + `knowledge_chunks` 混合取候选，
   再按 `relevance × confidence × freshness × severity` 排序，应用
   `DEFAULT_KIND_BUDGETS` 和 CJK-aware 总 token 预算，返回带 citation 的 hit；
   prompt 默认最多注入 20 条。
2. **prompt 使用 recall**：`build_system_prompt(config, knowledge, query)`
   接收当前用户消息；空 query 保留 recent-list 兜底。Prompt 中展示
   `[kn_id] kind · source · confidence`，要求模型引用 id。
3. **candidate capture**：`KnowledgeService.capture_turn()` 在回合结束捕获
   显式纠正/偏好短语（`不对`/`以后`/`记住`/`prefer`/`remember` 等），只写
   `kind=fact, status=candidate`，带 session/turn source，不做额外 LLM 调用。
   `learning.capture: off | candidate` 控制，默认 candidate。
4. **agent 写入候选优先**：`knowledge` 工具的 `fact/preference/decision/
   assumption/lesson` 写为 candidate；`command/doc/contract/glossary` 可 active。
   CLI `atlas knowledge add` 保持人类写入。
5. **反馈与晋升门禁**：`feedback(effective)` 不得把 quarantined/candidate
   直接变 active；`promote(actor="agent")` 只接受 `method=replay|test`，
   manual 只能由 CLI/human 调用。
6. **写入事务**：`Database.transaction()` 包裹 item + chunks + FTS；FTS 失败
   回滚，不再静默留下不可检索的 item。
7. **LIKE 转义**：搜索回退对 `%`/`_`/`\` 做转义，使用 `ESCAPE`。
8. **Curator replay / failure signature / single-flight**：失败 lesson 按
   `kind + command + 最后一行错误` 的签名去重；`atlas curator run --replay`
   重新执行记录的命令，只有 exit 0 才 `promote(method=replay)`，否则保持 candidate。
   `jobs` 中 1 小时内的 running 记录用于 single-flight。
9. **Source hash / trust / secret scan**：`knowledge_items` 增加 `source_hash`、
   `trust`；`refresh_sources()` 对 file-backed doc/command/contract 计算 hash，
   变化后标 stale；写入拒绝疑似密钥；`recall` 默认排除 untrusted。
10. **TTL / budget**：`consolidate()` 对长期未命中的 fact（365 天）、command
    （180 天）、contract（365 天）标 stale；Curator 每次运行受
    `learning.budget.max_cost_usd_per_night` 限制（当前是 per-run 预算）。
11. **Hybrid chunk retrieval**：`search()` 在 FTS5 结果之外查询
    `knowledge_chunks.content`，按 term 命中数打分；同一 item 的 FTS + chunk
    结果合并为 `origin=hybrid`，chunk snippet 带 `chunk_id`。仍然没有向量索引。

## 后果

**收益**

- 旧但相关的记忆可以进入 top-k；prompt 带 id/source，可反馈、可引用。
- 自动捕获不产生 active 噪声；候选只进入 Review/Curator 流程。
- 修复 quarantine 复活、自我晋升、通配符搜索和部分写入一致性。

**代价 / 推迟**

- capture 是规则式的，不做语义抽取/LLM 提取；可能漏掉非显式纠正。
- 召回是 FTS5 + chunk 词面匹配的混合检索；没有 embedding/向量索引。
- 非 assumption 的通用 TTL 已覆盖 fact/command/contract；Curator 的 idle 调度和
  真正的 per-night 预算仍未落地。
- `permission`/prompt injection 的完整策略仍按 M1 处理；本次只做 secret scan 与
  untrusted 召回过滤。

## 验证

- `tests/test_memory.py::test_recall_prefers_relevant_memory_over_recent_noise`
- `test_prompt_uses_query_aware_recall`
- `test_wildcard_search_is_escaped`
- `test_feedback_does_not_revive_quarantined`
- `test_agent_cannot_self_promote_manual`
- `test_write_rolls_back_when_fts_insert_fails`
- `test_capture_turn_creates_candidate_only`
- `test_agent_knowledge_write_is_candidate`
- `test_consolidate_stales_unused_lesson`
- `test_curator_dedupes_same_failure_signature`
- `test_curator_replay_promotes_passing_candidate`
- `test_untrusted_memory_is_not_recalled_by_default`
- `test_secret_content_is_rejected`
- `test_refresh_sources_marks_changed_doc_stale`
- `test_consolidate_stales_old_unused_fact`
- `test_curator_budget_stops_llm_and_falls_back`
- `test_search_uses_chunks_for_hybrid_recall`

全量：`pytest -q` → 133 passed；`ruff check src tests` 通过；`mypy src` 通过。
