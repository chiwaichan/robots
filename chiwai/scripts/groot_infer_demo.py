#!/usr/bin/env python3
"""Run a GR00T N1.7 inference against the live server and PRINT the output.

Unlike tests_integ/groot/test_n17_live_server.py (which only asserts), this
prints every action the model returns, its shape, a sample of values, and the
per-call latency. Good for eyeballing what the model actually emits.

Prereqs:
  - A GR00T N1.7 server running (REAL_G1) and reachable, e.g. localhost:5555.
  - Run from the strands-robots repo so `strands_robots` imports work.

Usage:
  cd ~/repos/robots
  python ~/groot_infer_demo.py
  python ~/groot_infer_demo.py --host localhost --port 5555 --iters 5 \
         --prompt "pick up the red block"

Env (alternative to flags): GROOT_SERVER_HOST, GROOT_SERVER_PORT.
"""
from __future__ import annotations

import argparse
import os
import time

import numpy as np

from strands_robots.policies.groot.client import Gr00tInferenceClient


def build_real_g1_obs(prompt: str) -> dict:
    """A valid REAL_G1 observation (random video + plausible state + a task string).

    Shapes mirror the bundled live test. Video is random pixels — fine for a
    smoke/inference demo; swap in real camera frames (uint8 HxWx3, resized to
    256x256) to see perception-driven outputs.
    """
    identity_rot6d = np.array([1.0, 0.0, 0.0, 0.0, 1.0, 0.0], dtype=np.float32)
    left_eef = np.concatenate([[0.15, 0.25, 0.2], identity_rot6d]).astype(np.float32)
    right_eef = np.concatenate([[0.15, -0.25, 0.2], identity_rot6d]).astype(np.float32)
    video = np.random.randint(0, 255, (1, 2, 256, 256, 3), dtype=np.uint8)
    return {
        "video": {"ego_view": video},
        "state": {
            "left_wrist_eef_9d": left_eef[np.newaxis, np.newaxis, :],
            "right_wrist_eef_9d": right_eef[np.newaxis, np.newaxis, :],
            "left_hand": np.zeros((1, 1, 7), dtype=np.float32) + 0.01,
            "right_hand": np.zeros((1, 1, 7), dtype=np.float32) + 0.01,
            "left_arm": np.array([[[0.1, -0.3, 0.0, 1.0, 0.0, 0.0, 0.0]]], dtype=np.float32),
            "right_arm": np.array([[[0.1, 0.3, 0.0, 1.0, 0.0, 0.0, 0.0]]], dtype=np.float32),
            "waist": np.zeros((1, 1, 3), dtype=np.float32),
        },
        "language": {"annotation.human.task_description": [[prompt]]},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default=os.environ.get("GROOT_SERVER_HOST", "localhost"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("GROOT_SERVER_PORT", "5555")))
    ap.add_argument("--iters", type=int, default=3, help="how many inferences to run")
    ap.add_argument("--prompt", default="pick up the object", help="language instruction")
    ap.add_argument("--full", action="store_true", help="print full arrays, not just a sample")
    args = ap.parse_args()

    print(f"Connecting to GR00T N1.7 server at {args.host}:{args.port} ...")
    client = Gr00tInferenceClient(host=args.host, port=args.port, timeout_ms=60_000)

    if not client.ping():
        raise SystemExit("ping failed — is the server up and reachable on that host/port?")
    print("ping OK\n")

    # Show what the server expects/produces (modalities).
    cfg = client.call_endpoint("get_modality_config")
    print("Server modality config:")
    for k, c in cfg.items():
        print(f"  {k:8s} keys={getattr(c, 'modality_keys', '?')} delta={getattr(c, 'delta_indices', '?')}")
    print()

    obs = build_real_g1_obs(args.prompt)
    print(f'Prompt: "{args.prompt}"')
    print(f"Running {args.iters} inference(s)...\n")

    latencies = []
    last = None
    for i in range(args.iters):
        t0 = time.perf_counter()
        actions = client.get_action(obs)
        dt = (time.perf_counter() - t0) * 1000
        latencies.append(dt)
        last = actions
        print(f"  iter {i+1}/{args.iters}: {dt:7.0f} ms")

    print("\n=== Action output (last inference) ===")
    for key, arr in last.items():
        a = np.asarray(arr)
        finite = "finite" if np.isfinite(a).all() else "!! HAS NaN/Inf !!"
        print(f"\n{key}: shape={a.shape} dtype={a.dtype}  [{finite}]")
        flat = a.reshape(-1)
        if args.full:
            print(a)
        else:
            head = ", ".join(f"{v:.4f}" for v in flat[:8])
            print(f"  first values: [{head}{' ...' if flat.size > 8 else ''}]")
            print(f"  min={flat.min():.4f}  max={flat.max():.4f}  mean={flat.mean():.4f}")

    warm = latencies[1:] or latencies  # drop cold first call if we have >1
    print("\n=== Latency ===")
    print(f"  cold (1st): {latencies[0]:.0f} ms")
    if len(latencies) > 1:
        print(f"  warm avg  : {sum(warm)/len(warm):.0f} ms  (min {min(warm):.0f}, max {max(warm):.0f})")
    print("\nDone.")


if __name__ == "__main__":
    main()
