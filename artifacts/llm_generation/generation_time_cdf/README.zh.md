# generation_time_cdf

**单个智能体步骤的*可观测*模型生成耗时有多长，以及总生成时间如何在快、慢两类步骤之间分布？**

## 实验概览

一个智能体步骤的**可观测生成时间（observable generation time）**是从它的**最近输入事件（latest input event）**（`user_message` 或 `tool_result`）到它的**最后一个模型输出事件（last model-output event）**（`reasoning`、`text` 或 `tool_call`）之间的时段，取自该步骤有序的 `timing_events[]`。具体到每个步骤：取最早的模型输出时间戳作为 `first_output`；在输入时间戳中，仅保留那些在 `first_output` 之前（含同时刻）的（即可能触发了本次输出的输入）；时段即 `(latest output - latest such input)`，仅在严格为正时保留。没有输入或没有输出事件、或时段非正的步骤不贡献任何值。

这是一个**trace 级估计（trace-level estimate）**，而非 serving-engine 计时器。它不包含前置的人类等待时间以及任何响应后的用量记账事件，并且只能反映 trace 实际记录下来的事件。

本实验在单步生成时间阈值 `T` 上绘制两条互补的 CDF，按提供商分面，横轴为细粒度对数间隔的时长轴：

- **计数 CDF** — 生成时间 `≤ T` 的智能体步骤所占比例。
- **总量 CDF** — 由生成时间 `≤ T` 的步骤所贡献的*累加*生成时间所占比例，即墙钟时间实际花在了哪里。

方法与假设：

- **精确，非抽样。** 每个具有正可观测时段的智能体步骤都向其提供商的列表贡献一个值；CDF、分位数与累加时间分箱都在全集上计算。（旧 loader 在此处本就保留了每一个值——这一指标从未有过 reservoir 上限——因此迁移是逐值一致的。）
- **提供商分组**沿用旧 loader 的 `str(provider) or "<unknown-provider>"` 回退逻辑，因此缺失/为空的提供商落入 `<unknown-provider>`。
- **与引擎无关的时间戳。** 时间戳从 DB 读取为整型的 epoch 微秒（`CAST(epoch_us(timestamp) AS BIGINT)`），并在 Python 中重建为 naive datetime，绝不以原始 `TIMESTAMP` 形式取出（native duckdb 会将其封送为 `datetime`，而 duckdb-wasm 会封送为字符串）。两个同时区 datetime 之间的时段精确等于 naive 微秒时段，因此各时长与迁移到 DuckDB 之前的结果逐位一致。

## 代码结构

`plot.py` 是一条建立在共享 trace DuckDB 之上的 query→shape→plot 流水线：

- `load_generation_seconds_by_provider(con)` — 唯一的数据加载代码。它从 `timing_events` 拉取每个 step 的输入与模型输出时间戳（作为 epoch 微秒整数，按 `round_pk` 摄入顺序），并从 `rounds` 拉取每个 step 的 `provider`，随后计算每个 step 的可观测时段并追加到该提供商的列表。返回完整的各提供商列表，不做抽样。
- `_input_to_last_output_span_seconds(inputs, outputs)` — 为单个 step 的事件复现迁移到 DuckDB 之前的 `timing.input_to_last_output_span_seconds`（first-output 门控、候选输入、正时段过滤）。
- `_epoch_us_to_datetime(...)` — 从 epoch 微秒重建一个 naive datetime。
- 两张图与两个 CSV 由共享的 `cdf.py` 辅助函数生成（`plot_count_cdf_by_provider` / `plot_cumulative_duration_cdf_by_provider` 及其 `write_*` 对应函数）——matplotlib/CSV 行为与迁移前脚本保持不变。
- `main()` — 接入标准的 `trace_db` CLI（`--db` | `-i/--input` | `-o/--output-dir`）并嵌入自包含的 PNG sidecar。

数据层（解析、surrogate key、schema）位于 `artifacts/utils/trace_db.py`；参见 `artifacts/utils/DB_SCHEMA.md`。

## 运行方式

```bash
# default merged trace, output next to this README
uv run python artifacts/llm_generation/generation_time_cdf/plot.py

# a specific trace (materialized to a temp DuckDB cache on first use)
uv run python artifacts/llm_generation/generation_time_cdf/plot.py -i trace/sample.jsonl

# a prebuilt DB (run_all.py's build-db step passes this), into a chosen dir
uv run python artifacts/llm_generation/generation_time_cdf/plot.py --db "$TMPDIR/trace.duckdb" -o "$TMPDIR/out"
```

## 输出

- `llm_generation_time_count_cdf_by_provider.png` / `.csv` — 各提供商在生成时间阈值上的计数 CDF（step `≤ T`），含每箱及累计的计数/占比。
- `llm_generation_time_total_cdf_by_provider.png` / `.csv` — 各提供商的累加时间 CDF，含每箱秒数/小时数及累计时间占比。

每张 PNG 以压缩文本块的形式嵌入了本 README、上述 CSV，以及绘图代码（`plot.py` + 共享的 `artifacts/utils/` 模块）。可用 `uv run python artifacts/utils/png_sidecar.py extract <png>` 解包。

## SyFI result analysis

### llm_generation_time_count_cdf_by_provider.png

各提供商的智能体步骤累计*计数*相对于可观测生成时间阈值，横轴为对数时间轴。读取每条曲线达到某一高度的位置，即可比较各步骤完成的快慢：曲线早升意味着大多数步骤生成迅速，而长而平的右尾标记出慢的少数派。图内表格给出各提供商实际的 `p25/p50/p90/p99` 与均值，虚线地标线锚定了熟悉的时长（一秒、一分钟），便于定位这些分位数。这是 trace 观测到的时段（从最近输入事件到最后一个模型输出），并非 serving-engine 计时器，因此它折叠进了 TTFT、推理以及 trace 日志记录的影响。

### llm_generation_time_total_cdf_by_provider.png

同样是这些单步时段，但每个步骤现在贡献其*时长*而非一个单位，因此曲线刻画的是直到阈值 `T` 为止累加的生成时间（单位为小时）——即 **墙钟时间实际花在了哪里**。由于慢步骤承载了不成比例的时间，这条曲线比计数 CDF 饱和得晚得多：少量长步骤组成的尾部主导了总量。两张图之间的差距才是要点——差距越大，该提供商的时间开销就越集中于它的慢尾。
