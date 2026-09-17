# Phase 3b: Long-Window Open-Loop Validation

## 1. Purpose and scope

Phase 3b validates the lower-rate transition region identified by the Phase 3a
finite open-loop scan. The experiment tests how offered load affects request
throughput, tail latency, SLO goodput, queue pressure, KV-cache usage and
preemption for the fixed Phase-1 workload.

This report covers the completed `phase3b-v2` matrix at 10, 11 and 12
requests/s. It is long-window finite validation, not a formal long-term
sustainable-capacity certification. The current runner derives the number of
formal prompts from `rate * 300`; it does not stop arrivals using a strict
wall-clock boundary. The evidence also lacks arrival-window-aligned queue
slope, end backlog, counter deltas, P99 confidence intervals and a same-window
Little's Law check.

All conclusions are specific to the WSL2/GPU-PV environment, model, server
configuration and workload recorded in this checkout. They must not be
generalized to another GPU, driver, model, server configuration or request
mix.

## 2. Experimental configuration

- Run dates: 2026-08-12–13 UTC
- GPU: NVIDIA GeForce RTX 4060 8GB
- vLLM: 0.26.0
- Container image: `vllm/vllm-openai@sha256:ffb2d59b1c059a5bd8d781320c9f5189de8293693b7d95da54befddaa54abf52`
- Model: `Qwen/Qwen3-0.6B`, revision
  `c1899de289a04d12100db370d81485cdf75e47ca`
- Server settings: max model length 2048, GPU memory utilization 0.65,
  prefix caching disabled, chunked prefill enabled
- Input/output length: 512/128 tokens
- Warmup requests: 8 per run
- Nominal arrival horizon: 300 seconds
- Maximum client concurrency: 1024
- Offered rates: 10, 11 and 12 requests/s
- Repetitions: 5 per rate
- Load-generator seeds: 31001–31005, paired across rates
- Pre-registered SLOs: TTFT ≤ 200 ms and E2E ≤ 4,000 ms
- Namespace: `phase3b-v2`
- Plan SHA256: `2d3969f4059b976d7b171960e0eca2b88369a76891310254cb625326736406a3`

The plan contains 15 logical cells. The accepted-attempt ledger maps each
logical cell to exactly one complete run. One immutable attempt failed during
the first execution of the rate-11, seed-31005 cell; retry `a2` was accepted.
The failed attempt remains in the evidence store and is excluded from all
analysis tables.

## 3. Evidence integrity

- 15/15 logical cells were accepted.
- 49,500/49,500 formal requests in accepted runs completed successfully.
- 0 benchmark request failures were admitted into the analysis.
- 15/15 accepted telemetry summaries were complete.
- All accepted telemetry streams reported zero polling errors and no cumulative
  counter reset.
- The excluded failed attempt recorded 3,299/3,300 completed requests; its log
  contains a connection-reset failure and its manifest remains immutable.
- Per-run summaries, repetition aggregates, telemetry summaries, SLO summaries
  and the chart were regenerated through the manifest-bound offline pipeline.

The primary evidence paths are:

- [`open-loop-steady-summary.csv`](../results/summary/open-loop-steady-summary.csv)
- [`open-loop-steady-aggregate.csv`](../results/summary/open-loop-steady-aggregate.csv)
- [`open-loop-steady-slo-summary.csv`](../results/summary/open-loop-steady-slo-summary.csv)
- [`open-loop-steady-telemetry-summary.csv`](../results/summary/open-loop-steady-telemetry-summary.csv)
- [`phase3b-v2-open-loop-steady-plan.tsv`](../results/plans/phase3b-v2-open-loop-steady-plan.tsv)
- [`phase3b-v2-accepted-attempts.tsv`](../results/plans/phase3b-v2-accepted-attempts.tsv)
- [`open-loop-steady.png`](../charts/open-loop-steady.png)

The excluded first attempt is [`l11-s31005-b5-a1 manifest`](../results/manifests/open_loop_steady-phase3b-v2-in512-out128-l11-s31005-b5-a1.json).

## 4. Aggregate results

Values are medians across five accepted repetitions. Brackets show the
repetition minimum and maximum. Benchmark-wide completed throughput includes
the completion/drain period after the final arrival; it is not a steady-window
completed rate. `SLO good fraction` is the request-level fraction satisfying
both pre-registered thresholds. The pooled value uses all accepted requests at
that offered rate.

| Offered rate | Realized send rate | Benchmark-wide completed req/s | P99 TTFT (ms) | P99 E2E (ms) | SLO good fraction | Pooled SLO fraction |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 10.017 [9.996, 10.042] | 9.975 [8.705, 10.008] | 146.5 [127.5, 41,117.0] | 3,475.0 [2,811.5, 47,598.2] | 99.4% [0.7%, 100.0%] | 69.2% (10,385/15,000) |
| 11 | 11.025 [10.780, 11.069] | 10.948 [8.545, 11.008] | 470.0 [149.1, 93,207.3] | 4,936.4 [3,519.7, 98,962.7] | 94.2% [0.09%, 99.85%] | 68.9% (11,368/16,500) |
| 12 | 12.042 [9.479, 12.070] | 10.093 [6.917, 11.963] | 64,566.4 [408.4, 149,769.0] | 69,185.3 [4,954.8, 157,493.8] | 2.9% [0.0%, 89.75%] | 34.7% (6,239/18,000) |

The main result is repetition instability below the obvious overload point.
At 10 requests/s, three repetitions were near the SLO while two developed
severe queueing. At 11 requests/s, the same pattern appears: three repetitions
were near the SLO while two had very large tails. At 12 requests/s, three
repetitions were strongly overloaded and the remaining two still missed the
SLO consistently.

The realized arrival rate tracked the configured rate in most repetitions, but
the 12 requests/s range includes one run at only 9.479 requests/s and the 11
requests/s range includes one at 10.780 requests/s. This is evidence that
client scheduling/backpressure must be measured explicitly before treating the
offered rate as a strict arrival-window rate.

## 5. Queue, KV-cache and preemption evidence

The following values are medians across five accepted telemetry summaries, with
the repetition range in brackets. These are benchmark-envelope summaries.
Their counter deltas include warmup, formal arrivals and drain; they are not
arrival-window counter deltas.

| Offered rate | Max waiting requests | KV-cache P95 (%) | Preemptions | Queue-time counter delta (s) | Approx. drain (s) |
|---:|---:|---:|---:|---:|---:|
| 10 | 5 [0, 400] | 51.4 [48.7, 99.9] | 0 [0, 531] | 1.4 [0.0, 50,982.4] | 1.40 [1.12, 45.17] |
| 11 | 8 [0, 967] | 75.1 [63.0, 99.9] | 16 [0, 581] | 25.4 [0.6, 192,776.3] | 1.53 [1.18, 80.14] |
| 12 | 802 [7, 967] | 99.9 [91.2, 99.9] | 644 [17, 674] | 136,666.8 [23.0, 326,451.3] | 58.49 [2.06, 140.74] |

The clean repetitions at 10 and 11 requests/s show low waiting, low KV-cache
pressure and no preemption. The outlier repetitions at those same rates show
near-full KV-cache usage, large waiting populations, preemption and rapidly
growing queue-time counters. At 12 requests/s, the median and repetition range
show a clear capacity-pressure regime: KV-cache usage is effectively full,
waiting reaches hundreds of requests, preemptions are frequent and drain time
is much longer.

These metrics are consistent with scheduler/KV-cache pressure, but they do not
identify the causal scheduler or cache mechanism. The strong difference between
repetitions also means that a median alone is insufficient for selecting a
sustainable operating point.

## 6. Interpretation

### Confirmed facts

- The v2 evidence pipeline accepted all 15 logical cells and selected one
  complete immutable attempt for each cell.
- Accepted runs completed 49,500/49,500 formal requests with no admitted
  benchmark failure.
- Accepted telemetry had no polling error and no cumulative-counter reset.
- 12 requests/s is overloaded for this workload under the declared SLO.
- 10 and 11 requests/s are not consistently SLO-compliant across repetitions;
  each has severe outlier repetitions.
- No tested rate passes the final sustainable-capacity gate.

### Evidence-based inference

For this workload and the 200/4,000 ms SLO, the useful operating region is
below the tested 10–12 requests/s band, or else the system has a substantial
run-to-run state or scheduling instability that must first be explained. The
data support this as an experiment-design inference, not as a measured
sustainable rate.

The transition signature remains consistent with scheduler/KV-cache pressure:
the bad repetitions show near-full KV-cache usage together with waiting,
preemption, very large queue-time accumulation and tail-latency explosion.
The evidence does not distinguish between cache capacity, scheduler behavior,
server state/carryover, client timing effects or another interaction.

### Not yet proven

Phase 3b v2 still does not prove formal steady-state capacity because:

1. arrivals are generated from `rate * 300` prompts rather than a strict
   wall-clock stop condition;
2. schedule lag and actual client-cap reach are not explicitly recorded;
3. queue slope, end backlog and completion partition are not aligned to a
   declared arrival window;
4. counter deltas cover the full benchmark envelope rather than the arrival
   window; and
5. cross-repetition P99 confidence intervals and a same-window Little's Law
   check are not available.

Consequently, this report should be cited as long-window finite validation and
boundary evidence, not as a production capacity certification.

## 7. Decision and next step

Phase 3b v2 is complete as an evidence-producing experiment, but the final
steady-state capacity gate remains open. Do not publish 10, 11 or 12
requests/s as sustainable SLO capacity.

Before starting the single-variable scheduler/KV-cache RCA, do the following:

1. Inspect the accepted outlier repetitions and their actual execution order,
   server logs and metrics timelines. The 10/11 requests/s bimodality must be
   explained or bounded before selecting a capacity point.
2. Implement strict fixed-wall-clock arrivals, schedule-lag/client-cap reach,
   arrival-window queue slope/end backlog and counter deltas, completion
   partitioning, bootstrap P99 confidence intervals and same-window Little's
   Law analysis.
3. Run a new immutable lower-rate validation, for example `phase3b-v3` at
   8/9/10 requests/s, with the same workload, server settings, SLOs, five
   paired seeds and evidence-admission rules. Retain 10 requests/s as the
   boundary control.
4. Publish a sustainable operating point only when actual arrivals are tracked,
   steady completions follow arrivals, the queue does not grow, tails are
   stable and within SLO, there are no failures or preemptions, and Little's
   Law is approximately consistent in the same measurement window.
