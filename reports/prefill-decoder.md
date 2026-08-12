# vLLM Prefill/Decode Workload Decomposition

## 1. Purpose

Measure how prompt length and generated-output length affect TTFT, TPOT, E2E
latency, throughput, queueing, KV Cache usage and GPU activity at fixed
concurrency.

This report describes the phase-2 experiment in the fixed WSL2/GPU-PV lab
environment. It is not a claim about bare-metal Linux or other models.

## 2. Environment and method

- Run date: 2026-08-07 UTC
- GPU: NVIDIA GeForce RTX 4060 8GB
- vLLM: 0.26.0
- Model: `Qwen/Qwen3-0.6B`
- Model revision: `c1899de289a04d12100db370d81485cdf75e47ca`
- Maximum model length: 2048 tokens
- GPU memory utilization: 0.65
- Prefix caching: disabled
- Chunked prefill: enabled
- Client temperature: 0; `ignore-eos`: enabled
- Request rate: `inf` (closed-loop)
- Maximum concurrency: 8
- Requests per case and repetition: 64
- Warmup requests per run: 8
- Repetitions per case: 5

The runner captured detailed vLLM JSON, per-request latency data, one-second
`nvidia-smi` telemetry, Docker stats, before/after `/metrics` snapshots and a
0.5-second selected-metrics JSONL time series.

## 3. Workload matrix

| Case | Input tokens | Output tokens | Purpose |
|---|---:|---:|---|
| A | 128 | 32 | Light control |
| B | 1536 | 32 | Prefill-heavy |
| C | 128 | 256 | Decode-heavy |
| D | 1536 | 256 | Mixed-heavy |

## 4. Results

Values are medians across five repetitions. Brackets are the min-max range
across repetitions.

| Case | Output tok/s | Input tok/s | P50 TTFT ms | P99 TTFT ms | P99 TPOT ms | P99 E2E ms |
|---|---:|---:|---:|---:|---:|---:|
| A | 1,013.81 [1,004.05, 1,035.77] | 4,055.23 [4,016.18, 4,143.07] | 44.39 [29.79, 54.80] | 66.45 [60.68, 176.00] | 7.26 [7.17, 7.34] | 265.47 [257.61, 368.10] |
| B | 277.67 [277.10, 278.76] | 13,328.33 [13,300.78, 13,380.38] | 226.46 [225.61, 246.94] | 558.93 [551.01, 569.69] | 26.46 [26.42, 26.63] | 1,147.80 [1,122.38, 1,166.90] |
| C | 1,157.94 [1,152.48, 1,159.66] | 578.97 [576.24, 579.83] | 49.18 [41.59, 50.46] | 63.40 [61.75, 69.42] | 6.84 [6.82, 6.86] | 1,781.75 [1,776.27, 1,785.55] |
| D | 542.81 [542.57, 543.01] | 3,256.87 [3,255.39, 3,258.09] | 226.85 [225.68, 228.90] | 563.37 [558.02, 573.84] | 14.43 [14.41, 14.47] | 4,004.72 [3,969.30, 4,007.11] |

The matrix chart is [`prefill-decoder-matrix.png`](../charts/prefill-decoder-matrix.png).

## 5. Queue, KV Cache and GPU evidence

The following are extrema across the five 0.5-second metrics streams for each
case. `kv_cache_usage_perc` is converted from a fraction to a percentage.

| Case | Max running | Max waiting | Max capacity-waiting | Max KV Cache usage | Max preemptions |
|---|---:|---:|---:|---:|---:|
| A | 8 | 0 | 0 | 2.8–3.5% | 0 |
| B | 8 | 1–4 | 1–4 | 33.9% | 0 |
| C | 8 | 0 | 0 | 8.0–8.3% | 0 |
| D | 8 | 2–5 | 2–5 | 38.8% | 0 |

No deferred waiting was observed. The one-second GPU captures reported maximum
GPU utilization of 98% for A and 100% for B–D; the maximum observed SM clock
was 2,790 MHz. These are coarse duty-cycle measurements, not proof of kernel
occupancy or memory-bandwidth saturation.

## 6. Analysis

### Confirmed facts

- All 20 formal runs completed: 1,280/1,280 requests succeeded and 0 failed.
- With output fixed at 32 tokens, increasing input from 128 to 1536 tokens
  (A→B) reduced output throughput by 72.6%, increased median P99 TTFT by
  8.41×, and increased median P99 E2E by 4.32×.
- With input fixed at 128 tokens, increasing output from 32 to 256 tokens
  (A→C) left median P99 TTFT and P99 TPOT nearly unchanged (-4.6% and -5.8%),
  but increased P99 E2E by 6.71×. Request throughput fell by 85.7%, while
  output-token throughput increased only 14.2%.
- At the long-input setting, increasing output from 32 to 256 tokens (B→D)
  left P99 TTFT nearly unchanged (+0.8%) but increased P99 E2E by 3.49×.
- Long-input cases B and D were the only cases with capacity waiting in the
  sampled time series. No preemption or metrics polling error was recorded.

### Evidence-based inferences

- The dominant signature of a long prompt in this workload is higher first-token
  latency, lower output-token throughput, and capacity waiting. This is
  consistent with increased Prefill work and longer scheduler occupancy, but
  TTFT also includes queueing, tokenization and the first Decode step.
- The dominant signature of a long output is much longer E2E latency and lower
  request throughput. At input length 128, TTFT and steady-state TPOT stayed
  close to the control values, which is consistent with Decode extending the
  request rather than delaying its first token.
- The mixed case combines both effects: it has the highest E2E latency and the
  highest observed KV Cache usage, while its P99 TTFT remains at the
  long-prompt level.

### Unverified hypotheses

- The one-second GPU telemetry cannot distinguish Prefill kernel cost,
  Decode-kernel cost, launch gaps or CPU scheduling delays. Kernel-level
  profiling is required for that attribution.
- The capacity waiting in B and D may reflect batching and chunked-prefill
  scheduling interactions, but this experiment does not isolate those causes.
- These closed-loop results do not establish a sustainable online request rate
  or a production SLO operating point.

## 7. Evidence and limitations

Evidence files:

- [`prefill-decoder-summary.csv`](../results/summary/prefill-decoder-summary.csv): one row per run
- [`prefill-decoder-aggregate.csv`](../results/summary/prefill-decoder-aggregate.csv): median/min/max by case
- `results/raw/pd-*.json`: detailed benchmark results
- `results/telemetry/pd-*-metrics.jsonl`: sampled vLLM gauges and counters
- `results/telemetry/pd-*-gpu.csv`: GPU telemetry
- `artifacts/logs/pd-*.log`: load-generator logs

The benchmark logs contain the known CPU-only load-generator warnings about
Triton drivers and the AVX2 extension; they did not produce failed requests.
The experiment uses one model, one GPU, WSL2/GPU-PV, a finite 64-request burst,
one-second GPU sampling and 0.5-second metrics sampling.

## 8. Next experiment

Proceed to phase 3 open-loop saturation. Use the phase-2 cases as workload
anchors, especially B for prefill pressure and C for decode duration, then
increase finite request rates while monitoring P99 TTFT/E2E, waiting requests,
KV Cache usage, GPU utilization and failure rate.
