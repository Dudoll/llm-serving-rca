# Metric Definitions and Data Flow

## Request path

```text
Client -> HTTP/API -> Tokenizer -> Scheduler -> Prefill
       -> KV Cache -> Decode loop -> Streaming response
```

## Latency

```text
TTFT = first output token arrival - request send time

TPOT = (last output token arrival - first output token arrival)
       / (number of generated tokens - 1)

E2E = request completion - request send time
```

TTFT includes queueing, CPU-side request handling, tokenization, scheduling,
Prefill and the first Decode step. TPOT mainly describes steady-state Decode.

## Tensor shapes

```text
input_ids:     [B, S]
hidden_states: [B, S, H]
Q:             [B, num_heads, S, head_dim]
K/V cache:     [L, B, num_kv_heads, S, head_dim]
```

Approximate KV Cache capacity:

```text
KV bytes ~= 2 * L * B * S * num_kv_heads * head_dim * bytes_per_element
```

The factor 2 represents K and V.

## Throughput

- requests/s: completed requests divided by benchmark duration
- output tokens/s: generated tokens divided by duration
- total tokens/s: prompt plus generated tokens divided by duration

These metrics are not interchangeable. A configuration may increase total
throughput while making TTFT P99 unacceptable.

## Evidence rules

- Raw per-request JSON is the primary latency evidence.
- Prometheus histograms are cumulative and bucketed.
- GPU utilization alone does not identify the bottleneck.
- Cross-run conclusions use median and dispersion, not a single run.

