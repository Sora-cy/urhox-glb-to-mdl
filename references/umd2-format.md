# UMD2（UrhoX `.mdl`）二进制格式

用现役 41 骨基底模型逐字节反推并验证过的结论。`scripts/probe_mdl.py` 可以直接解析打印。

## 1. 整体布局

```
[4]   "UMD2"
[u32] numVertexBuffers
  循环: [u32] vertexCount
        [u32] elementCount
        [elementCount × u32] elementDesc
        [u32] morphRangeStart
        [u32] morphRangeCount
        [vertexCount × stride] 顶点数据
[u32] numIndexBuffers
  循环: [u32] indexCount [u32] indexSize  [indexCount × indexSize] 索引数据
[u32] numGeometries
  循环: [u32] boneMappingCount
        [boneMappingCount × u32] boneMapping
        [u32] numLodLevels
        [24 × numLodLevels] LOD 记录
[u32] numMorphs          ← 注意这 4 字节在几何段之后
[u32] numBones
  循环: 骨骼名(null 结尾) + 121 字节
[24]  bbox min + bbox max
[12]  center
[10]  尾部
```

## 2. 逐项要点（这些写错一个字节就全盘崩溃）

| 项目 | 值 | 说明 |
|---|---|---|
| 顶点 stride | **72** | POSITION12 + NORMAL12 + COLOR4 + TEXCOORD8 + TANGENT16 + WEIGHTS16 + INDICES4 |
| `elementDesc` 位布局 | `type \| semantic<<8 \| index<<16` | size 表 `{0:4, 1:4, 2:8, 3:12, 4:16, 5:4, 6:4}` |
| 顶点元素顺序 | POSITION, NORMAL, COLOR(UBYTE4_NORM), TEXCOORD, TANGENT, BLENDWEIGHTS, BLENDINDICES | 与现役基底一致，顺序错了驱动表读不到 |
| LOD 记录 | **24 字节** | `lodDistance(4) + primitiveType(4) + vbRef(4) + ibRef(4) + indexStart(4) + indexCount(4)` |
| **`boneMappingCount`** | **必须 0** | 网上流传"必须列出全部骨骼"，在这版引擎上填了反而**卡死在解析阶段** |
| 骨骼名 | **null 结尾的 ASCII** | 名字 + `\0` |
| 骨骼条目 | 名字+null 后固定 **121 字节** | parent(4) + pos(12) + quat(16) + scale(12) + offset(48) + radius(4) + bbox(24) + mask(1) |
| **四元数顺序** | **(w, x, y, z)** | glTF 是 (x,y,z,w)，**必须换位**。写反 → 每根骨骼旋转全错 |
| 骨骼 pos/rot/scale | **局部**（相对父骨骼） | 写世界坐标会让骨骼姿态与包围盒全错 |
| `offsetMatrix` | glTF 列主序 mat4 → 取 **3×4 行主序** | 取 `m[0],m[4],m[8],m[12], m[1],m[5],m[9],m[13], m[2],m[6],m[10],m[14]` |
| 根骨骼 parentIndex | **指向自身** | Urho3D 约定；现役 mdl 的 Root.parent = 0 |
| radius / bbox / collision_mask | 全 0 | 现役样本恒为 0，照抄最稳 |
| 尾部 | bbox(24) + center(12) + 10 字节 | `generateSecondUV(1) + secondUVSizes(4) + hasStrokeNormal(1) + convexCollisionCount(4)` |

**骨骼条目长度是最阴的一条**：必须是 `名字 + null + 121`。多写 1 字节（比如顺手补个 animated 标志）
会让后面每一根骨骼名都错位，表现为"第 2 根骨骼开始名字乱码 / 引擎读到 0 骨"。
实测现役 mdl 里 Root→Hip 的间距是 126 = `"Root"`(4) + null(1) + 121。

## 3. 索引大小

`indexSize = 2` 当顶点数 ≤ 0xFFFF，否则 4。注意 LOD 的 `indexCount` 必须是 3 的倍数。

## 4. 用 probe 核对

```bash
python scripts/probe_mdl.py model.mdl
```

重点看：顶点数/索引数是否与源 GLB 一致、骨骼数与名字是否可读、包围盒 Y 是否≈身高。
