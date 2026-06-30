#!/usr/bin/env bash
# Run a GR00T N1.7 inference against the live server and print the output.
# Wrapper around ~/groot_infer_demo.py — handles repo cd, server check, and
# (re)starting the container/server if it's down. Pass-through args go to the
# python script, e.g.:
#   ./run_groot_infer.sh --iters 5 --prompt "pick up the red block"
#   ./run_groot_infer.sh --full
set -euo pipefail

REPO="${ROBOTS_REPO:-$HOME/repos/robots}"
HOST="${GROOT_SERVER_HOST:-localhost}"
PORT="${GROOT_SERVER_PORT:-5555}"
CONTAINER="${GROOT_CONTAINER:-gr00t}"
CKPT="${GROOT_CKPT:-/tmp/gr00t-ckpt/nvidia__GR00T-N1.7-3B}"
EMBODIMENT="${GROOT_EMBODIMENT:-REAL_G1}"

server_up() { timeout 4 bash -c "cat < /dev/null > /dev/tcp/$HOST/$PORT" 2>/dev/null; }

if server_up; then
  echo "✓ GR00T server reachable at $HOST:$PORT"
else
  echo "✗ Server not reachable at $HOST:$PORT — attempting to bring it up..."

  # Start the container if it isn't running (only meaningful for localhost).
  if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
      echo "  starting existing container '$CONTAINER'..."
      docker start "$CONTAINER" >/dev/null
    else
      echo "  creating container '$CONTAINER' from gr00t:thor..."
      docker run -d --runtime nvidia --ipc=host --name "$CONTAINER" \
        -v "$CKPT:/data/checkpoints" \
        -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
        -p "$PORT:5555" gr00t:thor tail -f /dev/null >/dev/null
    fi
  fi

  # Start the inference server inside the container if not already running.
  if ! docker exec "$CONTAINER" pgrep -f run_gr00t_server >/dev/null 2>&1; then
    echo "  launching N1.7 server ($EMBODIMENT) inside container..."
    docker exec -d "$CONTAINER" bash -lc \
      "cd /workspace/gr00t && python -u -m gr00t.eval.run_gr00t_server \
        --model-path /data/checkpoints --port 5555 --host 0.0.0.0 \
        --embodiment-tag $EMBODIMENT > /tmp/srv.log 2>&1"
  fi

  echo -n "  waiting for server to load model"
  for _ in $(seq 1 60); do          # up to ~2 min
    if server_up; then echo " — up!"; break; fi
    echo -n "."; sleep 2
  done
  if ! server_up; then
    echo
    echo "ERROR: server still not reachable. Last log lines:"
    docker exec "$CONTAINER" tail -15 /tmp/srv.log 2>/dev/null || true
    exit 1
  fi
fi

echo
cd "$REPO"
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
exec env GROOT_SERVER_HOST="$HOST" GROOT_SERVER_PORT="$PORT" \
  python "$HERE/groot_infer_demo.py" "$@"
