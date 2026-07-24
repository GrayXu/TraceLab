# codex_wall_internal_gap

**对于 Codex 工具调用，有多少墙钟时间位于 runner 实际所做工作之外
——端到端 vs. 内部延迟的差距？**

## 实验概览

Codex trace 对每次工具调用携带两种延迟概念：

- **端到端（墙钟）**——`tool_wall_latency_ms`，即从模型
  发出函数调用到其输出被记录之间的时间戳跨度。
- **内部**——`tool_internal_latency_ms`，即从工具输出中解析出的
  runner 上报的 `Wall time: … seconds`，亦即命令本身运行了多久。

本实验量化**正残差**
`gap = max(tool_wall_latency_ms − tool_internal_latency_ms, 0)`：即 runner *没有*归因为
执行命令的那部分端到端时间。在
归一化 trace 中，这个残差是唯一可用于审批 / 用户等待
开销的信号，因为工具输入、输出以及显式审批事件都未被保留，
因此最好将其读作围绕该调用的客户端等待的**上界**，而非
直接的审批测量。

只有 `(provider = 'codex')` 且**同时**具备两种计时、墙钟时间为正、且
内部时间非负的调用才进入残差统计。论文 float
`tab:codex_tool_e2e_internal`（以 `codex_tool_e2e_internal.tex` / `.md` 形式发出）
将这些聚合为一个 `All timed` 行外加每个主要的类执行工具
（`exec_command`、`write_stdin`、`shell_command`、`apply_patch`）一行，报告调用数、
端到端 / 内部 / 残差小时数求和、平均残差，以及 P50/90/99 残差
秒数。该脚本还写出若干 CSV 分解（逐工具、逐类别、残差
分桶、直接人工墙钟时间、top-gap 示例）和一份 `result_analysis.md` 叙述。

## 运行方式

```bash
# released DuckDB, outputs written next to this README
uv run python artifacts/tool_calls/codex_wall_internal_gap/analyze.py --db trace/syfi_coding_trace.duckdb

# default merged trace
uv run python artifacts/tool_calls/codex_wall_internal_gap/analyze.py
```

标准 `trace_db` CLI（`--db` | `-i/--input` | `-o/--output-dir`）。实用参数：
`--top-gap-examples`（top-gap-examples CSV 中的行数，默认 50）。

## 输出

- `codex_tool_e2e_internal.tex`——论文 float `tab:codex_tool_e2e_internal`：
  逐工具的端到端 / 内部 / 正残差延迟，带均值和 P50/90/99。
- `codex_tool_e2e_internal.md`——该表的 GFM 镜像（数字相同，无标题），
  用于 web 端详情页。
- `headline.json`——用于 Overview 画廊卡片的几个 headline 数字。
- `result_analysis.md`——主要数字、覆盖率以及可解释性
  局限的叙述。
- CSVs：`codex_tool_timing_coverage.csv`、`codex_wall_internal_gap_by_tool.csv`、
  `codex_wall_internal_gap_by_category.csv`、`codex_wall_internal_gap_buckets.csv`、
  `codex_direct_human_wall_time.csv`、`codex_top_wall_internal_gap_examples.csv`。

## 关键数字（公开数据）

- 在**338k**次同时具备两种计时的 Codex 调用中，端到端时间为**598.6h**，内部时间为
  **470.1h**，留下**129.7h**的残差差距（占端到端的 21.7%）。
- `exec_command` 承担了其中大部分：**248k**次调用、**108.7h**残差。
- 残差通常极小但重尾：在所有计时调用上，中位数**0.16s**、P90 **0.31s**、P99
  **10.2s**。

## SyFI result analysis

### codex_tool_e2e_internal.md

Codex 观测到的端到端工具延迟大幅超过 runner 的内部执行
时间，因此有相当一部分工具墙钟时间并非真正的命令工作（论文中的
`tab:codex_tool_e2e_internal`）。338k 次同时计时的调用端到端
合计为 598.6h，而内部为 470.1h，留下 129.7h 残差（约占端到端的 21.7%）。`exec_command`
以 108.7h 残差（内部 53.6h，端到端 162.2h）主导了这一差距，这与 shell
命令最可能因权限/自动审批而停顿相符。该残差主要
由大量极小的间隙加上一条长尾构成：平均 1.38s，P50/P90 保持很小
（0.16s/0.31s），但 P99 达到 10.2s。`write_stdin` 是相反的形状——巨大的端到端（429.3h），却
几乎全是内部，仅留 18.7h 残差——这证实了开销集中在
命令启动，而非长时间运行的交互式会话。应将此残差读作围绕调用的
客户端等待（审批、shell 启动、调度）的上界，而非直接的审批测量。
