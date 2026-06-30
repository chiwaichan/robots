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
import time

import numpy as np

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


def clamp_action(cmd: dict, cur: dict, max_arm: float, max_grip: float) -> dict:
    """Safety: limit how far any joint can move from its CURRENT angle per step.

    Guarantees gentle motion regardless of what the policy emits. arm joints are
    capped at +/-max_arm degrees, the gripper at +/-max_grip (0-100 range).
    """
    out = {}
    for k, target in cmd.items():
        now = float(cur[k])
        lim = max_grip if k == "gripper.pos" else max_arm
        out[k] = now + max(-lim, min(lim, target - now))
    return out


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
    try:
        for step in range(args.steps):
            raw = robust_read(robot)
            t = time.time()
            actions = client.get_action(build_nested_obs(raw, args.task))
            dt = time.time() - t
            target = to_lerobot_action(actions, step=0)    # receding horizon: take step 0
            cmd = clamp_action(target, raw, args.max_step_deg, args.max_grip_step)
            log.info("step %2d  infer=%4.0fms  cmd=%s",
                     step, dt * 1000, {k: round(v, 1) for k, v in cmd.items()})
            if not args.dry_run:
                robust_send(robot, cmd)
            time.sleep(max(0.0, period - dt))
    finally:
        try:
            robot.robot.disconnect()
        except Exception as e:
            log.warning("disconnect: %s", str(e)[:80])
        log.info("Done.")


if __name__ == "__main__":
    main()
