# LLM Serving RCA Project Guidance

This repository is both an engineering project and a learning project for LLM serving systems. Preserve both goals when explaining or changing code.

## Core research question

The main question is sustainable serving capacity under open-loop load.

Keep these concepts distinct:

- configured arrival rate
- scheduled arrival time
- actual dispatch rate
- completion rate
- concurrency
- outstanding requests
- waiting queue
- steady-state throughput
- formal arrival-window metrics
- post-window drain metrics

Do not treat them as interchangeable.

## Metric explanation protocol

When explaining any experiment metric, state:

1. What event is counted: scheduled, dispatched, first token, completed, failed, or server counter change.
2. What time window is used.
3. The exact numerator.
4. The exact denominator.
5. What system property the metric is intended to measure.
6. What misleading conclusion could result from using the wrong event or window.

Use a simple timeline when window boundaries matter.

Example:

```text
t0                       t0+60                         t0+300
|--------------------------|-----------------------------|
 startup / ramp-up          steady analysis
|--------------------------------------------------------|
                 formal arrival window
```

## Queueing reasoning

For sustainable-capacity analysis, reason in terms of:

```text
arrival rate      lambda
completion rate   mu
outstanding       O(t)
waiting queue     Q(t)
```

A useful relationship is:

```text
O(t2) - O(t1) ~= arrivals(t1,t2) - completions(t1,t2)
```

Therefore, do not judge capacity from throughput alone. Also inspect whether outstanding requests or the waiting queue are accumulating.

For Phase 3b v3, preserve the current semantic split:

- formal arrival window: `[t0, t0 + 300s)`
- steady analysis subwindow: `[t0 + 60s, t0 + 300s)`
- the first 60 seconds are excluded only from steady rate / queue-trend calculations
- request-level SLO evidence still covers the full formal arrival cohort, including requests that complete after the dispatch deadline

## Explanation style

When the user asks `why`, `explain`, `what does this mean`, `how does this work`, `为什么`, `什么意思`, or `如何理解`, switch to teaching mode.

In teaching mode:

- answer the conceptual question before discussing implementation;
- explain the causal chain step by step;
- use one concrete example from this repository when possible;
- define specialized LLM-serving terms on first use;
- prefer a small timeline, equation, or numeric example over abstract jargon;
- do not modify code unless the user explicitly asks for a code change;
- do not start with a large source-code dump.

For a source-code question, explain in this order:

1. role of the file/function;
2. important data flow and control flow;
3. metric or experiment semantics;
4. implementation details;
5. line-by-line explanation only when useful.

## RCA reasoning

Organize debugging and performance analysis as:

```text
phenomenon
-> evidence
-> candidate mechanism
-> validation method
-> expected result
-> conclusion / next step
```

Clearly distinguish:

- confirmed fact
- evidence-based inference
- unverified hypothesis

## Experiment changes

Before changing metric semantics, experiment windows, request cohorts, or capacity gates:

1. state the current definition;
2. state the proposed definition;
3. explain why the change is needed;
4. identify affected raw evidence;
5. identify affected analysis / aggregation / reports / tests;
6. preserve reproducibility of previous experiment versions.

Do not silently change experiment semantics.

## Implementation discipline

When making code changes:

- prefer immutable raw evidence and regenerable summaries;
- fail closed when required timestamps or evidence are missing;
- do not infer strict-window behavior from nominal `num_prompts` or total benchmark duration;
- keep capacity-gate evidence separate from secondary RCA telemetry;
- add or update tests when changing experiment semantics;
- avoid broad refactors unless they are required for the requested change.
