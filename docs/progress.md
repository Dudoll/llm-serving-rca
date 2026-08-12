# LLM Serving Performance & RCA Lab：统一进度入口

> 最后更新：2026-08-10
>
> 本文件只负责阶段状态与证据导航。系统边界见 [`design.md`](design.md)，研究问题
> 和验收标准见 [`experiment-plan.md`](experiment-plan.md)。

## 当前结论

| 项目 | 当前状态 |
|---|---|
| 当前阶段 | Phase 3b：12/13/14 req/s long-window validation（目标是 steady-state gate） |
| 最近完成 | Phase 3a：finite open-loop boundary scan |
| 总体状态 | Phase 0/1/2/3a 证据已存在；Phase 3b 计划/证据链就绪，SLO 取值和正式 GPU 运行待完成 |
| 已有正式证据 | 85 run、14,080 个成功请求、0 失败请求 |
| 已定位边界 | finite scan 的 transition band 为 12–14 req/s；尚未证明长期 sustainable rate |
| Git checkpoint | 上一 clean checkpoint 为 `6ad9a54`；Phase 2/3 evidence 与本轮设计实现尚在 working tree |
| 当前阻塞 | 无外部阻塞；正式运行前需选定 SLO，再预留约 75 分钟 nominal horizons，另加 warmup/cooldown/drain；稳态结论还需补齐表中 analysis gates |

上述总数来自 30-run baseline、20-run Prefill/Decode 和 35-run Phase 3a 报告；
不把 smoke/pilot 或待执行 Phase 3b 算入。它们是当前 checkout 的实验事实，不是其他
硬件、模型或部署环境的容量承诺，也不是 complete-manifest 数量。尚未 backfill 的
历史 run 在分析输出中必须显式标为 `legacy-unmanifested`。

## 状态说明

- ✅ 已完成：实验和验收证据已经保存。
- ▶️ 可开始：实现与前置条件具备，但正式实验尚未完成。
- ⏳ 计划中：目标和阶段门已定义，尚未实现或执行。
- ⚠️ 需复核：有结果，但存在证据缺口或需要补充验证。
- 🚫 阻塞：没有外部状态变化或用户决定就无法继续。

## 阶段总表

| 阶段 | 状态 | 已完成内容 | 证据入口 | 下一步 / 验收条件 |
|---|---|---|---|---|
| 0. 环境固化与功能链路 | ✅ | WSL2 GPU、Docker、vLLM、model revision 固定；UVA RCA、API、metrics、streaming smoke 完成 | [`rca-wsl-uva.md`](../reports/rca-wsl-uva.md) | 仅在环境或版本变化时复核 |
| 1. Closed-loop baseline | ✅ | 6 concurrency × 5，共 30 run、3,840 请求、0 失败 | [`baseline.md`](../reports/baseline.md)、[`baseline-aggregate.csv`](../results/summary/baseline-aggregate.csv) | c16 只作为候选 knee；不把 `request-rate=inf` 当作服务容量 |
| 2. Prefill/Decode 分解 | ✅ | A/B/C/D 各 5 次，共 20 run、1,280 请求、0 失败；metrics/GPU/log evidence 齐全 | [`prefill-decoder.md`](../reports/prefill-decoder.md)、[`prefill-decoder-aggregate.csv`](../results/summary/prefill-decoder-aggregate.csv) | B/C 作为后续 prefill/decode 压力锚点 |
| 3a. Finite open-loop boundary scan | ✅ | 4–16 req/s 七档 × 5，共 35 run、8,960 请求、0 失败；定位 12–14 transition band | [`open-loop.md`](../reports/open-loop.md)、[`open-loop-aggregate.csv`](../results/summary/open-loop-aggregate.csv) | 只形成边界候选，不声明长期 sustainable capacity |
| 3b. Long-window validation | ▶️ | 12/13/14 req/s、`MAX_CONCURRENCY=1024`、paired seeds、持久化 planned order、SLO/plan binding、container attestation、benchmark-envelope counter delta 与 manifest-bound consumers 已实现 | [`design.md`](design.md)、[`open_loop_steady.env`](../configs/open_loop_steady.env) | 选定 SLO；补 fixed-wall-clock/schedule-lag+cap-reach、窗口对齐 queue slope/end backlog/counter delta、accepted-attempt/execution ledger、P99 CI/Little's Law |
| 4. KV Cache / scheduler 压力 | ⏳ | 变量、metrics 和 OOM/preemption 判定规则已定义 | [`experiment-plan.md`](experiment-plan.md)、[`metric-definitions.md`](metric-definitions.md) | 用 Phase 3a/3b 边界隔离一个 cache/scheduler 变量 |
| 5. 长短请求混部 RCA | ⏳ | 80/20 对照、tagging 和 RCA 路径已定义 | [`experiment-plan.md`](experiment-plan.md) | 完成短请求收益、长请求代价和总吞吐回归 |
| 6. Host CPU / 容器资源干扰 | ⏳ | CPU quota、contention、memory pressure 与 perf 路径已定义 | [`experiment-plan.md`](experiment-plan.md) | 至少定位一个 Host 限制导致的退化 |
| 7. GPU timeline / kernel profiling | ⏳ | Nsight Systems/Compute 进入条件已定义 | [`experiment-plan.md`](experiment-plan.md) | 只对稳定、可复现的 GPU 现象 profiling |
| 8. 优化、复验与回归 | ⏳ | correctness/performance/regression gate 与 A/B 模板已定义 | [`design.md`](design.md)、[`experiment-plan.md`](experiment-plan.md) | 至少两项优化，其中一项明确报告副作用 |
| 9. 可迁移性与最终交付 | ⏳ | bare-metal 对照、artifact 分层和 portfolio 交付清单已定义 | [`design.md`](design.md) | 发布 evidence index、完整 RCA、面试材料和已验证简历 bullets |

## 最近完成：Phase 3a finite boundary scan

Phase 3a 固定 input/output=512/128、256 formal requests、8 warmups、max
concurrency=256，对 4/6/8/10/12/14/16 req/s 各重复 5 次。

已确认：

- 35 个 formal run 全部完成，8,960/8,960 请求成功；
- 14 req/s 的五次重复中四次出现 preemption，capacity waiting 与 KV Cache
  near-full 同时出现；
- 16 req/s 五次均出现 preemption、明显 backlog 和吞吐平台；
- 12–14 req/s 是需要稳态验证的 transition band。

尚未确认：

- 12 req/s 能否在长 arrival window 内保持 queue 不增长；
- 任一 rate 是否满足 production SLO；当前没有预先声明 TTFT/E2E SLO；
- finite-batch throughput/offered ratio 是否能代表 steady-window completion rate。

因此 [`open-loop.md`](../reports/open-loop.md) 不把 12 req/s 称为长期 sustainable
capacity。

## 当前下一步：Phase 3b long-window validation

1. 在 `configs/open_loop_steady.env` 中复核 nominal 300 秒 horizon、12/13/14 rates、
   `MAX_CONCURRENCY=1024` 与五个 load-generator seeds，并填写预先声明的 TTFT/E2E
   SLO；空 SLO 会使 runner fail closed；
2. 用 `benchmark/plan_open_loop.py` 生成 paired-block 计划并保存 planned order、SLO
   和 plan identity；runner/validator 会绑定 path/hash 与唯一 plan row；
3. 运行 `scripts/run_open_loop_steady.sh`，保留 arrival 结束后的 drain evidence；
4. 先用 `benchmark/validate_evidence.py` 验证 RunBundle，再生成 latency 与 telemetry
   per-run/aggregate tables；
5. 根据 actual sent/completed、queue slope、end backlog、drain、P99 CI、SLO
   goodput、preemption 和 Little's Law 共同判定；
6. 若边界稳定，再进入 scheduler/KV 单变量 RCA；否则修正实验设计而不是扩大 rate。

第 4 步的 normalizer/aggregators/telemetry/SLO consumers 默认拒绝 incomplete/missing
manifest；历史重建必须显式开启 legacy bypass 并保留 evidence status。SLO/plan
identity 与实际 container attestation 已进入执行链；剩余工作是严格 fixed-wall-clock
与 schedule-lag/cap-reach、window-aligned queue slope/end backlog/counter delta、
accepted-attempt/execution ledger、P99 CI 和 Little's Law。当前 before/after delta
覆盖整个 benchmark envelope，不能替代 arrival-window delta。完成前只能称
long-window finite validation。

## 证据入口

### 设计与规范

- [`README.md`](../README.md)：招聘者入口、项目快照和快速开始
- [`design.md`](design.md)：架构、RunBundle、状态机、artifact 与 regression gates
- [`experiment-plan.md`](experiment-plan.md)：Phase 0–9 研究问题与验收条件
- [`metric-definitions.md`](metric-definitions.md)：TTFT、TPOT、ITL、E2E、吞吐定义

### 已完成实验

- [`pilot-validation.md`](../reports/pilot-validation.md)：小规模 pipeline pilot
- [`baseline.md`](../reports/baseline.md)：正式 closed-loop baseline
- [`prefill-decoder.md`](../reports/prefill-decoder.md)：Prefill/Decode 分解
- [`open-loop.md`](../reports/open-loop.md)：Phase 3a finite boundary scan
- [`rca-wsl-uva.md`](../reports/rca-wsl-uva.md)：WSL2 UVA 启动故障 RCA

### Evidence roots

- `results/manifests/`：RunBundle manifest、状态、artifact checksum 与 validation
- `results/plans/`：确定性的实验执行顺序（Phase 3b 为
  `${RUN_NAMESPACE}-open-loop-steady-plan.tsv`）
- `results/raw/`：vLLM benchmark JSON 和 per-request 数据
- `results/telemetry/`：GPU、vLLM metrics、Docker stats
- `results/summary/`：per-run 与跨 repetition canonical tables
- `artifacts/env/`：环境快照
- `artifacts/logs/`：server、benchmark 和 runtime 日志
- `charts/`：从 canonical tables 生成的图

大型 evidence 默认不进入 Git；发布与隐私清理规则见
[`design.md`](design.md#9-artifact-分层与发布)。

## 更新规则

每完成一个阶段，只更新本文件的四处：

1. “当前结论”中的当前阶段、最近完成和 checkpoint；
2. “阶段总表”中的状态、证据入口和验收条件；
3. “当前下一步”中的固定矩阵、SLO 与 stage gate；
4. “更新记录”中的日期、验证结果和 Git checkpoint。

数值细节写入对应 `reports/*.md`，本文件不复制完整结果表。从本轮 evidence-system
checkpoint 起，只有 manifest complete、validator passed 的正式 run 才能增加顶部
总数；此前的 legacy baseline 必须保留其证据代际说明。

## 更新记录

| 日期 | 进展 | Git checkpoint |
|---|---|---|
| 2026-08-10 | 完成 Phase 3a 35-run finite scan；定位 12–14 transition band；进入 Phase 3b | working tree，待 evidence checkpoint |
| 2026-08-09 | 完成 Phase 2 Prefill/Decode matrix、报告和矩阵图 | `6ad9a54` 后的 working tree evidence |
| 2026-08-03 | 创建统一进度入口；Phase 0/1 已完成 | `3e01f9a` |
| 2026-07-31 | 完成正式 baseline：30 run、3,840 请求、0 失败 | `3e01f9a` |
