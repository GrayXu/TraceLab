# token_length_distribution

**每个 LLM 步骤的输入和输出有多大——Claude 与 Codex 又有何不同？**

## 实验概览

本实验生成《输入长度分布》与《输出长度分布》两个小节共享的那张合并论文表格（即 `src/05_LLMGeneration.tex` 中的 `tab:token_length_distribution`）。对每个提供商（Claude、Codex），在**所有 LLM 步骤**（轮次）上，报告三个每步 token 计数的 avg / p25 / p50 / p90 / p99：

- **前缀 token**——`prefix_tokens`，即被重放的累计上下文。
- **追加 token**——`newly_append_tokens`，即新加入、未被缓存的输入。
- **输出 token**——`output_tokens`，即生成的 token（含推理）。

前缀/追加的拆分与 `llm_generation/prefix_append_distribution` 所用的分解方式相同，而输出这一列与 `llm_generation/output_tokens` 是同一个指标；本实验存在的唯一目的就是产出这张按提供商合并的 `.tex` 表格。另外两个实验各自保留它们的图和 CDF。

方法与假设：

- **精确，非采样。** DuckDB 保留每一行，因此分位数与均值都在全部有效轮次上计算（不做 reservoir 采样）。
- **逐列过滤。** 每个 token 列各自独立地限制为非 null、非负值（`column IS NOT NULL AND column >= 0`），与两个来源实验保持一致。
- **逐步（per step）。** 计量单位是一个 LLM 轮次；此处不做任何会话/请求层面的聚合。

## 运行方式

```bash
# default merged trace, output next to this README
uv run python artifacts/llm_generation/token_length_distribution/analyze.py

# a specific trace (materialized to a temp DuckDB cache on first use)
uv run python artifacts/llm_generation/token_length_distribution/analyze.py -i trace/sample.jsonl

# a prebuilt DB, into a chosen dir
uv run python artifacts/llm_generation/token_length_distribution/analyze.py --db "$TMPDIR/trace.duckdb" -o "$TMPDIR/out"
```

## 输出

- `token_length_distribution.tex`——按提供商合并的表格；带溯源（provenance）头部的副本存放在论文仓库的 `figure-tex/tab_token_length_distribution.tex`。
- `token_length_distribution.md`——该表格的 GFM Markdown 镜像，渲染在网页详情页上。
- `headline.json`——供概览画廊卡片使用的少量头条数字。

按提供商的统计也会打印到 stdout。

## SyFI result analysis

### token_length_distribution.md

这是喂给 `tab:token_length_distribution` 的唯一一张表，它把工作负载的核心不对称性具体化了：每一步里，输入巨大而输出微小。在**前缀**一侧，Claude 的中位步骤要重放 141k 个缓存 token，Codex 为 120k——而由于 Claude 的上下文窗口更长，它的前缀会一直延伸到 920k 的 p99，而 Codex 在 238k 附近就饱和了。**追加**一侧要小两个数量级，Claude 中位仅 992 个新 token，Codex 1,086 个，这才是一步真正会被计费的那一薄片。**输出**更小：Claude 中位 354 个 token，Codex 188 个，p90 约为或低于 2k，连 p99 也只停留在低千位区间。输出如此之短与直觉相悖，但这正是工具循环的结果——一次完整回复平均被拆分到 6.7 个工具调用步骤里，因此每一次单独的生成都很简短，往往只是吐出下一个工具调用的参数。
