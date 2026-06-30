#!/usr/bin/env python3
"""Definitive usability probe for the allros SO101 N1.7 checkpoint.

Talks to the running GR00T N1.7 server with the LOW-LEVEL client in the nested
wire format the run_gr00t_server entrypoint expects (the same format
groot_infer_demo used for REAL_G1) — bypassing the Gr00tPolicy flat-key path,
which targets legacy servers. No hardware. Synthetic SO101 obs.
"""
from __future__ import annotations
import argparse
import time
import numpy as np
from strands_robots.policies.groot.client import Gr00tInferenceClient


def build_obs(T_video: int, hw: int) -> dict:
    # nested: video.{front,wrist}=(B,T,H,W,C) uint8 ; state.{single_arm,gripper}=(B,T,D) f32
    vid = lambda: np.random.randint(0, 255, (1, T_video, hw, hw, 3), dtype=np.uint8)
    return {
        "video": {"front": vid(), "wrist": vid()},
        "state": {
            "single_arm": np.zeros((1, 1, 5), dtype=np.float32),
            "gripper": np.zeros((1, 1, 1), dtype=np.float32),
        },
        "language": {"annotation.human.task_description": [["grab the cube"]]},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=5555)
    ap.add_argument("--hw", type=int, default=256)
    args = ap.parse_args()

    c = Gr00tInferenceClient(host=args.host, port=args.port, timeout_ms=60_000)
    print("ping:", c.ping())
    print("modality config:", {k: getattr(v, "modality_keys", v)
                               for k, v in c.call_endpoint("get_modality_config").items()})

    last = None
    for T in (1, 2):                       # try single then 2-frame temporal window
        try:
            obs = build_obs(T, args.hw)
            t = time.time()
            actions = c.get_action(obs)
            dt = (time.time() - t) * 1000
            print(f"\n✅ get_action OK with video T={T}  ({dt:.0f} ms)")
            last = actions
            break
        except Exception as e:
            print(f"  T={T} failed: {str(e)[:160]}")

    if last is None:
        print("\n❌ NOT usable — server rejected all obs formats (see errors above).")
        return

    print("=== action keys returned ===")
    ok = True
    for k, v in last.items():
        a = np.asarray(v, dtype=np.float32)
        fin = np.isfinite(a).all()
        ok = ok and fin
        print(f"  {k}: shape={a.shape} finite={fin} sample={np.round(a.ravel()[:6],3).tolist()}")
    has = any("single_arm" in k for k in last) and any("gripper" in k for k in last)
    print()
    print("✅ USABLE — checkpoint loads, serves, and returns finite SO101 (single_arm+gripper) actions."
          if (ok and has) else
          "⚠️ Returned actions but not the expected single_arm+gripper keys — inspect above.")


if __name__ == "__main__":
    main()
