# tool_call_counts

**编码智能体实际会调用哪些工具、调用频率如何，以及这些调用失败的频率有多高——
分别针对 Claude Code 和 Codex？**

## 实验概览

trace 中的每个智能体步骤都带有一个 `tools[]` 列表，记录该步骤中模型发起的工具调用。
本实验按 `(provider, tool)` 统计这些调用，并为每个提供商渲染一个水平柱状图面板，
工具按调用量排序，并用红色叠加层标记返回错误的调用占比。

方法与假设：

- **每次调用一行。** 我们统计的是 `tool_calls`（即 UNNEST 后的 `tools[]`）中的条目，而非智能体步骤——
  一个调用了三次 `Bash` 的步骤贡献三条记录。
- **MCP 工具被合并。** 任何名称以 `mcp_` 开头的工具都被别名归入单个 `mcp`
  桶，因为那些冗长晦涩、带服务器限定名的名称单独看都很罕见，在聚合层面也缺乏信息量。
- **罕见工具被折叠。** *仅对图而言*，单个提供商内部调用次数少于
  `--min-tool-calls-for-plot`（默认 20）的工具会被汇总进一个
  `Other (<N calls/tool)` 柱。CSV 保留完整的逐工具明细——数据中不丢弃任何内容，
  只是从图中省略。
- **线性、截断的坐标轴。** 工具使用高度倾斜（一两个工具占主导），因此每个面板
  将其 x 轴截断在第二大柱的约 1.05 倍处，并为被截断的领头柱标注其
  真实计数。这样可以让长尾保持可读，而不会被压缩在一根巨大的柱子旁边。
- **错误**被统计为 `is_error` 为 true 的调用，画成调用柱内部一根较短的柱。

## 代码结构

`plot.py` 是一个基于共享 trace DuckDB 的轻量级 query→shape→plot 流水线：

- `load_tool_counts_by_provider(con, *, min_calls)`——一条 `GROUP BY provider, tool_name` 查询
  （`mcp_*` → `mcp` 别名在 SQL 中完成），随后在 Python 中执行罕见工具折叠（求和
  与顺序无关）。返回 `{provider: {tool_name: ToolCounts(calls, error_calls)}}`。
- `plot_tool_counts(...)`——构建按提供商分面板和截断坐标轴的图。
- `tool_count_panel_cap(...)`——共享的截断/标注规则，被图和 CSV 同时使用，
  以使表中的 `panel_cap` / `*_plot_width` 列与渲染出的柱完全一致。
- `write_tool_call_counts_by_provider(...)`——完整明细 CSV。
- `main()`——将标准 `trace_db` CLI（`--db` | `-i/--input` | `-o/--output-dir`）接到
  上述逻辑，并嵌入自包含的 PNG sidecar。

数据层（解析、代理键、schema）位于 `artifacts/utils/trace_db.py`；参见
`artifacts/utils/DB_SCHEMA.md`。

## 运行方式

```bash
# default merged trace, output next to this README
uv run python artifacts/tool_calls/tool_call_counts/plot.py

# a specific trace (materialized to a temp DuckDB cache on first use)
uv run python artifacts/tool_calls/tool_call_counts/plot.py -i trace/sample.jsonl

# a prebuilt DB (run_all.py's build-db step passes this), into a chosen dir
uv run python artifacts/tool_calls/tool_call_counts/plot.py --db "$TMPDIR/trace.duckdb" -o "$TMPDIR/out"
```

实用参数：`--top-tools`（每个面板最多柱数，默认 30）、`--min-tool-calls-for-plot`
（罕见工具折叠阈值，默认 20）。

## 输出

- `tool_call_counts.png`——按提供商分面板的工具调用计数，带错误叠加层。
- `tool_call_counts_by_provider.csv`——完整的逐工具计数：`calls`、`error_calls`、`error_rate`，
  外加图的几何列（`panel_cap`、`call_plot_width`、`call_is_clipped`、…）。

该 PNG 是自包含的——它嵌入了本 README、CSV 以及绘图代码。用
`uv run python artifacts/utils/png_sidecar.py extract <png>` 解包。

## SyFI result analysis

### tool_call_counts.png

工具使用呈陡峭集中：命令执行在两个提供商中都居首，紧随其后的是 Read、Edit 这类
文件操作（论文中的 `fig:tool_call_counts`）。Claude 倚重 `Bash`
（被截断的领头者，标注其真实的 67k 次调用），其次是 `Read`（32k）和 `Edit`（18k）；
Codex 倚重 `exec_command`（252k），其次是 `write_stdin`（76k）和 `exec`（47k）。这种
集中极其显著——在 Claude 的 54 个不同工具和 Codex 的 31 个工具中，前三名
就占了 Claude 调用的 80% 以上以及 Codex 的约 95%。头部之外的一切都是一条由
专用工具和 MCP 工具构成的细长长尾，被折叠进 `Other` 柱。红色错误叠加层
标出了可靠性的异常值——Claude 的 `ExitPlanMode` 和 `AskUserQuestion` 失败频率远高于
那些高频原语——精确的逐工具 `error_rate` 见 CSV。
