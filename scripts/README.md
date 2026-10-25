# Thor bring-up

```sh
scripts/thor-up.sh            # vLLM 31B NVFP4 (:8001, eyes) + E4B (:8000, ears + agent), health checks, jm doctor, jm serve
scripts/thor-up.sh --dry-run  # print the commands only
scripts/thor-down.sh          # stop jm serve and both containers
```
- Starts what is there: the 31B folder (found under `/home/jetson`, or `JM_31B_DIR`) and E4B (Hugging Face cache, downloaded on first run; set `HF_TOKEN` if the download is refused). `--only-31b` / `--only-e4b` pick one.
- Both up: `JM_GEMMA_URL` = E4B, `JM_VISION_URL` = 31B. Only E4B: E4B does everything. Only 31B: 31B does everything, but no audio, so use the headset with `--text`.
- Models load one after the other (31B first) so their memory reservations don't race. Re-running reuses containers that are already up.
- Settings are saved to `/tmp/journeyman/env.sh`; `source` it before other `./jm` commands. `jm serve` logs to `/tmp/journeyman/serve.log`.
- Out of memory? Lower `JM_31B_MEM` / `JM_E4B_MEM` (fractions of the GPU memory, default 0.50 / 0.25) or `JM_MAX_LEN` (8192). NVFP4 not detected? `JM_31B_ARGS="--quantization modelopt_fp4"`. Startup errors and their fixes: [docs/VLLM-THOR.md](../docs/VLLM-THOR.md).
- Want to probe vLLM from the Mac? `JM_VLLM_HOST=0.0.0.0` (default: Thor only).
