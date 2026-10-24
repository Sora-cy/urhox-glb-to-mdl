#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一条命令跑完「带骨骼 GLB → UrhoX/Maker 可用 .mdl」四步，并**强制校验**。

用法:
    python scripts/pipeline.py --in brute_rigged.glb --live baseline.mdl --out ./out
    python scripts/pipeline.py --in x.glb --live baseline.mdl --out ./out \
        --name brute --leg-blend 0 --live-height 0.998

四步与硬指标（任何一步不合格就**不产出**最终文件）:
    ① glb_to_urhox.py   GLB → UMD2（骨骼数、顶点数会打印出来核对）
    ② reweight.py       重算蒙皮权重（打印各骨骼占比，左右应大致对称）
    ③ lower_arms.py     T-pose 手臂烘焙下垂（默认 65°）
    ④ retarget.py       重定向到现役骨架 + 预蒙皮
                        **静止还原误差必须 ≤ 0.01**（大于就是腿会被撕断的那种病）

参数要点:
    --live          现役/基底 .mdl（借它的骨骼段；见 SKILL.md「为什么必须借骨架」）
    --leg-blend     腿部关节高度对齐强度，**必须 0**（>0 会把脚掌压扁）
    --live-height   基底模型自身空间的身高（默认 0.998，换基底必须改）
    --name          产物名，默认取输入文件名主干
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
ERR_LIMIT = 0.01


def step(label, args, expect=None, cwd=None):
    print("\n--- %s ---" % label, flush=True)
    p = subprocess.run([PY] + args, capture_output=True, text=True, cwd=str(cwd or HERE))
    out = (p.stdout or "") + (p.stderr or "")
    print("\n".join(out.strip().splitlines()[-10:]), flush=True)
    if p.returncode != 0:
        raise SystemExit("!! 失败：%s" % label)
    if expect:
        m = re.search(expect[0], out)
        if not m:
            raise SystemExit("!! 没读到期望输出：%s（%s）" % (expect[0], label))
        val = float(m.group(1))
        if val > expect[1]:
            raise SystemExit("!! %s = %.4f 超过阈值 %.4f —— 不部署" % (label, val, expect[1]))
        print("   %s = %.4f ✓" % (label, val), flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True, help="带骨骼的 GLB")
    ap.add_argument("--live", required=True, help="现役/基底 .mdl（提供骨骼段）")
    ap.add_argument("--out", required=True, help="中间产物与最终 mdl 的输出目录")
    ap.add_argument("--name", default=None)
    ap.add_argument("--arm-deg", default="65", help="手臂下垂角度")
    ap.add_argument("--leg-blend", default="0", help="腿部对齐强度，必须 0")
    ap.add_argument("--live-height", default="0.998", help="基底模型自身空间身高")
    ap.add_argument("--deploy", default=None, help="可选：最终 mdl 复制到该目录")
    args = ap.parse_args()

    # 子进程以 skill 的 scripts/ 为 cwd 运行，所以这里必须先把调用者给的相对路径
    # 解析成绝对路径，否则会被当成相对 scripts/ 而找不到文件。
    src = Path(args.src).resolve()
    live = Path(args.live).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        raise SystemExit("找不到输入 GLB：%s" % src)
    if not live.exists():
        raise SystemExit("找不到基底 mdl：%s" % live)
    name = args.name or src.stem.replace("_rigged", "")

    s1 = out_dir / ("%s_1.mdl" % name)
    s2 = out_dir / ("%s_2.mdl" % name)
    s3 = out_dir / ("%s_3.mdl" % name)
    final = out_dir / ("%s_final.mdl" % name)

    step("① GLB → UMD2", [str(HERE / "glb_to_urhox.py"), str(src), str(s1)])
    step("② 重算蒙皮权重", [str(HERE / "reweight.py"), str(s1), str(s2)])
    step("③ 手臂烘焙下垂", [str(HERE / "lower_arms.py"), str(s2), str(s3), args.arm_deg])
    step("④ 重定向 + 预蒙皮",
         [str(HERE / "retarget.py"), str(s3), str(live), str(final),
          args.leg_blend, args.live_height],
         expect=(r"静止还原误差：最大 ([\d.]+)", ERR_LIMIT))

    if args.deploy:
        d = Path(args.deploy).resolve()
        d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(final, d / ("%s.mdl" % name))
        print("\n已部署 → %s" % (d / ("%s.mdl" % name)), flush=True)

    print("\n✅ %s 完成 → %s（%.0f KB）"
          % (name, final, final.stat().st_size / 1024))
    print("   待办：① 给资源生成 .meta  ② 在配置里登记模型路径与 height  "
          "③ 跑一次回归确认骨骼数与包围盒")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
