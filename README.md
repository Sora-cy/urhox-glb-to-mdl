# urhox-glb-to-mdl

把**无骨骼的 GLB 模型**（AI 生成的、或从网上下载的）转成 **TapTap Maker / UrhoX 引擎能正常加载的带骨骼 `.mdl`**。

整套流程全部本地完成，不需要厂商的 AI 转换服务（按次收费且不可控）。

> English version: [README_EN.md](./README_EN.md)

---

## 为什么需要这个

UrhoX 的资源加载器**不解析 glTF**。把 GLB 直接放进 `assets/` 目录只会得到一个空模型：

- 骨骼数 = 0
- 包围盒 = (1, 1, 1)
- 角色完全不动

必须转成 UMD2 格式的 `.mdl`。而这个格式没有公开文档，下面是逐字节反推 + 实测验证出来的结论。

---

## 快速开始

```bash
# 0. 准备一个"基底" .mdl —— 项目里已经能正常跑的带骨骼模型
#    （它的骨骼段后面要借，见「三条硬规则」）

# 1. 给无骨骼 GLB 绑骨骼（afk-rig 四步封装）
python scripts/rig_glb.py model.glb            # → model_rigged.glb

# 2. 四步转换，一条命令跑完并强制校验
python scripts/pipeline.py --in model_rigged.glb --live baseline.mdl \
       --out ./work --name mymodel \
       --deploy ./assets/model/mymodel/Meshes   # 可选：直接复制到部署目录

# 2.5 贴图：先抽模型自带的，别去生成（零配额消耗）
python scripts/extract_glb_textures.py model.glb --out-dir ./tex --name mymodel
python scripts/derive_normal.py ./tex/mymodel_D.png   # GLB 没有法线贴图时再派生
```

第 ④ 步会打印 `静止还原误差` —— **必须是 0.0000**，超过 0.01 脚本会拒绝产出文件。

依赖：Python 3 + `numpy` + `Pillow`、Node.js（`afk-rig`）。

---

## 三条硬规则（最贵的三个坑）

### 1. 必须"借"现役骨架

用自己的骨架数值（哪怕字节级自洽、逆矩阵互乘偏差 0.00000）**引擎必卡死在解析阶段**，
原因未明。唯一可行路线是：只重定向**顶点**，骨骼段整段照抄一个现役 `.mdl` 的字节。

二分实验结论：

| 配置 | 结果 |
|---|---|
| 自己的顶点 + 现役骨架 | ✅ 加载 |
| 自己的骨骼名 + 现役骨骼数值 | ✅ 加载 |
| **任意一套完整自建骨骼数值** | ❌ **全部挂死** |

### 2. 预蒙皮必须对"混合矩阵"求逆

引擎静止时算的是 `v_rest = Σ wᵢ·Aᵢ·v'`（其中 `Aᵢ = BoneWorld_restᵢ · offsetᵢ`）。
要让静止姿态精确还原成原模型，必须解：

```python
M = Σ (wᵢ / Σw) · Aᵢ        # 先混合
v' = inv(M) · v              # 再对混合矩阵求逆
```

**不能**写成 `v' = Σ wᵢ·Aᵢ⁻¹·v` —— 矩阵求和与求逆不可交换。
写错的后果：关节权重过渡带（≈50/50）还原不回去，误差可达 **0.19**（而模型身高才 1.0），
表现为**腿从膝盖以下被撕断、脚掌没厚度**。

### 3. 不做任何坐标轴旋转

源 GLB 和现役骨架**都是 Y-up**。多做一个 -90°X 的"Z-up 转换"，模型就会躺倒、头埋进地里。

判断方法：看包围盒 `size`，如果是 `(X≈1.84, Y≈0.375, Z≈1.90)` 就是转错了
（正常应该是 `(0.38~0.97, 0.998, 0.19)`）。

---

## 流程

```
无骨骼 GLB
   │  scripts/rig_glb.py            （afk-rig：start / inspect / bind / export）
   ▼
带骨骼 GLB
   │  scripts/pipeline.py           （四步合一 + 强制校验）
   ├─ ① glb_to_urhox.py   GLB → UMD2
   ├─ ② reweight.py       重算蒙皮权重
   ├─ ③ lower_arms.py     T-pose 手臂烘焙下垂
   └─ ④ retarget.py       重定向到现役骨架 + 预蒙皮
   ▼
可部署的 .mdl
```

也可以拆开单步跑（排错时用）：

```bash
python scripts/glb_to_urhox.py in.glb out.mdl
python scripts/reweight.py     in.mdl out.mdl [power=4]
python scripts/lower_arms.py   in.mdl out.mdl [deg=65]
python scripts/retarget.py     in.mdl baseline.mdl out.mdl [leg_blend=0] [live_height=0.998]
```

**关键参数**

| 参数 | 说明 |
|---|---|
| `--live` | 基底 `.mdl`，借它的骨骼段 |
| `--leg-blend` | **必须是 0**，大于 0 会把脚掌压扁 |
| `--live-height` | 基底模型**自身空间**的身高，默认 0.998，换基底必须改 |
| `--arm-deg` | 手臂下垂角度，默认 65 |

---

## 部署后的三件事

1. 给资源生成 `.meta`（引擎需要 uuid 才好引用）
2. 在配置里登记模型路径 + `height`（**模型自身空间的身高**，不是世界身高）
3. 跑一次回归，确认 `高度=0.998 骨骼=<基底骨数>`

---

## 目录结构

```
SKILL.md                        Skill 定义（若作为 WorkBuddy Skill 使用）
scripts/
  rig_glb.py                    给无骨骼 GLB 绑骨骼（afk-rig 四步封装）
  glb_to_urhox.py               GLB(glTF 2.0) → UMD2 .mdl
  reweight.py                   按骨骼线段距离重算蒙皮权重
  lower_arms.py                 T-pose 手臂烘焙下垂
  retarget.py                   重定向到现役骨架 + 预蒙皮 + 静止还原校验
  pipeline.py                   四步合一 + 强制校验
  probe_mdl.py                  解析 .mdl，打印顶点/骨骼/包围盒
  extract_glb_textures.py        抽出模型自带的 PBR 贴图（_D / _N / _MR）
  derive_normal.py               从 baseColor 派生法线贴图（Sobel）
  decimate_glb.py               GLB 顶点聚类减面（面数炸裂的模型）
  shrink_base.py                收缩"展示底板"（只挪顶点，不改三角/索引数）
  prop_single_bone.py           静物权重全归单根骨骼（避免扭成麻花）
references/
  umd2-format.md                UMD2 二进制格式逐字节说明
  troubleshooting.md            19 条故障字典（含"已排除的假线索"）
  generation-spec.md            AI 生成提示词规范 / afk-rig 细节 / 云端配额
```

---

## 排错

按症状查 [`references/troubleshooting.md`](./references/troubleshooting.md)。
最常被误判的三条：

| 症状 | 真因 |
|---|---|
| 腿被撕断 | 预蒙皮写成了 `Σ w·A⁻¹`（不可交换），不是网格问题 |
| 引擎解析卡死 | 用了自建骨架数值，不是包围盒 / LOD 问题 |
| 模型躺倒 / 没头 | 多做了 Z-up 旋转，两边其实都是 Y-up |

---

## 验证方式

本仓库的通用化脚本做过**字节级回归**：用同一个模型分别跑"项目内原产线"和"本仓库脚本"，
两份 `.mdl` 完全相同（`737082` 字节，`identical: True`）。

顺带记录一个因此被抓出来的错误：`lower_arms` 的手臂骨骼集合**不能包含锁骨**，
否则肩线会被一起拧歪（与原产线产物相差 29417 字节）。

---

## 许可

MIT，见 [LICENSE](./LICENSE)。
