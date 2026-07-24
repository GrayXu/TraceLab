# total_input_growth

**在单个编码会话内，总输入长度（前缀 + 追加）从一个智能体步骤到下一个是如何变化的——当它缩小时，那是计数抖动还是一次真正的上下文压缩？**

## 实验概览

trace 中的每一行是一个智能体步骤。本实验按顺序遍历一个会话的各个步骤，记录每个步骤的**总输入长度**（`prefix_tokens +
newly_append_tokens`）变化——正的 delta 表示窗口在增长，负的 delta 表示在缩小——并把每一次下降归入三个桶之一。

方法与假设：

- 一个步骤的**总输入**为 `prefix_tokens + newly_append_tokens`（缓存的前缀加上新追加的输入）。每个步骤的指标是该数量相对于**同一会话中上一个被看到的步骤**的有符号 delta。
- **配对。** 只有当当前步骤的**第一个 timing event** 是一个可见的输入事件——即一个 `user_message` 或一个 `tool_result`（该步骤的*触发器*）——且该会话此前已被看到过时，才会发出一个增长事件。`previous` 步骤是按 trace 顺序为该会话最后观察到的任意步骤，与其触发器无关。会话内的步骤按 ingestion 顺序排序（`round_pk` = 文件顺序），即迁移到 DuckDB 之前的扫描所用的同一行顺序定序。
- **缩减桶**（阈值来自 `artifacts/utils/growth.py`，可在 CLI 覆盖）：
  - **micro-reduction** —— 下降 `≤ 1024` 个 token（计数抖动）；
  - **major-reduction** —— 下降 `≥ 50000` 个 token（一次真正的上下文压缩）；
  - **ordinary reduction** —— 介于两者之间的任何情况。
- **报告的触发器。** Summary 行按三种方式切分——`all`、`user` 与 `tool_result`——依据当前步骤的触发器，并按 scope（`merged` 加上每个提供商）切分。
- 与 `trace_facts` overview summary 共用增长 helper（`build_growth_stats`、`reduction_bucket`、CSV writer）。

## 代码结构

这是一个**混合式（hybrid）**实验：trace DuckDB 负责单遍 ingest，而 Python 负责按会话的定序与增长分桶。

- `iter_growth_events_from_db(con)` —— 唯一的数据加载代码。两条按 ingestion 顺序的查询（步骤标量 `ORDER BY round_pk`，以及每个步骤在 `event_index = 1` 处的*第一个* timing event，用于取触发器类型和时间戳），在 Python 中借助一个 `last_by_session` map 遍历，为每个合格的步骤发出一个增长事件——精确复现旧的逐行 JSONL 扫描。
- `_epoch_us_to_iso(...)` —— 时间戳以整数 epoch-microseconds 拉取（native/wasm 一致），并重建为规范的 `…Z` ISO 字符串，因此时间戳列与迁移到 DuckDB 之前的输出逐比特一致。
- `build_growth_stats(...)` / `reduction_bucket(...)` / `write_summary_csv(...)` /
  `write_events_csv(...)` —— `artifacts/utils/growth.py` 中未改动的共享 helper。
- `write_filtered_events_csv(...)` —— 稳定排序后的 reduction / micro-reduction 下钻。

数据层位于 `artifacts/utils/trace_db.py`（参见 `artifacts/utils/DB_SCHEMA.md`）。

## 运行方式

```bash
# default merged trace (materialized to a temp DuckDB cache on first use)
uv run python artifacts/session/total_input_growth/analyze.py

# a specific trace
uv run python artifacts/session/total_input_growth/analyze.py -i trace/sample.jsonl

# a prebuilt DB (run_all.py's build-db step passes this), into a chosen dir
uv run python artifacts/session/total_input_growth/analyze.py --db "$TMPDIR/trace.duckdb" -o "$TMPDIR/out"
```

旋钮：`--micro-reduction-max-tokens` / `--major-reduction-min-tokens` 重新调校缩减桶，
`--no-drilldowns` 只写 summary，`--limit-events` 在稳定排序后对每个下钻设上限，
而 `--summary-csv` / `--events-csv` / `--reductions-csv` / `--micro-csv` 覆盖各自的路径。

## 输出

- `total_input_growth_summary.csv` —— 按 `(scope, trigger)` 的增长/缩减桶计数及 delta 统计。
- `total_input_growth.md` —— 论文 float `tab:context_growth_and_compaction` 的 GFM 镜像
  （Claude vs Codex，按步骤触发器），渲染在网页详情页上。
- `total_input_growth_events.csv` —— 每一个同会话的增长事件，按 trace 顺序。
- `total_input_reductions.csv` —— 仅 negative-delta 事件（全部三个缩减桶）。
- `total_input_micro_reductions.csv` —— 仅 micro-reduction 事件。

无图。

## SyFI result analysis

### total_input_growth.md

上下文几乎总是逐步骤增长（论文的 `tab:context_growth_and_compaction`）。在所有步骤中，窗口在 **99.59%** 的 Claude 步骤和 **97.18%** 的 Codex 步骤上增长——新的用户输入、工具结果以及输出全都叠加到先前的上下文之上——每个增长的步骤增加约 1.8k 个 token。缩减很罕见，且呈现提供商特性：对 Claude 而言，负值占 0.34%，且偏向 **major**（0.22%，真正的压缩），而 Codex 的缩减占 2.81%，且大多落在无害的 **micro/ordinary** 区间。这一分布还按触发器集中：Codex 的缩减堆积在**用户触发**步骤上（其中 20.91% 的步骤缩小，对比工具触发的 0.85%），而两个提供商的工具触发步骤都有约 99% 的时间在增长。

### total_input_growth_summary.csv

核心表格。每一行是一个 `(scope, trigger)` 切片。先读 **positive / zero / negative** 这个划分：在样本中窗口在约 99.6% 的步骤上增长，所以上下文累积是压倒性的常态，而缩减很罕见。在 negative 之内，**micro / ordinary / major** 这几列把无害的计数抖动与真正的压缩区分开——major-reduction 只占很小一部分，但携带最大的 `max_reduction`。`avg_raw_delta` / `p10` / `median` / `p90` 这几列描述每个步骤的增长分布，而 `total_context_increase` 是累加的正向增长。对比 `user` 与 `tool_result` 触发器的行，可以看出缩减是聚集在用户触发步骤还是工具触发步骤周围。

### total_input_growth_events.csv

完整的事件级下钻——每个同会话步骤对一行，含上一/当前步骤的索引（`round_index`）、total/prefix/append 的 token 数及其 delta、触发器、模型、时间戳和 trace key。这是 summary 背后的原材料；用它可以把任意单个 delta 追溯回它的那两个步骤。

### total_input_reductions.csv

negative-delta 子集，按 `(provider, trigger, session_id, current_line_number)` 稳定排序。真正的压缩就住在这里——按 `raw_delta_tokens` 排序或过滤，可以找到最大的上下文坍缩，并查看产生它们的前缀-对-追加划分。

### total_input_micro_reductions.csv

micro-reduction 子集（下降 `≤ 1024` 个 token）。这些几乎总是 token 计数抖动，而非有意的压缩；这个文件的存在是为了确认这些小负值是噪声，而不是分桶逻辑误标的东西。
