# LLM Serving Performance & RCA Lab：完整实验与学习路线

## 1. 项目目标与边界

本项目不是“成功部署一次 vLLM”，而是建立一条可重复的系统性能分析链路：

```text
固定环境 -> 建立基线 -> 找到饱和点 -> 构造异常 -> 建立证据链
-> 验证根因 -> 实施修复 -> 复验收益和副作用 -> 形成可审计报告
```

实验系统的组件边界、RunBundle、manifest 状态机、evidence validator 和发布分层
由 [`design.md`](design.md) 定义；本文件负责研究问题、控制变量与阶段验收条件。

当前主实验环境固定为：

- Windows + WSL2 Ubuntu 22.04
- NVIDIA GeForce RTX 4060 8GB
- vLLM 0.26.0 固定 image digest
- `Qwen/Qwen3-0.6B` 固定 model revision
- 单机单卡、OpenAI-compatible HTTP server

所有结论只适用于当前 WSL2/GPU-PV 环境。若需要证明 bare-metal Linux
性能，最后必须在独立 Linux GPU 主机上做交叉验证。

## 2. 实验纪律

每个实验都遵循相同规则：

1. 一次只改变一个主要变量。
2. 原始 JSON、日志、metrics 和 GPU telemetry 不手工修改。
3. 功能 smoke 不能替代 warmup，warmup 不能计入正式结果。
4. 正式配置至少重复 5 次，报告中使用 median，并给出 min-max。
5. 同时报告吞吐和尾延迟，不能只报告平均值或单一 speedup。
6. 将内容分为“已确认事实、基于证据的推断、尚未验证的假设”。
7. 修复后使用同一 workload 复验，并记录吞吐、P99 和资源代价。
8. Windows 前台应用会共享 GPU；正式运行期间记录并尽量保持负载稳定。
9. 每个正式 run 必须有 manifest；只有 evidence validator 通过且状态为
   `complete` 的 run 才能进入聚合。
10. 随机种子和 planned order 在运行前写入 plan。跨配置比较优先使用 paired seed
    blocks；实际运行顺序需由独立 execution ledger 证明，并单独检查 order/carryover
    effect。
11. SLO、停止条件和 regression threshold 必须在运行前冻结，不能根据结果曲线
    事后选择。
12. 性能结论必须先通过 correctness gate；collector error、counter reset、缺失
    sidecar 或重复 repetition 都视为证据失败，不能静默跳过。

第 9 条已在 runner/resume 和默认 normalizer/aggregator/telemetry/SLO consumer path
强制；历史重建只能显式使用 `--allow-legacy-unmanifested` 并保留 evidence status，
不能把该 bypass 用于新实验验收。SLO/plan identity 与 actual container attestation
已经实现。collector validator 已检查 metrics endpoints、关键指标族和不超过 5 秒的
相邻 sample gap，并检查 GPU 整体 span；GPU 仍没有 max-gap 或逐端点 window
alignment。不能仅凭 manifest 存在就认为稳态窗口证据完整。

## 3. 指标与系统映射

| 指标 | 主要含义 | 重点关联层次 |
|---|---|---|
| TTFT | 排队、tokenization、Prefill、首次调度 | Scheduler、CPU、GPU Prefill |
| TPOT | 第一 token 后的平均生成间隔 | Decode、KV Cache、batching |
| ITL | 相邻 token 间隔及抖动 | Decode 调度、抢占、系统干扰 |
| E2E | 用户观察到的总延迟 | Queue + Prefill + Decode |
| Output tok/s | 有效生成吞吐 | GPU 利用率、batch size |
| Request queue time | 请求进入到开始执行的等待 | 饱和、HOL blocking |
| Scheduled / actual sent rate | load generator 是否真正施加了计划负载 | client lag、concurrency cap |
| Steady completed rate | 同一稳态窗口内完成的请求率 | 服务跟随 offered load 的能力 |
| SLO goodput | 正确且同时满足 TTFT/E2E SLO 的请求率 | 可用服务容量 |
| Queue slope / end backlog | backlog 是否随时间持续增长 | 稳态与过载判定 |
| Drain time | 停止到达后清空 backlog 的时间 | 有限批次隐藏的欠账 |
| KV Cache usage | 可并发 sequence 的内存约束 | 显存容量、调度、preemption |
| GPU util / memory / clocks | GPU 活跃度与资源状态 | GPU 饱和、降频、共享干扰 |
| CPU / RSS / cgroup | Host 侧资源消耗 | HTTP、tokenizer、scheduler |

关系式只用于建立直觉，不替代实测：

```text
E2E ~= queue_time + TTFT_work + (output_tokens - 1) * TPOT
Little's Law: concurrency ~= arrival_rate * average_latency
```

Little's Law 检查只使用对齐的 steady measurement window：以 server 采样得到的
`mean(running + waiting)` 对比同一窗口的 `completed_rate * mean(E2E)`。它用于发现
窗口、单位或采样错误，不单独决定容量。

## 4. 分阶段执行计划

### 阶段 0：环境固化与功能链路

状态：已完成。

要学习：WSL2 GPU-PV、NVIDIA Container Runtime、容器共享内存、image
digest、model revision、vLLM EngineCore 启动路径。

证据：

- 环境快照；
- 固定镜像、模型 revision 和 server 参数；
- `/health`、`/v1/models`、`/metrics`；
- streaming smoke 的 TTFT/TPOT/E2E；
- `UVA is not available` 的完整 RCA。

验收：服务可重复启动，单请求生成成功，metrics 可采集，失败原因有复现实验。

### 阶段 1：正式 closed-loop concurrency baseline

状态：已完成。30 个 run、3,840 个请求、0 失败；结果见
`reports/baseline.md`。

问题：并发增加时，吞吐在哪一点开始趋于饱和，TTFT/TPOT/P99 如何变化？

固定项：

- input=512，output=128；
- 128 requests，8 warmups；
- request-rate=inf；
- prefix cache disabled；
- temperature=0，ignore-eos；
- concurrency=1/2/4/8/16/32，每组 5 次。

采集：vLLM detailed JSON、`/metrics` 前后快照、1 秒 GPU telemetry、
Docker stats、server 和 load-generator 日志。

分析：

- 每个并发计算 output tok/s、P50/P95/P99 TTFT、P99 TPOT/E2E 的 median；
- 给出跨重复实验 min-max；
- 标记吞吐边际收益明显下降，同时 P99 显著上升的拐点；
- 检查失败、preemption、waiting requests 和 GPU clocks 异常。

验收：30 个正式 run 全部完成；每个 run 的证据文件齐全；形成两张图和
`reports/baseline.md`。如果任一 run 失败，先停止批量实验并 RCA，不跳过。

### 阶段 2：Prefill 与 Decode 负载分解

状态：已完成；20 个正式 run 全部成功，时间序列 metrics 采集器和矩阵图已完成。

问题：长 prompt 和长 output 分别影响哪类指标？

固定 concurrency=8，使用 2x2 矩阵：

| Case | Input | Output | 主要压力 |
|---|---:|---:|---|
| A | 128 | 32 | 轻负载控制组 |
| B | 1536 | 32 | Prefill-heavy |
| C | 128 | 256 | Decode-heavy |
| D | 1536 | 256 | 混合重负载 |

每组 64 requests、5 次重复。1536 而非 2048，是为了给输出和协议模板留下
context 空间。

预期签名：

- Prefill-heavy：TTFT、input tok/s 和 GPU compute 活跃度变化更明显；
- Decode-heavy：E2E、TPOT/ITL 和持续 GPU 活跃时间更明显；
- 若观察不符合预期，先检查 queue time、实际 token 长度和 batching，不能直接
  归因于 GPU。

验收：已用数据解释 TTFT 与 TPOT 的不同来源，并形成 workload matrix 对比图和
`reports/prefill-decoder.md`。结论和限制见该报告。

### 阶段 3：open-loop 到达率与服务饱和

问题：在更接近在线服务的到达模型下，容量压力从哪里开始；在预先声明的 SLO 下，
哪个 rate 能在长时间到达窗口内保持稳态？

#### 阶段 3a：finite boundary scan

状态：已完成。35 个正式 run、8,960 个请求全部成功；结果见
[`reports/open-loop.md`](../reports/open-loop.md)。

固定 input/output=512/128、256 requests、8 warmups、max concurrency=256，扫描
4/6/8/10/12/14/16 req/s，每档重复 5 次。该扫描确认 12–14 req/s 是需要进一步
验证的 transition band：14 req/s 开始出现 near-full KV Cache、capacity waiting
和 preemption，16 req/s 是明显过载点。

阶段 3a 的目标是缩小边界，不是证明长期容量。它使用有限 request batch，停止
到达后 queue 可以 drain；`completed throughput / configured rate` 也只是包含 drain
影响的有限批次诊断比值，不是请求完成率。**不能把 12 req/s 写成已经验证的长期
sustainable rate。**

验收：已区分 low-load tracking、12–14 req/s transition band 和 rate-16 overload，
并保存 raw JSON、metrics time series、GPU telemetry、Docker stats 与日志。

#### 阶段 3b：long-window validation toward steady state

状态：待运行；配置和 deterministic plan/runner 已实现，执行需要 GPU 空闲。

固定 Phase 3a 的 server 和 512/128 workload，只验证 12/13/14 req/s。当前 runner
按 `num_prompts = rate * 300 seconds` 构造 nominal 300-second arrival horizon，并从
raw `start_times` 报告 realized arrival span；它不是 wall-clock 精确截止的固定窗口。
五个 repetition/block 使用五个不同 load-generator seed，同一 block 的三个 rate
共用 seed，planned rate order 由固定 plan seed 确定并持久化。当前
`MAX_CONCURRENCY=1024`，用于降低 client semaphore 隐藏 server backlog 的风险；仍需
用 schedule-lag/cap-reach 指标验证。warmup 不计入正式请求，有限 arrival sequence
结束后继续采集到所有请求完成。

必须同时采集：

- scheduled rate、actual sent rate、schedule lag 和 client-cap reach；
- steady-window completed rate、post-window completions 和 drain time；
- P99 TTFT/E2E/TPOT、预先声明 SLO 下的 goodput 和 bootstrap confidence interval；
- waiting/running time series、queue slope、end backlog、queue counter delta；
- KV Cache P95/max、preemption、GPU/host 资源、失败和 collector errors。

当前 runner、paired plan、manifest/evidence validator、realized send/approximate
drain、queue/KV/preemption telemetry 汇总、SLO/plan identity 绑定和 actual container
attestation 已实现。TTFT/E2E SLO 在 checked-in config 中故意留空，runner 会拒绝
正式执行。选定后，planner 把阈值、namespace、workload 条件与 planned order 写入
plan；runner 再把 namespace/attempt、plan row/path/hash 和 SLO 写入 manifest/raw
metadata，并由 validator 重新校验。telemetry aggregate 已优先用 metrics-before/after
计算 benchmark-envelope counter delta；该窗口包含 warmup、formal requests 与 drain。

正式验收的剩余 gates 是：严格 fixed-wall-clock loadgen 与 schedule-lag/client
cap-reach；arrival-window 对齐的 queue slope/end backlog 和 completion 切分；正式
窗口 counter delta；accepted-attempt 映射与 actual execution ledger；P99 bootstrap
CI 与同窗口 Little's Law。当前 300 秒仍只是 prompt count 推导的 nominal expected
horizon；queue maximum 和 benchmark-envelope counter delta 都不能替代上述指标。
当前实现应称 long-window finite validation。

validator 目前验证 metrics endpoints 包住 first arrival/last completion、关键指标族
齐全且相邻 sampling gap 不超过 5 秒，并要求 GPU CSV 的递增 timestamp span 不短于
raw duration；GPU 尚无 max-gap，也未将两端逐点对齐 arrival/completion。这些窗口
证据补齐前，不把 runner 的一次成功退出等同于完整稳态验收。

Telemetry/SLO analyzers 和 open-loop aggregate 已默认绑定并重验 complete manifest，
输出会传播 evidence status；历史 bypass 必须显式开启。跨所有 phase 的统一 evidence
index 仍属于发布层改进，但不再是这些 consumers 的 fail-closed 缺口。

容量验收必须同时满足：actual sent 跟上计划、steady completed 跟上 actual sent、
queue 不持续正增长、tails 跨重复稳定且满足预先声明 SLO、无失败/preemption，且
Little's Law 在同一窗口内数量级一致。任一条件不满足，该 rate 只能标为 transition
或 overload，不能标为 sustainable。

停止条件：连续配置出现持续 queue 增长、P99 发散、失败或 preemption 时，停止向
更高 rate 扩展，先完成 evidence validation 和 RCA。

### 阶段 4：KV Cache 与 scheduler 压力

问题：显存分配和 sequence 数如何限制并发？

变量按顺序分开测试：

1. 增加 context 长度和并发；
2. 调整 `gpu-memory-utilization`；
3. 调整 scheduler 可接纳的 sequence 数；
4. 对比 chunked prefill on/off。

重点指标：`kv_cache_usage_perc`、running/waiting requests、preemptions、
GPU memory、TTFT P99、TPOT P99、失败和 OOM。

验收：至少构造一次可恢复的 KV Cache 高压场景，证明压力是来自 cache 容量、
scheduler 限制还是模型权重/运行时常驻显存。禁止把所有显存不足统称为 OOM。

### 阶段 5：长短请求混部 RCA

问题：少量长请求为什么会伤害短请求尾延迟？

对照组：100% 短请求。实验组：80% 短请求 + 20% 长请求。请求到达率和短请求
总量保持可比较，并给短、长请求分别打标签。

RCA 路径：

```text
短请求 P99 恶化
-> 检查 waiting/running/queue time
-> 检查 batch 与长 Prefill 时间重叠
-> 检查 KV Cache 与 preemption
-> 改变调度或流量隔离参数
-> 同 workload 复验
```

候选修复：长短请求分池、限制长请求并发、调整调度参数、chunked prefill。
每个修复都要报告短请求收益、长请求代价和总吞吐变化。

验收：形成第一份完整 RCA：现象、证据、候选原因、验证、根因、修复、副作用。

### 阶段 6：Host CPU 与容器资源干扰

问题：GPU 未饱和时，Host 侧 CPU、内存或调度是否成为瓶颈？

实验按低风险顺序执行：

1. 默认资源；
2. 限制 server CPU 数量；
3. 制造独立 CPU contention；
4. 限制容器 memory，并观察 reclaim/OOM；
5. 必要时比较 CPU affinity。

系统证据：Docker/cgroup CPU 和 memory、context switch、page fault、RSS、
load average、vLLM queue、GPU util。`perf` 仅分析 Host CPU 路径，不能替代
GPU profiler。

验收：至少定位一个 Host 资源限制导致的性能退化，并区分 tokenizer、HTTP、
scheduler、内存回收和 GPU 执行等待。

### 阶段 7：GPU 时间线与 kernel-level profiling

前提：前面实验已经出现稳定、可重复且值得深挖的 GPU 现象。

工具分工：

- `nvidia-smi`：低频整体利用率、显存、clock、温度；
- Nsight Systems：CPU thread、CUDA API、kernel launch、同步和时间线；
- Nsight Compute：仅针对选定 kernel 的 occupancy、memory throughput 等细节。

先用 Nsight Systems 判断是 launch gap、同步、Prefill 大 kernel 还是 Decode
小 kernel/调度问题，再决定是否使用 Nsight Compute。不要从全量 kernel 指标开始。

验收：选一个前面已证实的现象，用时间线解释 CPU/GPU 协同，而不是只贴截图。

### 阶段 8：优化、复验与回归检查

每个优化使用同一模板：

| 项目 | 内容 |
|---|---|
| Baseline | 原配置和原始证据 |
| Change | 一次只改一个变量 |
| Expected signature | 预期改变哪些指标 |
| Validation | 同 workload、同重复次数 |
| Benefit | median 和 P99 改善 |
| Cost | 显存、吞吐、长请求或稳定性代价 |
| Regression | 其他 workload 是否退化 |

每次 A/B 在性能比较前先通过 correctness gate：completed/failed、HTTP/stream 解析、
实际 token 长度、model/tokenizer/非目标参数一致、collector 完整性和抽样
prompt/response hash 均符合预先定义的规则。性能 gate 同时检查 baseline、压力场景
和一个非目标 workload，并报告 SLO goodput、queue/KV/preemption 与资源代价。

验收：至少完成两项优化，其中一项必须包含明确副作用；报告 point estimate、重复间
spread/CI、正确性与回归结果，避免把参数调优写成无条件收益。

### 阶段 9：可迁移性验证与最终交付

有条件时，在 bare-metal Linux 或云 GPU 上重跑最小对照集：

- concurrency=1、饱和点、过载点；
- 一组 Prefill-heavy；
- 一组 Decode-heavy；
- 混合负载 RCA 的 baseline/fix。

最终交付：

- 一键启动、停止、健康检查和 benchmark；
- 环境快照、原始 JSON、telemetry、图表；
- baseline 报告和至少一份完整 RCA；
- 架构/数据流图；
- 5 分钟与 20 分钟面试讲稿；
- 两条只使用已验证数字的简历 bullet；
- 已知限制和下一步。
- `results/manifests/` run index 与 checksum；
- 可下载的 full raw/telemetry/log evidence bundle，或明确说明哪些证据仅本地保存；
- 无 GPU CI：fixtures、validator、aggregation regression、Python/shell checks。

## 5. 执行顺序与阶段门

```text
阶段 0 完成
  -> 阶段 1 正式 baseline
  -> 阶段 2 Prefill/Decode
  -> 阶段 3a finite boundary scan
  -> 阶段 3b long-window / steady-state validation
  -> 阶段 4 KV Cache
  -> 阶段 5 混合负载 RCA
  -> 阶段 6 Host 资源 RCA
  -> 阶段 7 GPU profiling（有稳定现象才进入）
  -> 阶段 8 优化复验
  -> 阶段 9 最终证据包
```

暂时不进入 Kubernetes、多 GPU、Tensor Parallel、SGLang 对比、Speculative
Decoding 或自定义 CUDA kernel。它们不会替代当前单卡实验方法和 RCA 证据链。

## 6. 建议的两周验收节奏

### 第一个两周

- 完成正式 baseline、Prefill/Decode matrix；
- 交付 summary CSV、4 张图、baseline 报告；
- 能解释 TTFT、TPOT、E2E 和吞吐之间的关系。

### 第二个两周

- 完成 Phase 3b 稳态验收、KV Cache 压力、混合负载 RCA；
- 交付一份完整 RCA 和一项优化复验；
- 能解释 queue、batching、KV Cache 与 P99 的因果链。

### 第三个两周

- 完成 Host 资源实验和有针对性的 GPU timeline；
- 交付第二份 RCA、最终 README、面试讲稿和简历 bullet；
- 视资源决定是否做 bare-metal 对照。
