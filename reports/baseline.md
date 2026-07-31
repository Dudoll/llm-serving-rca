# vLLM Single-GPU Closed-Loop Baseline

## 1. Purpose

Establish a reproducible WSL2 single-GPU baseline and identify how concurrency
changes throughput, TTFT, TPOT and tail latency before fault injection.

## 2. Environment

- Formal environment snapshot: `artifacts/env/environment-20260731T053625Z.txt`
- Baseline-start Git commit: `b32d553`
- GPU: NVIDIA GeForce RTX 4060 8GB
- WSL Kernel: `6.18.33.2-microsoft-standard-WSL2`
- vLLM: 0.26.0
- Model: `Qwen/Qwen3-0.6B`
- Model revision: `c1899de289a04d12100db370d81485cdf75e47ca`
- Image digest: `sha256:ffb2d59b1c059a5bd8d781320c9f5189de8293693b7d95da54befddaa54abf52`
- Prefix Cache: disabled
- Chunked Prefill: enabled
- Server generation config: `vllm`
- Client temperature: 0

## 3. Workload

- Dataset: random, fixed length
- Input length: 512 tokens
- Output length: 128 tokens
- Maximum concurrency: 1 / 2 / 4 / 8 / 16 / 32
- Requests per run: 128
- Repetitions: 5
- Warmup requests: 8 per run
- Request rate: `inf`
- `ignore-eos`: enabled

This is a closed-loop limited-concurrency burst workload. It measures batching
and latency under a finite request set; it is not a production arrival model.

## 4. Evidence integrity

- 30 raw JSON result files
- 30 benchmark logs
- 30 GPU telemetry CSV files
- 30 `/metrics` before/after pairs
- 30 Docker stats before/after pairs
- 3,840 successful requests, 0 failed requests
- Runtime server log contains no `ERROR`, traceback, CUDA OOM or preemption record
- Final `vllm:num_preemptions_total` remained 0

The control process hit its 30-minute wait limit after c16-r2. The vLLM server
remained running and the first 22 runs were already complete. The runner was
made idempotent, verified and skipped those 22 JSON files, then completed the
remaining eight runs without overwriting existing results. The benchmark
payload and server configuration did not change. This introduced a longer
pause before c16-r3 and is retained as an execution caveat.

## 5. Results

Values are the median across five repetitions. Throughput brackets show the
min-max range across repetitions.

| Concurrency | Output tok/s | Gain vs previous | P50 TTFT ms | P95 TTFT ms | P99 TTFT ms | P99 TPOT ms | P99 E2E ms |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 140.93 [140.51, 147.54] | - | 29.80 | 32.30 | 34.12 | 7.11 | 932.56 |
| 2 | 258.38 [257.42, 260.81] | +83.33% | 47.59 | 53.21 | 55.03 | 7.66 | 1007.25 |
| 4 | 458.87 [456.83, 480.51] | +77.60% | 91.57 | 98.83 | 101.15 | 8.60 | 1134.69 |
| 8 | 737.75 [726.19, 752.01] | +60.77% | 119.21 | 184.56 | 197.52 | 10.77 | 1450.13 |
| 16 | 1112.79 [1109.91, 1128.79] | +50.84% | 208.66 | 307.79 | 380.32 | 14.13 | 1933.57 |
| 32 | 1400.14 [1397.33, 1403.32] | +25.82% | 285.31 | 699.97 | 781.92 | 22.21 | 3376.33 |

Artifacts:

- `results/summary/baseline-summary.csv`: per-run values
- `results/summary/baseline-aggregate.csv`: median/min/max aggregation
- `results/summary/gpu-telemetry-summary.csv`: GPU samples aligned to each main run
- `charts/baseline-in512-out128-throughput.png`
- `charts/baseline-in512-out128-ttft.png`

## 6. Analysis

### Confirmed facts

- From c1 to c32, median output throughput increased 9.93x, from 140.93 to
  1400.14 output tokens/s.
- The marginal throughput gain per concurrency doubling decreased from 83.33%
  at c1->c2 to 25.82% at c16->c32.
- Over the same c1->c32 range, P99 TTFT increased 22.92x, P99 TPOT increased
  3.12x and P99 E2E increased 3.62x.
- c16->c32 added 25.82% throughput while P99 TTFT increased 105.59%, P99 TPOT
  increased 57.15% and P99 E2E increased 74.62%.
- Main-run GPU samples were generally near 97-99% utilization. Across formal
  runs, P95 GPU utilization was 98-100%, median SM clock was 2775-2790 MHz and
  maximum observed temperature was 67 C.
- No request failed, and there was no observed preemption, OOM, runtime traceback
  or sustained clock reduction.
- c1-r1 contained two large per-request inter-token stalls: approximately
  318 ms and 414 ms, while the normal per-request ITL median was about 8 ms.
  The other four c1 runs had P99 E2E near 0.93 s. The outlier run was retained.

### Evidence-based inferences

- Throughput had not fully saturated at c32 because c16->c32 still delivered a
  measurable 25.82% gain. Calling c32 the saturation point would overstate the
  evidence.
- c16 is a candidate latency/throughput knee for this workload: c32 provides the
  highest throughput, but its incremental throughput is accompanied by a much
  larger tail-latency increase. The correct operating point still depends on an
  explicit SLO.
- High `nvidia-smi` utilization at c1 did not imply high serving efficiency.
  Batching raised output throughput by almost 10x while utilization remained
  high, so the one-second utilization metric is a duty-cycle signal rather than
  proof of SM occupancy, memory-bandwidth efficiency or scheduler efficiency.
- Stable clocks and temperature make thermal throttling an unlikely explanation
  for the main concurrency trend.

### Unverified hypotheses

- The c1-r1 ITL stalls may come from WSL/Windows scheduling, GPU-PV latency or a
  runtime synchronization event. One-second GPU telemetry showed no matching
  sustained utilization or clock drop, so it cannot identify the cause.
- A higher concurrency or a finite-rate open-loop experiment may reveal a clearer
  queueing/saturation boundary.
- Transient KV Cache and waiting-request peaks are unknown because this baseline
  saved `/metrics` only before and after each run; post-run gauges return to zero.

## 7. Limitations

- WSL2 GPU-PV rather than bare-metal Linux
- Windows desktop applications share the GPU
- One small model, one GPU and one fixed-length workload
- Closed-loop burst traffic, not an open-loop production arrival process
- Concurrency order was not randomized
- One-second `nvidia-smi` samples cannot resolve sub-second stalls
- `/metrics` snapshots do not preserve transient queue or KV Cache peaks
- The CPU-only load-generator container reports WSL pin-memory/Triton warnings;
  these do not describe the GPU server process

## 8. Next experiment

Run the Prefill/Decode 2x2 workload matrix at concurrency 8:

| Case | Input | Output | Purpose |
|---|---:|---:|---|
| A | 128 | 32 | light control |
| B | 1536 | 32 | Prefill-heavy |
| C | 128 | 256 | Decode-heavy |
| D | 1536 | 256 | mixed-heavy |

Before that run, add periodic `/metrics` polling so queue, running/waiting request
counts and KV Cache usage are captured as time series rather than only snapshots.
