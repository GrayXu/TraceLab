# append_by_prefix_bin

**在缓存前缀已经如此之大的前提下，一个步骤会追加多少*新*（未缓存）token？**

填充 `tab:append_by_prefix`（`src/05_LLMGeneration.tex`）——它是前缀-vs-追加散点图（`fig:prefill_append_relationship`）的定量配套。对 Claude 和 Codex，每个智能体步骤按其 `prefix_tokens` 分箱，并在每个箱内报告 `newly_append_tokens` 的分布：count、avg、p50、p90、p99。

前缀箱采用倍增方式，以 1024-token 为单位：`<1k, 1-2k, 2-4k, 4-8k, 8-16k, 16-32k, 32-64k, 64-128k, 128-256k, >256k`。`prefix_tokens` / `newly_append_tokens` 的核算与 `prefix_append_distribution` 和 `token_length_distribution` 所用的相同，因此各处数字彼此对得上。

## 运行方式

```bash
uv run python artifacts/llm_generation/append_by_prefix_bin/analyze.py --db trace/syfi_coding_trace.duckdb
uv run python artifacts/llm_generation/append_by_prefix_bin/analyze.py        # default merged trace
```

## 输出

- `append_by_prefix_bin.tex`——供论文使用的 Claude/Codex 表格（空箱渲染为 `--`）。
- `append_by_prefix_bin.md`——该表格的 GFM Markdown 镜像，渲染在网页详情页上。
- `headline.json`——供概览画廊卡片使用的少量头条数字。
- stdout——同一份按提供商的拆分，纯文本形式。

## 关键数字（公开数据）

- 追加与前缀呈**反**相关。在 `<1k` 的前缀（冷启动——一次缓存未命中或首次请求）时，Claude 的中位追加为 **78k** token，Codex 为 **124k**。
- 一旦前缀超过约 32k（增量式工具循环 / 用户步骤），两个提供商的中位追加都骤降到 **远低于 1k**，只有不大的 p99 尾部。
- 各箱揭示出提供商的结构：Claude 的前缀几乎是直接跳到大值（`1-2k` 箱为空，`2-4k` 仅 2 步），因为它的 system prompt 很大；Codex 实际上在接近其 256k 上下文处封顶（只有 6 步超过它）。

无图。

## SyFI result analysis

### append_by_prefix_bin.md

这张表（`tab:append_by_prefix`）量化了前缀-vs-追加散点图背后的反相关关系：一个步骤已经缓存得越多，它追加得就越少。在最小的前缀箱（`<1k`——一次缓存未命中或最初的那次请求，此时几乎没有内容被缓存）里，中位追加巨大，Claude 为 78k token，Codex 为 124k，因为几乎整个 prompt 都得作为新内容发送。一旦前缀增长超过 32k，中位追加就骤降到远低于 1k（在 `32-64k`..`>256k` 各箱里 Claude 为 951→762，Codex 为 954→771），因为那些步骤只是把一个增量的工具结果或用户轮次叠加到已缓存的上下文之上。各箱也暴露了提供商的结构：Claude 的前缀几乎直接跳到大值——它的 `1-2k` 箱为空，`2-4k` 只有 2 步，反映出一个很大的 system prompt——而 Codex 实际上在接近其 256k 上下文窗口处封顶，只有 6 步超过它。
