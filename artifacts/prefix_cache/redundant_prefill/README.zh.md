# redundant_prefill

**在一个步骤必须预填充的每个 token(即未缓存的"追加")中,有多少是真正*新鲜的* ——
系统从未见过的用户 prompt 和工具结果 —— 又有多少只是模型自己先前的输出和被重新发送的、完美缓存本可
提供的上下文?新鲜部分的比例就是不可压缩的预填充下限,因而也是可达到的前缀缓存命中率的上界。**

## 实验概览

轨迹中的每一行是一个智能体步骤,包含一段缓存的 `prefix_tokens` 部分和一段新预填充的
`newly_append_tokens`("追加")部分。本实验按顺序遍历一个会话的各个步骤,将追加拆分为
**新鲜**与**非新鲜** token,并按提供商和步骤触发来源报告各项总量。

方法与假设:

- 一个步骤的**总输入**是 `prefix_tokens + newly_append_tokens`。每个步骤的**上下文增长**
  是 `max(0, total_input(S) - total_input(P))`,其中 `P` 是同一会话中遇到的上一个步骤 ——
  即存活进入 `S` 的净新增上下文(回退,即 compaction,贡献为 0)。
- **新鲜 token。** 在 `P` 和 `S` 之间,上下文增长来自 `P` 处模型的输出(如今已成为对话的一部分)
  加上任何真正新的用户/工具 token。因此
  `fresh(S) = context_growth(S) - output_tokens(P)`。`output_tokens` **已经包含**了推理
  token(`reasoning_output_tokens` 是其子集,而非额外的计数 —— 参见
  `trace_facts/overview_summary`),且 Codex 的推理在经验上会被带入后续上下文,所以应当减去完整的
  `output_tokens` 才是正确的量。
- **追加(分母)。** `append(S) = newly_append_tokens(S)` —— 在 `S` 处实际预填充的未缓存
  token。`fresh % of append = total_fresh / total_append`。
- **配对。** 一个步骤只有在它**不是**其会话的第一个步骤、且其**首个时序事件**
  是 `user_message` 或 `tool_result`(即该步骤的*触发来源*)时才符合条件。`P` 是该会话在
  轨迹顺序(`round_pk` = 文件顺序)中最后遇到的那个步骤,无论其触发来源为何 —— 与
  `session/total_input_growth` 完全一致。会话的首个步骤没有前驱,因此既不计入新鲜也不计入
  追加总量。
- **报告的触发来源。** `all`、`user` 和 `tool_result`,各自按范围(`merged`、`claude`、
  `codex`)分别报告。

上下文增长的求和值精确复现了 `session/total_input_growth` 中的 `total_context_increase`;本实验在此基础上
增加了 `output_tokens` 的减除以及 `append` 分母。

## 代码结构

这是一个**混合式**实验,与其他有状态实验一样:轨迹 DuckDB 流式提供行,Python 负责维护每个会话
的顺序。

- `read_accums(con)` —— 在 `rounds` 上做一次 SQL 扫描(并 join 进每个轮次的首个时序事件),
  按 `round_pk` 排序,在 Python 中借助一个 `last_by_session` 映射进行遍历,将每个触发步骤与其前驱配对,
  并按 `(scope, trigger)` 累积 `append` / `context_growth` / `prior_output`。
- `FreshAccum` —— 每个分组的累加器;`fresh_tokens = context_growth - prior_output`。
- `write_summary_csv(...)` / `write_latex_table(...)` —— CSV(原始整数,作为唯一可信源)与论文表格
  (`\,M` token 格式化,按提供商的新鲜 %)。

数据层位于 `artifacts/utils/trace_db.py`(参见 `artifacts/utils/DB_SCHEMA.md`);触发来源的映射与
`artifacts/utils/growth.py` 共享。

## 运行方式

```bash
# default merged trace (materialized to a temp DuckDB cache on first use)
uv run python artifacts/prefix_cache/redundant_prefill/analyze.py

# a prebuilt DB (run_all.py's build-db step passes this), into a chosen dir
uv run python artifacts/prefix_cache/redundant_prefill/analyze.py --db "$TMPDIR/trace.duckdb" -o "$TMPDIR/out"
```

## 输出

- `redundant_prefill_summary.csv` —— 每个 `(scope, trigger)`:事件数、总追加、总上下文增长、
  总先前输出、总新鲜,以及新鲜占追加的百分比。
- `redundant_prefill_table.tex` —— 论文表格(`tab:redundant_prefill`),已复制进论文的
  `figure-tex/`。
- `redundant_prefill_table.md` —— 该表格的 GFM Markdown 镜像,渲染在网页详情页上。
- `headline.json` —— 供 Overview 画廊卡片使用的几个 headline 数字。

## SyFI result analysis

### redundant_prefill_table.md

新鲜 token 只占全部预填充的一小片(论文的 `tab:redundant_prefill`):仅有 **19.0%** 的被追加 token
是真正新的(Claude 12.3%,Codex 25.8%),因此剩下约 81% 原则上是可由缓存提供的 —— 这就是与最优状态之间的
差距。对新鲜比例取倒数即得到 **预填充放大系数**,即实际预填充的 token 数是一个无驱逐的完美
缓存所需的多少倍:**整体 5.3x**(Claude 8.1x,Codex 3.9x)。这一拆分高度依赖于触发来源:
**用户触发**的步骤几乎全是被重新发送的上下文(新鲜仅占其追加的 1.7% Claude / 4.5% Codex ——
为一段简短的新 prompt 重新发送了一个很大的窗口),而 **工具触发**步骤则承载了绝大部分真正新的内容
(27.1% / 40.5%)。Codex 在新鲜比例上始终高于 Claude,这与其更短的重发窗口和更重的工具输出相吻合。
新鲜 % 是前缀缓存命中率的天花板 —— 将其与 `cache_hit_ratio` 中实测的命中率对比。
