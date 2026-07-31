# vLLM Single-GPU Baseline

## 1. Purpose

Establish a reproducible WSL2 single-GPU baseline and identify the concurrency
range where throughput saturates and tail latency starts to deteriorate.

## 2. Environment

- Initial environment snapshot: `artifacts/env/environment-20260731T051937Z.txt`
- GPU: NVIDIA GeForce RTX 4060 8GB
- WSL Kernel: 6.18.33.2-microsoft-standard-WSL2
- vLLM: 0.26.0
- Model: Qwen/Qwen3-0.6B
- Model revision: `c1899de289a04d12100db370d81485cdf75e47ca`
- Image digest: `sha256:ffb2d59b1c059a5bd8d781320c9f5189de8293693b7d95da54befddaa54abf52`

## 3. Workload

- Dataset: random
- Input length: 512
- Output length: 128
- Concurrency: 1 / 2 / 4 / 8 / 16 / 32
- Requests per run: 128
- Repetitions: 5
- Request rate: inf
- Prefix Cache: disabled

## 4. Results

| Concurrency | Output tok/s | P50 TTFT | P95 TTFT | P99 TTFT | P99 TPOT |
|---:|---:|---:|---:|---:|---:|
| 1 | [TO FILL] | [TO FILL] | [TO FILL] | [TO FILL] | [TO FILL] |
| 2 | [TO FILL] | [TO FILL] | [TO FILL] | [TO FILL] | [TO FILL] |
| 4 | [TO FILL] | [TO FILL] | [TO FILL] | [TO FILL] | [TO FILL] |
| 8 | [TO FILL] | [TO FILL] | [TO FILL] | [TO FILL] | [TO FILL] |
| 16 | [TO FILL] | [TO FILL] | [TO FILL] | [TO FILL] | [TO FILL] |
| 32 | [TO FILL] | [TO FILL] | [TO FILL] | [TO FILL] | [TO FILL] |

## 5. Analysis

### Confirmed facts

- [TO FILL]

### Evidence-based inferences

- [TO FILL]

### Unverified hypotheses

- [TO FILL]

## 6. Limitations

- WSL2 GPU-PV rather than bare-metal Linux
- Windows desktop applications share the GPU
- One small model and one GPU
- Burst/limited-concurrency workload, not production open-loop traffic

## 7. Next experiment

Separate Prefill-heavy and Decode-heavy workloads.
