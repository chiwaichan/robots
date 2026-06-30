# Running GR00T N1.7 Inference on Jetson AGX Thor — Field Guide

A complete, battle-tested walkthrough for standing up **NVIDIA Isaac-GR00T N1.7**
inference (service / Docker mode) on a **Jetson AGX Thor**, driven through the
`strands-robots` repo's `gr00t_inference` tooling.

> **Bottom line up front:** GR00T N1.7 *does* run on Thor and returns real
> inference, but the stock `strands-robots` lifecycle tool builds the **wrong
> Docker image** on Thor (a CUDA 12.8 *datacenter* image whose PyTorch has no
> Thor kernels). You must build Isaac-GR00T's **Thor-specific** Dockerfile
> (CUDA 13.0) yourself, then run + serve manually. Along the way you'll hit ~6
> other Jetson-specific gotchas, all documented below with fixes.
>
> Verified working: `tests_integ/groot/test_n17_live_server.py` — **4/4 pass**,
> including a real synthetic-observation → action-chunk inference on the Thor GPU.

---

## 0. Target platform (what "Thor" means here)

| Property | Value |
|---|---|
| Board | Jetson AGX Thor (Blackwell GPU, **compute capability `sm_110`**) |
| JetPack / L4T | R38.x (Ubuntu 24.04 userspace) |
| CUDA (host) | **13.0** (`/usr/local/cuda-13.0`) |
| Memory | 128 GB unified (shared CPU/GPU) |
| Docker | 28.x, **`nvidia` container runtime registered** (default runtime is still `runc`) |
| Arch | `aarch64` |

Key consequences of being Thor:
- GPU is **`sm_110`**. PyTorch wheels built only for `sm_90/100/120` (datacenter
  Blackwell / Hopper) **will not run a single kernel** — you get
  `CUDA error: no kernel image is available for execution on the device`.
- `nvidia-smi` reports `[N/A]` for memory (unified-memory iGPU). Don't rely on it.
- `--gpus all` (the dGPU way) is **rejected** on Tegra; you must use
  `--runtime nvidia`.

---

## 1. The single most important thing

**The `strands-robots` `gr00t_inference` lifecycle tool is NOT Thor-aware.**

Its build step (`action="build_image"` / `lifecycle="full"`) runs
`bash docker/build.sh` inside the cloned Isaac-GR00T repo. That builds
`docker/Dockerfile`, which is:

```dockerfile
FROM nvidia/cuda:12.8.0-devel-ubuntu22.04      # CUDA 12.8, datacenter aarch64 (GB200/Grace-Hopper)
# -> installs torch 2.7.1+cu128, arch ['sm_90','sm_100','sm_120']   (NO sm_110)
```

On Thor that image builds and even loads the model, but **GPU compute fails**.

Isaac-GR00T ships a dedicated Thor path that the tool never uses:

```
scripts/deployment/thor/Dockerfile      # FROM nvidia/cuda:13.0.0-devel-ubuntu24.04, Python 3.12
scripts/deployment/thor/install_deps.sh # installs torch 2.10.0 (cu13), arch ['sm_110','sm_121']
scripts/deployment/thor/pyproject.toml
scripts/activate_thor.sh
```

(See `Isaac-GR00T/CLAUDE.md` and `getting_started/hardware_recommendation.md` —
Thor = CUDA 13.0, Ubuntu 24.04, Python 3.12.)

**So: build the Thor Dockerfile manually. Everything downstream is reusable.**

---

## 2. End-to-end procedure (the happy path, knowing what we know now)

Assumes the `strands-robots` repo is set up and `gr00t_inference` importable.
Times are rough on Thor over home broadband.

### 2.1 Get the Isaac-GR00T source (the tool can do the clone)
The tool clones to `~/.strands_robots/Isaac-GR00T` at tag `n1.7-release`.
You can let `gr00t_inference(action="build_image", ...)` clone it (it will then
build the WRONG image — that's fine, or interrupt after clone), or clone yourself:

```bash
git clone --depth 1 --branch n1.7-release --recurse-submodules \
  https://github.com/NVIDIA/Isaac-GR00T ~/.strands_robots/Isaac-GR00T
```

### 2.2 Build the **Thor** image (~17 min, ~19 GB)
```bash
cd ~/.strands_robots/Isaac-GR00T
docker build -f scripts/deployment/thor/Dockerfile -t gr00t:thor .
```
This pulls cu13 wheels (`torch`, `tensorrt-cu13`, `nvidia-*-cu13`, flash-attn).

**Verify the image actually has Thor GPU kernels before going further:**
```bash
docker run --rm --runtime nvidia gr00t:thor \
  python -c "import torch; print(torch.__version__, torch.cuda.get_arch_list()); \
  a=torch.randn(512,512,device='cuda'); print('GPU OK', float((a@a).sum()))"
# Expect: 2.10.0 ['sm_110', 'sm_121']  ... GPU OK <number>   (NO 'no kernel image' error)
```

### 2.3 Download the checkpoint (~6.6 GB) — **outside `/home`**
Two Jetson gotchas converge here (Xet bug + `/home` mount ban — see §3).
Easiest robust path: download with Xet disabled into a **non-`/home`** dir.

```bash
export HF_HUB_DISABLE_XET=1            # avoids the hf_xet brotli crash (§3.1)
export HF_HUB_ENABLE_HF_TRANSFER=0
mkdir -p /tmp/gr00t-ckpt
huggingface-cli download nvidia/GR00T-N1.7-3B \
  --local-dir /tmp/gr00t-ckpt/nvidia__GR00T-N1.7-3B
```
If two tiny files (`statistics.json`, `experiment_cfg/dataset_statistics.json`)
fail with a `brotli` error even with Xet off, fetch them with curl (§3.1):
```bash
CK=/tmp/gr00t-ckpt/nvidia__GR00T-N1.7-3B
B=https://huggingface.co/nvidia/GR00T-N1.7-3B/resolve/main
curl -fsSL --compressed "$B/statistics.json" -o "$CK/statistics.json"
curl -fsSL --compressed "$B/experiment_cfg/dataset_statistics.json" -o "$CK/experiment_cfg/dataset_statistics.json"
```
Sanity: `ls -la $CK/*.safetensors` → two shards (~4.99 GB + ~1.92 GB).

> **Why not `~/.strands_robots/checkpoints/...`?** That's under `/home`, which the
> tool's container-mount guard refuses (§3.2). Keep the checkpoint somewhere like
> `/tmp/gr00t-ckpt` or another non-`/home`, non-system path on the same ext4 disk.

### 2.4 Run the container — `--runtime nvidia`, mount the checkpoint
```bash
CK=/tmp/gr00t-ckpt/nvidia__GR00T-N1.7-3B
docker run -d --runtime nvidia --ipc=host --name gr00t \
  -v "$CK:/data/checkpoints" \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  -p 5555:5555 \
  gr00t:thor tail -f /dev/null
```
> Use `--runtime nvidia`, **not** `--gpus all` (§3.3). Add `--cap-add SYS_PTRACE`
> if you want to attach `py-spy`/`gdb` for debugging.

### 2.5 Start the N1.7 inference server (inside the container)
```bash
docker exec -d gr00t bash -lc 'cd /workspace/gr00t && \
  python -m gr00t.eval.run_gr00t_server \
    --model-path /data/checkpoints \
    --port 5555 --host 0.0.0.0 \
    --embodiment-tag REAL_G1 \
  > /tmp/srv.log 2>&1'
```
- Entry point is `gr00t.eval.run_gr00t_server` (the **N1.7** server). It takes
  `--embodiment-tag` but **not** `--data-config` (that was N1.5/N1.6). See §3.4.
- `--embodiment-tag REAL_G1` is a real, valid tag and matches the bundled test.
  **Do not** use `new_embodiment` — the base model rejects it ("for finetuning only").
- Model load takes ~1–2 min (loads 2 safetensors shards onto the GPU).

### 2.6 Verify it's actually serving (don't trust the log or `ss` — §3.5)
```bash
# TCP reachability from the host (works because of -p 5555:5555):
timeout 4 bash -lc 'cat < /dev/null > /dev/tcp/localhost/5555' && echo "5555 OPEN"
```
Then run the bundled hardware-free test from the `strands-robots` repo:
```bash
cd <strands-robots repo>
GROOT_LIVE_SERVER=1 GROOT_SERVER_HOST=localhost GROOT_SERVER_PORT=5555 \
  python -m pytest tests_integ/groot/test_n17_live_server.py -v -o addopts=""
```
Expect **4 passed**, including `test_get_action_real_inference` — it sends a
synthetic REAL_G1 observation (random video + fake joint/EEF state + a language
instruction) and asserts a valid `navigate_command` action chunk of shape
`(1, 40, 3)`, finite values. That's real diffusion inference on the Thor GPU.

---

## 3. The gotchas (symptom → cause → fix)

### 3.1 HuggingFace **Xet / brotli** download crash
- **Symptom:** checkpoint download dies with
  `Failed to download 'nvidia/GR00T-N1.7-3B': brotli: decoder process called with data when 'can_accept_more_data()' is False`,
  often after several GB already landed in `.cache/huggingface/download/*.incomplete`.
- **Cause:** the repo is on HF's **Xet** backend (content-chunked, brotli-compressed).
  `hf_xet` 1.4.2 has a brotli back-pressure bug under heavy parallelism on aarch64.
  Even with Xet off, a couple of small JSON files served with HTTP `Content-Encoding: br`
  trip `brotlicffi` 1.2.0.0's streaming decoder.
- **Fix:** `export HF_HUB_DISABLE_XET=1` (forces classic HTTPS download of the big
  files). For the 1–2 small files that still fail, fetch with **`curl --compressed`**
  (curl decodes brotli natively). Files affected last time: `statistics.json` and
  `experiment_cfg/dataset_statistics.json` (~4.3 MB each; these hold normalization
  stats the model needs — not optional).
- **Note:** the lifecycle tool's download step **skips** when the local dir is merely
  *non-empty* — a partial/aborted download can be mistaken for "complete." If retrying,
  either delete the dir first or be sure it's truly complete (both shards full size).

### 3.2 Container mount refused under `/home`
- **Symptom:** `start_container` →
  `refusing to mount '/home/.../checkpoints/...': under protected host path '/home'`.
- **Cause:** `gr00t_inference` hard-blocks bind-mounting any host path under
  `/`, `/etc`, `/root`, **`/home`**, `/var`, `/usr`, … (prompt-injection hardening,
  `_BLOCKED_VOLUME_HOST_PATHS`). The default checkpoint dir
  (`~/.strands_robots/checkpoints`) is under `/home`, so it's self-blocking.
- **Fix:** put the checkpoint somewhere **outside** those prefixes, e.g.
  `/tmp/gr00t-ckpt/...` (on Thor `/tmp` is ext4 on the same NVMe, so moving from
  `/home` is an instant rename, not a copy). Pass that path as `hf_local_dir` and
  use `checkpoint_path=/data/checkpoints` (the in-container mount target).

### 3.3 `--gpus all` rejected on Tegra
- **Symptom:** `docker run` →
  `error running prestart hook: ... invoking the NVIDIA Container Runtime Hook directly
  (e.g. specifying the docker --gpus flag) is not supported. Please use --runtime=nvidia`.
- **Cause:** the tool hardcodes `docker run --gpus all` (no override). Jetson/Tegra
  needs the **NVIDIA Container Runtime** instead of the `--gpus` hook.
- **Fix:** run the container yourself with `--runtime nvidia` (the tool can't, so the
  `start_container`/`lifecycle` path can't be used on Thor for this step — run manually,
  then you can still use `gr00t_inference(action="start", ...)` which only `docker exec`s
  into the existing container). Alternatively set Docker's `default-runtime` to `nvidia`
  in `/etc/docker/daemon.json` — but `--gpus all` will *still* be rejected, so manual
  `--runtime nvidia` is the reliable route.

### 3.4 N1.7 server invocation differs from N1.5/N1.6
- **Symptom:** wrong/!legacy entrypoint, or `Unrecognized options: --server`, or
  `--embodiment-tag new_embodiment` rejected ("for finetuning custom robots… not the
  base model directly").
- **Cause:** N1.7 uses `python -m gr00t.eval.run_gr00t_server` which accepts
  `--embodiment-tag` (name or value, case-insensitive) but **not** `--server` or
  `--data-config`. If driving via `gr00t_inference`, pass `protocol="n1.7"` so it picks
  the right command (default `protocol="n1.5"` runs the legacy
  `scripts/inference_service.py`).
- **Fix:** use `--embodiment-tag REAL_G1` (or another *real* tag from the model's
  `embodiment_id.json` — e.g. `OXE_DROID*`, `XDOF`, `REAL_R1_PRO_SHARPA*`). `REAL_G1`
  is the one the bundled test targets. Run with `--help` to list known tags.

### 3.5 The "false hang" — server looks stuck but is actually serving
This one cost the most time. **The server can be fully up while every signal you'd
casually check says it's dead.** Specifically:
- **The redirected log freezes at `Loading checkpoint shards: 100%`** and never prints
  `Server is ready and listening`. → **Python block-buffers stdout when it's a file**,
  so the "ready" line sits unflushed in the buffer. The log is NOT a reliable progress
  signal. (Run with `python -u` or `PYTHONUNBUFFERED=1` to avoid this.)
- **The worker shows ~0% CPU with dozens of threads in `futex_wait`.** → That's the
  **normal resting state** of a loaded model whose server is blocked on
  `socket.recv()` waiting for a client. Idle ≠ deadlocked.
- **`ss -ltn` inside the container shows nothing on :5555.** → `ss` does **not** reliably
  show the **ZMQ**-bound socket. False negative.
- **How to actually check:** do a **TCP connect** to `localhost:5555`, or just run the
  client/test. To inspect a truly-stuck process: `--cap-add SYS_PTRACE` on the container,
  `uv pip install py-spy` into `/opt/gr00t-venv`, then `py-spy dump --pid <worker>`. A
  healthy server shows `MainThread (idle): run (gr00t/policy/server_client.py:137)` —
  i.e. parked in `socket.recv()`. That's serving, not hung.

### 3.6 PyTorch / Thor compute-capability mismatch (the original blocker)
- **Symptom:** model loads, then any GPU op throws
  `CUDA error: no kernel image is available for execution on the device`.
  `torch.cuda.is_available()` returns `True` (misleading!).
- **Cause:** image's torch built for `sm_90/100/120`, but Thor is `sm_110`.
- **Fix:** §1/§2.2 — build the Thor (cu13) image; its torch arch is
  `['sm_110','sm_121']`. Always smoke-test with a real `a@b` on `cuda`, not just
  `is_available()`.

---

## 4. Quick verification cheatsheet

```bash
# GPU kernels work in the image?
docker run --rm --runtime nvidia gr00t:thor python -c \
 "import torch;a=torch.randn(64,64,device='cuda');print('OK',float((a@a).sum()))"

# Server reachable?
timeout 4 bash -lc 'cat < /dev/null > /dev/tcp/localhost/5555' && echo OPEN || echo CLOSED

# Full inference test (no robot hardware):
GROOT_LIVE_SERVER=1 GROOT_SERVER_HOST=localhost GROOT_SERVER_PORT=5555 \
  python -m pytest tests_integ/groot/test_n17_live_server.py -v -o addopts=""

# What is a "stuck" server actually doing?
docker exec gr00t /opt/gr00t-venv/bin/py-spy dump --pid \
  $(docker exec gr00t pgrep -f run_gr00t_server | tail -1)
```

---

## 5. Key file / path reference

| What | Where |
|---|---|
| Isaac-GR00T clone | `~/.strands_robots/Isaac-GR00T` (tag `n1.7-release`) |
| **Correct Thor Dockerfile** | `Isaac-GR00T/scripts/deployment/thor/Dockerfile` (CUDA 13.0) |
| Wrong (default) Dockerfile | `Isaac-GR00T/docker/Dockerfile` (CUDA 12.8) ← built by `docker/build.sh` |
| Thor deps installer | `Isaac-GR00T/scripts/deployment/thor/install_deps.sh` |
| N1.7 server entrypoint | `gr00t/eval/run_gr00t_server.py` (in image: `/workspace/gr00t/...`) |
| Server serve loop | `gr00t/policy/server_client.py` (`PolicyServer.run()` → `socket.recv()`) |
| Valid embodiment tags | model's `embodiment_id.json` in the checkpoint |
| strands-robots GR00T tool | `strands_robots/tools/gr00t_inference.py` |
| strands-robots GR00T policy | `strands_robots/policies/groot/policy.py` (version auto-detect) |
| Hardware-free live test | `tests_integ/groot/test_n17_live_server.py` |
| GR00T docs (this repo) | `docs/policies/groot.md` |
| Jetson install notes | `docs/getting-started/installation.md` (`UV_TORCH_BACKEND=auto` → cu130) |

### Versions seen on a working Thor setup
- Image torch: **2.10.0**, arch `['sm_110','sm_121']`, CUDA 13.0
- (Wrong image torch: 2.7.1+cu128, arch `['sm_90','sm_100','sm_120']`)
- `huggingface_hub` 1.8.0, `hf_xet` 1.4.2, `brotlicffi` 1.2.0.0
- Checkpoint `nvidia/GR00T-N1.7-3B`: 2 shards (4.99 GB + 1.92 GB) + configs ≈ 6.6 GB
- DiT params ≈ 1.09B, SelfAttentionTransformer ≈ 201M

---

## 6. Two ways to run GR00T (so you pick the right one)

1. **Service / Docker mode** (this guide): server in a container, talk to it over
   ZMQ (`create_policy("groot", port=5555, ...)` or the client directly). Needs the
   Thor Docker image. Good for isolation and matching NVIDIA's deployment.
2. **Local / in-process mode** (`create_policy("groot", model_path=..., device="cuda")`):
   loads the model in your **host** Python. Requires a **host** torch with Thor (sm_110)
   support. On a Thor dev box that usually means a cu130 torch (e.g. a conda env like
   `~/miniconda3/envs/gr00t` with `torch 2.x+cu130`, arch including `sm_110`). The default
   `strands-robots` venv may ship CPU-only torch — check `torch.cuda.get_arch_list()`.

---

## 7. TL;DR checklist
- [ ] Build `gr00t:thor` from `scripts/deployment/thor/Dockerfile` (NOT `docker/build.sh`).
- [ ] Smoke-test GPU matmul in the image (must NOT say "no kernel image").
- [ ] Download `nvidia/GR00T-N1.7-3B` with `HF_HUB_DISABLE_XET=1`, into a **non-`/home`** dir;
      curl the 2 stats files if they brotli-fail.
- [ ] `docker run --runtime nvidia` (NOT `--gpus all`), mount checkpoint → `/data/checkpoints`.
- [ ] Start `gr00t.eval.run_gr00t_server --embodiment-tag REAL_G1` (NOT `new_embodiment`).
- [ ] Verify by **TCP connect / the live test**, NOT by the buffered log or `ss`.
- [ ] `test_n17_live_server.py` → 4 passed = done.

_Generated from a full Thor bring-up session. Reuse the existing `gr00t:thor`
image and `/tmp/gr00t-ckpt` checkpoint to skip the long steps._
