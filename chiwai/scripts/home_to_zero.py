#!/usr/bin/env python3
"""Drive the SO101 follower arm smoothly to its calibrated 0 (home) position.

Two purposes:
  1) Calibration homing — park every joint at 0 so you have a known reference.
  2) Actuation test — if the arm physically swings to 0, torque/drive works,
     which proves a GR00T run that "didn't move" just had targets sitting near
     the current pose (not a hardware fault).

No cameras are opened (homing needs no vision, and it keeps the USB bus clear).
Motion is a smooth ramp from the CURRENT pose to the target over --secs, so the
arm eases to 0 instead of snapping. Ctrl-C is safe at any point.
"""
from __future__ import annotations

import argparse
import logging
import time

from strands_robots import Robot

ARM = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("home_arm")


def _send(robot, cmd, tries: int = 5, delay: float = 0.05):
    """send_action with flush-on-retry, in case of a serial glitch."""
    last = None
    for _ in range(tries):
        try:
            return robot.robot.send_action(cmd)
        except Exception as e:            # noqa: BLE001
            last = e
            try:
                robot.robot.bus.port_handler.clearPort()
            except Exception:             # noqa: BLE001
                pass
            time.sleep(delay)
    raise last


def _read(robot, tries: int = 8, delay: float = 0.06):
    last = None
    for _ in range(tries):
        try:
            return robot.robot.get_observation()
        except Exception as e:            # noqa: BLE001
            last = e
            try:
                robot.robot.bus.port_handler.clearPort()
            except Exception:             # noqa: BLE001
                pass
            time.sleep(delay)
    raise last


def main():
    ap = argparse.ArgumentParser(description="Smoothly home the SO101 to its calibrated 0 position.")
    ap.add_argument("--port", default="/dev/ttyACM0", help="SO101 serial port")
    ap.add_argument("--calibration-id", default="my_awesome_follower_arm")
    ap.add_argument("--target", type=float, default=0.0, help="target position for every joint (default 0 = home)")
    ap.add_argument("--secs", type=float, default=2.5, help="ramp duration current->target (slower = gentler)")
    ap.add_argument("--rate", type=float, default=30.0, help="command rate (Hz)")
    ap.add_argument("--hold", type=float, default=3.0, help="seconds to hold at target with torque on")
    ap.add_argument("--no-gripper", action="store_true", help="leave the gripper where it is")
    args = ap.parse_args()

    joints = [m for m in ARM if not (args.no_gripper and m == "gripper")]

    robot = Robot("so101", mode="real", port=args.port, id=args.calibration_id, cameras={})
    # lerobot 0.5.0 releases torque on disconnect by default — so a serial-comms
    # glitch (the 0.5.0 regression) makes the arm DROP. Keep torque on disconnect
    # so the arm holds its pose through a glitch instead of collapsing.
    try:
        robot.robot.config.disable_torque_on_disconnect = False
    except Exception:                     # noqa: BLE001
        pass
    log.info("Connecting SO101 @ %s (id=%s)...", args.port, args.calibration_id)
    for attempt in range(4):
        try:
            robot.robot.connect(calibrate=False)
            break
        except Exception as e:            # noqa: BLE001
            log.warning("connect attempt %d failed: %s", attempt + 1, str(e).splitlines()[-1][:70])
            try:
                robot.robot.disconnect()
            except Exception:             # noqa: BLE001
                pass
            time.sleep(0.8)
    else:
        raise SystemExit("could not connect to SO101 (check servo power/cabling)")

    # Belt-and-suspenders: make sure torque is on so the motors actually drive.
    try:
        robot.robot.bus.enable_torque()
    except Exception as e:                # noqa: BLE001
        log.warning("enable_torque: %s", str(e)[:80])

    obs = _read(robot)
    start = {f"{m}.pos": float(obs[f"{m}.pos"]) for m in joints}
    log.info("start pose:  %s", {k: round(v, 1) for k, v in start.items()})
    log.info("driving to target=%.1f over %.1fs ...", args.target, args.secs)

    n = max(1, int(args.secs * args.rate))
    period = 1.0 / args.rate
    try:
        for i in range(1, n + 1):
            a = i / n
            cmd = {k: start[k] * (1.0 - a) + args.target * a for k in start}   # linear ease current->target
            _send(robot, cmd)
            time.sleep(period)

        time.sleep(0.2)
        try:
            obs2 = _read(robot)
        except Exception as e:            # noqa: BLE001
            log.error("post-move read FAILED — the servo bus went SILENT (%s).",
                      str(e).splitlines()[-1][:60])
            log.error("The arm most likely BROWNED OUT / tripped overload protection while "
                      "lifting the heavy shoulder_lift+elbow joints toward 0. This is a POWER "
                      "issue, not software. Check: 12V supply rating (need ~12V/5A), barrel-jack "
                      "seated, and consider homing to a low/rested pose instead of straight-0.")
            return
        end = {f"{m}.pos": float(obs2[f"{m}.pos"]) for m in joints}
        log.info("end pose:    %s", {k: round(v, 1) for k, v in end.items()})
        moved = max(abs(end[f"{m}.pos"] - start[f"{m}.pos"]) for m in joints)
        reached = max(abs(end[f"{m}.pos"] - args.target) for m in joints)
        log.info("max joint movement: %.1f deg  ->  %s", moved,
                 "ARM MOVED ✅ (torque/drive OK)" if moved > 1.0
                 else "NO MOVEMENT ❌ — check 12V motor power / Max_Torque_Limit / cabling")
        if moved > 1.0 and reached > 15.0:
            log.warning("reached only within %.0f deg of target — a joint may have stalled/tripped "
                        "under gravity load (likely shoulder_lift/elbow). Suspect power/torque limit.", reached)

        if args.hold > 0:
            log.info("holding at target for %.1fs (torque on)...", args.hold)
            time.sleep(args.hold)
    finally:
        try:
            robot.robot.disconnect()       # lerobot may release torque on disconnect (config default)
        except Exception as e:             # noqa: BLE001
            log.warning("disconnect: %s", str(e)[:80])
        log.info("Done.")


if __name__ == "__main__":
    main()
