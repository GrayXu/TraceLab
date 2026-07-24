# session_compaction_counts

**一个编码会话会经历多少次上下文压缩？**

一次 *压缩* 是论文与
[`total_input_growth`](../total_input_growth) 中那些单纯的尺寸桶相区分的行为事件：当运行中的上下文接近其上限时，它会被 summarize/丢弃成一段简短的历史，然后再缓慢地重新累积。我们从每个步骤的总输入长度（`prefix_tokens + newly_append_tokens`）结构性地检测它，在每个会话内按 `round_pk` 排序。当**三个条件全部**成立时，步骤 `i` 即为一次压缩：

1. **大幅缩减** —— `total[i-1] - total[i] >= 64k`（`--min-drop-tokens`，默认为
   `growth.MAJOR_REDUCTION_MIN_TOKENS`）。因此每一次压缩同时也是一次 *major reduction*；压缩是同时满足 (2) 和 (3) 的那个严格子集。
2. **接近上下文上限** —— 下降前的水平 `total[i-1]` 至少达到该会话观测到的最大总输入的 `--near-max-ratio`（0.75）。下降发生在会话的峰值附近，而不是在早期的小幅回落处。
3. **恢复缓慢** —— 在接下来的 `--rebound-steps`（3）个步骤内，上下文*没有*反弹到下降前水平的 `--rebound-ratio`（0.75），且其后至少还有一个步骤。一个立即弹回的下降是 branch/edit 产物，不是压缩。

每一次压缩都归因于步骤 `i` 的触发器，使用论文其余部分通用的同一套
**用户触发** / **工具触发** 划分：`user_message` →
用户触发（一个显式的 `/compact`，或一个迫使 summarization 的新请求）；
`tool_result` → 工具触发（循环中途的自动压缩）。

## 运行方式

```bash
# pinned public trace
uv run python artifacts/session/session_compaction_counts/analyze.py --db trace/syfi_coding_trace.duckdb

# default merged trace
uv run python artifacts/session/session_compaction_counts/analyze.py

# loosen/tighten the definition
uv run python artifacts/session/session_compaction_counts/analyze.py \
    --near-max-ratio 0.8 --rebound-steps 5
```

## 输出

- `session_compaction_counts.tex` —— 合并后的 summary 表（`tab:session_compaction`）。
- `session_compaction_counts.md` —— 该表格的 GFM Markdown 镜像，渲染在网页详情页上。
- `headline.json` —— 用于 Overview gallery 卡片的几个 headline 数字。
- stdout —— 合并 + 按提供商（Claude / Codex）：总压缩数、拥有
  ≥1 次的会话占比、每个会话的分布（avg / p25 / p50 / p90 / p99，分别在全部会话上以及仅在 ≥1 次的那些上），以及用户触发与工具触发的触发器划分。

## 关键数字（公开数据，默认口径）

- 在 **2,750** 次 major reduction（≥64k 下降）中，**2,520** 次（91.6%）符合压缩资格。
- 8,058 个会话中有 **8.8%** 经历了至少一次压缩。
- 主要为**工具触发**（79.2%，循环中途），而非用户触发。
- 在 **Codex** 中远比 **Claude** 常见（Codex 占会话的 17.4%，1,984 个事件；Claude 占 4.4%，536 个）。
- 在确实发生压缩的会话中，均值为 3.5，且尾部很长（Codex p99 = 37）。

无图。

## SyFI result analysis

### session_compaction_counts.md

大多数大幅上下文下降都是真正的压缩，且它们对 Codex 的冲击远大于 Claude（论文的
`tab:session_compaction`）。在 major（≥64k）reduction 中，Claude 有 **536/669** 符合资格，Codex 有
**1,984/2,081**——合计 2,750 中的 2,520——所以这套接近上限 + 恢复缓慢的结构性检验很少误报。压缩在单个会话中并不常见，但按提供商极不均衡：只有 **4.4%** 的 Claude 会话曾发生压缩，而 Codex 则为 **17.4%**，且在确实发生的会话中，Codex 的均值与尾部都大于 Claude（Codex avg 4.16，p99 = 37；Claude avg 2.28，p99 = 12）。触发器划分呼应了自主性这一发现：Codex 的压缩压倒性地为**工具触发**（82.5%，循环中途的自动压缩），而 Claude 的则更为均衡（67.0% 工具 / 33.0% 用户）。
