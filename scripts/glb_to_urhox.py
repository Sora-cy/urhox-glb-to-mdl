#!/usr/bin/env python3
"""GLB(glTF 2.0) -> UrhoX .mdl (UMD2) 转换器（含骨骼与蒙皮）。

背景：UrhoX 的资源加载器不解析 glTF，把 GLB 直接放进 assets 只会得到空模型
（骨骼数 0、包围盒 (1,1,1)），角色不会动。外部生成的模型必须先转成 .mdl。

UMD2 布局（对照现役写实基底 mdl 样本逆向验证，41 骨全部正确读出）：
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
  vec3 bboxMin, vec3 bboxMax
  per geom: vec3 center
  bool generateSecondUV, u32 secondUVSizes, bool hasStrokeNormal, u32 convexCollisionCount

用法:
    python glb_to_urhox.py <input.glb> <output.mdl>
"""
from __future__ import annotations
import json
import struct
import sys
from pathlib import Path

TYPE_INT, TYPE_FLOAT, TYPE_VECTOR2, TYPE_VECTOR3, TYPE_VECTOR4 = 0, 1, 2, 3, 4
TYPE_UBYTE4, TYPE_UBYTE4_NORM = 5, 6
SEM_POSITION, SEM_NORMAL, SEM_TANGENT = 0, 1, 3
SEM_TEXCOORD, SEM_COLOR, SEM_BLENDWEIGHTS, SEM_BLENDINDICES = 4, 5, 6, 7
TRIANGLE_LIST = 0

# 项目驱动表期望的顶点布局（顺序与现役写实基底一致）
ELEMENTS = [
    (TYPE_VECTOR3, SEM_POSITION, 0),
    (TYPE_VECTOR3, SEM_NORMAL, 0),
    (TYPE_UBYTE4_NORM, SEM_COLOR, 0),
    (TYPE_VECTOR2, SEM_TEXCOORD, 0),
    (TYPE_VECTOR4, SEM_TANGENT, 0),
    (TYPE_VECTOR4, SEM_BLENDWEIGHTS, 0),
    (TYPE_UBYTE4, SEM_BLENDINDICES, 0),
]
ESIZE = {TYPE_INT: 4, TYPE_FLOAT: 4, TYPE_VECTOR2: 8, TYPE_VECTOR3: 12,
        TYPE_VECTOR4: 16, TYPE_UBYTE4: 4, TYPE_UBYTE4_NORM: 4}

COMP_FMT = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
COMP_SIZE = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
NUM_COMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class Writer:
    def __init__(self) -> None:
        self.buf = bytearray()

    def u32(self, v):
        self.buf += struct.pack("<I", int(v))

    def u8(self, v):
        self.buf += struct.pack("<B", int(v) & 0xFF)

    def f32(self, v):
        self.buf += struct.pack("<f", float(v))

    def vec2(self, v):
        self.buf += struct.pack("<2f", *[float(x) for x in v])

    def vec3(self, v):
        self.buf += struct.pack("<3f", *[float(x) for x in v])

    def vec4(self, v):
        self.buf += struct.pack("<4f", *[float(x) for x in v])

    def raw(self, b):
        self.buf += bytes(b)

    def boolean(self, v):
        self.u8(1 if v else 0)

    def file_id(self, s):
        self.raw(s.encode("ascii"))

    def cstring(self, s):
        self.raw(s.encode("ascii", "replace"))
        self.u8(0)


def element_desc(t, s, i=0):
    return t | (s << 8) | (i << 16)


# afk-rig 的后缀式命名 -> 项目驱动表要求的 Mixamo 命名
# SceneView:AnimateActor 按名驱动步态（pose("L_Thigh", ...)），名字不匹配会静默跳过
BONE_RENAME = {
    "root": "Root", "pelvis": "Pelvis",
    "spine_01": "Spine01", "spine_02": "Spine02", "spine_03": "Spine03",
    "neck_01": "Neck", "head": "Head", "head_leaf": "HeadTop",
    "clavicle_l": "L_Clavicle", "upperarm_l": "L_Upperarm",
    "lowerarm_l": "L_Forearm", "hand_l": "L_Hand", "middle_01_l": "L_HandTip",
    "thigh_l": "L_Thigh", "calf_l": "L_Calf", "foot_l": "L_Foot",
    "ball_l": "L_ToeBase", "ball_leaf_l": "L_ToeTip",
    "clavicle_r": "R_Clavicle", "upperarm_r": "R_Upperarm",
    "lowerarm_r": "R_Forearm", "hand_r": "R_Hand", "middle_01_r": "R_HandTip",
    "thigh_r": "R_Thigh", "calf_r": "R_Calf", "foot_r": "R_Foot",
    "ball_r": "R_ToeBase", "ball_leaf_r": "R_ToeTip",
}


def map_bone_name(raw: str) -> str:
    return BONE_RENAME.get(raw.lower(), raw)


def quat_to_mat(q):
    x, y, z, w = q
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ]


def mat_mul(a, b):
    out = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            out[i][j] = a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j]
    return out


def load_glb(path: Path):
    data = path.read_bytes()
    magic, _ver, _t = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError("不是 GLB")
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


def read_accessor(js, bin_data, idx):
    acc = js["accessors"][idx]
    n = NUM_COMP[acc["type"]]
    fmt = COMP_FMT[acc["componentType"]]
    size = COMP_SIZE[acc["componentType"]]
    count = acc["count"]
    base = acc.get("byteOffset", 0)
    if "bufferView" in acc:
        bv = js["bufferViews"][acc["bufferView"]]
        base += bv.get("byteOffset", 0)
        stride = bv.get("byteStride", 0) or n * size
    else:
        stride = n * size
    out = []
    for i in range(count):
        out.append(struct.unpack_from("<%d%s" % (n, fmt), bin_data, base + i * stride))
    return out


def build_parents(js):
    parent = {}
    for i, n in enumerate(js.get("nodes", [])):
        for c in (n.get("children") or []):
            parent[c] = i
    return parent


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    js, bin_data = load_glb(src)

    if not js.get("meshes"):
        print("GLB 无网格")
        return 1
    prim = js["meshes"][0]["primitives"][0]
    attrs = prim.get("attributes", {})
    if "POSITION" not in attrs:
        print("网格缺少 POSITION")
        return 1

    positions = read_accessor(js, bin_data, attrs["POSITION"])
    normals = read_accessor(js, bin_data, attrs["NORMAL"]) if "NORMAL" in attrs else None
    uvs = read_accessor(js, bin_data, attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else None
    joints = read_accessor(js, bin_data, attrs["JOINTS_0"]) if "JOINTS_0" in attrs else None
    weights = read_accessor(js, bin_data, attrs["WEIGHTS_0"]) if "WEIGHTS_0" in attrs else None
    if "indices" not in prim:
        print("网格缺少索引")
        return 1
    indices = read_accessor(js, bin_data, prim["indices"])

    nvert = len(positions)
    print("GLB: verts=%d indices=%d tris=%d" % (nvert, len(indices), len(indices) // 3))

    # 骨骼：优先用 skin.joints，其次退化为全部节点
    skin = (js.get("skins") or [None])[0]
    if skin and skin.get("joints"):
        joint_nodes = skin["joints"]
    else:
        joint_nodes = list(range(len(js.get("nodes", []))))

    ibm = None
    if skin and skin.get("inverseBindMatrices") is not None:
        ibm = read_accessor(js, bin_data, skin["inverseBindMatrices"])

    jpos = {}
    for i, nid in enumerate(joint_nodes):
        jpos[nid] = i

    parents = build_parents(js)

    def world_of(nid):
        t = [0.0, 0.0, 0.0]
        r = [0.0, 0.0, 0.0, 1.0]
        s = [1.0, 1.0, 1.0]
        chain = []
        cur = nid
        seen = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = parents.get(cur)
        for n in reversed(chain):
            node = js["nodes"][n]
            nt = node.get("translation", [0.0, 0.0, 0.0])
            nr = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
            ns = node.get("scale", [1.0, 1.0, 1.0])
            rm = quat_to_mat(nr)
            # 局部：先缩放，再旋转，再平移（glTF 规范）
            nsx, nsy, nsz = ns
            lrot = [[rm[i][0] * nsx, rm[i][1] * nsy, rm[i][2] * nsz] for i in range(3)]
            # 复合：新平移 = 父旋转缩放作用于子平移 + 父平移
            t = [
                rm[0][0] * nsx * t[0] + rm[0][1] * nsy * t[1] + rm[0][2] * nsz * t[2] + nt[0],
                rm[1][0] * nsx * t[0] + rm[1][1] * nsy * t[1] + rm[1][2] * nsz * t[2] + nt[1],
                rm[2][0] * nsx * t[0] + rm[2][1] * nsy * t[1] + rm[2][2] * nsz * t[2] + nt[2],
            ]
            s = [s[0] * nsx, s[1] * nsy, s[2] * nsz]
            # 累乘旋转缩放矩阵
            new_r = [[0.0] * 3 for _ in range(3)]
            for i in range(3):
                for j in range(3):
                    new_r[i][j] = lrot[i][0] * s[0] + lrot[i][1] * s[1] + lrot[i][2] * s[2]
            r = new_r
        return r, t, s

    bones = []
    for i, nid in enumerate(joint_nodes):
        node = js["nodes"][nid]
        name = node.get("name") or ("joint_%d" % i)
        m, t, s = world_of(nid)
        # 分解 scale（列长度）
        s0 = (m[0][0] ** 2 + m[1][0] ** 2 + m[2][0] ** 2) ** 0.5 or 1.0
        s1 = (m[0][1] ** 2 + m[1][1] ** 2 + m[2][1] ** 2) ** 0.5 or 1.0
        s2 = (m[0][2] ** 2 + m[1][2] ** 2 + m[2][2] ** 2) ** 0.5 or 1.0
        # 归一化旋转矩阵
        rm = [[m[i][0] / s0, m[i][1] / s1, m[i][2] / s2] for i in range(3)]
        # 矩阵 -> 四元数
        tr = rm[0][0] + rm[1][1] + rm[2][2]
        if tr > 0:
            sr = (tr + 1.0) ** 0.5
            qw = 0.5 / sr
            qx = (rm[2][1] - rm[1][2]) * qw
            qy = (rm[0][2] - rm[2][0]) * qw
            qz = (rm[1][0] - rm[0][1]) * qw
        elif rm[0][0] > rm[1][1] and rm[0][0] > rm[2][2]:
            sr = 2.0 * (1.0 + rm[0][0] - rm[1][1] - rm[2][2]) ** 0.5
            qx = 0.25 * sr
            qy = (rm[0][1] + rm[1][0]) / sr
            qz = (rm[2][0] + rm[0][2]) / sr
            qw = (rm[2][1] - rm[1][2]) / sr
        elif rm[1][1] > rm[2][2]:
            sr = 2.0 * (1.0 + rm[1][1] - rm[0][0] - rm[2][2]) ** 0.5
            qx = (rm[0][1] + rm[1][0]) / sr
            qy = 0.25 * sr
            qz = (rm[1][2] + rm[2][1]) / sr
            qw = (rm[0][2] - rm[2][0]) / sr
        else:
            sr = 2.0 * (1.0 + rm[2][2] - rm[0][0] - rm[1][1]) ** 0.5
            qx = (rm[0][2] + rm[2][0]) / sr
            qy = (rm[1][2] + rm[2][1]) / sr
            qz = 0.25 * sr
            qw = (rm[1][0] - rm[0][1]) / sr
        # 逆绑定矩阵（蒙皮的关键）：glTF 是列主序 mat4，UrhoX 要行主序 mat3x4
        if ibm and i < len(ibm):
            m = ibm[i]
            off_m = [m[0], m[4], m[8], m[12],
                     m[1], m[5], m[9], m[13],
                     m[2], m[6], m[10], m[14]]
        else:
            off_m = [1.0, 0.0, 0.0, 0.0,
                     0.0, 1.0, 0.0, 0.0,
                     0.0, 0.0, 1.0, 0.0]
        # 骨骼坐标必须是**局部**的（相对父骨骼）：现役 mdl 的 Root pos≈(0,0,0)、
        # Hip≈(0,0,0.51)。用 world_of 的世界坐标会让骨骼姿态与包围盒全错。
        _n = js["nodes"][nid]
        t = [float(v) for v in _n.get("translation", [0.0, 0.0, 0.0])]
        _r = _n.get("rotation", [0.0, 0.0, 0.0, 1.0])
        qx, qy, qz, qw = [float(v) for v in _r]
        _s = _n.get("scale")
        s0, s1, s2 = ([float(v) for v in _s] if _s else [1.0, 1.0, 1.0])
        bone = {
            "name": map_bone_name(name),
            "parent": -1,
            "pos": [float(v) for v in t],
            # ⚠️ 四元数顺序：glTF 是 (x,y,z,w)，UrhoX UMD2 是 (w,x,y,z)。
            # 实测现役 41 骨中 25 根 rot[0]≈1（单位旋转）、rot[3]≈1 的为 0 根，
            # 确认第 0 位是 w。顺序写错会让每根骨骼旋转全部错乱 —— 表现为
            # 加载卡死 / 模型拧成麻花 / 只剩整体上下浮动没有步态。
            "rot": [float(qw), float(qx), float(qy), float(qz)],
            "scale": [float(s0), float(s1), float(s2)],
            "offset": off_m,
        }
        # 父骨骼索引
        pid = node.get("_parent")
        if pid is None:
            for pi, pn in enumerate(js.get("nodes", [])):
                if nid in (pn.get("children") or []):
                    pid = pi
                    break
        if pid is not None and pid in jpos:
            bone["parent"] = jpos[pid]
        bones.append(bone)

    w = Writer()
    w.file_id("UMD2")

    # --- 顶点缓冲 ---
    w.u32(1)
    w.u32(nvert)
    w.u32(len(ELEMENTS))
    for t, s, i in ELEMENTS:
        w.u32(element_desc(t, s, i))
    w.u32(0)
    w.u32(0)
    for vi in range(nvert):
        w.vec3(positions[vi])
        w.vec3(normals[vi] if normals else (0.0, 1.0, 0.0))
        w.u8(255); w.u8(255); w.u8(255); w.u8(255)
        w.vec2(uvs[vi] if uvs else (0.0, 0.0))
        w.vec4((1.0, 0.0, 0.0, 1.0))
        wt = list(weights[vi]) if weights else [1.0, 0.0, 0.0, 0.0]
        ssum = sum(wt) or 1.0
        for v in wt:
            w.f32(v / ssum)
        for v in (list(joints[vi]) if joints else [0, 0, 0, 0]):
            w.u8(int(v) & 0xFF)
    index_size = 2 if nvert <= 0xFFFF else 4
    w.u32(1)
    w.u32(len(indices))
    w.u32(index_size)
    for idx in indices:
        w.raw(struct.pack("<H" if index_size == 2 else "<I", int(idx[0])))
    w.u32(1)                      # geometries
    # boneMapCount=0：实测现役 41 骨基底模型也是 0（引擎按「恒等映射」处理）。
    # 网上流传的"必须列出全部骨骼"在这版引擎上不适用，填了反而会让加载卡死。
    w.u32(0)
    # 4 级 LOD，与现役基底同构（现役：lodDistance 0/11.96/32.67/90.18，
    # 三角面 100%/85%/65%/45%）。这里共用同一份 VB/IB，只按比例递减绘制量；
    # 引擎对只有 1 级 LOD 的模型行为未验证，先对齐结构。
    lods = [
        (0.0, 1.00),
        (11.96, 0.85),
        (32.67, 0.65),
        (90.18, 0.45),
    ]
    w.u32(len(lods))              # LOD levels
    for dist, ratio in lods:
        cnt = int(len(indices) * ratio) // 3 * 3
        if cnt < 3:
            cnt = 3
        w.f32(dist)               # lodDistance
        w.u32(TRIANGLE_LIST)      # primitiveType
        w.u32(0)                  # vertexBuffer ref
        w.u32(0)                  # indexBuffer ref
        w.u32(0)                  # indexStart
        w.u32(cnt)                # indexCount
    w.u32(0)
    w.u32(len(bones))
    for bi, b in enumerate(bones):
        w.cstring(b["name"])
        # Urho3D 里根骨骼的 parentIndex 指向自身（现役 mdl 的 Root.parent=0）
        pidx = b["parent"]
        if pidx is None or pidx < 0:
            pidx = bi
        w.u32(int(pidx))
        w.vec3(b["pos"])
        w.vec4(b["rot"])
        w.vec3(b["scale"])
        for v in b["offset"]:
            w.f32(v)
        w.f32(0.0)      # radius（现役样本恒为 0）
        w.vec3((0.0, 0.0, 0.0))   # bone bbox min（现役样本恒为 0）
        w.vec3((0.0, 0.0, 0.0))   # bone bbox max（现役样本恒为 0）
        w.u8(0)         # collision_mask
        # 骨骼条目 = 名字+null+121（4 parent +12 pos +16 rot +12 scale +48 mat3x4
        # +4 radius +24 bbox +1 animated）。实测现役 mdl 的 Root→Hip 间距 126
        # = "Root"+null(5) + 121，少写或多写 1 字节都会让后续骨骼名全部错位。
    xs = [p[0] for p in positions]; ys = [p[1] for p in positions]; zs = [p[2] for p in positions]
    w.vec3((min(xs), min(ys), min(zs)))
    w.vec3((max(xs), max(ys), max(zs)))
    w.vec3(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2))
    w.boolean(False); w.u32(0); w.boolean(False); w.u32(0)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(bytes(w.buf))
    print("WROTE %s size=%d" % (dst, len(w.buf)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
