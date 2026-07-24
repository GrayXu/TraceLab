# cache_hit_idle_relationship

**低前缀缓存命中率的智能体步骤是否紧随长时间的空闲等待或长时间的工具执行?也就是说,缓存未命中
是否可以用缓存在前一段间隔内被驱逐掉来解释?**

## 实验概览

本目录下共享两个脚本,二者都将一个智能体步骤的 **前缀缓存命中率**与其**前置间隔**关联起来:

```
hit_ratio = prefix_tokens / (prefix_tokens + newly_append_tokens)
```

一个步骤按其**首个时序事件的类型**进行分类:

- 一个 `user_message` 步骤 —— 其前置间隔是自同一会话中上一次活动以来的**人类空闲等待**;
- 一个 `tool_result` 步骤 —— 其前置间隔是该步骤所消费的领头 tool result 的**工具执行时长**
  (`result_at - emitted_at`)。

`cache_hit_idle_gap_analysis.py` 按 `(scope, trigger)` 聚合低命中步骤有多频繁地处于长间隔之后
(即间隔/命中汇总 CSV)。`plot_user_wait_time_vs_hit_rate.py` 将原始的每个步骤的 (间隔, 命中率) 点云
绘制为按提供商分面板的散点图。负相关关系 —— 低命中率集中在长间隔处 —— 与基于时间的缓存驱逐相吻合。

方法与关键假设:

- **前缀命中率** = `prefix_tokens / (prefix_tokens + newly_append_tokens)`;token 总和非正的步骤
  会被剔除。
- **步骤触发来源** = 该步骤*首个*时序事件(`event_index = 1`)的 `event_type`;仅测量
  `user_message` 和 `tool_result` 步骤。
- **人类空闲等待**(`user` 触发)= `first_activity(current) - last_activity(同一会话中的上一个
  步骤)`,在 `≥ 0` 时保留。`first_activity` 是携带时间戳的第一个时序事件(否则取最早的活动时间戳);
  `last_activity` 是该步骤的所有时序事件及其工具的 `emitted_at`/`result_at` 中最晚的时间戳。
- **工具执行时长**(`tool_result` 触发)= 该步骤领头 tool-result 调用 id 上 `result_at - emitted_at`
  的**最大值**,在一个**按会话限定**的、由*先前*步骤发出的工具映射中查找(tool call id 在一个
  会话内唯一)。负的时长会被丢弃。
- **会话遍历**是有状态且对顺序敏感的:步骤按 `(provider, session_id)` 分组,并按
  `(round_index, first_activity_ts)` 排序。该遍历精确复现了迁移到 DuckDB 之前的单遍加载器 —— 工具只在一个
  步骤被处理*之后*才被"记住",因此一个 `tool_result` 步骤绝不会看到它自身步骤内发出的工具。
- **范围**在汇总中:`merged`(所有步骤),外加每个提供商(`claude`、`codex`)。散点图为每个提供商
  绘制一个面板。
- **精确计算,而非采样。** 每个可测量的步骤都有贡献;汇总中的分位数使用遗留的线性插值方法,散点图绘制每一个
  点(旧代码在此处不做任何抽稀)。点在每个会话内按文件顺序发出,会话则按首次出现顺序排列,因此散点图
  与迁移到 DuckDB 之前的图像逐像素一致。
- **与引擎无关的时间戳。** 时间戳被读取为整数的 epoch 微秒
  (`CAST(epoch_us(timestamp) AS BIGINT)`,工具的 `emitted_at`/`result_at` 同理),并在 Python 中做差,
  绝不以原始 `TIMESTAMP` 形式取出(原生 duckdb 会将其编排为 `datetime`,duckdb-wasm 则编排为字符串)。
  两个同时区瞬时点之差恰好等于朴素微秒之差,因此每个间隔都与迁移到 DuckDB 之前的结果按位一致。
- 这些都是**轨迹级别的估计值**,而非引擎计时器。

## 代码结构

两个脚本都查询共享的轨迹 DuckDB(`rounds` / `tool_calls` / `timing_events`),而不是重新解析 JSONL。
有状态的会话遍历是共享的:

- `cache_hit_idle_gap_analysis.py`
  - `load_rounds_by_session(con)` —— `{(provider, session_id): [RoundData, …]}`。分别在
    `timing_events`(按 `round_pk, event_index` 排序)和 `tool_calls`(按 `round_pk, tool_index`
    排序)上各做一次 SQL 扫描,加上 `rounds` 的标量值(按 `round_pk` 排序),按步骤组装成一个
    `RoundData`(触发事件类型、首/末活动微秒、领头 tool-result 调用 id,以及按 call id 索引的每个步骤
    的工具时长)。会话按 `(round_index, first_activity_ts)` 排序,复现旧的行顺序 tie-break。
  - `analyze(con, …)` —— 有状态遍历,按 `(scope, trigger)` 通过 `update_group` 累积一个 `GapGroup`
    (轮次/低命中/超空闲计数以及间隔列表)。
  - `percentile` / `format_pct` / `format_float` / `write_summary` —— 汇总数学计算与 CSV 写入,
    与迁移前脚本保持不变。
- `plot_user_wait_time_vs_hit_rate.py`
  - `collect_points(con, trigger=…)` —— 复用 `idle.load_rounds_by_session` 并运行相同的遍历,为一个
    触发来源产出 `{provider: ([wait_seconds…], [hit_rate…])}`。
  - `plot(...)` / `count_points(...)` / `default_output_path(...)` / `WAIT_TICKS` / `format_wait_time`
    —— 图像布局(对数 x 等待轴、按提供商分面板、低命中/长等待参考线)保持不变。
  - `main()` —— 接入标准的 `trace_db` CLI,并嵌入自包含的 PNG sidecar(本 README、间隔 CSV 以及两个
    脚本)。

两者都使用标准的 `trace_db.add_db_args` 接口(`--db` | `-i/--input` | `-o/--output-dir`);绘图脚本另外
添加了 `--trigger {all,user,tool_result}` 以及一个 `--output` 单 PNG 路径(仅在指定单一具体触发来源时
有效)。数据层(解析、代理键、schema)位于 `artifacts/utils/trace_db.py`;参见
`artifacts/utils/DB_SCHEMA.md`。

## 运行方式

```bash
# default merged trace, outputs next to this README
uv run python artifacts/prefix_cache/cache_hit_idle_relationship/cache_hit_idle_gap_analysis.py
uv run python artifacts/prefix_cache/cache_hit_idle_relationship/plot_user_wait_time_vs_hit_rate.py

# a specific trace (materialized to a temp DuckDB cache on first use)
uv run python artifacts/prefix_cache/cache_hit_idle_relationship/cache_hit_idle_gap_analysis.py -i trace/sample.jsonl

# a prebuilt DB (run_all.py's build-db step passes this), into a chosen dir
uv run python artifacts/prefix_cache/cache_hit_idle_relationship/plot_user_wait_time_vs_hit_rate.py --db "$TMPDIR/trace.duckdb" -o "$TMPDIR/out"

# a single scatter
uv run python artifacts/prefix_cache/cache_hit_idle_relationship/plot_user_wait_time_vs_hit_rate.py --trigger user
```

## 输出

写入到 `-o`(默认为本文件夹):

- `cache_hit_idle_gap_summary.csv` —— 每个 `(scope, trigger)` 一行,其中
  `scope ∈ {merged, claude, codex}` 且 `trigger ∈ {all, user, tool_result}`,包含各阈值
  (`low_hit_threshold`、`idle_threshold_seconds`)、轮次/低命中计数、超空闲占比
  (`low_gt_idle_share`、`all_gt_idle_share`、`nonlow_gt_idle_share`)以及间隔分位数
  (`low_gap_median_s`/`low_gap_p90_s`、`all_gap_median_s`/`all_gap_p90_s`)。
- `user_wait_time_vs_hit_rate_scatter.png` —— 用户消息等待 vs 命中率,Claude/Codex 面板。
- `tool_result_wait_time_vs_hit_rate_scatter.png` —— 工具时长 vs 命中率,Claude/Codex 面板。

每个散点 PNG 都是自包含的 —— 它内嵌了本 README、间隔汇总 CSV 以及两个脚本。
用 `uv run python artifacts/utils/png_sidecar.py extract <png>` 解包。

## SyFI result analysis

### user_wait_time_vs_hit_rate_scatter.png

每个步骤的**人类空闲等待**(x 轴,对数刻度,从约 1ms 到数据最大值,刻度位于 0s/10ms/.../7d/14d)
对 **前缀缓存命中率**(y 轴,0–100%),每个提供商一个面板,带有超过 5m 的阴影带、一条红色虚线的
10% 低命中下限,以及位于 5m 和 1h 的竖向参考线。这是论文 `fig:prefix_cache_hit_rate_by_idle_time` 的
用户触发那一半,它展示了基于时间的驱逐特征:低命中的点聚集在长等待处。在约 5m 以下几乎每个步骤
仍然命中,但越过该点后低命中尾巴变粗,而超过 1h 后缓存实际上已经消失。数据印证了这一点 —— 在命中率跌破
10% 的用户步骤中,其前置等待的中位数约为 12.5 min,p90 约为 7.3 h,而用户步骤整体的等待中位数约为
1.4 min。因此用户未命中是由空闲驱动的:前缀在人类停顿期间老化失效,下一个请求于是重新追加了它的大部分
上下文。

### tool_result_wait_time_vs_hit_rate_scatter.png

对**工具触发的步骤**采用相同布局,x 轴改为**领头工具时长**
(`result_at - emitted_at`)而非人类等待。工具间隔远比人类等待短,因此点云从亚秒级集中到几分钟,而 >5m 的带
非常稀疏 —— 对 Codex 尤其如此,它把长时间运行的工作推到后台,所以其工具步骤能迅速续接(间隔中位数约
0.25s,p90 约 30s)。命中率在几乎所有工具时长上都保持很高:缓存通常能在工具触发的后续中存活,因为大多数工具
都在任何合理的驱逐窗口之内返回。确实出现的少数低命中工具步骤拖向较长的时长,与人类等待面板是同样的
老化效应,只是在这里很罕见。这就是为什么 `tab:prefix_cache_hit_rate` 把 tool-result 命中率定在约 97.5%,
而用户触发的则落后。
