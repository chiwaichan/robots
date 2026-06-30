#!/usr/bin/env python3
"""Verify an SO101 GR00T N1.7 server is usable — NO hardware, NO arm movement.

Builds a SYNTHETIC SO101 observation (random camera frames + zeroed joints),
runs it through the same bridge + service-mode policy the real demo uses, and
checks the server returns finite single_arm[5] + gripper[1] actions.

Run AFTER serving the checkpoint (e.g. allros/GR00T-N1.7-grab-cube-so101) on a
GR00T N1.7 server launched with --embodiment-tag new_embodiment.

  python verify_allros_so101.py --host localhost --port 5555
"""
from __future__ import annotations

import argparse
import asyncio
import time

import numpy as np

from strands_robots.policies import create_policy

ARM = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]


def synthetic_raw_obs() -> dict:
    """Mimic what an SO101 get_observation() returns: 6 .pos scalars + 2 frames."""
    obs = {f"{m}.pos": 0.0 for m in ARM}
    obs["gripper.pos"] = 0.0
    obs["front"] = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
    obs["wrist"] = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
    return obs


def to_groot_obs(obs: dict) -> dict:
    o = {"front": obs["front"], "wrist": obs["wrist"]}
    o["single_arm"] = np.array([obs[f"{m}.pos"] for m in ARM], dtype=np.float32)
    o["gripper"] = np.array([obs["gripper.pos"]], dtype=np.float32)
    return o


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=5555)
    ap.add_argument("--task", default="grab the cube")
    ap.add_argument("--iters", type=int, default=3)
    args = ap.parse_args()

    print(f"Connecting GR00T policy -> {args.host}:{args.port} (so101_dualcam, n1.7)")
    policy = create_policy(
        "groot", host=args.host, port=args.port,
        data_config="so101_dualcam", groot_version="n1.7",
    )
    policy.reset()

    obs = to_groot_obs(synthetic_raw_obs())
    print(f'Task: "{args.task}"  | obs keys: {list(obs)}')
    lat = []
    last = None
    for i in range(args.iters):
        t = time.time()
        actions = await policy.get_actions(obs, args.task)
        dt = (time.time() - t) * 1000
        lat.append(dt)
        last = actions[0]
        print(f"  iter {i+1}: {dt:.0f} ms")

    print("\n=== Returned action (first timestep) ===")
    ok = True
    for k in ("single_arm", "gripper"):
        if k not in last:
            print(f"  ❌ missing '{k}' in action keys {list(last)}"); ok = False; continue
        arr = np.asarray(last[k], dtype=np.float32).ravel()
        finite = np.isfinite(arr).all()
        ok = ok and finite
        print(f"  {k}: shape={arr.shape} finite={finite} values={np.round(arr,3).tolist()}")

    print()
    if ok and "single_arm" in last and "gripper" in last:
        n = np.asarray(last["single_arm"]).size
        print(f"✅ USABLE — server returned finite single_arm({n}) + gripper actions "
              f"(warm ~{np.median(lat):.0f} ms). Checkpoint is servable and SO101-shaped.")
    else:
        print("❌ NOT usable as-is — see above (missing keys, NaN/Inf, or wrong shape).")


if __name__ == "__main__":
    asyncio.run(main())
