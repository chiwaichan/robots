#!/usr/bin/env python3
"""Ground-truth servo diagnostics for the SO101 — is the arm dropping because of
a VOLTAGE SAG (underpowered supply) or a TORQUE CAP (software/EEPROM limit)?

Reads the real STS3215 registers per joint:
  Present_Voltage   (0.1V units)  -> supply health, sag under load
  Present_Current   (~6.5mA units)-> how hard each motor is pulling
  Max_Torque_Limit  (0-1000)      -> 1000 = full torque; <1000 = throttled
  Torque_Limit / Protection_Current / Overload_Torque -> protection settings
  Present_Temperature, Torque_Enable

Modes:
  (default)   dump the static table at the current rest pose
  --monitor   gently drive toward --target while logging V/current EVERY step,
              and ABORT the instant voltage sags below --min-volt (catches a
              brownout in the act, before the arm collapses)

No cameras. Power-cycle the arm first if it tripped (overload latches).
"""
from __future__ import annotations

import argparse
import logging
import time

from strands_robots import Robot

ARM = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
STATIC = ["Present_Voltage", "Present_Temperature", "Max_Torque_Limit", "Torque_Limit",
          "Protection_Current", "Overload_Torque", "Torque_Enable", "P_Coefficient"]
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("servo_diag")


def read_reg(bus, reg):
    """{motor: raw_value} for a register, raw (no normalization). None on failure."""
    try:
        return bus.sync_read(reg, normalize=False, num_retry=20)  # match restored pre-#777 retry budget
    except Exception as e:                # noqa: BLE001
        log.warning("  read %s failed: %s", reg, str(e).splitlines()[-1][:50])
        return None


def dump_static(bus):
    log.info("\n=== static servo registers (at current pose) ===")
    table = {}
    for reg in STATIC:
        vals = read_reg(bus, reg)
        if vals is not None:
            table[reg] = vals
    # voltage / temperature are the headline numbers
    v = table.get("Present_Voltage", {})
    if v:
        volts = {m: round(x / 10.0, 1) for m, x in v.items()}
        log.info("Present_Voltage (V): %s", volts)
        lo = min(volts.values())
        log.info("  -> lowest %.1fV  %s", lo,
                 "✅ supply OK at rest" if lo >= 11.0 else "⚠️ already low at REST — supply/wiring suspect")
    for reg in STATIC:
        if reg in table and reg != "Present_Voltage":
            log.info("%-20s %s", reg, table[reg])
    mt = table.get("Max_Torque_Limit", {})
    if mt:
        throttled = {m: x for m, x in mt.items() if x < 1000}
        log.info("  -> Max_Torque_Limit: %s",
                 "all at 1000 (full) ✅" if not throttled else f"THROTTLED on {throttled} ❌ (arm can't hold load)")


def monitor_move(robot, bus, target, secs, rate, min_volt):
    obs = robot.robot.get_observation()
    start = {f"{m}.pos": float(obs[f"{m}.pos"]) for m in ARM}
    log.info("\n=== monitored move to %.0f (abort if V < %.1f) ===", target, min_volt)
    log.info("start: %s", {k: round(v, 1) for k, v in start.items()})
    n = max(1, int(secs * rate))
    period = 1.0 / rate
    for i in range(1, n + 1):
        a = i / n
        cmd = {k: start[k] * (1 - a) + target * a for k in start}
        try:
            robot.robot.send_action(cmd)
        except Exception as e:            # noqa: BLE001
            log.error("send failed @ step %d (%s) — bus likely browned out NOW.", i, str(e).splitlines()[-1][:40])
            return
        v = read_reg(bus, "Present_Voltage")
        c = read_reg(bus, "Present_Current")
        if v is None:
            log.error("voltage read failed @ step %d — bus went silent (brownout/reset).", i)
            return
        volts = {m: round(x / 10.0, 1) for m, x in v.items()}
        lo = min(volts.values())
        amps = {m: round(x * 6.5 / 1000.0, 2) for m, x in c.items()} if c else {}
        log.info("step %2d/%d  Vmin=%.1fV  V=%s  I(A)=%s", i, n, lo, volts, amps)
        if lo < min_volt:
            log.error(">>> VOLTAGE SAG to %.1fV — this is a POWER/SUPPLY brownout. Aborting to save the arm.", lo)
            return
    log.info("✅ completed move without a voltage sag — supply held up.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--calibration-id", default="my_awesome_follower_arm")
    ap.add_argument("--monitor", action="store_true", help="gently move to --target while logging V/current")
    ap.add_argument("--target", type=float, default=0.0)
    ap.add_argument("--secs", type=float, default=4.0)
    ap.add_argument("--rate", type=float, default=5.0)
    ap.add_argument("--min-volt", type=float, default=10.5, help="abort move if any servo drops below this (V)")
    args = ap.parse_args()

    robot = Robot("so101", mode="real", port=args.port, id=args.calibration_id, cameras={})
    log.info("Connecting SO101 @ %s ...", args.port)
    robot.robot.connect(calibrate=False)
    bus = robot.robot.bus
    try:
        dump_static(bus)
        if args.monitor:
            monitor_move(robot, bus, args.target, args.secs, args.rate, args.min_volt)
    finally:
        try:
            robot.robot.disconnect()
        except Exception:                 # noqa: BLE001
            pass
        log.info("\nDone.")


if __name__ == "__main__":
    main()
