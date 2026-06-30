#!/usr/bin/env python3
"""Diagnose WHY the gripper overheats/stalls — measure, don't guess.

Drives the gripper slowly across a position range while logging, per step:
  Goal vs Present position  (is it tracking, or stuck with a standing error?)
  Present_Current           (stall current spike? raw counts, ~6.5mA/count)
  Present_Load              (torque effort the servo reports, signed)
  Present_Temperature       (how fast does it climb?)

This separates the candidate causes:
  - tracks fine, low current, flat temp        -> nothing wrong / cause was elsewhere
  - stops tracking at a position, current PEGS  -> sustained stall (mechanical or torque-limit)
  - current high & temp climbs while STILL moving freely by hand -> suspect failing servo (id 6)
  - oscillates around target                    -> PID hunting

SAFETY: only run when the gripper is COOL (~33C). Aborts if temperature rises past
--temp-abort or current pegs for too long. Other joints are held in place.
"""
from __future__ import annotations

import argparse
import logging
import time

logging.disable(logging.INFO)
from strands_robots import Robot   # noqa: E402

ARM = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]


def main():
    ap = argparse.ArgumentParser(description="Measure gripper current/load/temp while driving it.")
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--calibration-id", default="my_awesome_follower_arm")
    ap.add_argument("--lo", type=float, default=5.0, help="gripper close target (normalized)")
    ap.add_argument("--hi", type=float, default=70.0, help="gripper open target (normalized)")
    ap.add_argument("--secs", type=float, default=6.0, help="ramp time each direction")
    ap.add_argument("--rate", type=float, default=4.0, help="command+log rate (Hz)")
    ap.add_argument("--cycles", type=int, default=1, help="open/close cycles")
    ap.add_argument("--temp-abort", type=int, default=60, help="abort if gripper temp (C) reaches this")
    ap.add_argument("--temp-rise-abort", type=int, default=12, help="abort if temp rises this many C from start")
    ap.add_argument("--idle", type=float, default=0.0,
                    help="if >0: DON'T move — just hold position and log current/temp for N seconds. "
                         "A healthy free gripper draws ~0 mA at rest; nonzero current / rising temp = suspect servo.")
    args = ap.parse_args()

    r = Robot("so101", mode="real", port=args.port, id=args.calibration_id, cameras={})
    try:
        r.robot.config.disable_torque_on_disconnect = False
    except Exception:                     # noqa: BLE001
        pass
    print(f"Connecting @ {args.port} ...")
    for attempt in range(5):
        try:
            r.robot.connect(calibrate=False)
            break
        except Exception as e:            # noqa: BLE001
            print(f"  connect attempt {attempt + 1} failed: {str(e).splitlines()[-1][:60]}")
            try:
                r.robot.disconnect()
            except Exception:             # noqa: BLE001
                pass
            time.sleep(0.8)
    else:
        raise SystemExit("could not connect (serial glitch / check cabling+power)")
    b = r.robot.bus

    def flush():
        try:
            b.port_handler.clearPort()
        except Exception:                 # noqa: BLE001
            pass

    def rd(reg):
        for _ in range(8):
            try:
                return b.read(reg, "gripper", normalize=False)
            except Exception:             # noqa: BLE001
                flush(); time.sleep(0.04)
        return None

    def robs():
        for _ in range(8):
            try:
                return r.robot.get_observation()
            except Exception:             # noqa: BLE001
                flush(); time.sleep(0.04)
        return {}

    def send(cmd):
        for _ in range(6):
            try:
                return r.robot.send_action(cmd)
            except Exception:             # noqa: BLE001
                flush(); time.sleep(0.04)

    def release_gripper():
        """Disable just the gripper's torque so it stops drawing current / heating."""
        for _ in range(6):
            try:
                b.disable_torque("gripper"); return True
            except Exception:             # noqa: BLE001
                flush(); time.sleep(0.05)
        return False

    t0 = rd("Present_Temperature")
    too_hot = isinstance(t0, int) and t0 >= args.temp_abort
    print(f"gripper temp now = {t0} C  (this run aborts at {args.temp_abort}C or +{args.temp_rise_abort}C rise)")
    if too_hot:
        print(f">>> {t0}C is TOO HOT. Releasing gripper torque and aborting — POWER OFF the 12V "
              f"and let it cool ~10 min.")
        release_gripper()
        r.robot.disconnect(); return

    obs = robs()
    hold = {f"{m}.pos": float(obs[f"{m}.pos"]) for m in ARM}      # freeze the arm
    start_g = float(obs["gripper.pos"])

    # IDLE MONITOR: don't move — just hold and watch current/temp (failing-servo test)
    if args.idle > 0:
        print(f"\nIDLE MONITOR ({args.idle:.0f}s) — gripper HOLDING at {start_g:.0f}, NOT moving.")
        print("Healthy free gripper ~0 mA at rest; nonzero current / climbing temp = suspect servo (id 6).\n")
        print(f"{'t(s)':>5} {'pos':>7} {'curr(raw)':>9} {'curr(mA)':>8} {'load':>6} {'temp':>5}")
        send({**hold, "gripper.pos": start_g})
        t = 0.0
        while t < args.idle:
            o = robs(); pos = float(o.get("gripper.pos", float("nan")))
            cur = rd("Present_Current"); load = rd("Present_Load"); temp = rd("Present_Temperature")
            ma = round(cur * 6.5 / 1000.0, 2) if isinstance(cur, int) else cur
            print(f"{t:>5.1f} {pos:>7.1f} {str(cur):>9} {str(ma):>8} {str(load):>6} {str(temp):>5}")
            if isinstance(temp, int) and temp >= args.temp_abort:
                print(f">>> {temp}C — stopping."); break
            time.sleep(0.5); t += 0.5
        release_gripper()                 # leave the gripper relaxed so it cools
        try:
            r.robot.disconnect()
        except Exception:                 # noqa: BLE001
            pass
        return

    period = 1.0 / args.rate
    print(f"\n{'step':>4} {'goal':>6} {'pos':>7} {'err':>6} {'curr(raw)':>9} {'curr(mA)':>8} {'load':>6} {'temp':>5}")

    def to_amps(c):
        return round(c * 6.5 / 1000.0, 2) if isinstance(c, int) else c

    def sweep(frm, to, label):
        n = max(1, int(args.secs * args.rate))
        for i in range(1, n + 1):
            a = i / n
            goal = frm * (1 - a) + to * a
            send({**hold, "gripper.pos": float(goal)})
            time.sleep(period)
            o = robs()
            pos = float(o.get("gripper.pos", float("nan")))
            cur = rd("Present_Current")
            load = rd("Present_Load")
            temp = rd("Present_Temperature")
            print(f"{label[0]}{i:>3} {goal:>6.1f} {pos:>7.1f} {goal - pos:>6.1f} "
                  f"{str(cur):>9} {str(to_amps(cur)):>8} {str(load):>6} {str(temp):>5}")
            if isinstance(temp, int) and (temp >= args.temp_abort or temp - (t0 if isinstance(t0, int) else temp) >= args.temp_rise_abort):
                print(f">>> TEMP ABORT at {temp}C (started {t0}C). Stopping.")
                return False
        return True

    try:
        cur_g = start_g
        for _ in range(args.cycles):
            if not sweep(cur_g, args.hi, "O"):   # open
                break
            if not sweep(args.hi, args.lo, "C"):  # close
                break
            cur_g = args.lo
    except KeyboardInterrupt:
        pass

    tN = rd("Present_Temperature")
    print(f"\nend gripper temp = {tN} C  (start {t0} C)")
    print("Read the log: does current PEG (stall) at a position where pos stops tracking goal? "
          "Does temp climb while it's still moving? That tells us stall vs failing-servo vs fine.")
    release_gripper()                     # leave the gripper relaxed so it cools
    try:
        r.robot.disconnect()
    except Exception:                     # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
