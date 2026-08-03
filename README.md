# LLM Serving Performance & RCA Lab

面向单机单卡 vLLM Serving 的可复现性能实验与根因分析项目。

## 当前实验边界

- Host: Windows + WSL2 Ubuntu 22.04
- GPU: NVIDIA GeForce RTX 4060 8GB
- Serving: vLLM 0.26.0
- Model: `Qwen/Qwen3-0.6B`
- Model revision: `c1899de289a04d12100db370d81485cdf75e47ca`
- Max model length: 2048 tokens
- Baseline: synthetic fixed-length workload
- 结论只适用于当前 WSL2/GPU-PV 环境，不泛化为 bare-metal Linux

## 当前状态

统一进度入口：[`docs/progress.md`](docs/progress.md)

- [x] GPU 和 Docker 透传已验证
- [x] WSL2 UVA 初始化错误已定位并修复
- [x] 实验环境和服务参数已固定
- [x] 启动、停止、健康检查和环境采集脚本
- [x] 单请求 streaming 测量客户端
- [x] Baseline runner 和汇总脚本
- [x] 并发 1/8/32 小规模 Pipeline pilot
- [x] 完整实验与系统学习路线
- [x] 正式并发 Baseline（1/2/4/8/16/32，各 5 次）
- [ ] Prefill/Decode 分离实验
- [ ] Open-loop 饱和实验
- [ ] 混合负载 RCA

## 快速开始

```bash
cd ~/projects/llm-serving-rca

./scripts/capture_env.sh
./scripts/start_server.sh
./scripts/health_check.sh
./scripts/run_smoke.sh
./scripts/run_pilot.sh
./scripts/capture_server_logs.sh
python3 benchmark/summarize_results.py
./scripts/setup_analysis_env.sh
./.venv/bin/python benchmark/plot_baseline.py
```

停止服务：

```bash
./scripts/stop_server.sh
```

## 正式 Baseline

```bash
./scripts/run_baseline.sh
python3 benchmark/summarize_results.py
python3 benchmark/aggregate_baseline.py
python3 benchmark/summarize_gpu_telemetry.py
./.venv/bin/python benchmark/plot_baseline.py
```

新实验由 `benchmark/poll_vllm_metrics.py` 每 0.5 秒保存 selected vLLM
metrics 到 JSONL，用于保留瞬时 queue、running/waiting requests、KV Cache 和
preemption 变化。首轮正式 baseline 早于该采集器，因此只具备 metrics 前后快照。

默认配置位于：

- `configs/server.env`
- `configs/baseline.env`

所有原始结果、telemetry 和日志均保存在仓库内，不应手工修改原始文件。

完整路线见 `docs/experiment-plan.md`。它定义了从 closed-loop baseline、
Prefill/Decode 分解、open-loop 饱和、KV Cache 压力、混合负载 RCA 到最终
优化复验的顺序和验收条件。

正式 baseline 已完成 30 个 run、3,840 个请求，0 失败。c1 到 c32 的
median output throughput 从 140.93 增至 1400.14 tok/s；c32 仍有吞吐收益，
但相对 c16 仅增加 25.82%，同时 P99 TTFT 增加 105.59%。完整结论和限制见
`reports/baseline.md`。

## 证据规则

每份报告明确区分：

1. 已确认事实
2. 基于证据的推断
3. 尚未验证的假设

性能结论至少需要原始结果、实验配置、环境快照和跨重复实验统计支持。
