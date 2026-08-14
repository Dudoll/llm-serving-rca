# 代码阅读地图

如果只记住一条主线，请从这里开始：

```text
configs/*.env
    -> scripts/run_one_bench.sh
    -> results/raw/<run-id>.json + sidecar artifacts
    -> results/manifests/<run-id>.json
    -> benchmark/validate_evidence.py
    -> benchmark/summarize_results.py
    -> benchmark/aggregate_*.py
    -> reports/ and charts/
```

## 四个边界

### 1. Result contract：raw JSON 能不能被读取

入口：[result_contract.py](../benchmark/result_contract.py)

这里只检查 raw result 自己是否自洽：必需字段、成功/失败计数、每请求数组长度、
有限数值、时间戳顺序和错误列表。它不读取 manifest，也不决定实验是否正式纳入。

### 2. Evidence admission：这次运行能不能被相信

入口：[evidence_admission.py](../benchmark/evidence_admission.py)

正常路径要求 manifest 为 `complete`，raw、log、GPU、metrics 和 before/after
sidecar 必须通过完整校验并且路径绑定正确。历史 raw 没有 manifest 时，只能显式使用
`--allow-legacy-unmanifested`，并会被标记为 `legacy-unmanifested`。

### 3. Result normalization：把一次运行变成一行

入口：[result_normalization.py](../benchmark/result_normalization.py)

`summarize_file()` 只做一件事：读取一个 raw JSON，验证它，然后计算
`input_throughput`、`arrival_span_s`、`realized_send_rate` 和
`drain_time_approx_s`。它不写文件，也不接收可编辑 CSV 作为事实来源。

这里的 **canonical row** 指“由 raw JSON 重新计算出来的标准行”。CSV 只是缓存；聚合
前会再次从 raw 重建并逐字段比较，防止有人直接修改 CSV 中的吞吐或延迟数字。

### 4. Experiment identity：哪些运行可以相互比较

入口：[experiment_identity.py](../benchmark/experiment_identity.py)

`experiment_spec_sha256` 来自固定的 workload、server 和 Git revision，并排除
artifact churn、attempt、block 和 order 等运行级变化。聚合和 gain 计算都必须带着
这个 identity，不能只凭 input/output/concurrency 猜测“条件相同”。

## `summarize_results.py` 现在做什么

入口：[summarize_results.py](../benchmark/summarize_results.py)

它现在是 CLI 编排器，不再承载所有业务规则：

1. 找到 `results/raw/*.json`；
2. 通过 `evidence_admission` 判断是否为 complete 或显式 legacy；
3. 通过 `result_normalization` 生成一行；
4. 加入 ExperimentSpec identity；
5. 通过 `gain_calculation.py` 计算同一实验条件内的相对变化；
6. 写出 `results/summary/*.csv`。

因此，修改规则时可以按问题定位：

| 想改什么 | 看哪里 |
|---|---|
| raw 字段或数组合法性 | `result_contract.py` |
| complete/legacy 准入 | `evidence_admission.py`、`validate_evidence.py` |
| 一行 CSV 的派生指标 | `result_normalization.py` |
| 实验分组和可比性 | `experiment_identity.py`、`aggregate_*.py` |
| concurrency gain | `gain_calculation.py` |
| 命令行参数和输出 | `summarize_results.py` |

## 运行和检查顺序

如果只是想按完整流程处理一个 phase，可以使用统一入口：

```bash
python3 benchmark/analyze_phase.py phase1 --legacy
python3 benchmark/analyze_phase.py phase2 --legacy
python3 benchmark/analyze_phase.py phase3a --legacy --plot
```

`--legacy` 只适用于没有 schema-v1 manifest 的历史 raw；新 run 不要加它。Phase 3b v1
已经将 SLO 冻结为 TTFT=200 ms、E2E=4,000 ms，执行离线分析：

```bash
python3 benchmark/analyze_phase.py phase3b \
  --ttft-slo-ms 200 \
  --e2e-slo-ms 4000 \
  --plot
```

这个入口只是把下面几个独立阶段串起来，不隐藏它们的输入/输出：

```text
summarize_results.py
    -> results/summary/<phase>-summary.csv
aggregate_*.py
    -> results/summary/<phase>-aggregate.csv
aggregate_telemetry.py (open-loop only)
    -> results/summary/<phase>-telemetry-summary.csv
plot_*.py (optional)
    -> charts/
```

学习代码时，建议先单独运行每一步；熟悉之后再使用 `analyze_phase.py`。

先看单次运行：

```bash
bash scripts/run_one_bench.sh --help  # shell runner 的参数以脚本实际输出为准
python3 benchmark/validate_evidence.py --help
```

再看离线分析：

```bash
python3 benchmark/summarize_results.py --help
python3 benchmark/aggregate_open_loop.py --help
python3 benchmark/analyze_slo.py --help
python3 -m unittest discover -s tests
```

Phase 3b v1 默认从 `results/plans/phase3b-v1-accepted-attempts.tsv` 读取每个 logical
cell 唯一接受的 complete attempt；runner 每次结束都会重建该 ledger，failed attempt
仍原样保留用于 RCA。Phase 3b v1 的结果见
[`reports/open-loop-steady.md`](../reports/open-loop-steady.md)。当前设计的剩余边界是
fixed wall-clock arrival、schedule-lag/client-cap reach、steady-window queue
slope/end backlog、window-aligned counter delta、P99 confidence intervals 和
Little's Law。代码通过这些边界前，不应把 long-window finite validation 写成长期
可持续容量。
