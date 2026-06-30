# chiwai/ — personal Thor + SO101 work (segregated from upstream)

Everything in this folder is **mine**, kept under a single top-level directory so
rebasing this fork onto `strands-labs/robots` upstream stays conflict-free
(upstream never touches `chiwai/`).

To sync with upstream later:
```bash
git fetch origin
git rebase origin/main      # my chiwai/ commits replay cleanly on top
git push fork main
```

## Contents

| Path | What it is |
|---|---|
| `THOR_GR00T_N17_SETUP.md` | Field guide: building + running GR00T N1.7 inference on Jetson AGX Thor, with all the Jetson gotchas and fixes. |
| `scripts/test_robot_hardware.sh` | Read-only hardware check — pings the SO101 servos and grabs a frame from each camera. **Does not move the arm.** Defaults: arm `/dev/ttyACM0`, cams `0 2`. |
| `scripts/groot_infer_demo.py` | Send an observation to a running GR00T server and print every action + latency. |
| `scripts/run_groot_infer.sh` | Wrapper: ensures the GR00T container/server is up, then runs `groot_infer_demo.py`. |
| `scripts/groot_so101_demo.py` | Closed-loop GR00T → SO101 example (Robot + create_policy("groot") + get_actions). Run with `--dry-run` first (no motor movement). |

## My rig (verified)
- Jetson AGX Thor, CUDA 13.0 / sm_110
- SO101 follower arm on `/dev/ttyACM0` (calibration id `my_awesome_follower_arm`)
- 2 USB cameras at capture indices **0** and **2** (`video1`/`video3` are metadata nodes)

## Key facts (don't re-litigate)
- The repo was built for **Jetson Thor + SO100/SO101** from its first commit.
- **Base GR00T N1.7 has no SO101 head** (server tags: REAL_G1, REAL_R1, OXE_DROID, XDOF only).
  Driving an SO101 with GR00T **requires a finetuned `new_embodiment` checkpoint** —
  e.g. `~/checkpoints/gr00t-wave` (a `so100_dualcam` finetune: front+wrist cams,
  single_arm[5]+gripper[1]). That is the intended path, not base-model inference.

## Quick start
```bash
# 1. Verify hardware (safe, no movement)
chiwai/scripts/test_robot_hardware.sh

# 2. (once a GR00T server is up) closed-loop example, dry-run first
python chiwai/scripts/groot_so101_demo.py --dry-run --task "pick up the cube"
```
