#!/usr/bin/env python3
"""SO101 follower arm driven by GR00T N1.7 — closed loop via the low-level client.

Drives an SO101 with a finetuned GR00T N1.7 SO101 checkpoint
(e.g. allros/GR00T-N1.7-grab-cube-so101) served by run_gr00t_server with
`--embodiment-tag new_embodiment`.

Why the low-level client (not create_policy("groot"))?
  The high-level Gr00tPolicy service path builds FLAT wire keys ("video.front")
  for the *legacy* GR00T server; the N1.7 run_gr00t_server entrypoint wants the
  NESTED format ({"video": {"front": ...}}) and rejects flat with
  "Observation must contain a 'video' key" (strands_robots issue #187).
  Gr00tInferenceClient with nested obs is verified working
  (see chiwai/scripts/verify_allros_lowlevel.py).

State/action don't auto-bridge: the SO101 reports six <motor>.pos scalars, but
the model speaks single_arm[5] + gripper[1] vectors — to_groot_state()/
to_lerobot_action() translate.

Usage (read-only, motors do NOT move):
  python groot_so101_demo.py --dry-run --task "grab the cube"
Then, when ready to actually actuate:
  python groot_so101_demo.py --task "grab the cube"
"""
from __future__ import annotations

import argparse
import logging
import os
import time

import numpy as np

try:
    import cv2                            # for --save-frames
except Exception:                         # noqa: BLE001
    cv2 = None

from strands_robots import Robot
from strands_robots.policies.groot.client import Gr00tInferenceClient

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("groot_so101")

# lerobot SO101 bus order = modality.json single_arm[0:5] then gripper[5:6]
ARM = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]


def build_nested_obs(raw: dict, task: str) -> dict:
    """lerobot get_observation() -> nested GR00T wire obs (B=1, T=1).

    Cameras come back as (H,W,C) uint8 under the configured names front/wrist;
    joints as <motor>.pos scalars. The server's processor resizes the frames.
    """
    def vid(x):
        a = np.asarray(x)
        return a[np.newaxis, np.newaxis, ...]            # (1,1,H,W,C)
    single_arm = np.array([[raw[f"{m}.pos"] for m in ARM]], dtype=np.float32)  # (1,5)
    gripper = np.array([[raw["gripper.pos"]]], dtype=np.float32)               # (1,1)
    # Single-camera mode: the dual-cam checkpoint needs front+wrist, but this rig
    # has one physical camera — feed the same frame into both required inputs.
    front = vid(raw["front"])
    wrist = vid(raw["wrist"]) if "wrist" in raw else front
    return {
        "video": {"front": front, "wrist": wrist},
        "state": {
            "single_arm": single_arm[np.newaxis, ...],    # (1,1,5)
            "gripper": gripper[np.newaxis, ...],          # (1,1,1)
        },
        "language": {"annotation.human.task_description": [[task]]},
    }


def to_lerobot_action(actions: dict, step: int = 0) -> dict:
    """One timestep of the action chunk -> lerobot {motor.pos: float}.

    actions: {"single_arm": (1,T,5), "gripper": (1,T,1)} from get_action.
    """
    arm = np.asarray(actions["single_arm"])[0, step]      # (5,)
    grip = np.asarray(actions["gripper"])[0, step].ravel()  # (1,)
    out = {f"{m}.pos": float(v) for m, v in zip(ARM, arm)}
    out["gripper.pos"] = float(grip[0])
    return out


def _flush(robot):
    """Flush the serial input buffer to re-sync after a corrupt read.

    A camera-induced glitch corrupts one read and leaves leftover bytes in the
    buffer, so every subsequent read keeps failing on the stale data. clearPort()
    (pyserial reset_input_buffer) realigns the stream so the next read is clean.
    """
    try:
        robot.robot.bus.port_handler.clearPort()
    except Exception:                    # noqa: BLE001
        pass


def robust_read(robot, tries: int = 8, delay: float = 0.06):
    last = None
    for _ in range(tries):
        try:
            return robot.robot.get_observation()
        except Exception as e:           # noqa: BLE001 - serial ConnectionError etc.
            last = e
            _flush(robot)
            time.sleep(delay)
    raise last


def robust_send(robot, cmd, tries: int = 5, delay: float = 0.05):
    last = None
    for _ in range(tries):
        try:
            return robot.robot.send_action(cmd)
        except Exception as e:           # noqa: BLE001
            last = e
            _flush(robot)
            time.sleep(delay)
    raise last


def clamp_action(cmd: dict, cur: dict, max_arm: float, max_grip: float,
                 grip_lo: float = 1.0, grip_hi: float = 74.0) -> dict:
    """Safety: limit how far any joint moves per step, AND cap the gripper to a
    torque-safe ABSOLUTE range.

    Two independent guards on the gripper:
      1. per-step rate limit (+/-max_grip) -> gentle motion, no current spikes;
      2. absolute [grip_lo, grip_hi] cap    -> the policy can NEVER walk the
         gripper to the unreachable open extreme, where it stalls against a
         standing position error and overheats (I2R). See
         chiwai/SO101_SERVO_RANGES.md (VERIFIED root cause) + lerobot PR #1809.
    Arm joints get the rate limit only.
    """
    out = {}
    for k, target in cmd.items():
        now = float(cur[k])
        lim = max_grip if k == "gripper.pos" else max_arm
        val = now + max(-lim, min(lim, target - now))
        if k == "gripper.pos":
            val = max(grip_lo, min(grip_hi, val))     # absolute torque-safe cap
        out[k] = val
    return out


def ramp_to(robot, targets: dict, secs: float = 2.5, rate: float = 30.0):
    """Smoothly ramp all joints from the current pose to `targets` {motor.pos: val}."""
    start = robust_read(robot)
    n = max(1, int(secs * rate))
    period = 1.0 / rate
    for i in range(1, n + 1):
        a = i / n
        step = {k: float(start[k]) * (1 - a) + float(v) * a for k, v in targets.items()}
        robust_send(robot, step)
        time.sleep(period)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0", help="SO101 serial port")
    ap.add_argument("--calibration-id", default="my_awesome_follower_arm")
    ap.add_argument("--front-cam", type=int, default=0)
    ap.add_argument("--wrist-cam", type=int, default=2)   # video2 = 2nd USB cam (video1/3 are metadata)
    ap.add_argument("--server-host", default="localhost")
    ap.add_argument("--server-port", type=int, default=5555)
    ap.add_argument("--task", default="grab the cube")
    ap.add_argument("--steps", type=int, default=20, help="control-loop iterations")
    ap.add_argument("--hz", type=float, default=5.0)
    ap.add_argument("--max-step-deg", type=float, default=6.0, help="max arm-joint move per step (deg)")
    ap.add_argument("--max-grip-step", type=float, default=25.0, help="max gripper move per step")
    ap.add_argument("--grip-min", type=float, default=1.0,
                    help="absolute gripper floor (normalized) — keep off the hard-closed stop")
    ap.add_argument("--grip-max", type=float, default=74.0,
                    help="absolute gripper ceiling (normalized) — torque-safe open limit; the policy "
                         "is never allowed past this so it can't stall/overheat. Raise toward ~95 "
                         "only AFTER recalibrating range_max at the natural open stop.")
    # MJPG (compressed) by default: 2 USB cams at 640x480x30 RAW (YUYV) saturate
    # the Jetson USB bus and corrupt the 1Mbaud servo serial link ("incorrect
    # status packet"). MJPG is ~10x less bandwidth at the same resolution, which
    # removes the contention. These cams only do 640x480 (no 320x240).
    ap.add_argument("--cam-width", type=int, default=640)
    ap.add_argument("--cam-height", type=int, default=480)
    ap.add_argument("--cam-fps", type=int, default=30)
    ap.add_argument("--cam-fourcc", default="MJPG", help="MJPG=compressed (low USB bw); YUYV=raw")
    ap.add_argument("--single-cam", action="store_true",
                    help="one physical camera (front) fed into BOTH model inputs (front+wrist); "
                         "no wrist cam opened — halves USB load, fits the 1-cam-on-USB-A setup")
    ap.add_argument("--dry-run", action="store_true", help="infer + print, never command motors")
    ap.add_argument("--action-horizon", type=int, default=16,
                    help="how many steps of GR00T's predicted action CHUNK to execute per inference "
                         "(the policy predicts a whole trajectory; executing only step 0 makes it crawl). "
                         "Clamped to the chunk length the server returns.")
    ap.add_argument("--home-first", action="store_true",
                    help="ramp the arm to a neutral pose BEFORE the GR00T loop, so the policy's "
                         "target is far away -> a big, visible sweep instead of holding in place")
    ap.add_argument("--save-frames", default="",
                    help="dir to dump the exact camera frame(s) sent to GR00T each step "
                         "(so you can SEE what the policy sees — wall vs cube). Needs opencv.")
    args = ap.parse_args()

    def cam(idx):
        return {"type": "opencv", "index_or_path": idx, "fourcc": args.cam_fourcc,
                "width": args.cam_width, "height": args.cam_height, "fps": args.cam_fps}
    cameras = {"front": cam(args.front_cam)}
    if not args.single_cam:
        cameras["wrist"] = cam(args.wrist_cam)
    log.info("cameras: %s", list(cameras))

    robot = Robot("so101", mode="real", port=args.port, id=args.calibration_id, cameras=cameras)
    log.info("Connecting SO101 @ %s (id=%s)...", args.port, args.calibration_id)
    for attempt in range(4):
        try:
            robot.robot.connect(calibrate=False)
            break
        except Exception as e:                     # noqa: BLE001
            log.warning("connect attempt %d failed: %s", attempt + 1, str(e).splitlines()[-1][:70])
            try:
                robot.robot.disconnect()
            except Exception:
                pass
            time.sleep(0.8)
    else:
        raise SystemExit("could not connect to SO101 after retries (check servo cabling/power)")
    raw0 = robust_read(robot)
    log.info("Connected. obs keys: %s", list(raw0.keys()))

    client = Gr00tInferenceClient(host=args.server_host, port=args.server_port, timeout_ms=60_000)
    if not client.ping():
        raise SystemExit("GR00T server ping failed — is it up on that host/port?")
    log.info("GR00T server ready (%s:%d). Task: %r%s",
             args.server_host, args.server_port, args.task,
             "  [DRY-RUN: motors will NOT move]" if args.dry_run else "")

    period = 1.0 / args.hz
    JOINTS = ARM + ["gripper"]

    # Home to a neutral pose first so GR00T's target is FAR from the start ->
    # a big, visible excursion instead of holding wherever the arm already sat.
    if args.home_first and not args.dry_run:
        neutral = {f"{m}.pos": 0.0 for m in ARM}
        neutral["gripper.pos"] = max(args.grip_min, 20.0)
        log.info("homing to neutral before GR00T (for a big visible move)...")
        ramp_to(robot, neutral, secs=2.5)

    if args.save_frames:
        os.makedirs(args.save_frames, exist_ok=True)

    cmd_min = {j: 1e9 for j in JOINTS}
    cmd_max = {j: -1e9 for j in JOINTS}
    try:
        for infer_i in range(args.steps):
            raw = robust_read(robot)
            if args.save_frames and cv2 is not None:          # dump what GR00T sees
                cv2.imwrite(f"{args.save_frames}/infer{infer_i:02d}_front.jpg",
                            np.asarray(raw["front"])[..., ::-1])
            t = time.time()
            actions = client.get_action(build_nested_obs(raw, args.task))
            dt = time.time() - t
            chunk_T = int(np.asarray(actions["single_arm"]).shape[1])
            horizon = max(1, min(args.action_horizon, chunk_T))

            # Execute GR00T's PREDICTED TRAJECTORY (the whole chunk), not just step 0.
            # Clamp each chunk step against the previous command (open-loop within
            # the chunk) so the motion is GR00T's, only rate-limited for safety.
            cur = {k: float(v) for k, v in raw.items() if k.endswith(".pos")}
            last = cur
            for h in range(horizon):
                target = to_lerobot_action(actions, step=h)
                cmd = clamp_action(target, cur, args.max_step_deg, args.max_grip_step,
                                   grip_lo=args.grip_min, grip_hi=args.grip_max)
                if not args.dry_run:
                    robust_send(robot, cmd)
                cur = cmd
                last = cmd
                for j in JOINTS:
                    cmd_min[j] = min(cmd_min[j], cmd[f"{j}.pos"])
                    cmd_max[j] = max(cmd_max[j], cmd[f"{j}.pos"])
                time.sleep(period)
            log.info("infer %2d  %4.0fms  chunk=%d/%d  last=%s",
                     infer_i, dt * 1000, horizon, chunk_T,
                     {k: round(v, 1) for k, v in last.items()})
    finally:
        spans = {j: round(cmd_max[j] - cmd_min[j], 1) for j in JOINTS if cmd_max[j] > -1e9}
        log.info("GR00T commanded travel per joint over the run (deg): %s", spans)
        try:
            robot.robot.disconnect()
        except Exception as e:
            log.warning("disconnect: %s", str(e)[:80])
        log.info("Done.")


if __name__ == "__main__":
    main()
