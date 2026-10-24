#!/usr/bin/env python3
"""把 AI 模型的顶点接入现役骨架（含预蒙皮修正）。

═══ 问题背景（这一版才真正解决）═══
  现役基底 .mdl 的骨骼 `pos/rot` 链与 `offsetMatrix` **互不自洽**
  （Root 的 offset 并不是其世界矩阵的逆）。因此引擎算出的静止蒙皮矩阵

      A_i = BoneWorld_rest_i · offset_i      （≠ 单位矩阵）

  并不是恒等变换。现役模型的顶点本来就是按这个"偏差"制作的，所以显示正常；
  把别的模型的顶点直接塞进这套骨架，每个部位都会被各自的 A_i 拧到别处，
  表现就是：躯干撕裂、膝盖顶点被拉飞、头部转向 90°（看着像横着走）。

═══ 解决办法：预蒙皮 ═══
  写文件前，先用 A_i 的逆把顶点"反算"一遍：

      v' = Σ w_i · A_i⁻¹ · v

  于是静止时引擎算得  Σ w_i A_i v' = v  → **精确还原模型原样**；
  骨骼被驱动后 Pose_i = Δ_i · BoneWorld_rest_i · offset_i = Δ_i · A_i，
  代入得 Δ_i · v —— 顶点绕该骨骼原始位置转动，形变正确。

═══ 管线 ═══
  glb_to_urhox.py → reweight.py → lower_arms.py → retarget_to_live.py（本脚本）

用法: python retarget_to_live.py <my.mdl> <live.mdl> <out.mdl> [腿部对齐强度 0~1]
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

MY_BONES = [
    "Root", "Pelvis", "Spine01", "Spine02", "Spine03", "Neck", "Head", "HeadTop",
    "L_Clavicle", "L_Upperarm", "L_Forearm", "L_Hand", "L_HandTip",
    "R_Clavicle", "R_Upperarm", "R_Forearm", "R_Hand", "R_HandTip",
    "L_Thigh", "L_Calf", "L_Foot", "L_ToeBase", "L_ToeTip",
    "R_Thigh", "R_Calf", "R_Foot", "R_ToeBase", "R_ToeTip",
]

NAME_MAP = {
    "Root": "Root", "Pelvis": "Pelvis",
    "Spine01": "Spine01", "Spine02": "Spine02", "Spine03": "Spine02",
    "Neck": "NeckTwist02", "Head": "Head", "HeadTop": "Head",
    "L_Clavicle": "L_Clavicle", "L_Upperarm": "L_Upperarm",
    "L_Forearm": "L_Forearm", "L_Hand": "L_Hand", "L_HandTip": "L_Hand",
    "R_Clavicle": "R_Clavicle", "R_Upperarm": "R_Upperarm",
    "R_Forearm": "R_Forearm", "R_Hand": "R_Hand", "R_HandTip": "R_Hand",
    "L_Thigh": "L_Thigh", "L_Calf": "L_Calf", "L_Foot": "L_Foot",
    "L_ToeBase": "L_ToeBase", "L_ToeTip": "L_ToeBase",
    "R_Thigh": "R_Thigh", "R_Calf": "R_Calf", "R_Foot": "R_Foot",
    "R_ToeBase": "R_ToeBase", "R_ToeTip": "R_ToeBase",
    # mixamo / afk-rig 骨架比我们的 28 骨多出手指与脚掌小球（L_Middle1 中指节、
    # ball_leaf_l/r 脚掌球）。现役 41 骨**没有**这些细分骨，沿用"末端归父级"的
    # 既有策略：手指归手、脚掌球归脚（与 L_HandTip→L_Hand、L_ToeTip→L_ToeBase
    # 同一套路），否则 retarget 会以"映射缺失"直接中止。
    "L_Middle1": "L_Hand", "R_Middle1": "R_Hand",
    "L_Middle2": "L_Hand", "R_Middle2": "R_Hand",
    "L_Middle3": "L_Hand", "R_Middle3": "R_Hand",
    "L_Thumb1": "L_Hand", "R_Thumb1": "R_Hand",
    "L_Index1": "L_Hand", "R_Index1": "R_Hand",
    "ball_leaf_l": "L_Foot", "ball_leaf_r": "R_Foot",
}

LIVE_H = 0.998
# 腿部关节对齐强度（1.0 = 完全对齐到现役骨骼高度，0 = 不对齐）
LEG_BLEND = 0.0
ESIZE = {0: 4, 1: 4, 2: 8, 3: 12, 4: 16, 5: 4, 6: 4}
W_OFF = 52
J_OFF = 68


def qmat(q):
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


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
        pos = np.array(struct.unpack_from("<3f", d, b + 4), dtype=float)
        rot = np.array(struct.unpack_from("<4f", d, b + 16), dtype=float)
        off = np.array(struct.unpack_from("<12f", d, b + 44), dtype=float).reshape(3, 4)
        bones.append({"name": name, "par": par, "pos": pos, "rot": rot, "off": off})
        p = b + 121
    return d, vcount, vstart, stride, bones


def rest_world(bones):
    """按 pos/rot 链算静止世界变换（与引擎一致），返回 4x4 列表。"""
    nb = len(bones)
    out = [None] * nb
    for k in range(nb):
        chain = []
        cur = k
        seen = set()
        while cur not in seen:
            seen.add(cur)
            chain.append(cur)
            if bones[cur]["par"] == cur:
                break
            cur = bones[cur]["par"]
        M = np.eye(4)
        for node in reversed(chain):
            b = bones[node]
            L = np.eye(4)
            L[:3, :3] = qmat(b["rot"])
            L[:3, 3] = b["pos"]
            M = M @ L
        out[k] = M
    return out


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    my_path, live_path, out_path = (Path(a) for a in sys.argv[1:4])
    global LEG_BLEND, LIVE_H
    if len(sys.argv) > 4:
        LEG_BLEND = float(sys.argv[4])
    if len(sys.argv) > 5:
        # 目标身高（模型自身空间）。默认 0.998 是现役基底归一化后的身高；
        # 换成别的基底模型时必须改，否则整体缩放会错位。
        LIVE_H = float(sys.argv[5])
    my, vcount, vstart, stride, my_bones = parse_mdl(my_path)
    live_bytes, _, _, _, live_bones = parse_mdl(live_path)

    live_idx = {b["name"]: i for i, b in enumerate(live_bones)}
    remap = []
    for b in my_bones:
        t = NAME_MAP.get(b["name"])
        if t is None or t not in live_idx:
            raise SystemExit("映射缺失: %s" % b["name"])
        remap.append(live_idx[t])
    print("骨骼映射 %d 条" % len(remap))

    # ---- 1) 顶点归一化：身高缩放到现役基底尺度，脚底对齐 y=0 ----
    ys = [struct.unpack_from("<f", my, vstart + i * stride + 4)[0] for i in range(vcount)]
    my_min_y = min(ys)
    my_h = max(1e-6, max(ys) - my_min_y)
    scale = LIVE_H / my_h
    print("归一化：身高 %.4f -> %.4f（scale=%.5f）" % (my_h, LIVE_H, scale))

    # ---- 2) 腿部关节高度对齐（关键）----
    # 现役骨架的腿比 Q 版模型长：髋高 0.513 / 膝 0.265 / 踝 0.001，
    # 而模型缩放后是髋 0.384 / 膝 0.214 / 踝 0.041。骨骼在更高/更低的支点上
    # 摆动，脚就会脱离小腿，表现为「膝关节、踝关节严重错位」（左右腿在步态里
    # 交替显现，所以常常只注意到一侧）。
    # 做法：按「腿骨权重之和」把顶点的 y 做一次分段线性重映射，让模型的
    # 髋/膝/踝正好落在现役骨骼的高度上；权重为 0 的躯干完全不受影响，
    # 因为整体乘了腿权重，接缝是平滑的。
    bwm = rest_world(my_bones)
    bwl = rest_world(live_bones)
    my_idx = {b["name"]: i for i, b in enumerate(my_bones)}
    live_idx_by_name = {b["name"]: i for i, b in enumerate(live_bones)}

    def anchor_pair(name):
        ym = (float(bwm[my_idx[name]][1, 3]) - my_min_y) * scale
        yl = float(bwl[live_idx_by_name[NAME_MAP[name]]][1, 3])
        return ym, yl

    pairs = [anchor_pair("L_Thigh"), anchor_pair("R_Thigh"),
             anchor_pair("L_Calf"), anchor_pair("R_Calf"),
             anchor_pair("L_Foot"), anchor_pair("R_Foot")]
    hip_my = (pairs[0][0] + pairs[1][0]) / 2
    hip_live = (pairs[0][1] + pairs[1][1]) / 2
    knee_my = (pairs[2][0] + pairs[3][0]) / 2
    knee_live = (pairs[2][1] + pairs[3][1]) / 2
    ankle_my = (pairs[4][0] + pairs[5][0]) / 2
    ankle_live = (pairs[4][1] + pairs[5][1]) / 2
    # 第三个参数 legblend 控制对齐强度：1.0 全对齐（腿会明显变长），
    # 0.5 折中，0 等同不对齐。默认 1.0。
    k = LEG_BLEND
    anchors = [
        (0.0, 0.0),                                              # 脚底不动，保持贴地
        (ankle_my, (ankle_live - ankle_my) * k),                  # 踝
        (knee_my, (knee_live - knee_my) * k),                     # 膝
        (hip_my, (hip_live - hip_my) * k),                        # 髋
        (hip_my + 0.15, 0.0),                                     # 髋以上渐隐到 0
    ]
    print("腿部对齐锚点（模型高 -> 位移）：" +
          " ".join("%.3f→%+.3f" % (a, d) for a, d in anchors))

    def leg_delta(y):
        if y <= anchors[0][0]:
            return anchors[0][1]
        for i in range(len(anchors) - 1):
            y0, d0 = anchors[i]
            y1, d1 = anchors[i + 1]
            if y <= y1:
                t = (y - y0) / max(1e-6, y1 - y0)
                return d0 + (d1 - d0) * t
        return anchors[-1][1]

    leg_bones = {18, 19, 20, 21, 22, 23, 24, 25, 26, 27}  # 我的骨架里腿部骨骼索引

    # ---- 3) 预蒙皮矩阵 A_i⁻¹（A = BoneWorld_rest · offset）----
    # 注意：引擎的静止姿态是 LBS：v_rest = Σ w_i · A_i · v'。
    # 想让它精确等于作者意图 v，必须解 (Σ w_i · A_i) · v' = v，
    # 即 v' = (Σ w_i · A_i)⁻¹ · v —— 对**混合矩阵**求逆，
    # 而不是对每根骨骼分别求逆再线性组合（Σ w A 和 Σ w A⁻¹ 不互逆，
    # 关节过渡带上会留下 0.19 量级的错位，表现为腿被撕开一道缝）。
    bw_rest = rest_world(live_bones)
    A_list = []
    invA = []
    for li, lb in enumerate(live_bones):
        A = np.eye(4)
        A[:3, :] = lb["off"]
        A = bw_rest[li] @ A
        A_list.append(A)
        invA.append(np.linalg.inv(A))
    devs = []
    for li, lb in enumerate(live_bones):
        A = np.eye(4)
        A[:3, :] = lb["off"]
        devs.append(float(np.abs(np.eye(4) - bw_rest[li] @ A).max()))
    print("现役骨架静止蒙皮矩阵偏离单位阵：最大 %.3f（这就是必须预蒙皮的原因）" % max(devs))

    # ---- 4) 逐顶点：缩放 + 腿部 y 对齐 + 预蒙皮 ----
    ref = {}   # 静止还原校验用：预蒙皮之前的目标坐标
    for i in range(vcount):
        base = vstart + i * stride
        px, py, pz = struct.unpack_from("<3f", my, base)
        w = struct.unpack_from("<4f", my, base + W_OFF)
        j = struct.unpack_from("<4B", my, base + J_OFF)
        s = sum(w)
        sy = (py - my_min_y) * scale
        if s > 1e-6:
            wleg = 0.0
            for k in range(4):
                if j[k] in leg_bones:
                    wleg += w[k]
            if wleg > 1e-6:
                sy = sy + min(1.0, wleg) * leg_delta(sy)
        p = np.array([px * scale, sy, pz * scale])
        ref[i] = p
        if s <= 1e-6:
            struct.pack_into("<3f", my, base, *p)
            continue
        # 混合矩阵 M = Σ w_i · A_i，取逆得到「让静止姿态精确还原」的写入坐标
        M = np.zeros((4, 4))
        for k in range(4):
            if w[k] <= 1e-6:
                continue
            li = remap[j[k]] if j[k] < len(remap) else 0
            M += (w[k] / s) * A_list[li]
        M[3] = (0.0, 0.0, 0.0, 1.0)
        try:
            Minv = np.linalg.inv(M)
        except np.linalg.LinAlgError:
            Minv = np.eye(4)
        acc = Minv[:3, :3] @ p + Minv[:3, 3]
        T = Minv[:3, :3]
        struct.pack_into("<3f", my, base, *acc)
        # 法线要用位置变换矩阵的逆转置（= M 的 3x3 转置）；切线是方向量，同位置。
        NT = M[:3, :3].T
        nr = np.array(struct.unpack_from("<3f", my, base + 12))
        nn = NT @ nr
        ln = np.linalg.norm(nn)
        if ln > 1e-6:
            struct.pack_into("<3f", my, base + 12, *(nn / ln))
        tg = np.array(struct.unpack_from("<4f", my, base + 36))
        tn = T @ tg[:3]
        lt = np.linalg.norm(tn)
        if lt > 1e-6:
            struct.pack_into("<4f", my, base + 36, *(list(tn / lt) + [tg[3]]))
        newj = tuple(remap[jj] if jj < len(remap) else 0 for jj in j)
        struct.pack_into("<4B", my, base + J_OFF, *newj)

    # ---- 3.5) 静止还原校验 ----
    # 引擎的蒙皮是 LBS：v_rest = Σ w_i · A_i · v'。而 v' = Σ w_j · A_j⁻¹ · p。
    # 当同一顶点被多根骨骼共享、且这些骨骼的 A_i 互不相同时，两者**不可交换**，
    # 静止姿态就无法精确还原 p —— 关节处会被撕开一道缝。这里量化误差，
    # 超过阈值就打印出来（分布位置决定是哪个关节出问题）。
    worst = []
    for i in range(vcount):
        base = vstart + i * stride
        cur = np.array(struct.unpack_from("<3f", my, base))   # 预蒙皮后的坐标 v'
        w = struct.unpack_from("<4f", my, base + W_OFF)
        j = struct.unpack_from("<4B", my, base + J_OFF)
        s = sum(w)
        if s <= 1e-6 or i not in ref:
            continue
        rec = np.zeros(3)
        for k in range(4):
            if w[k] <= 1e-6:
                continue
            li = j[k]
            A = np.eye(4)
            A[:3, :] = live_bones[li]["off"]
            A = bw_rest[li] @ A
            rec += (w[k] / s) * (A[:3, :3] @ cur + A[:3, 3])
        worst.append((float(np.linalg.norm(rec - ref[i])), i))
    worst.sort(reverse=True)
    if worst:
        print("静止还原误差：最大 %.4f  平均 %.4f" %
              (worst[0][0], sum(e for e, _ in worst) / len(worst)))
        for e, i in worst[:6]:
            base = vstart + i * stride
            w = struct.unpack_from("<4f", my, base + W_OFF)
            j = struct.unpack_from("<4B", my, base + J_OFF)
            k = int(np.argmax(w))
            print("   v%-5d err=%.4f 位置=(%+.2f,%+.2f,%+.2f) 主骨=%-12s w=%s" %
                  (i, e, ref[i][0], ref[i][1], ref[i][2],
                   live_bones[j[k]]["name"], ["%.2f" % x for x in w]))

    # ---- 4) 拼接现役骨骼段 ----
    mo = my.find(b"Root") - 4
    lo = live_bytes.find(b"Root") - 4
    out = bytes(my[:mo]) + live_bytes[lo:]
    Path(out_path).write_bytes(out)
    nb = struct.unpack_from("<I", out, mo)[0]
    print("WROTE %s  骨骼数=%d  大小=%d" % (out_path, nb, len(out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
