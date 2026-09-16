# Phase 3b v3: strict sustainable-capacity validation

## 1. Goal

Phase 3b v3 answers one question:

> For the fixed 512/128 workload and the frozen 200/4,000 ms TTFT/E2E SLO,
> which tested arrival rates can run for a strict 300-second arrival window
> without accumulating server debt?

The primary experiment matrix is **7/8/9/10 req/s**, five paired seeds per
rate. `10 req/s` is retained as the boundary control from v2.

v3 deliberately separates two concerns:

1. **Capacity gate**: decide whether a rate is sustainable.
2. **RCA evidence**: explain why a failed rate fails.

Do not mix the two layers in the primary result table.

## 2. What changes from v2

Phase 3b v1/v2 used a finite request count derived from:

```text
num_prompts = request_rate * 300
```

That makes 300 seconds a nominal horizon. When the finite arrivals stop, the
server can drain any accumulated backlog, so final 100% request completion does
not prove sustainable capacity.

v3 must use a strict wall-clock arrival boundary:

```text
t0                                      t0 + 300s
|----------------------------------------------|
        new arrivals are allowed here

                                                X no new dispatch
                                                |
                                                +--> already-dispatched
                                                     requests may drain
```

The load generator must stop **dispatching new requests** at `t0 + 300s`.
It must not approximate the window by precomputing a finite prompt count.

## 3. Experiment definition

The canonical v3 config is `configs/open_loop_steady_v3.env`.

Fixed workload:

- input length: 512 tokens
- output length: 128 tokens
- warmups: 8, completed before `t0`
- arrival window: 300 seconds
- steady analysis subwindow: `[t0 + 60s, t0 + 300s)`
- max client concurrency: 1024
- rates: 7/8/9/10 req/s
- repetitions: 5 paired seeds
- TTFT SLO: 200 ms
- E2E SLO: 4,000 ms

The first 60 seconds are excluded only from rate/queue-trend calculations to
avoid startup transient bias. They remain part of the formal arrival window and
request-level SLO evidence.

## 4. Fixed-window load generator contract

Implement a dedicated fixed-window load path. Reusing the legacy
`--num-prompts rate*duration` behavior is not acceptable for v3.

The load generator must:

1. Use a monotonic clock.
2. Generate the arrival process from the configured rate and seed.
3. Schedule each arrival against an absolute deadline derived from `t0`, not by
   repeatedly sleeping relative to the previous dispatch.
4. Refuse to dispatch a new request when its scheduled dispatch time is at or
   after `t0 + ARRIVAL_WINDOW_SECONDS`.
5. Allow requests dispatched before the deadline to complete after the deadline.
6. Record whether the client concurrency cap was ever reached.
7. Persist per-request timestamps sufficient to distinguish schedule, dispatch,
   first token and completion.

Required per-request fields:

```text
request_id
scheduled_at_s
dispatched_at_s
first_token_at_s
completed_at_s
status
```

Required run-level fields:

```text
formal_start_s
formal_end_s
configured_arrival_rate
actual_arrivals_during_window
client_cap_reached
schedule_lag_p99_ms
schedule_lag_max_ms
```

A run is invalid for capacity publication if the client cap is reached or the
arrival timeline cannot prove what load was actually offered.

## 5. Primary capacity gate

The capacity decision should stay small and auditable.

For each accepted run, compute only the following primary fields:

| Field | Meaning |
|---|---|
| configured rate | requested offered load |
| actual arrival rate | dispatches in the strict arrival window / window length |
| completed rate | completions in the steady analysis subwindow / subwindow length |
| waiting queue slope | linear trend of server waiting requests in the steady subwindow |
| end outstanding | dispatched-before-deadline requests not completed by `t0+300s` |
| P99 TTFT | request latency SLO signal |
| P99 E2E | request latency SLO signal |
| failures | request correctness gate |
| preemptions | server pressure gate |
| client cap reached | load-generator validity gate |

`end outstanding` is evidence, not a zero-required gate. A healthy steady system
normally has in-flight requests at an arbitrary cutoff. The important question
is whether backlog grows over time.

A rate is a sustainable-capacity candidate only when all accepted repetitions
show the same qualitative result:

```text
actual arrivals track configured load
completed rate tracks actual arrivals
waiting queue has no sustained positive trend
TTFT/E2E satisfy the frozen SLO
no request failure
no preemption
client concurrency cap is never reached
```

Do not publish a rate as sustainable from the median alone when repetitions are
bimodal or contain severe outliers.

## 6. RCA evidence is secondary

Continue collecting the existing telemetry because it is useful for diagnosis,
but do not place it in the primary capacity decision table.

Secondary RCA evidence includes:

- KV-cache usage
- capacity/deferred waiting reason
- GPU utilization, clocks, memory and temperature
- Docker CPU/RSS
- queue-time cumulative counters
- preemption details
- server logs

The intended flow is:

```text
capacity gate fails
        |
        v
identify the failing signal
(queue growth / SLO / failure / preemption)
        |
        v
use KV/GPU/scheduler telemetry for RCA
```

## 7. Canonical v3 output

The first table in the v3 report should be enough to answer the capacity
question:

| Rate | Actual arrival | Completed rate | Queue slope | End outstanding | P99 TTFT | P99 E2E | Failures | Preemptions | Client cap | Result |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 7 | | | | | | | | | | |
| 8 | | | | | | | | | | |
| 9 | | | | | | | | | | |
| 10 | | | | | | | | | | |

RCA tables and resource telemetry follow only after this table.

## 8. Implementation targets

Implement v3 in this order:

### A. Load generation

- Add strict fixed-wall-clock dispatch.
- Use absolute-time scheduling and a monotonic clock.
- Persist schedule lag and client-cap evidence.
- Keep post-window draining only for evidence collection.

### B. Window-aligned analysis

- Partition requests into dispatched/completed during the formal window and
  completed after the window.
- Compute actual arrival rate and steady-subwindow completed rate.
- Align vLLM waiting/running samples to the same timestamps.
- Compute waiting queue regression slope.
- Compute end outstanding directly from request timestamps.

### C. Capacity summary

- Produce one per-run capacity summary row.
- Aggregate across the five paired repetitions without hiding outliers.
- Fail closed when required timestamp or window evidence is missing.

### D. Tests

At minimum add tests for:

1. no dispatch occurs at or after the formal deadline;
2. a deliberately slow fake server creates positive queue/backlog evidence;
3. a stable fake server keeps queue bounded while end outstanding may remain
   non-zero at cutoff;
4. client-cap reach invalidates the capacity run;
5. missing timestamps fail analysis instead of silently falling back;
6. completion-after-window is separated from completion-during-window.

## 9. Definition of done

Phase 3b v3 is implementation-complete only when:

- `configs/open_loop_steady_v3.env` is executable through the new strict path;
- the legacy finite runner cannot silently execute the v3 namespace;
- the raw artifact proves formal start/end and per-request timing;
- capacity analysis uses the same strict time window across arrival, completion
  and queue data;
- the primary result table can be regenerated from immutable artifacts;
- all new unit tests and existing offline regression tests pass.

Until these conditions are met, v3 is a **code target**, not completed capacity
evidence.
