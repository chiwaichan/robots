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

### VERIFIED root cause + correct fix (2026-07-01)

Confirmed by code, web research, **and direct measurement** — the gripper servo is **HEALTHY**, not failing:
- **Idle test (decisive):** held at a *reachable* position (norm ~16) with torque ON, the gripper drew **0 mA for 30s and the temperature *fell*** (50→42°C across runs). A faulty servo would draw current and heat *here*; it doesn't. So at any reachable position with no standing error → ~0 current → it cools.
- **Mechanism = standing-error stall → I²R heat, not a fault.** Commanded to its open extreme, the deliberately-capped torque can't reach the goal, so it **stalls with a standing position error**; a blocked rotor does no mechanical work → all drive power becomes winding heat. There is **no spring** in the SO-ARM gripper (direct servo-driven jaw), confirming a pure position-error stall.
- **The conservative limits are a DELIBERATE anti-burnout mitigation:** [lerobot PR #1809](https://github.com/huggingface/lerobot/pull/1809) "Lower limits by 50% for current and torque for gripper motor" (2025-08-29) added exactly `Torque_Limit=500`/`Overload_Torque=25`/`Protection_Current=250` to prevent gripper burnout "reported by Seeed Studio." These are the FIX for a known SO-ARM problem, not the bug.
- **What triggered the overheating here:** driving the gripper to/past its calibrated open extreme (unreachable under the safe torque) **+ leaving torque ON when parked** (the diagnostic scripts' `disable_torque_on_disconnect=False`).

**Correct fix — do NOT raise the torque (that re-introduces the burnout PR #1809 prevents):**
1. **Recalibrate** so gripper `range_max` is recorded a few degrees *inside* the open hard-stop → "open" (100) becomes reachable → error→0 → no heat. *(Proper fix.)*
2. **Cap the open command** so it's never driven to the stall point (`--gripper-max`).
3. **Release gripper torque when idle/parked** (don't leave it energized holding an error).

> Supersedes BOTH the earlier "raise the torque limits" suggestion AND the "range_max overshoots a physical hard-stop" note — both were wrong. Root cause is a standing-error stall against an unreachable open goal under the (intentional) safe torque cap.

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

### B. Do NOT raise the gripper torque limits

An earlier draft suggested raising them — that is the **wrong** fix. Per [lerobot PR #1809](https://github.com/huggingface/lerobot/pull/1809) the 50% caps exist *specifically* to prevent gripper burnout; raising them just makes a stalled servo push harder = more heat. Idle measurement proved the servo is healthy (0 mA + cooling at a reachable held position), so there is nothing to "fix" in the servo. Instead make the open goal reachable (recalibrate `range_max` inward / cap the open command) and release gripper torque when idle — see **Finding 2 → VERIFIED root cause** above.

---

## Tools

- `chiwai/scripts/find_servo_ranges.sh` — free-move range finder (this data).
- `chiwai/scripts/test_servo_range.sh` — powered ±50% per-servo range check (12 positions).
- `chiwai/scripts/servo_diag.py` — static register dump (voltage/torque/temp) + monitored move.
