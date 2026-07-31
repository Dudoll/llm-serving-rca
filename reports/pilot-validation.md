# Baseline Pipeline Pilot Validation

## Purpose

Validate the end-to-end experiment pipeline before running the formal baseline.
This pilot is not used as a final performance claim.

## Workload

- Model: `Qwen/Qwen3-0.6B`
- Input length: 128 tokens
- Output length: 32 tokens
- Requests: 12
- Warmup requests: 2
- Maximum concurrency: 1 / 8 / 32
- Repetitions: 1
- Prefix Cache: disabled

## Results

| Max concurrency | Completed | Failed | Output tok/s | P99 TTFT ms | P99 TPOT ms |
|---:|---:|---:|---:|---:|---:|
| 1 | 12 | 0 | 152.34 | 19.81 | 6.45 |
| 8 | 12 | 0 | 737.05 | 63.71 | 7.35 |
| 32 | 12 | 0 | 1214.05 | 87.61 | 8.47 |

## Confirmed facts

- All three runs completed without failed requests.
- Raw JSON and per-request data were saved.
- Metrics snapshots, Docker stats and GPU telemetry were saved for each run.
- The summary script correctly read the vLLM 0.26.0 result schema.
- Because only 12 prompts were submitted, the concurrency-32 case did not
  exercise 32 simultaneous requests.

## Evidence-based inferences

- The measurement pipeline is ready for a larger formal experiment.
- Throughput increased with available concurrency in this pilot.
- TTFT and TPOT also increased with concurrency.

## Unverified hypotheses

- The formal workload will reveal a throughput saturation point.
- TTFT P99 will show a sharper knee once the number of prompts exceeds the
  maximum concurrency and queueing persists.

## Limitations

- One run per configuration
- Only 12 requests
- Short prompt and output lengths
- WSL2/GPU-PV environment
- The original pilot relied on the server's explicit `generation_config=vllm`;
  subsequent runs also explicitly set client-side `temperature=0`.

