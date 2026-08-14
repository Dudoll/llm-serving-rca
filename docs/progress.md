# LLM Serving Performance & RCA Lab: Project Status

> Last updated: 2026-08-12
>
> This file is the phase-status and evidence-navigation entry point. System
> boundaries are documented in [`design.md`](design.md); research questions and
> acceptance criteria are documented in [`experiment-plan.md`](experiment-plan.md).

## Current conclusion

| Item | Current status |
|---|---|
| Current phase | Phase 3b v1 completed as long-window finite validation; final steady-state gate remains open |
| Most recent completion | Phase 3b v1: 12/13/14 req/s, 15 accepted runs, 58,500 successful requests |
| Overall status | Phases 0/1/2/3a are complete; Phase 3b evidence is complete, but no tested rate passes the declared SLO and steady-state proof is incomplete |
| Declared SLO | TTFT ≤ 200 ms and E2E ≤ 4,000 ms, frozen before Phase 3b execution |
| Current boundary | Under this workload and SLO, the sustainable operating point is below the tested 12 req/s lower point; 10/11/12 req/s is the next validation band |
| Git checkpoint | Working tree contains the Phase 3b evidence-system implementation and generated evidence; no new clean checkpoint was created by this report update |
| External blockers | None; the remaining work is experiment and analysis implementation, not missing external state |

The current formal evidence total is 100 accepted runs and 72,580 successful
requests: 30 baseline runs, 20 Prefill/Decode runs, 35 Phase 3a runs and 15
accepted Phase 3b v1 runs. Historical baseline and Prefill/Decode evidence may
retain `legacy-unmanifested` status; the Phase 3b v1 consumers use the accepted
attempt ledger and complete manifests.

## Status legend

- ✅ Complete: experiment and required evidence are available.
- ⚠️ Complete with limitation: evidence is available, but a required conclusion
  gate remains open.
- ▶️ Ready: implementation and prerequisites exist, but execution has not
  started.
- ⏳ Planned: objective and acceptance criteria are defined.
- 🚫 Blocked: progress requires an external state change or user decision.

## Phase summary

| Phase | Status | Completed work | Evidence | Next acceptance condition |
|---|---|---|---|---|
| 0. Environment and functional path | ✅ | WSL2 GPU, Docker, vLLM, model revision, UVA RCA, API, metrics and streaming smoke fixed | [`rca-wsl-uva.md`](../reports/rca-wsl-uva.md) | Recheck only after an environment or version change |
| 1. Closed-loop baseline | ✅ | 6 concurrency levels × 5 repetitions; 30 runs, 3,840 successful requests, 0 failures | [`baseline.md`](../reports/baseline.md), [`baseline-aggregate.csv`](../results/summary/baseline-aggregate.csv) | Treat c16 as a candidate knee, not a proven saturation point |
| 2. Prefill/Decode decomposition | ✅ | A/B/C/D × 5; 20 runs, 1,280 successful requests, 0 failures; metrics/GPU/log evidence complete | [`prefill-decoder.md`](../reports/prefill-decoder.md), [`prefill-decoder-aggregate.csv`](../results/summary/prefill-decoder-aggregate.csv) | Use B/C as later prefill/decode stress anchors |
| 3a. Finite open-loop boundary scan | ✅ | 4–16 req/s, 7 rates × 5; 35 runs, 8,960 successful requests, 0 failures; 12–14 transition band identified | [`open-loop.md`](../reports/open-loop.md), [`open-loop-aggregate.csv`](../results/summary/open-loop-aggregate.csv) | Use the band as a candidate boundary only |
| 3b. Long-window validation | ⚠️ | 12/13/14 req/s, `MAX_CONCURRENCY=1024`, five paired seeds, frozen 200/4,000 ms SLO, complete manifests, accepted-attempt ledger and offline summaries | [`open-loop-steady.md`](../reports/open-loop-steady.md), [`open-loop-steady-aggregate.csv`](../results/summary/open-loop-steady-aggregate.csv) | Add fixed-wall-clock arrivals, schedule-lag/client-cap reach, arrival-window queue slope/end backlog/counter delta, P99 CI and Little's Law; then validate 10/11/12 req/s |
| 4. KV-cache / scheduler pressure | ⏳ | Variables, metrics and OOM/preemption rules defined | [`experiment-plan.md`](experiment-plan.md), [`metric-definitions.md`](metric-definitions.md) | Isolate one cache or scheduler variable at the Phase 3a/3b boundary |
| 5. Mixed short/long requests RCA | ⏳ | 80/20 control, tagging and RCA path defined | [`experiment-plan.md`](experiment-plan.md) | Measure short-request benefit, long-request cost and total-throughput regression |
| 6. Host CPU / container interference | ⏳ | CPU quota, contention, memory pressure and perf paths defined | [`experiment-plan.md`](experiment-plan.md) | Identify at least one host-side limitation |
| 7. GPU timeline / kernel profiling | ⏳ | Nsight Systems/Compute entry conditions defined | [`experiment-plan.md`](experiment-plan.md) | Profile only a stable and reproducible GPU symptom |
| 8. Optimization, revalidation and regression | ⏳ | Correctness, performance and regression gates defined | [`design.md`](design.md), [`experiment-plan.md`](experiment-plan.md) | Validate at least two optimizations and report one explicit trade-off |
| 9. Portability and final delivery | ⏳ | Bare-metal comparison, artifact tiers and portfolio checklist defined | [`design.md`](design.md) | Publish an evidence index, RCA, interview material and validated resume bullets |

## Phase 3b result

The complete Phase 3b v1 matrix contains 15 accepted bundles and 58,500
successful requests. Realized send rates closely matched configured rates:

| Offered rate | Median P99 TTFT | Median P99 E2E | Median SLO-good fraction | Median max waiting | Median preemptions |
|---:|---:|---:|---:|---:|---:|
| 12 req/s | 385.7 ms | 4,948.3 ms | 92.7% | 9 | 12 |
| 13 req/s | 3,860.3 ms | 8,251.4 ms | 42.8% | 58 | 313 |
| 14 req/s | 13,990.9 ms | 18,502.4 ms | 1.7% | 195 | 757 |

Rate 12 is the least degraded tested point, but it still misses the declared
SLO at the pooled level (89.4% of 18,000 requests), and preemptions occurred in
all five accepted repetitions. Rates 13 and 14 are clearly overloaded under
the same SLO. Therefore no tested Phase 3b v1 rate is a passing sustainable
operating point.

This conclusion is bounded by the current measurement design. The runner uses
`rate * 300` prompts, not a strict wall-clock arrival stop; the current
telemetry summaries use benchmark-envelope before/after counter deltas; and
arrival-window queue slope, end backlog, completion partition, P99 confidence
intervals and same-window Little's Law are not yet implemented. The report must
therefore use the term “long-window finite validation,” not “long-term
sustainable capacity.”

## Next work

1. Implement the remaining steady-window evidence fields: fixed-wall-clock
   arrival boundaries, schedule lag, client-cap reach, queue slope, end backlog,
   arrival-window counter deltas, completion partition, P99 bootstrap intervals
   and Little's Law.
2. Create a new immutable namespace, for example `phase3b-v2`, and run the
   10/11/12 req/s validation with the same workload, server settings, SLOs,
   five-seed paired design and evidence admission rules.
3. Use the v2 results to identify a passing SLO operating point before starting
   the single-variable scheduler/KV-cache RCA.

## Evidence entry points

### Reports

- [`open-loop-steady.md`](../reports/open-loop-steady.md): Phase 3b v1 report
- [`open-loop.md`](../reports/open-loop.md): Phase 3a finite boundary scan
- [`baseline.md`](../reports/baseline.md): formal closed-loop baseline
- [`prefill-decoder.md`](../reports/prefill-decoder.md): prefill/decode decomposition
- [`rca-wsl-uva.md`](../reports/rca-wsl-uva.md): WSL2 UVA startup RCA

### Generated evidence

- `results/manifests/`: RunBundle manifests and validation state
- `results/plans/`: deterministic plans and the Phase 3b accepted-attempt ledger
- `results/raw/`: benchmark JSON and per-request arrays
- `results/telemetry/`: GPU, vLLM metrics and Docker stats
- `results/summary/`: canonical per-run and aggregate tables
- `charts/`: plots generated from canonical tables

## Update history

| Date | Progress | Checkpoint |
|---|---|---|
| 2026-08-12 | Completed Phase 3b v1: 15 accepted runs, 58,500 successful requests; published report; no tested rate passed the declared SLO | Working tree |
| 2026-08-10 | Completed Phase 3a 35-run finite scan and identified the 12–14 req/s transition band | Working tree |
| 2026-08-09 | Completed Phase 2 Prefill/Decode matrix, report and matrix plot | `6ad9a54` descendant working tree |
| 2026-08-03 | Created the unified project-status entry point; Phases 0/1 complete | `3e01f9a` |
| 2026-07-31 | Completed the formal baseline: 30 runs, 3,840 requests, 0 failures | `3e01f9a` |
