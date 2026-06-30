#!/usr/bin/env bash
# Exercise all 6 SO101 servos through +/-50% of their range, ONE AT A TIME,
# to confirm every servo moves through its range. 6 joints x 2 directions =
# 12 tested positions, each read back and reported pass/fail.
#
# Gripper note: it is TORQUE-limited, not hard-stopped (see chiwai/SO101_SERVO_RANGES.md).
# Under stock conservative torque limits it stalls/overheats past ~74 normalized, so the
# gripper is capped at --gripper-max (default 74). After raising the gripper torque limits,
# run with e.g. --gripper-max 98 to test its full open/close range.
#
# Usage:
#   ./test_servo_range.sh                 # +/-50% of range, gentle ramps
#   ./test_servo_range.sh --percent 40    # smaller range (more conservative)
#   ./test_servo_range.sh --secs 2 --hold 1.5   # slower moves, longer pause
#   ./test_servo_range.sh --gripper-max 98      # after raising gripper torque limits
#
# Extra args pass straight through to test_servo_range.py.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python "${HERE}/test_servo_range.py" "$@"
