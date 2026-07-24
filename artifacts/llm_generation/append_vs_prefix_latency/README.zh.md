# append_vs_prefix_latency

**问题。** 追加占比高的智能体步骤是否真的比其它各方面均已匹配的前缀占比高的步骤更慢？不只是"追加占比高的行平均更慢"，而是：在按提供商、模型、分段类型（segment kind）、总输入长度与输出长度匹配之后，追加占比高的行是否能与前缀占比高的行干净地分离开来？

## 输入

`../timing_fit/timing_fit_trace.csv`（可用 `-i` 覆盖）——由 `../timing_fit/collect_timing_fit_trace.py` 生成的长格式（long-form）计时分段 CSV。**不是** JSONL trace。`artifacts/run_all.py` 在运行本实验前会从 `--input` 自动构建它。

## 方法 / 关键假设

- 行按 `(provider, model, segment_kind, total-token bin, output-token bin)` 分桶。在每个桶内，将 **append-heavy** 行（追加占比 `≥ --append-heavy-share`）与 **prefix-heavy** 行（追加占比 `≤ --prefix-heavy-max-append-share`）作比较。
- 报告两件事：
  - **effect size（效应量）** — append-heavy 行比一个已匹配的 prefix-heavy 行更慢的频率（`pair_weighted_append_slower_probability`）；
  - **separation quality（分离质量）** — 在用各行所在桶的 prefix-heavy 中位延迟对其归一化后，某个时长阈值能否区分这两类（`global_normalized_best_balanced_accuracy`）。
- 时长按组做截尾（`--trim-quantile`，默认 0.99），并过滤至 `[--min-duration-ms, --max-duration-ms]` 以剔除不合理的时段。

## 如何运行

推荐使用 dispatcher 路径：

```bash
uv run python artifacts/run_all.py \
  --only llm_generation/append_vs_prefix_latency \
  --db trace/syfi_coding_trace.duckdb
```

dispatcher 会先从 `--input` 构建 `../timing_fit/timing_fit_trace.csv`。手动直接运行时假定该 CSV 已存在：

```bash
uv run python artifacts/llm_generation/append_vs_prefix_latency/analyze.py
```

## 输出

- `append_vs_prefix_latency.json` / `.md` — 结论 + 汇总。
- `append_vs_prefix_matched_buckets.csv`, `append_vs_prefix_normalized_rows.csv`
- `append_vs_prefix_bucket_effects.png`, `append_vs_prefix_normalized_overlap.png`

## 独立 PNG

每张 PNG 都嵌入了本 README、各 CSV，以及 `analyze.py`。可用 `uv run python artifacts/utils/png_sidecar.py extract <png>` 解包。

## SyFI result analysis

### append_vs_prefix_bucket_effects.png

效应量视图，每个 `(provider, model, segment kind, total-token bin, output-token bin)` 匹配桶对应一个点。结论是 **append-heavy 步骤更慢，但这两类并不能干净分离**：按对加权的 `P(append-heavy 比已匹配的 prefix-heavy 行更慢)` 为 69.0%（Cliff's delta 0.381），且 append-heavy 在 1,218 个桶中的 983 个（80.7%）里是较慢的中位数，然而桶级延迟比值的中位数只有 **1.13x**。最大、最干净的那些桶（长上下文、短输出的 Codex `tool_result→tool_call`）比值约为 1.5–1.9x，因此当追加远超过微小输出时效应是真实存在的——但*典型*桶的差距并不大。

### append_vs_prefix_normalized_overlap.png

分离质量视图：每行的时长按其所在桶的 prefix-heavy 中位数归一化，然后将 append-heavy 与 prefix-heavy 行叠加。它们**严重重叠**——最佳全局归一化时长阈值（1.04x）也仅达到 61.7% 的平衡准确率（balanced accuracy），远低于干净分割所需的 75% 标准。因此"append-heavy 行构成一个可分离的慢类"这一强假设被否定：追加占比会移动分布，但不能干净地对一行的延迟进行分类。作为一份观测性 trace，匹配控制了 token 长度以及模型/分段身份，但没有控制排队、batch 组成、缓存驻留或瞬时负载，而这些都会增加重叠。
