# lerobot Feetech serial-read regression — root cause & fix

**Status:** Root-caused and fixed (2026-07-01).
**Affects:** SO-100 / SO-101 (and other Feetech STS3215 arms) driven by **lerobot ≥ 0.3.2** (incl. **0.5.0**, **0.5.1**) over a **CH340 / `cdc_acm` USB-serial adapter** (`/dev/ttyACM0`) at **1,000,000 baud**.
**Hardware here:** Jetson AGX Thor + Seeed Studio SO-ARM101 "Pro" (6× Feetech STS3215, model 777), single follower arm, controller `1a86:55d3` (QinHeng/CH340) on `/dev/ttyACM0`.

---

## TL;DR

lerobot **PR #777 "Hardware API redesign"** (merged **2025-06-05**) rewrote the Feetech motor-bus read path and **silently removed the read/write retries and the generous packet timeout** the old driver relied on:

| | OLD driver (pre-#777, ~mid-2025, git-source) | NEW driver (0.3.2 → 0.5.1) |
|---|---|---|
| Read retries | `NUM_READ_RETRY = 20` *("needed for feetech")* | `num_retry = 0` (one attempt) |
| Write retries | `NUM_WRITE_RETRY = 20` | `num_retry = 0` |
| Packet timeout | flat **1000 ms** | calculated **~50 ms** @ 1 Mbaud |

On a CH340/`cdc_acm` bus, normal USB latency hiccups used to be absorbed by the 20-retry loop. With the new code a **single** hiccup surfaces immediately as:

```
ConnectionError: Failed to sync read 'Present_Position' on ids=[1, 2, 3, 4, 5, 6] after 1 tries. [TxRxResult] There is no status packet!
```

(or the corruption variant `[TxRxResult] Incorrect status packet!`). The arm then appears to "drop" because the failing call triggers `disconnect()`, and lerobot's `disable_torque_on_disconnect=True` releases the motors.

**This is also why Seeed Studio pins a frozen fork** of lerobot and tells users not to use upstream (see [Seeed references](#seeed-studio-references)).

**Fix:** restore the retries + widen the timeout by editing **two files in the locally installed lerobot package** (see [The fix](#the-fix)). Do **not** downgrade — the new API is required by `strands_robots` / the GR00T integration.

---

## Symptoms

- `[TxRxResult] There is no status packet!` — read timed out / no reply (silence).
- `[TxRxResult] Incorrect status packet!` — reply corrupted (framing/misaligned buffer).
- Intermittent: works for a handful of transactions, then the bus goes silent; worse at higher control rates and when 2 USB cameras share the bus.
- Arm physically **drops** right after the error (that's `disable_torque_on_disconnect`, not a power fault).

**Not the cause (ruled out by measurement):** power supply (Present_Voltage held steady ~11.8 V with ~0 A current at the moment of failure), torque limits (arm joints `Max_Torque_Limit=1000`), or USB power.

---

## Root cause (with evidence)

- **Regression commit:** [PR #777 "Hardware API redesign"](https://github.com/huggingface/lerobot/pull/777), merged **2025-06-05** (merge `e23b41e79a`, parent `b536f47e3ff8`). It deleted the old `lerobot/common/robot_devices/motors/feetech.py` and created the new `lerobot/motors/feetech/feetech.py` (`MotorsBus`/`FeetechMotorsBus`, `GroupSyncRead`, `patch_setPacketTimeout`).
- **Folder move:** [PR #1417](https://github.com/huggingface/lerobot/pull/1417) (2025-07-01) relocated `lerobot/...` → `src/lerobot/...` (so upstream `main` now uses `src/lerobot/motors/...`; the *installed* package is still imported as `lerobot.motors...`).
- **First PyPI release with the regression:** **v0.3.2** (2025-08-01). Same read-path logic through **v0.5.0 / v0.5.1**.
- **The two behavioural changes (#777):**
  1. **Retries 20 → 0.** OLD `lerobot/common/robot_devices/motors/feetech.py` had `NUM_READ_RETRY = 20` / `NUM_WRITE_RETRY = 20` with the comment *"High number of retries is needed for feetech compared to dynamixel motors."* NEW `sync_read`/`_sync_read`/`sync_write`/`_sync_write` default to `num_retry = 0`, and `SOFollower.get_observation()` calls `sync_read("Present_Position")` with the default → **1 attempt**.
  2. **Timeout 1000 ms → ~50 ms.** OLD used a flat `setPacketTimeoutMillis(1000)`. NEW monkey-patches `setPacketTimeout` to `(tx_time_per_byte * packet_length) + (tx_time_per_byte * 3.0) + 50`, which at 1 Mbaud is ≈ 50 ms — too tight for CH340/`cdc_acm` buffering on a 6-motor sync read.

### Matching upstream issues (still open)

- [#1389](https://github.com/huggingface/lerobot/issues/1389) — **exact hardware**: STS3215 + CH340 + `/dev/ttyACM0`, "There is no status packet". (open)
- [#1252](https://github.com/huggingface/lerobot/issues/1252) — same "after 1 tries" error; thread workaround = pass `num_retry=3`. (open)
- [#526](https://github.com/huggingface/lerobot/issues/526) — "Incorrect status packet"; user fixed by relaxing `setPacketTimeout` by +200 ms. (closed)
- [#560](https://github.com/huggingface/lerobot/issues/560) — old-path "There is no status packet". (closed)
- [#1010](https://github.com/huggingface/lerobot/issues/1010) — "Incorrect status packet" traced to servo firmware 3.9→3.10 + supply voltage (a *different*, hardware cause worth ruling out). (closed)
- [#3299](https://github.com/huggingface/lerobot/issues/3299) — "after 4 tries … no status packet" on `motors_bus.py` `_sync_read`. (open)

---

## Seeed Studio references

Seeed ships these arms and **maintains a frozen fork of lerobot** because upstream breaks. Their wiki states (verbatim):

> *"please git clone the recommended GitHub repository `https://github.com/Seeed-Projects/lerobot.git`. The repository recommended in this documentation is a verified stable version; the official Lerobot repository is continuously updated to the latest version, **which may cause unforeseen issues**."*

- **Seeed fork (pinned "verified stable"):** https://github.com/Seeed-Projects/lerobot — fork of `huggingface/lerobot`, pinned around **v0.4.4**. Default branch `main`.
- **Seeed wiki — SO10x in LeRobot:** https://wiki.seeedstudio.com/lerobot_so100m/
- **Seeed wiki — SoArm in LeRobot (new):** https://wiki.seeedstudio.com/lerobot_so100m_new/
- **Product (SO-ARM101 Pro):** https://www.seeedstudio.com/SO-ARM101-Low-Cost-AI-Arm-Kit-Pro-p-6427.html
- **Upstream lerobot:** https://github.com/huggingface/lerobot · **PyPI:** https://pypi.org/project/lerobot/

> Note: the Seeed fork at v0.4.4 still uses the post-#777 read path, so it can exhibit the same issue. The reliable cure is the [code change below](#the-fix), regardless of which lerobot you run.

---

## The fix

Edit **two files inside the locally installed lerobot package**. These are runtime edits to the installed library (not the repo) and will be lost on `pip install --upgrade lerobot` — re-apply after any lerobot upgrade.

### Find your install path

```bash
python -c "import lerobot, os; print(os.path.dirname(lerobot.__file__))"
```

On this machine: `/home/chiwaichan/miniconda3/lib/python3.13/site-packages/lerobot` (lerobot **0.5.0**).

> Line numbers below are for **0.5.0**; if they drift in another version, match on the function names / code text instead.

### Back up first

```bash
LR=$(python -c "import lerobot, os; print(os.path.dirname(lerobot.__file__))")
cp -n "$LR/motors/feetech/feetech.py"  "$LR/motors/feetech/feetech.py.orig"
cp -n "$LR/motors/motors_bus.py"       "$LR/motors/motors_bus.py.orig"
```

### Change 1 — widen the packet timeout

**File:** `motors/feetech/feetech.py` (function `patch_setPacketTimeout`, ~line 95–99)

```diff
-    self.packet_timeout = (self.tx_time_per_byte * packet_length) + (self.tx_time_per_byte * 3.0) + 50
+    # PATCHED (chiwai): margin 50 -> 250 ms. lerobot 0.5.0's GroupSyncRead of all
+    # 6 motors over CH340/cdc_acm at 1Mbaud needs more slack than the stock 50ms.
+    self.packet_timeout = (self.tx_time_per_byte * packet_length) + (self.tx_time_per_byte * 3.0) + 250
```

### Change 2 — restore the read/write retries (pre-#777 = 20)

**File:** `motors/motors_bus.py` — change the default `num_retry` from `0` to `20` in **all four** sync methods:

| Method | ~line | change |
|---|---|---|
| `def sync_read(...)`  | 1118 | `num_retry: int = 0,` → `num_retry: int = 20,` |
| `def _sync_read(...)` | 1163 | `num_retry: int = 0,` → `num_retry: int = 20,` |
| `def sync_write(...)` | 1211 | `num_retry: int = 0,` → `num_retry: int = 20,` |
| `def _sync_write(...)`| 1251 | `num_retry: int = 0,` → `num_retry: int = 20,` |

```diff
   def sync_read(
       self,
       data_name: str,
       motors: NameOrID | Sequence[NameOrID] | None = None,
       *,
       normalize: bool = True,
-      num_retry: int = 0,
+      num_retry: int = 20,   # PATCHED (chiwai): restore pre-#777 NUM_READ_RETRY=20 (Feetech/CH340 needs it)
   ) -> dict[str, Value]:
```
```diff
   def _sync_read(
       self,
       addr: int,
       length: int,
       motor_ids: list[int],
       *,
-      num_retry: int = 0,
+      num_retry: int = 20,   # PATCHED (chiwai): pre-#777 default
       raise_on_error: bool = True,
       err_msg: str = "",
   ) -> tuple[dict[int, int], int]:
```
```diff
   def sync_write(
       self,
       data_name: str,
       values: Value | dict[str, Value],
       *,
       normalize: bool = True,
-      num_retry: int = 0,
+      num_retry: int = 20,   # PATCHED (chiwai): restore pre-#777 NUM_WRITE_RETRY=20
   ) -> None:
```
```diff
   def _sync_write(
       self,
       addr: int,
       length: int,
       ids_values: dict[int, int],
-      num_retry: int = 0,
+      num_retry: int = 20,   # PATCHED (chiwai): pre-#777 default
       raise_on_error: bool = True,
       err_msg: str = "",
   ) -> int:
```

> ⚠️ `motors_bus.py` has **many** lines reading `num_retry: int = 0` (ping, read, write, enable/disable_torque, etc.). **Only change the four sync methods above.** Use the surrounding function signature as the anchor so you don't edit the wrong ones.

### Verify the patches

```bash
LR=$(python -c "import lerobot, os; print(os.path.dirname(lerobot.__file__))")
grep -n "PATCHED" "$LR/motors/feetech/feetech.py" "$LR/motors/motors_bus.py"
# expect: 1 hit in feetech.py, 4 hits in motors_bus.py
```

### Revert

```bash
LR=$(python -c "import lerobot, os; print(os.path.dirname(lerobot.__file__))")
mv "$LR/motors/feetech/feetech.py.orig" "$LR/motors/feetech/feetech.py"
mv "$LR/motors/motors_bus.py.orig"      "$LR/motors/motors_bus.py"
```

---

## Validation

After the patches, the real robot read+write pattern (`Present_Position` read + `Goal_Position` write, 20 Hz) ran **200/200 iterations clean**, where it previously died after ~100. The integrated GR00T closed loop (camera → GR00T → arm) ran **30/30 steps** with live camera streaming and no bus silence. See `chiwai/scripts/servo_diag.py` for the diagnostic (reads `Present_Voltage`/`Present_Current` to distinguish a comms fault from a brownout).

---

## Two related gotchas (separate from this regression)

1. **Arm "drops" on any error** = `SOFollowerConfig.disable_torque_on_disconnect = True`. Set `robot.robot.config.disable_torque_on_disconnect = False` before `connect()` so a glitch **holds** the pose instead of releasing torque. (Applied in `chiwai/scripts/home_to_zero.py`.)
2. **Stale-buffer corruption** (`Incorrect status packet`) after a messy disconnect: the in-loop retries don't flush, so add a `robot.robot.bus.port_handler.clearPort()` between app-level retries to re-sync the serial buffer. (Applied in the `chiwai/scripts/*` read/send helpers.)

---

## Servo position ranges — THIS specific arm

Calibration id: **`my_awesome_follower_arm`**
Calibration file: `~/.cache/huggingface/lerobot/calibration/robots/so101_follower/my_awesome_follower_arm.json`
Servos: 6× Feetech **STS3215** (model 777), 12-bit encoder = **4096 counts/rev** → **1 count ≈ 0.0879°**.

Raw calibration (`MotorCalibration` per motor, read from the bus):

| motor | id | drive_mode | homing_offset | range_min | range_max | span (counts) | span (≈deg) |
|---|---:|---:|---:|---:|---:|---:|---:|
| shoulder_pan  | 1 | 0 |  -31  | 2028 | 2162 | **134** ⚠️ | ~12° |
| shoulder_lift | 2 | 0 | -162  | 1002 | 3278 | 2276 | ~200° |
| elbow_flex    | 3 | 0 |   65  |  736 | 2971 | 2235 | ~196° |
| wrist_flex    | 4 | 0 |  -80  |  781 | 3250 | 2469 | ~217° |
| wrist_roll    | 5 | 0 |   65  |  927 | 3156 | 2229 | ~196° |
| gripper       | 6 | 0 | -1196 | 2039 | 3461 | 1422 | ~125° |

### How these map to commanded positions (lerobot normalized units)

`get_observation()` / `send_action()` use **normalized** units, where `[range_min, range_max]` maps to:
- **Arm joints** (shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll): **`-100 … +100`** (center 0).
- **Gripper**: **`0 … 100`** (0 = closed).

Verified working travel (from `chiwai/scripts/test_servo_range.sh`, ±50% test): all 5 arm joints reach **−50 and +50** cleanly; values are clamped to **±100** at the calibrated ends.

### ⚠️ Two arm-specific caveats

1. **`shoulder_pan` span is anomalously small — 134 counts (~12°)** vs ~2200 for the other joints. This means the base-pan joint was almost certainly **not rotated through its full range during calibration**, so its normalized `-100…+100` only covers ~12° of physical travel. If you need real pan range, **re-calibrate** `shoulder_pan` (move it through its full sweep during the calibration step). Everything still "works" because commands stay within the recorded range — the range is just tiny.

2. **Gripper stalls open under power — it is TORQUE-limited, not hard-stopped.**
   > **Corrected 2026-07-01** (see [`SO101_SERVO_RANGES.md`](./SO101_SERVO_RANGES.md)): an earlier version of this note claimed a physical hard-stop at raw ~3094 / normalized 74. That was wrong — **by hand (torque off) the gripper reaches raw 3474, past its calibration `range_max` (3461)**, so there is no hard-stop there.
   - **Closed:** raw **~2042–2048** ≈ normalized **0.2–0.6** (on `range_min` 2039 — correctly calibrated).
   - **Open:** moves freely to raw **~3474** by hand; under power it stalls around raw ~3094 (norm ~74) because torque is throttled, not because it hits a wall.
   - **Root cause:** lerobot's `SOFollower.configure()` sets the gripper *alone* to conservative limits — `Max_Torque_Limit=500` (50%), `Overload_Torque=25%`, `Protection_Current=250`. Against the gripper mechanism's resistance, that throttled torque + early overload trip cuts power mid-travel → it stalls, **overheats (saw 58°C vs 33°C on the others)**, and latches (needs power-cycle + minutes to cool).
   - **Proper fix:** **raise the gripper torque limits** (e.g. `Max_Torque_Limit`→850, `Overload_Torque`→60, `Protection_Current`→400), not clamp its range. (`test_servo_range.sh` still defaults `--gripper-max 74` as a safety clamp until the limits are raised.)

---

## Re-calibration procedure (fixes `shoulder_pan` range + gripper `range_max`)

Calibration is **interactive** — run it yourself in a terminal (it prompts you to move joints and press ENTER; can't be fully scripted).

**1. Back up the current calibration** (the run overwrites it):
```bash
cp ~/.cache/huggingface/lerobot/calibration/robots/so101_follower/my_awesome_follower_arm.json{,.bak}
```

**2. Launch calibration** (lerobot 0.5.0 console script):
```bash
lerobot-calibrate --robot.type=so101_follower --robot.port=/dev/ttyACM0 --robot.id=my_awesome_follower_arm
```
If it prompts *"Press ENTER to use provided calibration file … or type 'c' …"*, type **`c`** + ENTER to run a **fresh** calibration.

**3. Follow the flow** (matches `SOFollower.calibrate()` in `lerobot/robots/so_follower/so_follower.py`):

1. **"Move `{robot}` to the middle of its range of motion and press ENTER"** — put the arm in a neutral mid pose, press ENTER. (Sets `homing_offset` so this pose = center / normalized 0.)
2. **"Move all joints except `wrist_roll` sequentially through their entire ranges of motion"** — physically move **each** joint to **both** extremes while it live-records min/max. **This is where the two fixes happen:**
   - **`shoulder_pan`** → rotate the base **fully CCW and fully CW** to each hard limit. ← fixes the ~12° under-range (span 134).
   - **`gripper`** → **fully open to the physical stop and fully close**. ← records `range_max` at the true open stop (~3094 raw) instead of the overshooting 3461, so normalized 100 = fully open.
   - Also sweep `shoulder_lift`, `elbow_flex`, `wrist_flex` through their full travel.
   - **`wrist_roll`** is auto-set to a full turn (0–4095) — you don't record it.
   - Press **ENTER** when done.
3. The new calibration JSON is written automatically.

**4. Verify:**
```bash
python chiwai/scripts/servo_diag.py            # confirm new range_min/range_max per servo
./chiwai/scripts/test_servo_range.sh           # shoulder_pan span should now be large; gripper reaches ~100
```
After a correct gripper recal, you can raise `--gripper-max` back toward ~95–100 (the clamp is only needed because the old `range_max` overshot). `lerobot-find-joint-limits` can also inspect/verify limits.

---

## Version / commit reference

| Version | Date | Read path |
|---|---|---|
| pre-#777 git (`b536f47e3ff8` and earlier) | ≤ 2025-06-04 | **OLD** — 20 retries + 1000 ms (stable; what worked "a year ago") |
| `e23b41e79a` (#777 merge) | 2025-06-05 | **NEW** — `num_retry=0` + ~50 ms (regression enters) |
| 0.3.2 | 2025-08-01 | NEW (first PyPI with regression) |
| 0.4.0 – 0.4.4 | 2025-10 → 2026-02 | NEW (Seeed fork pins ~0.4.4) |
| **0.5.0** (installed here) | 2026-03-09 | NEW |
| 0.5.1 | 2026-04-07 | NEW |

> There were **no PyPI releases between 0.1.0 (2024-03) and 0.3.2 (2025-08)** — the pre-regression "stable" lerobot of mid-2025 was a **git-source install**, which matches the "worked a year ago" timeline.
