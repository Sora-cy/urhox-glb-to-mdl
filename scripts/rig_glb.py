#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用 afk-rig 给**无骨骼 GLB** 绑一套人形骨骼并导出带骨骼的 GLB。

用法:
    python scripts/rig_glb.py model.glb                 # 产出 model_rigged.glb
    python scripts/rig_glb.py model.glb --out rig.glb --no-anim

固定四步（顺序不能变）：
    start   --skeleton human --hands single-bone
    inspect --issues          （只看不写，用来确认骨架没问题）
    bind
    export  --animations Walk,Idle_A --ground

**--hands single-bone 是必须的**：不加会生成 40 根手指骨，
后续重定向/权重全乱，而且大多数游戏驱动表根本没有手指骨。

Windows 注意：npx 实际是 npx.cmd，subprocess 直接调 "npx" 会 FileNotFoundError。
"""
import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd, timeout=900):
    cmd = list(cmd)
    if cmd and cmd[0] in ("npx", "npm"):
        cmd[0] = cmd[0] + ".cmd"          # Windows
    print("$ %s" % " ".join(cmd), flush=True)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-12:]
    print("\n".join(tail), flush=True)
    if p.returncode != 0:
        raise SystemExit("afk-rig 步骤失败：%s" % " ".join(cmd[2:4]))
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("glb", help="输入 GLB（不需要自带骨骼）")
    ap.add_argument("--out", default=None, help="输出 GLB，默认 <输入名>_rigged.glb")
    ap.add_argument("--skeleton", default="human")
    ap.add_argument("--animations", default="Walk,Idle_A")
    ap.add_argument("--no-anim", action="store_true", help="不导出动画")
    args = ap.parse_args()

    src = Path(args.glb)
    if not src.exists():
        raise SystemExit("找不到输入：%s" % src)
    out = Path(args.out) if args.out else src.with_name(src.stem + "_rigged.glb")
    cfg = src.with_name(src.stem + ".afk-rig.json")

    run(["npx", "-y", "afk-rig", "start", str(src),
         "--skeleton", args.skeleton, "--hands", "single-bone"])
    run(["npx", "-y", "afk-rig", "inspect", str(cfg), "--issues"])
    run(["npx", "-y", "afk-rig", "bind", str(cfg)])
    cmd = ["npx", "-y", "afk-rig", "export", str(cfg), "--out", str(out)]
    if not args.no_anim:
        cmd += ["--animations", args.animations, "--ground"]
    run(cmd)

    if not out.exists():
        raise SystemExit("导出失败：%s 不存在" % out)
    print("\n完成 → %s（%.0f KB）" % (out, out.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
