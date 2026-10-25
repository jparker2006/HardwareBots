# vLLM on Thor: Gemma 4 31B NVFP4 startup guide

How to get `ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor` serving the local `~/Gemma-4-31B-IT-NVFP4` folder on our Thor (JetPack 7.2.1 / Jetson Linux R39.2.1). The image was built for JetPack 7 / R38.

**Status: none of this has been run on our Thor yet.** Every claim below has a source. "Inferred" means we reasoned it from a source and nobody has observed it.

## TL;DR: what `scripts/thor-up.sh` now runs for the 31B

```bash
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches            # before each model; JM_DROP_CACHES=0 skips it
docker run -d --name jm-gemma-31b --runtime=nvidia --network host --ipc host \
  -v ~/.cache/huggingface:/root/.cache/huggingface $JM_DOCKER_ARGS \
  -v ~/Gemma-4-31B-IT-NVFP4:/models/Gemma-4-31B-IT-NVFP4:ro \
  ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor \
  vllm serve /models/Gemma-4-31B-IT-NVFP4 --served-model-name gemma-4-31b --host 127.0.0.1 --port 8001 \
    --gpu-memory-utilization 0.50 --max-model-len 8192 \
    --enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4 \
    --kv-cache-dtype bfloat16
```

Check what it will run without starting anything: `scripts/thor-up.sh --dry-run --only-31b`.

## Flags, one by one

| Flag | Our value | Why / source |
|---|---|---|
| `--enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4` | on | Exact flags in NVIDIA's Thor command ([Jetson AI Lab tutorial][jal]). |
| `--quantization` | **not set** | vLLM reads the method from the folder's `config.json` (`quant_method: modelopt`, `quant_algo: NVFP4`) and picks `modelopt_fp4` itself. NVIDIA's Thor command passes no `--quantization` ([tutorial][jal]). The model card uses `--quantization modelopt` ([card][card]). If you pass anything else, vLLM stops because the two don't match. |
| `--max-model-len` | 8192 (`JM_MAX_LEN`) | The default is the model's full 262,144 tokens (`config.json`), which reserves a huge KV cache. vLLM says to set it to your real workload ([vLLM Gemma 4 recipe][recipe]). Our biggest request is 6 images × 280 tokens (the default vision budget, [recipe][recipe]) plus text, so 8192 is plenty. |
| `--gpu-memory-utilization` | 0.50 (`JM_31B_MEM`); E4B gets 0.25 | This is a share of **all** of Thor's RAM, because CPU and GPU share it: 0.8 = 98.26 GiB of 122.82 GiB ([forum log][f31]). vLLM refuses to start unless that much is free at launch ([forum log][f31]). 0.50 + 0.25 leaves about 30 GiB for Ubuntu, `jm` and GStreamer. |
| `--kv-cache-dtype` | **bfloat16** (`JM_31B_KV`) | The checkpoint asks for an FP8 KV cache (`kv_cache_quant_algo: FP8`) but ships no k/v scales (0 of 1,728 tensors). On Thor this exact image and model crashed at `kv_cache.py:80 assert layer.k_scale > 0.0` ([forum][f31]). In vLLM v0.19.0 (the image's version), any value other than `auto` overrides the checkpoint, and bfloat16 is not a quantized KV cache, so that check is skipped ([`resolve_kv_cache_dtype_string`][tu], [`kv_cache.py`][kvc]). Cost: about 2× KV memory, which is small at 8192 tokens. `JM_31B_KV=auto` restores the checkpoint's FP8. |
| `--enforce-eager` | off | Skips torch.compile and CUDA-graph capture: startup is faster, decoding slower. Only use it as a fallback if startup dies in compile, Triton or ptxas (see the error table): `JM_31B_ARGS=--enforce-eager`. |
| `--limit-mm-per-prompt` | unset | The recipe uses `.audio 0` or `{"image": N}` to cut multimodal profiling memory ([recipe][recipe]). The 31B config has no audio tower anyway. Optional: `JM_31B_ARGS="--limit-mm-per-prompt.image 6 --limit-mm-per-prompt.video 0"` (dotted form, because `thor-up.sh` splits args on spaces). |
| `--pull always` | **never** | It's in the tutorial command, but Thor downloads at about 1 MB/s. The image comes over from the Mac. |

## R38 image on an R39.2.1 host

- **No official statement found.** The tutorial lists Thor = JetPack 7 (L4T r38.x) ([tutorial][jal]). NVIDIA staff tested this image with this model on JetPack 7.1 (R38, driver 580) and it reached "Application startup complete" ([forum][f31]). JetPack 7.2 = Jetson Linux 39.2 with CUDA 13.2.1 ([JetPack 7.2 notes][jp72]).
- **Inferred: it should work.** The container brings its own CUDA 13.0 user-space. `--runtime=nvidia` mounts the host's newer driver, and a newer driver runs code built for an older CUDA ([CUDA compatibility][cudacompat]). NVIDIA staff on JP 7.2: "We expect the upstream vLLM package to work directly on the JetPack 7.2 environment" ([forum][f72]).
- **Known R39 problems (people using vLLM 0.19–0.23 on JP 7.2):**
  - Triton doesn't recognize `sm_110` and needs `TRITON_PTXAS_PATH` ([forum][f72]).
  - The vision encoder fails with `cudaErrorUnsupportedPtxVersion` ([vLLM #38411][i38411]).
  - The flash-attention extension is missing on sm_110 ([vLLM #63267][i63267]).

  Fixes are in the error table below.
- **Use `--runtime=nvidia`, not `--gpus all`.** `--gpus all` is not supported on Thor ([jetson-containers #1661][jc1661]). `thor-up.sh` already uses `--runtime=nvidia`.

## Memory

| Item | Size | Source |
|---|---|---|
| Thor RAM visible to CUDA | 122.82 GiB | [forum log][f31] |
| 31B NVFP4 weight files | 32.6 GB (30.4 GiB): 4 shards of 9,954,637,578 + 9,977,124,474 + 9,999,145,338 + 2,702,570,418 bytes | [HF file list][tree] |
| Why it isn't ~16 GB | `lm_head`, the vision embedder and **every layer's self-attention** are left in BF16 (63 excluded modules in `hf_quant_config.json`). A 5090 user saw ~29.4 GiB allocated and called it expected ([vLLM #40291][i40291]) | checkpoint config |
| 31B budget at 0.50 | 61.4 GiB = ~31 GiB weights + activations + KV + graphs | inferred |
| E4B budget at 0.25 | 30.7 GiB | inferred |
| KV cache, 31B at 8192 tokens in bf16 | under 2 GiB: 10 global layers × 2 × 4 heads × 512 dims × 2 bytes ≈ 80 KB per token, plus 50 sliding layers with a 1024-token window | `config.json` (inferred) |

**The page cache is the trap.** Reading 32 GB of weights fills Linux's page cache. On Thor that is the same RAM the GPU uses, so the next vLLM start sees too little "free" memory and stops (the `Free memory on device` error). NVIDIA's fix is to drop caches and make sure no other server is running ([tutorial][jal], [forum][f31]). `thor-up.sh` now drops caches before each model starts (it needs sudo; if sudo fails it prints a WARN and keeps going).

## Startup time

- **No official number.** The tutorial only says a large model may "hang during load" when memory is short ([tutorial][jal]).
- **Expect several minutes on the first start (inferred).** vLLM reads 32.6 GB, then runs torch.compile and CUDA-graph capture.
- `JM_UP_TIMEOUT` stays at 1200 s per model. Watch progress with `docker logs -f jm-gemma-31b`.
- **Faster restarts (optional, unverified):** keep vLLM's compile cache between containers with `JM_DOCKER_ARGS="-v $HOME/.cache/vllm:/root/.cache/vllm"`. Re-running `thor-up.sh` already reuses containers that are still running.

## Error → fix

Search the output of `docker logs jm-gemma-31b` for these.

| Log line (or start of it) | Cause | Fix |
|---|---|---|
| `ValueError: Free memory on device cuda:0 (80.29/122.82 GiB) on startup is less than desired GPU memory utilization (0.8, 98.26 GiB)` | Page cache or another server is holding RAM ([forum][f31]) | Run `scripts/thor-down.sh`, then `sudo sysctl -w vm.drop_caches=3` ([tutorial][jal]), then retry. Still failing? Lower `JM_31B_MEM` / `JM_E4B_MEM`. |
| `AssertionError` at `kv_cache.py:80` `assert layer.k_scale > 0.0`, after `Using KV cache scaling factor 1.0 for fp8_e4m3` | FP8 KV cache path with missing or zero scales ([forum][f31]) | Already avoided by the default `JM_31B_KV=bfloat16`. If it still happens, check the four shard sizes above with `ls -l ~/Gemma-4-31B-IT-NVFP4`; NVIDIA's advice was to delete and re-download ([forum][f31]). |
| `torch.OutOfMemoryError: CUDA out of memory` while loading weights (`create_weights`) | The 31B needs about 30 GiB just for weights ([#40291][i40291]) | Start it alone with `--only-31b`, or raise `JM_31B_MEM=0.60` and lower `JM_E4B_MEM`. |
| `... KV cache is needed, which is larger than the available KV cache memory ... Try increasing gpu_memory_utilization or decreasing max_model_len` | The budget left after weights is too small for `--max-model-len` | Lower `JM_MAX_LEN` (e.g. 4096) or raise `JM_31B_MEM`. |
| `Quantization method specified in the model config (modelopt_fp4) does not match the quantization method specified in the quantization argument` | Wrong `--quantization` in `JM_31B_ARGS` | Remove it. The method is auto-detected. |
| Triton / `ptxas` errors that mention `sm_110` or `sm_110a` (during compile) | Triton doesn't know Thor's arch on JP 7.2 ([forum][f72]) | `JM_DOCKER_ARGS="-e TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas -e TORCH_CUDA_ARCH_LIST=11.0a"` (NVIDIA staff's env, [forum][f72]). Fallback: `JM_31B_ARGS=--enforce-eager`. |
| `CUDA error: the provided PTX was compiled with an unsupported toolchain` (`cudaErrorUnsupportedPtxVersion`) in `mm_encoder_attention.py` | The vision encoder's flash-attention kernel on Thor + JP 7.2 ([#38411][i38411]) | `JM_31B_ARGS="--mm-encoder-attn-backend TORCH_SDPA"` (a maintainer's suggestion; untested). Last resort: `--language-model-only`, which turns off the camera. |
| `ImportError: vllm.vllm_flash_attn requires the CUDA flash attention extensions` | The build has no flash-attention for sm_110 ([#63267][i63267]) | `JM_31B_ARGS="--attention-backend TRITON_ATTN"`. The issue says this alone may not be enough. |
| `RuntimeError: Failed to infer device type`, after `libcuda.so.1: cannot open shared object file` | The container can't see the GPU driver ([vLLM #26974][i26974], a Thor report) | Make sure `--runtime=nvidia` is used and the toolkit is set up (next row). Use the `*-jetson-thor` image: check `JM_VLLM_IMAGE`. |
| `unknown or invalid runtime name: nvidia` | NVIDIA Container Toolkit isn't set up for Docker | `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker` ([toolkit docs][ctk]). |
| `could not select device driver "" with capabilities: [[gpu]]` | `--gpus all` was used | Use `--runtime=nvidia`, which `thor-up.sh` does ([jetson-containers #1661][jc1661]). |
| The load hangs with no progress in the log | Memory pressure ([tutorial][jal]) | Free memory, drop caches, retry. Keep models on NVMe ([tutorial][jal]). |
| `NotImplementedError` in `quant_method.tie_weights` | Only in newer vLLM (v0.24.0) ([#48238][i48238]) | Don't upgrade the image. Stay on `gemma4-jetson-thor`. |
| Thinking text inside `content` | A Gemma 4 parser quirk ([tutorial][jal]) | Already handled: `journeyman/gemma.py` turns thinking off and strips it. |
| Tool-call JSON invalid when streaming | Gemma 4 streaming parser bug ([#38946][i38946]) | We don't stream (`gemma.py` sends non-streaming requests). |

## The one live check

On Thor:

```bash
scripts/thor-up.sh --only-31b --no-serve
curl -s localhost:8001/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"gemma-4-31b","messages":[{"role":"user","content":"Say OK"}],"max_tokens":8}'
```

**Pass:** it answers with "OK". Record the startup time printed by `thor-up.sh`, then `./jm doctor` for the image probe.

## Sources

[jal]: https://www.jetson-ai-lab.com/tutorials/gemma4-on-jetson/
[card]: https://huggingface.co/nvidia/Gemma-4-31B-IT-NVFP4
[tree]: https://huggingface.co/api/models/nvidia/Gemma-4-31B-IT-NVFP4/tree/main
[recipe]: https://github.com/vllm-project/recipes/blob/main/Google/Gemma4.md
[f31]: https://forums.developer.nvidia.com/t/gemma4-31b-engine-core-failing/369142
[f72]: https://forums.developer.nvidia.com/t/running-vllm-as-a-service-on-thor-jp7-2/373029
[jp72]: https://developer.nvidia.com/embedded/jetpack/downloads/archive-7.2
[cudacompat]: https://docs.nvidia.com/deploy/cuda-compatibility/
[ctk]: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
[tu]: https://github.com/vllm-project/vllm/blob/v0.19.0/vllm/utils/torch_utils.py
[kvc]: https://github.com/vllm-project/vllm/blob/v0.19.0/vllm/model_executor/layers/quantization/kv_cache.py
[i40291]: https://github.com/vllm-project/vllm/issues/40291
[i38411]: https://github.com/vllm-project/vllm/issues/38411
[i63267]: https://github.com/vllm-project/vllm/issues/63267
[i26974]: https://github.com/vllm-project/vllm/issues/26974
[i48238]: https://github.com/vllm-project/vllm/issues/48238
[i38946]: https://github.com/vllm-project/vllm/issues/38946
[jc1661]: https://github.com/dusty-nv/jetson-containers/issues/1661

- Jetson AI Lab, *Gemma 4 on Jetson*: [tutorial][jal]
- NVIDIA model card `nvidia/Gemma-4-31B-IT-NVFP4` and its file list: [card][card], [files][tree]
- vLLM Gemma 4 recipe: [recipe][recipe]
- NVIDIA forums: [Gemma4-31B engine core failing (Thor)][f31], [Running vLLM on Thor JP 7.2][f72]
- NVIDIA: [JetPack 7.2][jp72], [CUDA compatibility][cudacompat], [Container Toolkit][ctk]
- vLLM v0.19.0 source: [torch_utils.py][tu], [kv_cache.py][kvc]
- vLLM issues: [#40291][i40291], [#38411][i38411], [#63267][i63267], [#26974][i26974], [#48238][i48238], [#38946][i38946]
- [jetson-containers #1661][jc1661]
