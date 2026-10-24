#!/usr/bin/env python3
"""重新计算蒙皮权重（按骨骼线段距离），替换掉质量不佳的自动蒙皮。

为什么需要：
  afk-rig 的自动权重把约 41% 的权重全给了 spine_03，双腿合计只有约 9%，
  且左右不对称（L_Calf/L_Foot 几乎为 0）。表现出来就是"怪物基本不动，
  只有左脚有点动作"。

算法（经典的线段距离权重）：
  1. 从 offsetMatrix 求逆得到每根骨骼的绑定世界位置；
  2. 每根骨骼的"影响线段" = 自身关节位置 → 子关节平均位置（叶骨用一个短桩）；
  3. 顶点到线段的距离 d，取最近的 4 根骨骼，权重 ∝ 1/(d+eps)^p，归一化。

用法: python reweight.py <in.mdl> <out.mdl> [p]
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

ESIZE = {0: 4, 1: 4, 2: 8, 3: 12, 4: 16, 5: 4, 6: 4}
W_OFF = 52
J_OFF = 68


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
        par = struct.unpack_from("<i", d, b)[0]
        off = np.array(struct.unpack_from("<12f", d, b + 44), dtype=float).reshape(3, 4)
        bones.append((name, par, off))
        p = b + 121
    return d, vcount, vstart, stride, bones


def find_foot_bones(bones):
    """按名字定位左右脚骨，失败退回 28 骨默认索引并警告。"""
    names = [b[0] for b in bones]
    cand = {
        "L_Foot": ["L_Foot", "foot_l", "LeftFoot", "LeftAnkle"],
        "R_Foot": ["R_Foot", "foot_r", "RightFoot", "RightAnkle"],
    }
    out = {}
    for key, alts in cand.items():
        idx = None
        for a in alts:
            for i, n in enumerate(names):
                if n.lower() == a.lower():
                    idx = i
                    break
            if idx is not None:
                break
        out[key] = idx
    if out["L_Foot"] is None or out["R_Foot"] is None:
        print("警告：找不到 L_Foot/R_Foot，退回默认索引 20/25（骨架顺序可能不匹配）")
        return 20, 25
    return out["L_Foot"], out["R_Foot"]


def seg_dist(pt, a, b):
    ab = b - a
    dd = float(ab @ ab)
    if dd < 1e-12:
        return float(np.linalg.norm(pt - a))
    t = float((pt - a) @ ab) / dd
    t = max(0.0, min(1.0, t))
    return float(np.linalg.norm(pt - (a + t * ab)))


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    power = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
    d, vcount, vstart, stride, bones = parse_mdl(src)
    nb = len(bones)

    # 绑定世界位置 = inverse(offsetMatrix) 的平移列
    world = []
    for name, par, off in bones:
        m = np.eye(4)
        m[:3, :] = off
        inv = np.linalg.inv(m)
        world.append(inv[:3, 3])
    world = np.array(world)

    # 骨骼线段：自身 -> 子关节均值（叶骨用朝父方向的短桩）
    children = [[] for _ in range(nb)]
    for i, (name, par, off) in enumerate(bones):
        if par != i and 0 <= par < nb:
            children[par].append(i)
    head_h = float(np.linalg.norm(world.max(axis=0) - world.min(axis=0))) or 1.0
    stub = head_h * 0.06
    segs = []
    for i in range(nb):
        a = world[i]
        if children[i]:
            b = world[children[i]].mean(axis=0)
            if np.linalg.norm(b - a) < 1e-4:
                b = a + np.array([0.0, stub, 0.0])
        else:
            par = bones[i][1]
            if par != i and 0 <= par < nb:
                dirv = a - world[par]
                ln = np.linalg.norm(dirv)
                dirv = dirv / ln if ln > 1e-6 else np.array([0.0, 1.0, 0.0])
                b = a + dirv * stub
            else:
                b = a + np.array([0.0, stub, 0.0])
        segs.append((a, b))

    # 逐顶点重算
    # ⚠️ 脚掌必须单独兜底：踝关节正好是「小腿段终点 = 脚段起点」，两者的线段距离
    # 几乎相等，靠 1/d^p 决胜会随机把脚掌判给小腿（实测右脚 157 个顶点一路到脚底
    # 都归了 R_Calf，左右还不对称：L_Foot 150 vs R_Foot 97）。小腿一摆就把脚掌
    # 拽成一片薄面，表现为「右脚踝严重错位、脚掌没厚度」。
    # 这里用一条确定性的规则覆盖：踝以下整体归脚骨，踝附近用 band 做平滑过渡。
    #
    # 骨骼索引**按名字查找**（不同骨架顺序不同，写死索引是最常见的隐性错误）；
    # 找不到时退回 28 骨默认序（L_Foot=20 / R_Foot=25），并打印警告。
    IDX_L_FOOT, IDX_R_FOOT = find_foot_bones(bones)
    ankle_y = float(world[IDX_L_FOOT][1])
    band = 0.035
    overridden = 0
    tot = np.zeros(nb)
    for i in range(vcount):
        base = vstart + i * stride
        pos = np.array(struct.unpack_from("<3f", d, base))
        ds = np.array([seg_dist(pos, a, b) for a, b in segs])
        eps = 1e-4
        w = 1.0 / np.power(ds + eps, power)
        idx = np.argsort(-w)[:4]
        ws = w[idx]
        ws = ws / ws.sum()
        keep = ws > 0.02
        if not keep.any():
            keep[0] = True
        idx = idx[keep]
        ws = ws[keep]
        ws = ws / ws.sum()
        # 脚掌兜底：踝以下（含过渡带）按 x 符号强制归同侧脚骨
        if pos[1] < ankle_y + band:
            foot = IDX_L_FOOT if pos[0] >= 0 else IDX_R_FOOT
            t = (pos[1] - (ankle_y - band)) / (2 * band)   # 0=踝下 1=踝上
            t = max(0.0, min(1.0, t))
            wf = 1.0 - t
            if wf > 0.001:
                idx = list(idx)
                ws = list(ws * t)
                if foot in idx:
                    ws[idx.index(foot)] += wf
                else:
                    idx.append(foot)
                    ws.append(wf)
                # 重新排序并裁到 4 个
                order = sorted(range(len(idx)), key=lambda k: -ws[k])[:4]
                idx = [idx[k] for k in order]
                ws = [ws[k] for k in order]
                ssum = sum(ws) or 1.0
                ws = [x / ssum for x in ws]
                overridden += 1
        idx = np.array(idx, dtype=int)
        ws = np.array(ws)
        struct.pack_into("<4f", d, base + W_OFF, *(list(ws) + [0.0] * (4 - len(ws))))
        struct.pack_into("<4B", d, base + J_OFF, *(list(idx) + [0] * (4 - len(idx))))
        tot[idx] += ws

    print("脚掌权重兜底：%d 个顶点（踝 y=%.3f，过渡带 ±%.3f）" % (overridden, ankle_y, band))

    print("重新计算权重完成  p=%.1f  顶点=%d" % (power, vcount))
    print("各骨骼权重占比:")
    s = tot.sum() or 1.0
    order = np.argsort(-tot)
    for i in order:
        pct = tot[i] / s * 100
        if pct >= 0.8:
            print("   %-12s %5.1f%%" % (bones[i][0], pct))
    Path(dst).write_bytes(bytes(d))
    print("WROTE %s" % dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
