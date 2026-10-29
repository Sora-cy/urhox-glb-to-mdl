---
name: urhox-glb-to-mdl
description: 把无骨骼 GLB 模型（AI 生成或外部下载）转成 TapTap Maker / UrhoX 引擎能加载的带骨骼 .mdl，涵盖 afk-rig 骨骼绑定、UMD2 二进制格式、蒙皮权重重算、骨骼重定向与预蒙皮。This skill should be used when a project needs to import a 3D model into UrhoX/Urho3D-based Maker games, when a .mdl reports 0 bones, when a model renders torn / lying down / T-posing, or when converting glTF/GLB assets for TapTap Maker.
agent_created: true
---

# GLB → UrhoX/Maker `.mdl` 本地转换管线

## 概述

UrhoX 的资源加载器**不解析 glTF**：把 GLB 直接放进 `assets/` 只会得到空模型
（骨骼数 0、包围盒 (1,1,1)），角色不会动。本 skill 提供一条完整本地链路：
无骨骼 GLB → 绑骨骼 → 转成 UMD2 `.mdl` → 接进游戏能正常跑起来。
厂商的 AI 转换服务按次收费且不可控，这条链路全部本地完成。

## 何时使用

- 需要把 GLB / glTF 模型导入 TapTap Maker（UrhoX）项目
- 加载出来是空模型 / 0 骨骼 / 骨骼名乱码
- 模型躺倒、身上有洞、腿被撕断、脚掌没厚度、一直 T-pose 举着手
- 引擎解析阶段卡死（日志停在"开始解析 xxx"）
- 模型是灰模 / 贴图满身错位斑块，或不知道贴图该从哪来
- 挂在骨骼上的武器、道具位置歪了
- **静物/道具（棺材、雕像、木桶…）扭成麻花、或者"根本看不见"**
- 模型三角数炸裂（几十万面），手机上卡死

## 三条不可违背的硬规则

这三点是整套流程里最贵的三个坑，先读一遍再动手：

1. **必须借现役骨架**。用自己的骨架数值（哪怕字节级自洽）引擎必卡死在解析阶段，
   原因未明。做法是只重定向**顶点**，骨骼段整段照抄一个现役 `.mdl` 的字节。
   → 详见 `references/troubleshooting.md` §1
2. **预蒙皮必须对"混合矩阵"求逆**：`v' = (Σ wᵢ·Aᵢ)⁻¹·v`，
   **不是** `Σ wᵢ·Aᵢ⁻¹·v`（两者不可交换）。写错会让关节过渡带静止还原误差达 0.19 → 腿被撕断。
   → `retarget.py` 内置校验，必须看到 `静止还原误差：最大 0.0000`
3. **不做任何坐标轴旋转**。源 GLB 与现役骨架**都是 Y-up**，转错就会躺倒 / 没头。

## 流程

```
无骨骼 GLB
   │  scripts/rig_glb.py            （afk-rig 四步：start / inspect / bind / export）
   ▼
带骨骼 GLB ──────────────┐
   │  scripts/decimate_glb.py    │  scripts/extract_glb_textures.py   ← 独立于几何，可并行
   │  （仅面数炸裂的模型；       │  scripts/derive_normal.py
   │    必须先减再转）           │
   │  scripts/pipeline.py        │
   │  （一条命令跑完下面四步     │  ─ 贴图：先用模型自带的，缺法线再派生；
   │    并强制校验）             │    两个都不耗任何生成配额
   ├─ ① glb_to_urhox.py         │
   ├─ ② reweight.py             │
   ├─ ②b prop_single_bone.py    │  ← 仅静物/非人形道具
   ├─ ③ lower_arms.py           │  ← 静物跳过
   └─ ④ retarget.py             │
   ▼                            ▼
可部署的 .mdl               _D / _N / _MR.png
```

### 第 0 步：准备一个"基底" `.mdl`

从项目里找一个**已经能在游戏里正常跑**的带骨骼 `.mdl`（现役角色/怪物），
它的骨骼段就是后面要借的东西。记下它的**模型自身空间身高**
（通常是归一化后的 0.998，填错会导致整体缩放错位）。

### 第 1 步：绑骨骼

```bash
python scripts/rig_glb.py model.glb            # → model_rigged.glb
```

`--hands single-bone` 是必须的（否则多出 40 根手指骨）。
`inspect --issues` 为空之后**仍要目视核对关节位置**，Q 版比例常需手动微调。

### 第 2 步：转换（四步合一，带强制校验）

```bash
python scripts/pipeline.py --in model_rigged.glb --live baseline.mdl \
       --out ./work --name mymodel --deploy ./assets/model/mymodel/Meshes
```

关键参数：

| 参数 | 说明 |
|---|---|
| `--live` | 基底 `.mdl`（借它的骨骼段） |
| `--leg-blend` | **必须 0**。大于 0 会把脚掌压扁 |
| `--live-height` | 基底模型自身空间身高，默认 0.998，换基底必须改 |
| `--arm-deg` | 手臂下垂角度，默认 65 |

任一步不合格就**不产出**最终文件。第 ④ 步的 `静止还原误差` 必须 ≤ 0.01。

也可以拆开单步跑（排错时用）：

```bash
python scripts/glb_to_urhox.py in.glb out.mdl
python scripts/reweight.py     in.mdl out.mdl [power=4]
python scripts/lower_arms.py   in.mdl out.mdl [deg=65]
python scripts/retarget.py     in.mdl baseline.mdl out.mdl [leg_blend=0] [live_height=0.998]
```

### 第 2.5 步：贴图（先抽，别生成）

```bash
python scripts/extract_glb_textures.py model.glb --out-dir ./tex --name mymodel
python scripts/derive_normal.py ./tex/mymodel_D.png      # GLB 没有法线贴图时
```

AI 生成的模型**自带 PBR 贴图**，抽出来就能用，UV 不需要翻转。只有在 GLB 真的
没有贴图时才考虑其他方案。详见 `references/troubleshooting.md` §9。

### 第 3 步：部署后三件事

1. 给资源生成 `.meta`（引擎需要 uuid 才好引用），贴图也要
2. 在配置里登记模型路径 + `height`（**模型自身空间身高**，不是世界身高），
   并接上 `diffuse` / `normal` 贴图槽位
3. 跑一次回归，确认 `高度=0.998 骨骼=<基底骨数>`

⚠️ **Q 版模型借写实骨架时，步态幅度要收小**（实测量级：大腿 ±9° 而不是 ±30°）。
骨架是按写实比例建的，Q 版短腿大头是"借骨"，大幅屈伸会把关节处的网格扯变形。
同理，模型自带的装扮（头盔/披风/护目镜）**不要再叠加程序化挂件**。

## 静物 / 非人形道具

棺材、雕像、木桶、栅栏这类**不动的摆件**也走同一条管线，但要多做两件事、
少做一件事。不做的话会出现"扭成麻花"或"看不见"。

| 差异 | 做法 | 原因 |
|---|---|---|
| ① 跳过 `lower_arms.py` | 直接从 ② 的产物做 ④ | 该步按"人形手臂语义"把手臂顶点绕肩点转 65°，棺材的侧板/雕像的翅膀会整片被掰走 |
| ② 权重全归单根骨骼 | `python scripts/prop_single_bone.py in.mdl out.mdl Pelvis`（插在 ② 之后） | 静物是一整块，权重分散到头/手/脚各骨骼后，蒙皮会把整件道具扭成麻花 |
| ③ 处理"展示底板" | `python scripts/shrink_base.py in.mdl out.mdl --ratio 0.045 --radius 0.03` | AI 生成的道具常带一块方形平台，俯视角下就是地上一个突兀的方块 |

顺序（`① glb_to_urhox → ② reweight → ②b prop_single_bone → ③ 跳过 → ④ retarget`）。
`shrink_base` 要放在 **④ 之前**：retarget 会按新的最低点重新贴地。
**只挪顶点、不改三角/索引数量** —— 为什么见 `references/troubleshooting.md` §18。

### 静物要做面数预算

3D 生成服务**不保证面数**：同一批里石棺 4.9k、栅栏 9.3k，骨堆给了 **49.9 万**
（和 boss 同级）。一件 1 米高的小道具 6 个实例就是 300 万三角，手机上会直接卡死。

```bash
python scripts/probe_mdl.py model.mdl        # 先看顶点/三角数
python scripts/decimate_glb.py in_rigged.glb out_lod.glb --cells 100   # 太多就减面
```
`--cells` 是沿对角线切多少格：100 大约能砍掉 90% 的面（骨堆 49.9 万 → 4.2 万，
mdl 27.7MB → 1.2MB），越小越省但细节越糊。**必须在 GLB 上减**，
等于让 `glb_to_urhox` 用新数据重建 mdl；直接改 mdl 的计数会让引擎解析出错。

### 静物"看不见"多半是贴图太暗

AI 生成的静物贴图普遍是"写实深色材质"（湿木 0.156、锈铁 0.162，而角色贴图约 0.5）。
夜里光照本来就低，渲出来几乎全黑，和地面糊在一起 —— 看起来就像**根本没渲染**。

判断方法：**换成亮色自发光材质并摆到镜头正前方**，一眼定生死；纯靠真实材质
在暗场景里肉眼找，会把"太暗"误判成"没渲染"，然后去查材质/骨骼/剔除，白烧时间。

修法：用 gamma 把贴图平均亮度拉到 0.3~0.35（`out = in^(1/g)`）。
不要用乘法 —— 乘法会把手里的亮部直接过曝。

## 排错

按症状查 `references/troubleshooting.md`（19 条，含判断依据和"已排除的假线索"表）。
最常被误判的几条（每条都踩过）：

- **"腿被撕断"** → 预蒙皮写成了 `Σ w·A⁻¹`（不可交换），不是网格问题
- **"引擎卡死"** → 用了自建骨架数值，不是包围盒 / LOD 问题
- **"模型躺倒"** → 多做了 Z-up 旋转，两边其实都是 Y-up
- **"灰模 / 贴图斑块"** → 先用 `extract_glb_textures.py` 抽自带贴图，别去生成
- **"渲染破碎 / Singular matrix"** → 源 GLB 自身有问题，重新生成比修它快（§12）
- **"某些部件远处消失、靠近又出现"** → LOD 在砍索引，不是权重/剔除问题（§14）。
  **最容易误判的一条**：假 LOD 按比例截索引，等于随机砍掉后排部件。
- **"静物被扭成麻花"** → 权重分散到了人形各骨骼，不是模型坏了（§16）
- **"静物看不见"** → 先怀疑贴图太暗，不是没渲染（§17）
- **"改了 mdl 顶点/索引数后模型整个消失"** → 尾部还有未解析的几何记录（§18）
- **"手机上卡死"** → 先量三角数，AI 生成的面数会离谱到 50 万（§19）

格式细节（骨骼条目 121 字节、四元数 wxyz、`boneMappingCount=0` 等）
见 `references/umd2-format.md`；生成提示词规范、afk-rig 细节、云端配额
见 `references/generation-spec.md`。

## 依赖

- Python 3 + `numpy`（`reweight.py` / `retarget.py` / `derive_normal.py` /
  `decimate_glb.py` / `shrink_base.py` / `prop_single_bone.py` 用）
- Python 3 + `Pillow`（贴图抽取与法线派生用）
- Node.js（`afk-rig`，绑骨骼那一步）
- `scripts/probe_mdl.py` 用于解析 `.mdl` 核对顶点 / 骨骼 / 包围盒

> 两个 Python 需求常被忽略：同一台机器上可能存在多个解释器，跑之前先确认
> 目标解释器里 `numpy` 和 `PIL` 都能 import，否则会在流程中段报 `ModuleNotFoundError`。
