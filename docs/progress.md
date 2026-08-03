# LLM Serving Performance & RCA Lab：统一进度入口

> 最后更新：2026-08-03
>
> 使用方式：先看本文件判断当前阶段，再进入“证据入口”中的报告和原始数据。
> 阶段的目标、指标和验收标准以 [`experiment-plan.md`](experiment-plan.md) 为准。

## 当前结论

| 项目 | 当前状态 |
|---|---|
| 当前阶段 | 阶段 2：Prefill/Decode 负载分解 |
| 最近完成 | 阶段 1：closed-loop concurrency baseline |
| 总体状态 | 第一阶段证据包已完成，下一阶段尚未执行 |
| WSL 项目目录 | `/home/joel/projects/llm-serving-rca` |
| Windows 工作区镜像 | `C:\Users\Administrator\.codex\.chatgpt-projects\g-p-6a65af6f4bd4819182208141441d965d\llm-serving-rca` |
| vLLM 服务 | 已停止；需要实验时由 `scripts/start_server.sh` 启动 |
| Git checkpoint | `3e01f9a`，正式 baseline 证据已提交 |
| 当前阻塞 | 无技术阻塞；下一轮实验需要占用 RTX 4060 |

## 状态说明

- ✅ 已完成：实验和验收证据已经保存。
- ▶️ 可开始：前置条件已具备，但正式实验尚未完成。
- ⏳ 计划中：已经定义目标和验收条件，尚未准备或执行。
- ⚠️ 需复核：有结果，但存在异常、证据缺口或需要补充验证。
- 🚫 阻塞：没有外部状态变化或用户决定无法继续。

## 阶段总表

| 阶段 | 状态 | 已完成内容 | 证据入口 | 下一步 / 验收条件 | 阻塞项 |
|---|---|---|---|---|---|
| 0. 环境固化与功能链路 | ✅ | WSL2 GPU、Docker、vLLM、model revision 固定；UVA RCA 完成；API、metrics、streaming smoke 通过 | [`rca-wsl-uva.md`](../reports/rca-wsl-uva.md)、环境快照、startup log | 仅在环境或版本变化时复核 | 无 |
| 1. Closed-loop baseline | ✅ | 6 个 concurrency × 5 次，共 30 run；3,840 请求；0 失败；GPU/metrics/Docker 证据齐全 | [`baseline.md`](../reports/baseline.md)、[`baseline-aggregate.csv`](../results/summary/baseline-aggregate.csv)、吞吐/TTFT 图 | 进入阶段 2；保留 c16 作为候选 latency/throughput knee | 无 |
| 2. Prefill/Decode 分解 | ▶️ | 2×2 workload matrix 已定义；metrics JSONL poller 已实现并通过 smoke；正式实验未执行 | [`experiment-plan.md`](experiment-plan.md)、[`poll_vllm_metrics.py`](../benchmark/poll_vllm_metrics.py) | 在 concurrency=8 下执行 A/B/C/D，各 5 次；形成 `reports/prefill-decode.md` | 需要 GPU 空闲 |
| 3. Open-loop 饱和 | ⏳ | 到达率、停止条件和 Little's Law 检查方法已定义 | [`experiment-plan.md`](experiment-plan.md) | 使用有限 request-rate，区分 burst peak 与 sustainable throughput | 等待阶段 2 |
| 4. KV Cache / scheduler 压力 | ⏳ | 变量、重点 metrics 和 OOM/preemption 判定规则已定义 | [`experiment-plan.md`](experiment-plan.md)、[`metric-definitions.md`](metric-definitions.md) | 增加 context/concurrency，记录 KV Cache 和 waiting time series | 等待阶段 2 的采集链路 |
| 5. 长短请求混部 RCA | ⏳ | 对照组、实验组和 RCA 链路已定义 | [`experiment-plan.md`](experiment-plan.md) | 构造 80% 短请求 + 20% 长请求，完成一份完整 RCA | 等待阶段 3/4 |
| 6. Host CPU / 容器资源干扰 | ⏳ | CPU quota、contention、memory pressure 和 perf 证据路径已定义 | [`experiment-plan.md`](experiment-plan.md) | 至少定位一个 Host 资源限制导致的退化 | 等待阶段 5 |
| 7. GPU timeline / kernel profiling | ⏳ | Nsight Systems/Compute 的进入条件和工具分工已定义 | [`experiment-plan.md`](experiment-plan.md) | 仅针对已复现现象做时间线分析 | 等待稳定 GPU 现象 |
| 8. 优化、复验与回归 | ⏳ | baseline/change/benefit/cost/regression 模板已定义 | [`experiment-plan.md`](experiment-plan.md) | 完成至少两项优化，其中一项包含副作用 | 等待 RCA 结果 |
| 9. 可迁移性与最终交付 | ⏳ | bare-metal 对照和最终证据包清单已定义 | [`experiment-plan.md`](experiment-plan.md) | 形成 README、架构图、RCA、简历 bullets、面试讲稿 | 等待前序阶段 |

## 当前下一步

### 阶段 2：Prefill/Decode 2×2 matrix

| Case | Input tokens | Output tokens | 目的 |
|---|---:|---:|---|
| A | 128 | 32 | 轻负载 control |
| B | 1536 | 32 | Prefill-heavy |
| C | 128 | 256 | Decode-heavy |
| D | 1536 | 256 | 混合重负载 |

固定条件：concurrency=8、每组 64 requests、warmup=8、每组重复 5 次；
使用固定 model revision、temperature=0、prefix cache disabled，并保存 metrics
JSONL、GPU telemetry、raw JSON 和日志。

阶段 2 完成条件：

- A/B/C/D 共 20 个 run 全部成功；
- 每组 raw JSON、metrics time series、GPU telemetry 和日志齐全；
- 能解释 TTFT、TPOT、E2E 和 GPU/queue/KV Cache 指标的差异；
- 新增 [`reports/prefill-decode.md`](../reports/prefill-decode.md)；
- 在本文件把阶段 2 改为 ✅，填写实际结果和下一步。

## 证据入口

### 规范与路线

- [`README.md`](../README.md)：项目边界、快速开始和简要 checklist
- [`experiment-plan.md`](experiment-plan.md)：阶段 0–9 的目标、指标和验收条件
- [`metric-definitions.md`](metric-definitions.md)：TTFT、TPOT、ITL、E2E、吞吐定义

### 已完成实验

- [`pilot-validation.md`](../reports/pilot-validation.md)：小规模 pipeline pilot
- [`baseline.md`](../reports/baseline.md)：正式 baseline 结论、异常和限制
- [`rca-wsl-uva.md`](../reports/rca-wsl-uva.md)：WSL2 UVA 启动故障 RCA
- [`baseline-aggregate.csv`](../results/summary/baseline-aggregate.csv)：跨重复 median/min/max
- [`gpu-telemetry-summary.csv`](../results/summary/gpu-telemetry-summary.csv)：GPU 时间窗汇总
- [`baseline-in512-out128-throughput.png`](../charts/baseline-in512-out128-throughput.png)：吞吐图
- [`baseline-in512-out128-ttft.png`](../charts/baseline-in512-out128-ttft.png)：TTFT 图

### 原始证据目录

- `results/raw/`：vLLM benchmark JSON 和 per-request 数据
- `results/telemetry/`：GPU、metrics、Docker stats
- `artifacts/env/`：环境快照
- `artifacts/logs/`：server、benchmark 和 runtime 日志

## 更新规则

每完成一个阶段，只更新本文件的四处：

1. “当前结论”中的当前阶段、最近完成和 Git checkpoint。
2. “阶段总表”中的状态、已完成内容、证据入口、下一步和阻塞项。
3. “当前下一步”中的实验矩阵和验收条件。
4. “最后更新”日期。

原始数据不在本文件中复制；数值结论写入对应 `reports/*.md`，汇总数值写入
`results/summary/`，本文件只负责导航和进度状态。

## 更新记录

| 日期 | 进展 | Git checkpoint |
|---|---|---|
| 2026-08-03 | 创建统一进度入口；阶段 0/1 已完成，阶段 2 待执行 | `3e01f9a` |
| 2026-07-31 | 完成正式 closed-loop baseline：30 run、3,840 请求、0 失败 | `3e01f9a` |
