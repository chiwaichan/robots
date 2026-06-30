#!/usr/bin/env bash
# Home the SO101 follower arm to its calibrated 0 position (smooth ramp).
#
# Doubles as an actuation test: if the arm physically swings to 0, torque/drive
# is fine. No cameras opened.
#
# Usage:
#   ./home_arm.sh                       # ramp all joints to 0 over 2.5s, hold 3s
#   ./home_arm.sh --secs 4 --hold 5     # slower/gentler, longer hold
#   ./home_arm.sh --no-gripper          # leave the gripper where it is
#   ./home_arm.sh --target 0 --port /dev/ttyACM0 --calibration-id my_awesome_follower_arm
#
# Any extra args are passed straight through to home_to_zero.py.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python "${HERE}/home_to_zero.py" "$@"
