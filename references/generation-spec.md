# 生成端规范 + afk-rig 用法 + 验证清单

## 1. AI 生成模型的提示词规范（决定成败）

**必须满足**，否则 afk-rig 的 auto-fit 会估错关节，返工成本极高：

- ✅ **严格 T-pose**：双臂水平伸直、掌心朝下、双腿并直、双脚分开与肩同宽、脚底贴地
- ✅ **左右完全对称**
- ✅ 正面朝前、无遮挡（不要斗篷盖住躯干、不要长发盖住腿）
- ✅ 低模、单位米、身高约 1.75m
- ❌ **不要任何道具/武器**（刀、钩、盾、桶、背包、法杖）——手持物会让手臂弯曲、站姿歪斜，
  auto-fit 会把肩点估到 +0.6、脊柱偏 0.1。**道具一律由代码生成**
- ❌ 不要披风 / 长发 / 裙摆拖地（遮挡腿部轮廓）

跑通过的正面用例（T-pose 部分是固定后缀，直接复用）：

```
<subject 描述>, <style 描述>,
standing in a strict T-pose with both arms stretched out horizontally sideways
at shoulder height palms facing down, legs straight and parallel,
feet shoulder-width apart flat on the ground, facing directly forward to the viewer,
no weapons, no props, clean low-poly game asset, perfectly symmetrical,
model unit meters, height about 1.75 meters
```

## 2. afk-rig：给无骨骼 GLB 绑骨骼

固定四步（顺序不能变），`scripts/rig_glb.py` 已封装：

```bash
npx -y afk-rig start model.glb --skeleton human --hands single-bone
npx -y afk-rig inspect model.afk-rig.json --issues     # 应为 []
npx -y afk-rig bind   model.afk-rig.json
npx -y afk-rig export model.afk-rig.json --out model_rigged.glb \
     --animations Walk,Idle_A --ground
```

- **`--hands single-bone` 必须加**：不加会生成 40 根手指骨，后续重定向/权重全乱
- `inspect --issues` 为空也要**目视核对关节位置**，Q 版比例常需微调：
  `npx -y afk-rig render model.afk-rig.json --views front,left --size 1400 --out check.png`
  局部放大：`--focus pelvis --radius 0.1`
- 微调：`npx -y afk-rig set model.afk-rig.json thigh_l=0.10,-0.20,0.02 --mirror`
  （`--mirror` 自动镜像左右；改完**必须重新 bind 再 export**）
- Windows 下脚本里调 `npx` 要用 `npx.cmd`，否则 `FileNotFoundError`

## 3. 生成服务的配额（若用云端生成）

| 限制 | 值 |
|---|---|
| 每日提交数 | **5 个/天**（429 `daily submit limit exceeded (5/5)`） |
| 同时在线任务 | 2 个（429 `concurrent slot limit exceeded`） |
| 单任务耗时 | 7~13 分钟 |
| 调用凭证寿命 | ~15 分钟 |

**必须两段式**：提交只要几秒，把 job id 记下来；生成完再用**新凭证**取结果。
原地轮询必在半路 401。

⚠️ **日配额超限返回的也是 429**，正文是 `daily submit limit exceeded (5/5)`
（没有 "concurrent slot" 字样）。判错分支会把它误报成"并发已满，稍后再提交"，
导致一直空等——**先判 daily limit，再判并发**。

配额窗口**不一定按本地 00:00 翻篇**：实测本地 14:42 提交的批次，到次日 00:12 仍是 5/5。
两种可能：UTC 日切，或按提交时间滚动 24h。别在凌晨干等，排到早上和下午各试一次。

## 4. 部署后的必跑校验

1. 第 ④ 步输出 `静止还原误差：最大 0.0000`（> 0.01 不部署）
2. 引擎回归里确认 `高度=0.998 骨骼=41`（骨骼数 = 借来的现役骨架数量）
3. 本地姿态模拟三帧步态：双腿交替、无撕裂、脚掌有厚度

> 很多引擎的验证报告会有 `result: FAIL`，但那是环境噪音（缺环境贴图、
> 首帧着色器尖峰），以**脚本断言是否 PASSED** 为准，不要被报告级 FAIL 带偏。
