#!/usr/bin/env bash
# Verify access to the SO101 arm + 2 cameras. Read-only: pings servos and grabs
# one camera frame each. Does NOT move the arm.
#
# Usage:
#   ./test_robot_hardware.sh                  # defaults: arm /dev/ttyACM0, cams 0 & 1
#   ARM=/dev/ttyACM1 CAMS="2 4" ./test_robot_hardware.sh
set -uo pipefail

ARM="${ARM:-/dev/ttyACM0}"
CAMS="${CAMS:-0 2}"            # space-separated camera indices (0 & 2 = the two USB cams; 1 & 3 are metadata nodes)
BAUD="${BAUD:-1000000}"        # Feetech STS3215 default on SO101
IDS="${IDS:-1 2 3 4 5 6}"      # servo IDs to ping (6-DOF SO101)

echo "=================================================="
echo " Robot hardware check  (arm=$ARM  cams=[$CAMS])"
echo "=================================================="

# ---------- 1. Device nodes present? ----------
echo
echo "[1] Device nodes"
if [ -e "$ARM" ]; then
  echo "  ✅ arm node $ARM present (perms $(stat -c '%a' "$ARM"), writable=$([ -w "$ARM" ] && echo yes || echo NO))"
else
  echo "  ❌ arm node $ARM NOT FOUND — is it plugged in / powered?"
fi
for c in $CAMS; do
  if [ -e "/dev/video$c" ]; then echo "  ✅ /dev/video$c present"; else echo "  ❌ /dev/video$c NOT FOUND"; fi
done

# ---------- 2. Cameras: open + grab a frame ----------
echo
echo "[2] Cameras (grab one real frame each)"
python3 - "$CAMS" <<'PY'
import sys
idxs = [int(x) for x in sys.argv[1].split()]
try:
    import cv2
except Exception as e:
    print(f"  ❌ cannot import cv2: {e}"); sys.exit(0)
for i in idxs:
    cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap = cv2.VideoCapture(i)
    if not cap.isOpened():
        print(f"  ❌ camera {i}: could not open"); continue
    ok, frame = False, None
    for _ in range(5):                       # warm up; first reads can be empty
        ok, frame = cap.read()
        if ok and frame is not None: break
    if ok and frame is not None:
        h, w = frame.shape[:2]
        print(f"  ✅ camera {i}: frame {w}x{h}")
    else:
        print(f"  ❌ camera {i}: opened but no frame")
    cap.release()
PY

# ---------- 3. Arm: ping Feetech servos (read-only) ----------
echo
echo "[3] Arm servos (Feetech ping @ ${BAUD} baud — no movement)"
python3 - "$ARM" "$BAUD" "$IDS" <<'PY'
import sys
port, baud, ids = sys.argv[1], int(sys.argv[2]), [int(x) for x in sys.argv[3].split()]
try:
    import serial
except Exception as e:
    print(f"  ❌ cannot import pyserial: {e}"); sys.exit(0)
try:
    ser = serial.Serial(port, baud, timeout=0.1)
except Exception as e:
    print(f"  ❌ cannot open {port} @ {baud}: {e}"); sys.exit(0)

def ping(sid, tries=4):
    # Feetech/STS PING: FF FF ID 02 01 CHK ; checksum = ~(ID+LEN+INST)&0xFF
    chk = (~(sid + 0x02 + 0x01)) & 0xFF
    pkt = bytes([0xFF, 0xFF, sid, 0x02, 0x01, chk])
    for _ in range(tries):                   # retry: serial replies can be missed
        ser.reset_input_buffer()
        ser.write(pkt); ser.flush()
        resp = ser.read(6)                   # expect FF FF ID 02 ERR CHK
        if len(resp) >= 5 and resp[0] == 0xFF and resp[1] == 0xFF and resp[2] == sid:
            return True
    return False

found = [s for s in ids if ping(s)]
ser.close()
if found:
    print(f"  ✅ arm reachable on {port} — servos answered: {found}  ({len(found)}/{len(ids)})")
    if len(found) < len(ids):
        print(f"     note: this raw ping does not do Feetech half-duplex arbitration, so it can")
        print(f"     under-count farther servos. <{len(ids)} here ≠ a hardware fault. The")
        print(f"     authoritative check is connecting via lerobot's SO101 driver.")
else:
    print(f"  ❌ no servos responded on {port}. Wrong baud? arm unpowered? port in use by another process?")
PY

echo
echo "Done."
