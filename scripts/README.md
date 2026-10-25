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

# Plan B: everything on the Mac

```sh
scripts/mac-up.sh             # Ollama + gemma4:e4b (pulled if missing), camera, jm doctor, jm serve; prints the headset command
scripts/mac-up.sh --dry-run   # check and print the commands only
scripts/mac-up.sh --stop      # stop jm serve, the camera and an Ollama it started
python3 scripts/mac_camera.py --list   # cameras ffmpeg sees (the Brio is picked first)
```
- Local only: the model must be a local Ollama tag (`JM_MAC_MODEL`, default `gemma4:e4b`); `:cloud` tags and non-local `JM_OLLAMA_URL` are refused.
- It creates `jm-gemma4-e4b` (same weights, `num_ctx` = `JM_MAX_LEN`, 8192) so frames don't overflow Ollama's short default context.
- Camera: ffmpeg (`brew install ffmpeg`) pushes frames to `/api/frames` via `scripts/mac_camera.py` (`JM_MAC_CAMERA` = index or name, `JM_MAC_FPS`); else `pip3 install opencv-python` and `jm serve` reads `JM_CAMERA` itself. Force one with `JM_MAC_CAMERA_VIA=ffmpeg|opencv|none`. The first run asks for camera permission for the terminal.
- Voice: Ollama `/v1` dropped audio in our tests (docs/CONFORMANCE.md), so the headset command it prints uses `--text`. Try voice mode, but fall back to `--text` if it mishears.
- Logs and settings: `/tmp/journeyman/{serve,camera,ollama}.log`, `/tmp/journeyman/env.sh`.
