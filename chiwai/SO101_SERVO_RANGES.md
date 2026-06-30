# SO-101 servo ranges — measured vs calibrated (this specific arm)

**Date:** 2026-07-01
**Arm:** Seeed SO-ARM101, calibration id **`my_awesome_follower_arm`**
**Calibration file:** `~/.cache/huggingface/lerobot/calibration/robots/so101_follower/my_awesome_follower_arm.json`
**Servos:** 6× Feetech **STS3215** (model 777), **4096 counts/rev** → **1 count ≈ 0.0879°**.
**Related:** [`LEROBOT_FEETECH_SERIAL_REGRESSION.md`](./LEROBOT_FEETECH_SERIAL_REGRESSION.md) (serial fix + per-servo calibration section).

---

## How the data was collected

`chiwai/scripts/find_servo_ranges.sh` — disables torque so every joint can be moved **by hand**, shows live raw encoder positions, and tracks the **min/max raw counts** each servo reaches. Each joint was moved slowly through its **full physical travel** (both directions); the gripper was fully opened and closed by hand.

> Caveat: observed values are only as good as how fully each joint was moved by hand. If a number looks short of the true limit, re-move that joint.

---

## Measured (by hand) vs current calibration

| servo | obs min | obs max | obs span | ≈deg | cal range_min | cal range_max | cal span | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| **shoulder_pan**  | 652 | 3415 | **2763** | ~243° | 2028 | 2162 | **134** | ⚠️ **grossly under-calibrated** |
| shoulder_lift     | 936 | 3242 | 2306 | ~203° | 1002 | 3278 | 2276 | ~ok (cal slightly wider) |
| elbow_flex        | 766 | 3019 | 2253 | ~198° | 736 | 2971 | 2235 | ~ok |
| wrist_flex        | 792 | 2926 | 2134 | ~188° | 781 | 3250 | 2469 | cal max > observed (3250 vs 2926) — recheck travel |
| wrist_roll        | 560 | 3239 | 2679 | ~235° | 927 | 3156 | 2229 | cal narrower than real |
| **gripper**       | 2032 | **3474** | 1442 | ~127° | 2039 | 3461 | 1422 | opens **past** cal max by hand |

---

## Finding 1 — `shoulder_pan` is drastically under-calibrated

- **Real travel:** 652 → 3415 = **2763 counts (~243°)**.
- **Recorded:** 2028 → 2162 = **134 counts (~12°)**.
- The base-pan joint was clearly **not rotated through its full sweep during the original calibration**, so its normalized `-100…+100` covers only ~12° of ~243° available — roughly **5% of its real range**.
- **Impact:** every `shoulder_pan` command (teleop, GR00T, scripts) is squeezed into a 12° sliver. Nothing crashes (commands stay in-range), but the joint barely moves.
- **Fix:** recalibrate (rotate the base fully both ways during the ranges step), or write the measured min/max into the calibration.

---

## Finding 2 — the gripper is TORQUE-limited, NOT hard-stopped (corrects an earlier conclusion)

Earlier (under motor power) the gripper would only reach **normalized ~74 (raw ~3094)** before stalling and overheating, which was attributed to the calibration `range_max` (3461) overshooting a physical hard-stop. **That was wrong.**

- **By hand (torque off) the gripper reaches raw 3474 — *past* its calibration max (3461).** So there is **no mechanical hard-stop at ~3094**; the gripper moves its full range freely.
- The under-power stall at ~74 was a **torque/overload limit**: lerobot's `SOFollower.configure()` sets the gripper *alone* to conservative values —
  `Max_Torque_Limit = 500` (50%), `Overload_Torque = 25%`, `Protection_Current = 250`.
  When the gripper meets normal mechanism resistance, that throttled torque + early overload trip cuts power mid-travel, it stalls, and a stalled servo heats up (saw 58°C vs ~33°C on the others). The latch then needs a power-cycle (and several minutes to actually cool).
- **Closed position is correct:** raw ~2042–2048 ≈ normalized 0.2–0.6, sitting on `range_min` (2039). Encoder/closed calibration is fine.
- **Fix:** **raise the gripper's torque limits** (not clamp its range). Suggested: `Max_Torque_Limit` 500→**850**, `Overload_Torque` 25→**60**, `Protection_Current` 250→**400**.

> Supersedes the "gripper range_max overshoots the physical stop" note in the companion doc — the gripper has no hard-stop there; it was torque-starved.

---

## Suggested fixes

### A. Recalibrate (fixes `shoulder_pan` + captures true ranges)

**Clean / recommended** — interactive, also sets homing correctly:
```bash
cp ~/.cache/huggingface/lerobot/calibration/robots/so101_follower/my_awesome_follower_arm.json{,.bak}
lerobot-calibrate --robot.type=so101_follower --robot.port=/dev/ttyACM0 --robot.id=my_awesome_follower_arm
```
During the "move all joints through their range" step, **fully rotate `shoulder_pan` both ways** and **fully open/close the gripper**.

**Quick alternative** — write the measured min/max straight into the calibration JSON (homing_offset kept). Target values from the table above:

| servo | new range_min | new range_max |
|---|---:|---:|
| shoulder_pan  | 652  | 3415 |
| shoulder_lift | 936  | 3242 |
| elbow_flex    | 766  | 3019 |
| wrist_flex    | 792  | 2926 |
| wrist_roll    | 560  | 3239 |
| gripper       | 2032 | 3474 |

### B. Raise the gripper torque limits (so it actuates its full range)

One-time write to the gripper's STS3215 EEPROM (`chiwai/scripts/set_gripper_torque.sh`, TBD):
```
Max_Torque_Limit : 500 -> 850
Overload_Torque  : 25  -> 60
Protection_Current: 250 -> 400
```
Do this only when the gripper has cooled to ~33°C.

---

## Tools

- `chiwai/scripts/find_servo_ranges.sh` — free-move range finder (this data).
- `chiwai/scripts/test_servo_range.sh` — powered ±50% per-servo range check (12 positions).
- `chiwai/scripts/servo_diag.py` — static register dump (voltage/torque/temp) + monitored move.
