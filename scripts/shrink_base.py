#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把模型底部的「展示底板」顶点向中轴收缩，使它在视觉上消失。

═══ 为什么不直接删三角 ═══
先试过 `trim_base.py`：删掉底部三角 + 压缩顶点表 + 重映射索引。
`pose_sim` 渲染完全正常，但**引擎里整个模型不显示**（引擎报出的包围盒也从
0.569×0.814 变成 0.189×0.439，说明引擎按自己的规则解析出了别的东西）。
mdl 尾部还有 10 字节未完全解析的字段（几何/LOD 记录），改动顶点数与索引数
很可能破坏了它。所以换成**不动结构、只挪顶点**的做法：

    底部区域(y < cut)的顶点，把它的水平半径夹到 `--radius` 以内。

底板从 0.5 见方缩成树干底部一个小圆盘（被树干挡住），三角数、索引数、顶点数
全部不变，尾部数据依然有效。树干本身的顶点半径本来就小于该值，不受影响。

用法:
    python scripts/shrink_base.py in.mdl out.mdl [--ratio 0.05] [--radius 0.06]
"""
from __future__ import annotations

import argparse
import math
import struct
from pathlib import Path

import numpy as np

ELEM_SIZE = {0: 12, 1: 12, 2: 8, 3: 12, 4: 16, 5: 4, 6: 4}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--ratio", type=float, default=0.05,
                    help="作用区间：最低点以上 ratio×总高（默认 0.05）")
    ap.add_argument("--radius", type=float, default=0.06,
                    help="底部顶点的最大水平半径（模型空间，默认 0.06）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    d = bytearray(Path(args.src).read_bytes())
    p = 4
    nvb = struct.unpack_from("<I", d, p)[0]
    p += 4
    vcount = stride = vstart = 0
    for vi in range(nvb):
        cnt = struct.unpack_from("<I", d, p)[0]
        p += 4
        ec = struct.unpack_from("<I", d, p)[0]
        p += 4
        s = 0
        for _ in range(ec):
            q = struct.unpack_from("<I", d, p)[0]
            p += 4
            s += ELEM_SIZE.get(q & 0xFF, 4)
        p += 8
        if vi == 0:
            vcount, stride, vstart = cnt, s, p
        p += cnt * s

    pos = np.array([struct.unpack_from("<3f", d, vstart + i * stride)
                    for i in range(vcount)], dtype=np.float64)
    ymin, ymax = pos[:, 1].min(), pos[:, 1].max()
    cut = ymin + (ymax - ymin) * args.ratio

    # 中轴取"切线上方那圈顶点"的水平重心 —— 就是树干底部，比全模型重心准
    base = pos[(pos[:, 1] >= cut) & (pos[:, 1] < cut + (ymax - ymin) * 0.05)]
    cx, cz = (base[:, 0].mean(), base[:, 2].mean()) if len(base) else (0.0, 0.0)

    sel = np.nonzero(pos[:, 1] < cut)[0]
    r = np.hypot(pos[sel, 0] - cx, pos[sel, 2] - cz)
    far = sel[r > args.radius]
    print("顶点 %d  切线上 %.4f  中轴 (%.3f, %.3f)" % (vcount, cut, cx, cz))
    print("底部顶点 %d 个，其中半径 > %.3f 的 %d 个将被收缩"
          % (len(sel), args.radius, len(far)))

    if args.dry_run:
        print("（dry-run，未写文件）")
        return 0

    moved = 0
    for i in far:
        dx, dz = pos[i, 0] - cx, pos[i, 2] - cz
        ln = math.hypot(dx, dz)
        if ln < 1e-6:
            continue
        nx = cx + dx / ln * args.radius
        nz = cz + dz / ln * args.radius
        struct.pack_into("<3f", d, vstart + i * stride, nx, float(pos[i, 1]), nz)
        moved += 1
    Path(args.dst).write_bytes(bytes(d))
    print("写出 %s，收缩 %d 个顶点（三角/索引/顶点数均未变）"
          % (Path(args.dst).name, moved))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
