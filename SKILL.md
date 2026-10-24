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
   │  scripts/pipeline.py │  scripts/extract_glb_textures.py   ← 独立于几何，可并行
   │  （一条命令跑完下面   │  scripts/derive_normal.py
   │    四步并强制校验）  │
   ├─ ① glb_to_urhox.py   │  ─ 贴图：先用模型自带的，缺法线再派生；
   ├─ ② reweight.py       │    两个都不耗任何生成配额
   ├─ ③ lower_arms.py     │
   └─ ④ retarget.py       │
   ▼                      ▼
可部署的 .mdl         _D / _N / _MR.png
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

## 排错

按症状查 `references/troubleshooting.md`（12 条，含判断依据和"已排除的假线索"表）。
最常被误判的三条：

- **"腿被撕断"** → 预蒙皮写成了 `Σ w·A⁻¹`（不可交换），不是网格问题
- **"引擎卡死"** → 用了自建骨架数值，不是包围盒 / LOD 问题
- **"模型躺倒"** → 多做了 Z-up 旋转，两边其实都是 Y-up
- **"灰模 / 贴图斑块"** → 先用 `extract_glb_textures.py` 抽自带贴图，别去生成
- **"渲染破碎 / Singular matrix"** → 源 GLB 自身有问题，重新生成比修它快（§12）

格式细节（骨骼条目 121 字节、四元数 wxyz、`boneMappingCount=0` 等）
见 `references/umd2-format.md`；生成提示词规范、afk-rig 细节、云端配额
见 `references/generation-spec.md`。

## 依赖

- Python 3 + `numpy`（`reweight.py` / `retarget.py` / `derive_normal.py` 用）
- Python 3 + `Pillow`（贴图抽取与法线派生用）
- Node.js（`afk-rig`，绑骨骼那一步）
- `scripts/probe_mdl.py` 用于解析 `.mdl` 核对顶点 / 骨骼 / 包围盒

> 两个 Python 需求常被忽略：同一台机器上可能存在多个解释器，跑之前先确认
> 目标解释器里 `numpy` 和 `PIL` 都能 import，否则会在流程中段报 `ModuleNotFoundError`。
