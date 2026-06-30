#!/usr/bin/env python3
"""Free-move range finder for the SO101.

Disables torque on all 6 servos so you can move every joint BY HAND, displays
live positions, and tracks the min/max RAW encoder counts each servo reaches.
Move each joint slowly through its FULL travel (both directions), then press
Ctrl+C to print a copy-paste summary (raw min/max/span + the current calibration
for comparison).

This is the data needed to fix the calibration (e.g. shoulder_pan's tiny range
and the gripper's overshooting range_max).

NOTE: torque is OFF -> the arm goes LIMP and will sag under gravity. Support it
before releasing torque (the script waits for you to press ENTER first).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

logging.disable(logging.INFO)
from strands_robots import Robot   # noqa: E402

ORDER = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
COUNTS_PER_DEG = 4096 / 360.0      # STS3215: 4096 counts/rev


def main():
    ap = argparse.ArgumentParser(description="Move all 6 SO101 joints by hand; live positions + min/max.")
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--calibration-id", default="my_awesome_follower_arm")
    ap.add_argument("--rate", type=float, default=10.0, help="display refresh rate (Hz)")
    args = ap.parse_args()

    r = Robot("so101", mode="real", port=args.port, id=args.calibration_id, cameras={})
    try:
        r.robot.config.disable_torque_on_disconnect = False
    except Exception:                     # noqa: BLE001
        pass

    print(f"Connecting SO101 @ {args.port} ...")
    r.robot.connect(calibrate=False)
    b = r.robot.bus

    def flush():
        try:
            b.port_handler.clearPort()
        except Exception:                 # noqa: BLE001
            pass

    input("\n>>> SUPPORT THE ARM (it will go limp), then press ENTER to release torque...")
    for _ in range(6):
        try:
            b.disable_torque()
            break
        except Exception:                 # noqa: BLE001
            flush(); time.sleep(0.1)
    print(">>> TORQUE OFF — move each joint slowly through its FULL range. Ctrl+C when done.\n")

    def read_raw():
        for _ in range(8):
            try:
                return b.sync_read("Present_Position", normalize=False)
            except Exception:             # noqa: BLE001
                flush(); time.sleep(0.03)
        return None

    mn: dict = {}
    mx: dict = {}
    period = 1.0 / args.rate
    n_lines = len(ORDER) + 1              # header + 6 rows
    printed = False
    try:
        while True:
            raw = read_raw()
            if raw is None:
                time.sleep(period); continue
            for m in ORDER:
                if m in raw:
                    mn[m] = min(mn.get(m, raw[m]), raw[m])
                    mx[m] = max(mx.get(m, raw[m]), raw[m])
            rows = [f"{'motor':<14}{'raw now':>9}{'raw min':>9}{'raw max':>9}{'span':>7}{'≈deg':>7}"]
            for m in ORDER:
                now = raw.get(m, 0)
                lo, hi = mn.get(m, now), mx.get(m, now)
                span = hi - lo
                rows.append(f"{m:<14}{now:>9}{lo:>9}{hi:>9}{span:>7}{span / COUNTS_PER_DEG:>7.0f}")
            if printed:
                sys.stdout.write(f"\033[{n_lines}A")     # move cursor up to overwrite
            sys.stdout.write("\n".join(rows) + "\n")
            sys.stdout.flush()
            printed = True
            time.sleep(period)
    except KeyboardInterrupt:
        pass

    # --- copy-paste summary ---
    print("\n\n========== COPY THIS BACK ==========")
    print("# observed raw min/max per servo (move-by-hand):")
    for m in ORDER:
        lo, hi = mn.get(m, "?"), mx.get(m, "?")
        span = (mx.get(m, 0) - mn.get(m, 0))
        print(f"  {m:<14} min={lo:<6} max={hi:<6} span={span:<6} (~{span / COUNTS_PER_DEG:.0f} deg)")
    print("\n# current calibration (for comparison):")
    cal = b.calibration
    for m in ORDER:
        c = cal.get(m)
        if c:
            print(f"  {m:<14} range_min={c.range_min:<6} range_max={c.range_max:<6} "
                  f"span={c.range_max - c.range_min}")
    print("====================================")

    try:
        r.robot.disconnect()
    except Exception:                     # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
