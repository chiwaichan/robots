#!/usr/bin/env python3
"""SO101 follower arm driven by GR00T N1.7 — strands_robots simplified API.

The GR00T counterpart of examples/molmoact2_so101_pickplace.py. Same two-call
pattern; only the policy differs:

  1. Robot("so101", mode="real", ...)            -> connected hardware robot
  2. create_policy("groot", port=5555, ...)      -> talks to the GR00T N1.7
                                                    server (so100_2rgb head)

The GR00T server must already be running with `--embodiment-tag so100_2rgb`
(the base N1.7 model's SO100/SO101 head). data_config "so101_dualcam" maps the
robot's front/wrist cameras + joint/gripper state to GR00T's modality keys.

Usage (read-only, motors do NOT move):
  python groot_so101_demo.py --dry-run --task "pick up the cube"
Then, when ready to actually actuate:
  python groot_so101_demo.py --task "pick up the cube"
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time

from strands_robots import Robot
from strands_robots.policies import create_policy

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("groot_so101")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0", help="SO101 serial port")
    ap.add_argument("--calibration-id", default="my_awesome_follower_arm")
    ap.add_argument("--front-cam", type=int, default=0)
    ap.add_argument("--wrist-cam", type=int, default=1)
    ap.add_argument("--server-host", default="localhost")
    ap.add_argument("--server-port", type=int, default=5555)
    ap.add_argument("--task", default="pick up the cube")
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--hz", type=float, default=5.0)
    ap.add_argument("--dry-run", action="store_true", help="infer + print, never command motors")
    args = ap.parse_args()

    robot = Robot(
        "so101",
        mode="real",
        port=args.port,
        id=args.calibration_id,
        cameras={
            "front": {"type": "opencv", "index_or_path": args.front_cam,
                      "width": 640, "height": 480, "fps": 30},
            "wrist": {"type": "opencv", "index_or_path": args.wrist_cam,
                      "width": 640, "height": 480, "fps": 30},
        },
    )
    log.info("Connecting SO101 @ %s (id=%s)...", args.port, args.calibration_id)
    robot.robot.connect(calibrate=False)
    obs0 = robot.robot.get_observation()
    log.info("Connected. obs keys: %s", list(obs0.keys()))

    # Service mode: connect to the running GR00T N1.7 server. so101_dualcam maps
    # front/wrist cameras + single_arm/gripper state to the model's modalities.
    policy = create_policy(
        "groot",
        host=args.server_host,
        port=args.server_port,
        data_config="so101_dualcam",
    )
    policy.reset()
    log.info("GR00T policy ready (server %s:%d). Task: %r%s",
             args.server_host, args.server_port, args.task,
             "  [DRY-RUN: motors will NOT move]" if args.dry_run else "")

    async def run():
        period = 1.0 / args.hz
        for step in range(args.steps):
            obs = robot.robot.get_observation()
            t = time.time()
            actions = await policy.get_actions(obs, args.task)
            dt = time.time() - t
            a = actions[0]
            log.info("step %2d  infer=%.0fms  action=%s",
                     step, dt * 1000, {k: round(float(v), 1) for k, v in a.items()})
            if not args.dry_run:
                robot.robot.send_action(a)
            await asyncio.sleep(max(0, period - dt))

    try:
        asyncio.run(run())
    finally:
        try:
            robot.robot.disconnect()
        except Exception as e:
            log.warning("disconnect: %s", str(e)[:80])
        log.info("Done.")


if __name__ == "__main__":
    main()
