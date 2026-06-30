#!/usr/bin/env bash
# Exercise all 6 SO101 servos through +/-50% of their range, ONE AT A TIME,
# to confirm every servo moves through its range. 6 joints x 2 directions =
# 12 tested positions, each read back and reported pass/fail.
#
# Gripper note: it is TORQUE-limited, not hard-stopped (see chiwai/SO101_SERVO_RANGES.md).
# The stock 50% torque cap is a DELIBERATE anti-burnout mitigation (lerobot PR #1809); driving
# the gripper to its open extreme stalls it -> standing-error I2R heat. The servo is healthy
# (0 mA + cooling at a reachable hold). So the gripper is capped at --gripper-max (default 74).
# Do NOT raise the torque limits to go higher -- instead recalibrate range_max a few degrees
# inside the open stop, then --gripper-max ~98 maps to a reachable "open".
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
