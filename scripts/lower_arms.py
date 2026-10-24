#!/usr/bin/env python3
"""把 T-pose 的手臂烘焙成自然下垂（按权重混合，不会在肩部产生接缝）。

背景：生成的模型是严格 T-pose（双臂水平），而游戏的 AnimateActor 只把上臂
压 -35°/-42°，远不够——怪物看起来就是一直举着手。这里在**绑定姿势**里就把
手臂放下来，之后游戏的姿态驱动再叠加即可。

做法：绕肩点为轴、绕 Z 轴（模型横轴）旋转手臂顶点，旋转量按该顶点在
「上臂/前臂/手」几根骨骼上的权重之和做混合，所以肩关节处是平滑过渡。

用法: python lower_arms.py <in.mdl> <out.mdl> [角度=65]
"""
from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

import numpy as np

ESIZE = {0: 4, 1: 4, 2: 8, 3: 12, 4: 16, 5: 4, 6: 4}
W_OFF = 52
J_OFF = 68
# 手臂骨骼**按名字**分组（不同骨架顺序不同，写死索引是最常见的隐性错误）；
# 名字匹配不上时退回 28 骨默认索引并警告。
# ⚠️ **不要包含锁骨**：锁骨/肩部顶点跟着转会把肩线一起拧歪。
# 只动"上臂 → 前臂 → 手 → 指尖"这几节。
LEFT_ARM_NAMES = ("L_Upperarm", "L_Forearm", "L_Hand", "L_HandTip",
                  "upperarm_l", "lowerarm_l", "hand_l")
RIGHT_ARM_NAMES = ("R_Upperarm", "R_Forearm", "R_Hand", "R_HandTip",
                   "upperarm_r", "lowerarm_r", "hand_r")
UPPER_ALTS = {"L": ["L_Upperarm", "upperarm_l", "LeftArm"],
              "R": ["R_Upperarm", "upperarm_r", "RightArm"]}


def parse_mdl(path):
    d = bytearray(Path(path).read_bytes())
    p = 4
    nvb = struct.unpack_from("<I", d, p)[0]
    p += 4
    vcount = vstart = stride = 0
    for vi in range(nvb):
        vc = struct.unpack_from("<I", d, p)[0]
        p += 4
        ec = struct.unpack_from("<I", d, p)[0]
        p += 4
        st = 0
        for _ in range(ec):
            desc = struct.unpack_from("<I", d, p)[0]
            p += 4
            st += ESIZE.get(desc & 0xFF, 4)
        p += 8
        if vi == 0:
            vcount, vstart, stride = vc, p, st
        p += vc * st
    nib = struct.unpack_from("<I", d, p)[0]
    p += 4
    for _ in range(nib):
        ic, isz = struct.unpack_from("<II", d, p)
        p += 8 + ic * isz
    ng = struct.unpack_from("<I", d, p)[0]
    p += 4
    for _ in range(ng):
        bmc = struct.unpack_from("<I", d, p)[0]
        p += 4 + 4 * bmc
        nl = struct.unpack_from("<I", d, p)[0]
        p += 4 + 24 * nl
    p += 4
    nb = struct.unpack_from("<I", d, p)[0]
    p += 4
    bones = []
    for _ in range(nb):
        e = d.find(b"\x00", p)
        name = d[p:e].decode("ascii", "replace")
        b = e + 1
        off = np.array(struct.unpack_from("<12f", d, b + 44), dtype=float).reshape(3, 4)
        bones.append((name, off))
        p = b + 121
    return d, vcount, vstart, stride, bones


def world_of(off):
    m = np.eye(4)
    m[:3, :] = off
    return np.linalg.inv(m)[:3, 3]


def rot_z(pt, pivot, ang):
    c, s = math.cos(ang), math.sin(ang)
    v = pt - pivot
    return np.array([v[0] * c - v[1] * s, v[0] * s + v[1] * c, v[2]]) + pivot


def arm_sets(bones):
    """返回 (左手臂索引集合, 右手臂索引集合, 左上臂索引, 右上臂索引)。"""
    names = [b[0].lower() for b in bones]

    def pick(alts, default):
        for a in alts:
            for i, n in enumerate(names):
                if n == a.lower():
                    return i
        return default

    left = {i for i, n in enumerate(names)
            if any(n == a.lower() for a in LEFT_ARM_NAMES)}
    right = {i for i, n in enumerate(names)
             if any(n == a.lower() for a in RIGHT_ARM_NAMES)}
    if not left or not right:
        print("警告：手臂骨骼按名字匹配失败，退回 28 骨默认索引")
        left, right = {9, 10, 11, 12}, {14, 15, 16, 17}
    return left, right, pick(UPPER_ALTS["L"], 9), pick(UPPER_ALTS["R"], 14)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    deg = float(sys.argv[3]) if len(sys.argv) > 3 else 65.0
    d, vcount, vstart, stride, bones = parse_mdl(src)
    LEFT_ARM, RIGHT_ARM, IDX_L_UPPER, IDX_R_UPPER = arm_sets(bones)

    pL = world_of(bones[IDX_L_UPPER][1])
    pR = world_of(bones[IDX_R_UPPER][1])
    # 绕 Z 轴旋转方向：让手臂往 -Y（向下）走
    angL = -math.radians(deg) if pL[0] >= 0 else math.radians(deg)
    angR = -math.radians(deg) if pR[0] >= 0 else math.radians(deg)
    print("肩点 L=%s R=%s  角度 L=%.1f° R=%.1f°"
          % (["%+.3f" % v for v in pL], ["%+.3f" % v for v in pR],
             math.degrees(angL), math.degrees(angR)))

    touched = 0
    for i in range(vcount):
        base = vstart + i * stride
        w = struct.unpack_from("<4f", d, base + W_OFF)
        j = struct.unpack_from("<4B", d, base + J_OFF)
        wl = wr = 0.0
        for k in range(4):
            if j[k] in LEFT_ARM:
                wl += w[k]
            elif j[k] in RIGHT_ARM:
                wr += w[k]
        if wl < 1e-4 and wr < 1e-4:
            continue
        pos = np.array(struct.unpack_from("<3f", d, base))
        if wl > 1e-4:
            pos = rot_z(pos, pL, angL * min(1.0, wl))
        if wr > 1e-4:
            pos = rot_z(pos, pR, angR * min(1.0, wr))
        struct.pack_into("<3f", d, base, *pos)
        touched += 1

    print("手臂顶点处理 %d / %d" % (touched, vcount))
    Path(dst).write_bytes(bytes(d))
    print("WROTE %s" % dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
