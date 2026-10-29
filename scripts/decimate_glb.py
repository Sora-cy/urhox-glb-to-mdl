#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLB 减面（顶点聚类），用于 AI 生成的「面数炸裂」模型。

═══ 为什么必须做在 GLB 阶段 ═══
3D 生成服务不保证面数：同一个服务，石棺 4.9k 三角，骨堆给 49.9 万（和 boss 同级）。
一件 1 米高的小道具 6 个实例就是 300 万三角，手机端必炸。

**不能直接改 .mdl 的顶点/索引数**：试过"删三角 + 压缩顶点表 + 重映射索引"，
本地 pose_sim 渲染完美，但引擎里整个模型不显示，引擎报的包围盒从 0.57×0.81
变成 0.19×0.44 —— mdl 尾部还有未完全解析的几何/LOD 记录，改计数就破坏它。
所以在 GLB 上减面，让 glb_to_urhox 用**新的**顶点/索引重新生成 mdl，一切自洽。

═══ 做法 ═══
顶点聚类（vertex clustering）：把空间切成网格，同一格的顶点并成一个，
三角形随之重连，退化三角（两个顶点落进同一格）丢弃。优点是拓扑无关、不会破洞，
缺点是把细节按格子粒度抹平 —— 对"石头/骨头/木头"这类有机形状很合适。

⚠️ JOINTS_0 / WEIGHTS_0 不做平均（会算错蒙皮），直接取簇代表顶点的那一份。
   本管线里 reweight.py 会按骨骼距离**重算**权重，所以这里粗一点无所谓。

用法:
    python scripts/decimate_glb.py in.glb out.glb --cells 240
    python scripts/decimate_glb.py in.glb out.glb --cells 240 --dry-run
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

import numpy as np

NUM_COMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
COMP_FMT = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
COMP_SIZE = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
ATTRS = ("POSITION", "NORMAL", "TEXCOORD_0", "JOINTS_0", "WEIGHTS_0")


def load_glb(path: Path):
    data = path.read_bytes()
    magic, _ver, _total = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise SystemExit("%s 不是 GLB" % path)
    js, bin_data = None, b""
    off = 12
    while off + 8 <= len(data):
        clen, ctype = struct.unpack_from("<I4s", data, off)
        body = data[off + 8: off + 8 + clen]
        if ctype == b"JSON":
            js = json.loads(body.decode("utf-8"))
        elif ctype == b"BIN\x00":
            bin_data = body
        off += 8 + clen + ((4 - clen % 4) % 4)
    return js, bin_data


def read_acc(js, bin_data, idx):
    acc = js["accessors"][idx]
    n = NUM_COMP[acc["type"]]
    fmt = COMP_FMT[acc["componentType"]]
    size = COMP_SIZE[acc["componentType"]]
    base = acc.get("byteOffset", 0)
    if "bufferView" in acc:
        bv = js["bufferViews"][acc["bufferView"]]
        base += bv.get("byteOffset", 0)
        stride = bv.get("byteStride", 0) or n * size
    else:
        stride = n * size
    out = np.zeros((acc["count"], n))
    for i in range(acc["count"]):
        out[i] = struct.unpack_from("<%d%s" % (n, fmt), bin_data, base + i * stride)
    return out


def pack_acc(arr, component_type):
    fmt = COMP_FMT[component_type]
    flat = np.asarray(arr).reshape(-1)
    if component_type == 5126:
        return struct.pack("<%df" % flat.size, *flat.astype(np.float32))
    ints = np.rint(flat).astype(np.int64)
    return struct.pack("<%d%s" % (ints.size, fmt), *ints.tolist())


def cluster(pos: np.ndarray, cells: int):
    """返回 (簇代表位置, 顶点的簇编号)。"""
    lo, hi = pos.min(axis=0), pos.max(axis=0)
    diag = float(np.linalg.norm(hi - lo)) or 1.0
    cell = diag / max(2, cells)
    keys = np.floor((pos - lo) / cell).astype(np.int64)
    # 三个维度压成一个整数键（范围远小于 2^21，安全）
    key = (keys[:, 0] * 1_000_003 + keys[:, 1]) * 1_000_003 + keys[:, 2]
    _uniq, inv, counts = np.unique(key, return_inverse=True, return_counts=True)
    n = len(_uniq)
    rep = np.zeros((n, 3))
    for d in range(3):
        rep[:, d] = np.bincount(inv, weights=pos[:, d], minlength=n) / counts
    return rep, inv, counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--cells", type=int, default=240,
                    help="网格分辨率：沿最长对角线切多少格（越大保留细节越多）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    js, bin_data = load_glb(Path(args.src))
    # 每个 bufferView 的原始字节。不原地改：GLB 里多个 accessor 常共用/交错同一个
    # bufferView（POSITION 和 NORMAL 挤在一起是常态），原地重写会踩到别人。
    # 改法是**新增独占 bufferView + 把 accessor 指过去**，最后统一剪掉没人引用的。
    views = []
    for bv in js["bufferViews"]:
        off = bv.get("byteOffset", 0)
        views.append(bytearray(bin_data[off: off + bv["byteLength"]]))

    def add_view(data: bytes) -> int:
        views.append(bytearray(data))
        js["bufferViews"].append({"buffer": 0, "byteLength": len(data)})
        return len(views) - 1

    total_before = total_after = 0
    for mesh in js.get("meshes", []):
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            if "POSITION" not in attrs:
                continue
            pos = read_acc(js, bin_data, attrs["POSITION"])
            idx = read_acc(js, bin_data, prim["indices"]).astype(np.int64).ravel() \
                if "indices" in prim else np.arange(len(pos), dtype=np.int64)
            tris = idx.reshape(-1, 3)
            total_before += len(tris)

            rep, inv, counts = cluster(pos, args.cells)
            tris2 = inv[tris]
            keep = (tris2[:, 0] != tris2[:, 1]) & (tris2[:, 1] != tris2[:, 2]) & (tris2[:, 0] != tris2[:, 2])
            tris2 = tris2[keep]
            total_after += len(tris2)

            # 每个簇挑一个代表顶点，用来搬 JOINTS/WEIGHTS（不平均，避免权重算错）
            first = np.full(len(rep), -1, dtype=np.int64)
            seen = np.zeros(len(rep), dtype=bool)
            for vi in np.argsort(inv, kind="stable"):
                c = inv[vi]
                if not seen[c]:
                    seen[c] = True
                    first[c] = vi

            for name in ATTRS:
                if name not in attrs:
                    continue
                acc = js["accessors"][attrs[name]]
                old = read_acc(js, bin_data, attrs[name])
                if name == "POSITION":
                    new = rep
                elif name == "NORMAL":
                    # 法线用簇内平均再归一化：直接取代表顶点的话，平均位置后光照会花
                    acc_n = np.zeros((len(rep), 3))
                    for d in range(3):
                        acc_n[:, d] = np.bincount(inv, weights=old[:, d], minlength=len(rep))
                    ln = np.linalg.norm(acc_n, axis=1, keepdims=True)
                    new = np.where(ln > 1e-6, acc_n / np.maximum(ln, 1e-9),
                                   np.array([[0.0, 1.0, 0.0]]))
                elif name == "TEXCOORD_0":
                    uv = np.zeros((len(rep), old.shape[1]))
                    for d in range(old.shape[1]):
                        uv[:, d] = np.bincount(inv, weights=old[:, d], minlength=len(rep)) / counts
                    new = uv
                else:                      # JOINTS_0 / WEIGHTS_0：取代表顶点
                    new = old[first]
                acc["bufferView"] = add_view(pack_acc(new, acc["componentType"]))
                acc["byteOffset"] = 0
                acc["count"] = int(len(new))

            i_acc = js["accessors"][prim["indices"]]
            flat = tris2.ravel()
            if i_acc["componentType"] == 5123 and flat.max() > 65535:
                raise SystemExit("索引超出 uint16，需要改成 5125")
            i_acc["bufferView"] = add_view(pack_acc(flat, i_acc["componentType"]))
            i_acc["byteOffset"] = 0
            i_acc["count"] = int(flat.size)

    print("三角 %d → %d（-%.1f%%），簇 %d" % (
        total_before, total_after, 100.0 * (1 - total_after / max(1, total_before)), len(rep)))
    if args.dry_run:
        return 0
    if total_after == 0:
        raise SystemExit("减面后没有三角了，把 --cells 调大")

    # 剪掉没人引用的 bufferView（就是被替换掉的原始顶点/索引数据），并重映射索引
    used = set()
    for a in js["accessors"]:
        if "bufferView" in a:
            used.add(a["bufferView"])
    for im in js.get("images", []):
        if "bufferView" in im:
            used.add(im["bufferView"])
    keep_idx = sorted(used)
    remap = {old: new for new, old in enumerate(keep_idx)}
    for a in js["accessors"]:
        if "bufferView" in a:
            a["bufferView"] = remap[a["bufferView"]]
    for im in js.get("images", []):
        if "bufferView" in im:
            im["bufferView"] = remap[im["bufferView"]]
    dropped = len(views) - len(keep_idx)
    js["bufferViews"] = [js["bufferViews"][i] for i in keep_idx]
    views = [views[i] for i in keep_idx]
    print("剪掉 %d 个无引用的 bufferView" % dropped)

    # 重建 BIN：按 bufferView 索引顺序拼接，4 字节对齐
    out = bytearray()
    for i, bv in enumerate(js["bufferViews"]):
        chunk = views[i]
        pad = (4 - len(out) % 4) % 4
        if pad:
            out += b"\x00" * pad
        bv["byteOffset"] = len(out)
        bv["byteLength"] = len(chunk)
        out += chunk
    if js.get("buffers"):
        js["buffers"][0]["byteLength"] = len(out)

    jbytes = json.dumps(js, separators=(",", ":")).encode("utf-8")
    jbytes += b" " * ((4 - len(jbytes) % 4) % 4)
    out += b"\x00" * ((4 - len(out) % 4) % 4)
    total = 12 + 8 + len(jbytes) + 8 + len(out)
    Path(args.dst).write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<I4s", len(jbytes), b"JSON") + jbytes
        + struct.pack("<I4s", len(out), b"BIN\x00") + out)
    print("写出 %s（%.1f MB）" % (Path(args.dst).name, total / 1048576))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
