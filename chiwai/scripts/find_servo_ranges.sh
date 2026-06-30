#!/usr/bin/env bash
# Free-move range finder: disables torque so you can move all 6 SO101 joints by
# hand, shows live positions, and tracks raw min/max per servo. Move each joint
# through its FULL range, then Ctrl+C to print a copy-paste summary.
#
#   ./find_servo_ranges.sh
#
# The arm goes LIMP when torque releases — support it (the script waits for ENTER).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python "${HERE}/find_servo_ranges.py" "$@"
