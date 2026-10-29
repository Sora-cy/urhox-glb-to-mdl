#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把静物的蒙皮权重**全部归到一根骨骼**（默认 Pelvis）。

═══ 为什么需要 ═══
静物（棺材、雕像、路灯）走的是给人形角色设计的管线：afk-rig 强行按人形绑骨架，
retarget 再按"语义"把顶点映射到头/手/脚各骨骼。人形模型这样没问题，但**静物是
一整块**：它的顶点会被分散到人体各处骨骼上，蒙皮时每个顶点被拉向不同方向，
于是一口棺材被扭成麻花（本地 pose_sim 实测：整体弯折塌陷、表面褶皱交错）。
端到引擎里就是"要么畸形、要么干脆看不到"。

解法：静物**不需要形变**，整块刚体跟随一根骨骼就够了。所有顶点权重 = 1.0 归
Pelvis 后，蒙皮矩阵退化成单骨骼 A，而 retarget 的预蒙皮写的是 v' = A⁻¹·v，
引擎渲染时 v = A·v' = v —— **形状精确保持，且永远不会撕裂**。

用法:
    python prop_single_bone.py <in.mdl> <out.mdl> [骨骼名=Pelvis]
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

W_OFF = 52
J_OFF = 68


def parse(path):
    d = bytearray(Path(path).read_bytes())
    p = 4
    nvb = struct.unpack_from("<I", d, p)[0]
    p += 4
    vcount = vstart = stride = 0
    for vi in range(nvb):
        vc = struct.unpack_from("<I", d, p)[0]; p += 4
        ec = struct.unpack_from("<I", d, p)[0]; p += 4
        st = 0
        for _ in range(ec):
            desc = struct.unpack_from("<I", d, p)[0]; p += 4
            st += {0: 4, 1: 4, 2: 8, 3: 12, 4: 16, 5: 4, 6: 4}.get(desc & 0xFF, 4)
        p += 8
        if vi == 0:
            vcount, vstart, stride = vc, p, st
        p += vc * st
    nib = struct.unpack_from("<I", d, p)[0]; p += 4
    for _ in range(nib):
        ic, isz = struct.unpack_from("<II", d, p); p += 8 + ic * isz
    ng = struct.unpack_from("<I", d, p)[0]; p += 4
    for _ in range(ng):
        bmc = struct.unpack_from("<I", d, p)[0]; p += 4 + 4 * bmc
        nl = struct.unpack_from("<I", d, p)[0]; p += 4 + 24 * nl
    p += 4
    nb = struct.unpack_from("<I", d, p)[0]; p += 4
    bones = []
    for _ in range(nb):
        e = d.find(b"\x00", p)
        bones.append(d[p:e].decode("ascii", "replace"))
        p = e + 1 + 121
    return d, vcount, vstart, stride, bones


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    bone_name = sys.argv[3] if len(sys.argv) > 3 else "Pelvis"

    d, vcount, vstart, stride, bones = parse(src)
    # W_OFF / J_OFF 是硬编码的（52 / 68），来自本管线 mdl 的固定顶点布局：
    # POSITION(12) + NORMAL(12) + TEXCOORD(8) + 20 字节其它 + BLENDWEIGHTS(16) + BLENDINDICES(4)
    # 换基底/换管线后布局可能变，这里挡一道，免得静默写坏法线或 UV。
    if stride < 72:
        print("!! 顶点 stride=%d < 72，与硬编码的权重偏移(52/68)不匹配。"
              "先用 probe_mdl.py 确认布局再改 W_OFF/J_OFF。" % stride)
        return 1
    try:
        bi = bones.index(bone_name)
    except ValueError:
        print("!! 找不到骨骼 %s，现有：%s" % (bone_name, bones[:6]))
        return 1

    for i in range(vcount):
        base = vstart + i * stride
        struct.pack_into("<4f", d, base + W_OFF, 1.0, 0.0, 0.0, 0.0)
        struct.pack_into("<4B", d, base + J_OFF, bi, 0, 0, 0)
    dst.write_bytes(bytes(d))
    print("   权重全部归 %s（索引 %d），顶点 %d 个" % (bone_name, bi, vcount))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
