#!/usr/bin/env python3
"""Exercise all 6 SO101 servos through +/-50% of their range, ONE AT A TIME.

Confirms every servo moves through its range. For each joint it drives to
+percent% then -percent% of the joint's half-range (others held at center),
reads the position back, and reports pass/fail. 6 joints x 2 directions = 12
tested positions.

Units are lerobot's normalized joint positions (same as get_observation):
  - arm joints: range about [-100, +100], center 0       -> +/-50 at 50%
  - gripper:    torque-safe ~[0, 74], center ~37          -> ~18 / ~56 at 50%

The gripper is NOT hard-stopped (by hand it moves its full range, past norm 100),
but under power lerobot throttles it (Max_Torque_Limit=500/50%, Overload_Torque=25%)
so it STALLS + overheats if pushed past ~74. We cap it at --gripper-max until those
torque limits are raised. See chiwai/SO101_SERVO_RANGES.md.

Motion is smoothly ramped and stays at 50% (well inside limits), so it's gentle.
Torque is kept on through disconnect so the arm holds, never drops.
"""
from __future__ import annotations

import argparse
import logging
import time

from strands_robots import Robot

ARM = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
ALL = ARM + ["gripper"]
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("servo_range")


def center_half(joint: str, gripper_max: float = 74.0) -> tuple[float, float]:
    """(center, half_span) of the SAFE travel for a joint (normalized units).

    The gripper is NOT hard-stopped: by hand it moves freely past normalized 100
    (measured raw 3474 vs calibration range_max 3461). But under power lerobot
    throttles the gripper alone (Max_Torque_Limit=500/50%, Overload_Torque=25%,
    Protection_Current=250), so it STALLS and overheats if commanded past ~74.
    Cap it at ~[0, gripper_max] until those torque limits are raised; then
    gripper_max can go to ~100. See chiwai/SO101_SERVO_RANGES.md.
    """
    if joint == "gripper":
        return gripper_max / 2.0, gripper_max / 2.0   # torque-safe travel ~[0, gripper_max]
    return 0.0, 100.0                                  # arm joints ~[-100, 100]


def _flush(robot):
    """Flush the serial buffer to re-sync after a corrupt/stale read."""
    try:
        robot.robot.bus.port_handler.clearPort()
    except Exception:                     # noqa: BLE001
        pass


def read_pos(robot, tries: int = 6, delay: float = 0.05) -> dict:
    last = None
    for _ in range(tries):
        try:
            obs = robot.robot.get_observation()
            return {j: float(obs[f"{j}.pos"]) for j in ALL}
        except Exception as e:            # noqa: BLE001
            last = e
            _flush(robot)                 # clear misaligned bytes, then retry
            time.sleep(delay)
    raise last


def _send(robot, cmd, tries: int = 6, delay: float = 0.04):
    last = None
    for _ in range(tries):
        try:
            return robot.robot.send_action(cmd)
        except Exception as e:            # noqa: BLE001
            last = e
            _flush(robot)
            time.sleep(delay)
    raise last


def cmd_of(targets: dict) -> dict:
    return {f"{j}.pos": float(targets[j]) for j in ALL}


def ramp_to(robot, targets: dict, secs: float, rate: float = 30.0):
    """Smoothly drive all joints from the current pose to `targets`."""
    start = read_pos(robot)
    n = max(1, int(secs * rate))
    period = 1.0 / rate
    for i in range(1, n + 1):
        a = i / n
        step = {j: start[j] * (1 - a) + targets[j] * a for j in ALL}
        _send(robot, cmd_of(step))
        time.sleep(period)


def main():
    ap = argparse.ArgumentParser(description="Move all 6 SO101 servos through +/-50% of range, one at a time.")
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--calibration-id", default="my_awesome_follower_arm")
    ap.add_argument("--percent", type=float, default=50.0, help="fraction of each joint's half-range to test")
    ap.add_argument("--secs", type=float, default=1.5, help="ramp duration per move")
    ap.add_argument("--hold", type=float, default=1.0, help="pause at each tested position (s)")
    ap.add_argument("--tol", type=float, default=12.0, help="pass tolerance (normalized units)")
    ap.add_argument("--gripper-max", type=float, default=74.0,
                    help="gripper torque-safe open ceiling (norm); it STALLS (not hits a stop) past "
                         "~74 under stock torque limits. Raise toward ~100 after bumping gripper torque.")
    args = ap.parse_args()

    robot = Robot("so101", mode="real", port=args.port, id=args.calibration_id, cameras={})
    try:
        robot.robot.config.disable_torque_on_disconnect = False   # hold, never drop
    except Exception:                     # noqa: BLE001
        pass

    log.info("Connecting SO101 @ %s ...", args.port)
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

    try:
        robot.robot.bus.enable_torque()
    except Exception as e:                # noqa: BLE001
        log.warning("enable_torque: %s", str(e)[:80])

    _flush(robot)            # clear any stale bytes left by a prior run's messy disconnect
    time.sleep(0.2)

    center = {j: center_half(j, args.gripper_max)[0] for j in ALL}
    log.info("centering all joints first ...")
    ramp_to(robot, center, args.secs)
    time.sleep(0.4)

    results = []     # (joint, label, target, actual, ok)
    pos_n = 0
    for j in ALL:
        c, half = center_half(j, args.gripper_max)
        hi = c + (args.percent / 100.0) * half
        lo = c - (args.percent / 100.0) * half
        if j == "gripper":   # stay within the gripper's torque-safe reach (it stalls, not stops, past ~74)
            hi = min(hi, args.gripper_max - 2.0)
            lo = max(lo, 1.0)
        for label, tgt in ((f"+{args.percent:.0f}%", hi), (f"-{args.percent:.0f}%", lo)):
            targets = dict(center)
            targets[j] = tgt
            ramp_to(robot, targets, args.secs)
            time.sleep(args.hold)
            pos_n += 1
            actual = read_pos(robot)[j]
            ok = abs(actual - tgt) <= args.tol
            results.append((j, label, tgt, actual, ok))
            log.info("pos %2d/12  %-14s %-5s target=%6.1f  actual=%6.1f  %s",
                     pos_n, j, label, tgt, actual, "OK ✅" if ok else "OFF ❌")
            ramp_to(robot, center, args.secs * 0.7)   # return that joint to center

    log.info("\nre-centering ...")
    ramp_to(robot, center, args.secs)

    # per-servo verdict: a servo passes if BOTH directions reached target
    log.info("\n=== per-servo range check ===")
    all_ok = True
    for j in ALL:
        hits = [r for r in results if r[0] == j]
        good = all(r[4] for r in hits)
        all_ok = all_ok and good
        spans = ", ".join(f"{r[1]}->{r[3]:.0f}" for r in hits)
        log.info("  %-14s %s   (%s)", j, "✅ both directions OK" if good else "❌ check this servo", spans)
    log.info("\n%s", "ALL 6 SERVOS OK ✅" if all_ok else "⚠️ one or more servos did not reach range — see ❌ above")

    try:
        robot.robot.disconnect()
    except Exception as e:                # noqa: BLE001
        log.warning("disconnect: %s", str(e)[:80])
    log.info("Done (torque left ON so the arm holds).")


if __name__ == "__main__":
    main()
