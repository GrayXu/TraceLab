# session_timing_distribution

**在一个编码智能体所消耗的墙钟时间里，有多少是人类在思考、LLM 在生成、以及工具在执行——分别按会话、按请求、按步骤、按单独延迟看？**

`session_cost_distribution` 的时间域姊妹篇。计算 `tab:timing_distribution`（`src/04_SessionContext.tex`）背后的数据：对每种粒度和每个类别，给出每单位的 avg /
p50 / p90 / p99，以及在有明确总量的 block 中给出该类别在总时间中的占比（与成本表相同的 Avg/P50/P90/P99 + % 布局）。类别集合随粒度而不同，因为**人类思考是一个请求之间的量**：

- **每会话** —— `Total elapsed`（墙钟时间第一个→最后一个 timing event），并给出按会话求和的
  `Human thinking`、`LLM generation`、`Tool execution` 占比。
- **每会话，human capped (1h)** —— 仍以会话为单位，但每个人类空闲间隔先截断到 1 小时再求和，用于观察缓存 TTL 相关的时间预算。
- **每请求** —— `Total (response time)`（轮次端到端）= `LLM generation` + `Tool execution` +
  可能存在的重叠。没有 human 项：human wait 位于各请求*之间*，永远不会在一个请求内部。
- **每步骤** —— 仅 `LLM generation` vs `Tool execution`（一个轮次没有 human 项，也没有端到端）。
- **每单独延迟** —— 严格为正的人类输入等待、正的每轮可观测生成跨度、以及正的每工具有效延迟。这些行与 human-wait、generation-time、tool-latency 的 CDF/summary 视图对齐。

## 定义

- **LLM generation**（每步骤）—— 可观测的生成跨度，从最近的合格输入事件 →
  最后一个模型输出事件；与 `llm_generation/generation_time_cdf` 以及
  `human_in_the_loop/user_turn_decomposition` 中的每轮次生成一致。
- **Tool execution**（每步骤）—— 严格为正的有效工具延迟之和
  （`tool_internal_latency_ms`，否则 `tool_wall_latency_ms`）；与
  `tool_calls/tool_latency_distribution` 一致。
- **Human thinking** —— 前一个任意类型事件 → 下一个 `user_message`，且严格为正。每会话行按 session
  求和，因此没有第二条用户消息的会话贡献 `0s`；每单独延迟 block 的 human 行直接报告正间隔分布，并与
  `human_in_the_loop/human_input_wait` 一致。
- **请求端到端** 与 `user_turn_decomposition` 逐轮次匹配。生成与工具执行可以重叠（并发工具、工具调用期间的生成流式传输），因此它们的占比可能略微超过测得的端到端总量。
- **请求** —— 一个用户轮次（与 `user_turn_decomposition`、
  `user_turn_response_time`、`session_internal_counts`、`session_cost_distribution` 相同的轮次状态机）。**步骤** ——
  一个 LLM 轮次。**会话** —— 一个 `session_id`（4,258 个会话有正的墙钟时间跨度；其余是 single-timestamp 的，被丢弃）。

## 运行方式

```bash
uv run python artifacts/session/session_timing_distribution/analyze.py --db trace/syfi_coding_trace.duckdb
uv run python artifacts/session/session_timing_distribution/analyze.py            # default merged trace
```

## 输出

- `session_timing_distribution.tex` —— 合并后的单列计时表（Avg / P50 / P90 / P99
  + % time），用于论文。
- `session_timing_distribution.md` —— 该表格的 GFM Markdown 镜像，渲染在网页详情页上。
- `headline.json` —— 用于 Overview gallery 卡片的几个 headline 数字。
- stdout —— 合并 + 按提供商（Claude / Codex）的各类别分位数与时间占比。

## 关键数字（公开数据）

- **会话大部分时间是空闲的：人类思考占会话墙钟时间的 91.6%**（在一个 7.5h 的会话中平均 6.8h；中位数极小——一个单请求的会话没有请求间间隔）。这条长长的空闲尾部（会话 p99 ≈ 175h）正是把 prompt 前缀推过缓存 TTL 的原因。
- **正的人类输入等待与 CDF 对齐：p50 1.4 min，p90 15.2 min，p99 11.2h**。
- **单独 LLM/tool 分布与 summary 对齐：** 可观测生成跨度 p50 6.5s、p90 25.9s；正的工具有效延迟 p50 0.3s、p90 13.5s。
- **在一个请求内部，主导的是工具执行，而非生成：工具占 61.0%，生成占 37.7%。**
- 平均响应时间：**3.7 min / 请求**（p50 26s，p90 5.5 min）；平均有效工作量为每个步骤 **10.3s
  生成 + 16.7s 工具**。

在与提供商无关的定义下，会话的人类占比为 Claude 90.5%、Codex 93.1%。

无图。

## SyFI result analysis

### session_timing_distribution.md

一个编码会话大部分时间是空闲的，在等待人类（论文的 `tab:timing_distribution`）。
**人类思考占会话墙钟时间的 91.6%**，远超 LLM 生成（3.2%）和工具执行（5.1%）；大多数会话都很短——中位数是一个没有请求间间隔的单请求——但一条沉重的尾部，即那些被搁置数小时或数天的会话（会话 p99 elapsed ≈ 175h），累积了大部分的空闲时间。把每个间隔截断在一小时（与缓存相关的预算）会使人类占比降到 64.3%，而生成和工具分别为 13.6% 和 22.1%。每单独延迟 block 使用与 CDF/summary 相同的正值：人类等待 p50 1.4 min、p90 15.2 min，LLM 生成跨度 p50 6.5s、p90 25.9s，正的工具延迟 p50 0.3s、p90 13.5s。在单个请求内部，human 项消失，而**工具执行以 61.0% 领先于生成的 37.7%**；平均一个请求端到端运行 3.7 min（中位数 26s，p90 5.5 min），而每个有效步骤，模型花费约 10.3s 在生成、约 16.7s 在工具上。
