#!/usr/bin/env bash
# Diagnose WHY the gripper overheats/stalls — measure, don't guess.
# Slowly opens/closes the gripper while logging goal-vs-actual position,
# Present_Current (raw + mA), Present_Load, and Present_Temperature each step.
# Auto-aborts on over-temp. Other joints are held in place.
#
# RUN ONLY WHEN THE GRIPPER IS COOL (~33C).
#
# Usage:
#   ./gripper_load_test.sh                       # 1 open/close cycle, 5..70 normalized
#   ./gripper_load_test.sh --cycles 3            # 3 cycles
#   ./gripper_load_test.sh --hi 60 --lo 10       # gentler range
#   ./gripper_load_test.sh --secs 8 --rate 5     # slower sweep, finer logging
#
# Extra args pass straight through to gripper_load_test.py.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python "${HERE}/gripper_load_test.py" "$@"
