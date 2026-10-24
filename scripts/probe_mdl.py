#!/usr/bin/env python3
"""解析 UrhoX/Urho3D .mdl (UMD2)，打印完整结构（顶点/索引/几何/骨骼）。

已验证的布局（对照现役写实基底 mdl 样本）：
  "UMD2"
  u32 numVertexBuffers
    per vb: u32 vertexCount, u32 elementCount, u32[elementCount] elements,
            u32 morphRangeStart, u32 morphRangeCount, vertex data
  u32 numIndexBuffers
    per ib: u32 indexCount, u32 indexSize, index data
  u32 numGeometries
    per geom: u32 boneMappingCount, u32[count] mappings, u32 numLodLevels,
              per lod: f32 lodDistance, u32 primType, u32 vbRef, u32 ibRef,
                           u32 indexStart, u32 indexCount
  u32 numMorphs
  u32 numBones
    per bone: null-terminated name, u32 parentIndex, vec3 pos, quat rot,
              vec3 scale, mat3x4 offset(48B), f32 radius, bbox(24B), u8 animated

用法: python mdl_struct.py <file.mdl>
"""
from __future__ import annotations
import struct
import sys
from pathlib import Path

TYPE_INT, TYPE_FLOAT, TYPE_VECTOR2, TYPE_VECTOR3, TYPE_VECTOR4 = 0, 1, 2, 3, 4
TYPE_UBYTE4, TYPE_UBYTE4_NORM = 5, 6
SEM = {0: "POSITION", 1: "NORMAL", 2: "BINORMAL", 3: "TANGENT",
        4: "TEXCOORD", 5: "COLOR", 6: "BLENDWEIGHTS", 7: "BLENDINDICES",
        8: "LIGHTMAPUV", 9: "OBJECTINDEX"}
ESIZE = {TYPE_INT: 4, TYPE_FLOAT: 4, TYPE_VECTOR2: 8, TYPE_VECTOR3: 12,
        TYPE_VECTOR4: 16, TYPE_UBYTE4: 4, TYPE_UBYTE4_NORM: 4}
BONE_FIXED = 4 + 12 + 16 + 12 + 48 + 4 + 24 + 1  # =121


class Reader:
    def __init__(self, data: bytes) -> None:
        self.d = data
        self.p = 0

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def f32(self):
        v = struct.unpack_from("<f", self.d, self.p)[0]
        self.p += 4
        return v

    def vec3(self):
        v = struct.unpack_from("<3f", self.d, self.p)
        self.p += 12
        return v

    def quat(self):
        v = struct.unpack_from("<4f", self.d, self.p)
        self.p += 16
        return v

    def string(self):
        e = self.d.find(b"\x00", self.p)
        if e < 0:
            raise ValueError("未终止的字符串")
        s = self.d[self.p:e].decode("ascii", "replace")
        self.p = e + 1
        return s

    def skip(self, n):
        self.p += n


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    d = Path(sys.argv[1]).read_bytes()
    r = Reader(d)
    fid = d[0:4].decode("ascii", "replace")
    r.skip(4)
    print("fileID:", fid, " 大小:", len(d))
    if fid != "UMD2":
        return 1

    nvb = r.u32()
    print("\n顶点缓冲数:", nvb)
    for i in range(nvb):
        vcount = r.u32()
        ecount = r.u32()
        elems = []
        for _ in range(ecount):
            desc = r.u32()
            elems.append((desc & 0xFF, (desc >> 8) & 0xFF, (desc >> 16) & 0xFF))
        stride = sum(ESIZE.get(t, 4) for t, _s, _i in elems)
        mstart = r.u32()
        mcount = r.u32()
        print(f"  vb[{i}] 顶点={vcount} 元素={ecount} stride={stride} morph={mstart},{mcount}")
        print(f"        元素: {[f'{SEM.get(s, s)}/{t}' for t, s, _ in elems]}")
        r.skip(vcount * stride)

    nib = r.u32()
    print("\n索引缓冲数:", nib)
    for i in range(nib):
        icount = r.u32()
        isize = r.u32()
        print(f"  ib[{i}] 索引={icount} 大小={isize}")
        r.skip(icount * isize)

    ng = r.u32()
    print("\n几何段数:", ng)
    for gi in range(ng):
        bmc = r.u32()
        r.skip(4 * bmc)
        nlod = r.u32()
        lods = []
        for _ in range(nlod):
            dist = r.f32()
            prim = r.u32()
            vbr = r.u32()
            ibr = r.u32()
            st = r.u32()
            cnt = r.u32()
            lods.append((dist, prim, vbr, ibr, st, cnt))
        print(f"  geom[{gi}] 骨骼映射={bmc} LOD={nlod} {lods}")

    nmorphs = r.u32()
    nbones = r.u32()
    print(f"\nmorph数={nmorphs} 骨骼数={nbones}")

    for bi in range(nbones):
        nm = r.string()
        par = r.i32()
        pos = r.vec3()
        rot = r.quat()
        scl = r.vec3()
        r.skip(48)
        rad = r.f32()
        r.skip(24)
        ani = d[r.p]
        r.skip(1)
        print(f"  bone[{bi}] {nm:<16} parent={par:<3} pos=({pos[0]:+.4f},{pos[1]:+.4f},{pos[2]:+.4f}) rad={rad:.4f} animated={ani}")

    # 剩余字节
    print(f"\n解析结束 offset={r.p}, 剩余 {len(d)-r.p} 字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
