# RCA: vLLM Fails to Start on WSL2 with `UVA is not available`

## Symptom

vLLM 0.26.0 exited during EngineCore initialization:

```text
RuntimeError: UVA is not available
```

## Confirmed evidence

- Docker Desktop and the NVIDIA runtime were available.
- `nvidia-smi` worked inside WSL2 and inside GPU containers.
- vLLM resolved `Qwen3ForCausalLM` and reached V2 Model Runner initialization.
- The failure occurred while constructing `UvaBuffer` for staged writes.
- The model had not failed due to GPU OOM.

## Candidate causes

1. WSL2 pinned memory disabled by vLLM platform defaults.
2. Missing NVIDIA container runtime.
3. GPU memory exhaustion.
4. Unsupported model architecture.

## Validation

The container was relaunched with:

```text
VLLM_WSL2_ENABLE_PIN_MEMORY=1
```

No other model, GPU-memory or endpoint parameter was changed.

## Root cause

vLLM's V2 Model Runner requires pinned memory for the UVA-backed staged-write
buffer. On WSL2 this behavior is disabled by default unless explicitly enabled.

## Fix

Add the following container environment variable:

```text
VLLM_WSL2_ENABLE_PIN_MEMORY=1
```

## Regression evidence

- Model weights loaded successfully.
- Torch compilation and CUDA Graph capture completed.
- `/v1/models` returned HTTP 200.
- `/metrics` returned vLLM metrics.
- A Chat Completion request generated tokens successfully.

## Limitations

This validates functional startup on the current WSL2 kernel and driver. It
does not prove identical pinned-memory performance or behavior on bare-metal
Linux.

