# urhox-glb-to-mdl

Convert a **rig-less GLB model** (AI-generated or downloaded) into a **skeleton-equipped `.mdl`
that TapTap Maker / the UrhoX engine can actually load**.

Everything runs locally. No vendor AI conversion service (they charge per model and you cannot
control the result).

> 中文版：[README.md](./README.md)

---

## Why this exists

The UrhoX resource loader **does not parse glTF**. Drop a GLB into `assets/` and you get an
empty model:

- bone count = 0
- bounding box = (1, 1, 1)
- the character never moves

The model must be converted to the UMD2 `.mdl` format. That format has no public spec, so
everything below was reverse-engineered byte by byte and verified against shipping assets.

---

## Quick start

```bash
# 0. Pick a "baseline" .mdl — any rigged model that already works in your project.
#    Its bone section is what we borrow (see "Three hard rules").

# 1. Rig the rig-less GLB (afk-rig wrapper)
python scripts/rig_glb.py model.glb            # -> model_rigged.glb

# 2. Run the four conversion steps in one command, with hard assertions
python scripts/pipeline.py --in model_rigged.glb --live baseline.mdl \
       --out ./work --name mymodel \
       --deploy ./assets/model/mymodel/Meshes   # optional: copy straight into place

# 2.5 Textures: extract the ones the model already ships with (costs no quota)
python scripts/extract_glb_textures.py model.glb --out-dir ./tex --name mymodel
python scripts/derive_normal.py ./tex/mymodel_D.png   # only if the GLB has no normal map
```

Step ④ prints a `rest-reduction error`. It **must be `0.0000`** — anything above `0.01` and the
script refuses to emit the file.

Requirements: Python 3 + `numpy` + `Pillow`, and Node.js (for `afk-rig`).

---

## Three hard rules (the three most expensive lessons)

### 1. You must *borrow* a working skeleton

Using your own skeleton values — even byte-consistent ones, even with inverse-matrix round-trip
error `0.00000` — makes the engine **hang during parsing**. The cause is unknown. The only
working route: remap **vertices** onto an existing skeleton and copy that model's bone section
verbatim.

Bisection results:

| Configuration | Result |
|---|---|
| Own vertices + shipping skeleton | ✅ loads |
| Own bone names + shipping bone values | ✅ loads |
| **Any fully self-built bone section** | ❌ **always hangs** |

### 2. Pre-skin by inverting the *blended* matrix

At rest the engine computes `v_rest = Σ wᵢ·Aᵢ·v'` where `Aᵢ = BoneWorld_restᵢ · offsetᵢ`.
To make the rest pose reproduce the original mesh exactly, solve:

```python
M = Σ (wᵢ / Σw) · Aᵢ        # blend first
v' = inv(M) · v              # then invert the blended matrix
```

Do **not** write `v' = Σ wᵢ·Aᵢ⁻¹·v` — matrix summation and inversion do not commute.
Getting this wrong leaves error up to **0.19** in the joint weight transition band
(for a model that is only 1.0 tall), which shows up as **legs torn off below the knee and feet
with no thickness**.

### 3. No axis rotation at all

Both the source GLB and the shipping skeleton are **Y-up**. Adding a "-90° X for Z-up" step makes
the model lie down with its head buried in the ground.

How to tell: check the bounding box `size`. `(X≈1.84, Y≈0.375, Z≈1.90)` means height ended up on
the Z axis, i.e. rotated wrongly (correct is around `(0.38~0.97, 0.998, 0.19)`).

---

## Pipeline

```
rig-less GLB
   │  scripts/rig_glb.py            (afk-rig: start / inspect / bind / export)
   ▼
rigged GLB
   │  scripts/pipeline.py           (all four steps + hard assertions)
   ├─ ① glb_to_urhox.py   GLB -> UMD2
   ├─ ② reweight.py       recompute skinning weights
   ├─ ③ lower_arms.py     bake T-pose arms down
   └─ ④ retarget.py       retarget onto the baseline skeleton + pre-skin
   ▼
deployable .mdl
```

Steps can also be run individually while debugging:

```bash
python scripts/glb_to_urhox.py in.glb out.mdl
python scripts/reweight.py     in.mdl out.mdl [power=4]
python scripts/lower_arms.py   in.mdl out.mdl [deg=65]
python scripts/retarget.py     in.mdl baseline.mdl out.mdl [leg_blend=0] [live_height=0.998]
```

**Key arguments**

| Argument | Meaning |
|---|---|
| `--live` | baseline `.mdl`; its bone section is borrowed |
| `--leg-blend` | **must be 0**; anything above flattens the feet |
| `--live-height` | the baseline's height **in its own model space**; default 0.998, change it when you swap baselines |
| `--arm-deg` | arm droop angle, default 65 |

---

## After deploying

1. Generate a `.meta` for the asset (the engine needs a uuid to reference it)
2. Register the model path plus `height` (height **in the model's own space**, not world space)
3. Run your regression and confirm `height=0.998 bones=<baseline bone count>`

---

## Layout

```
SKILL.md                        Skill definition (when used as a WorkBuddy Skill)
scripts/
  rig_glb.py                    rig a rig-less GLB (afk-rig wrapper)
  glb_to_urhox.py               GLB (glTF 2.0) -> UMD2 .mdl
  reweight.py                   recompute skinning weights by bone-segment distance
  lower_arms.py                 bake T-pose arms down
  retarget.py                   retarget onto the baseline skeleton + pre-skin + rest check
  pipeline.py                   all four steps + hard assertions
  probe_mdl.py                  parse a .mdl and dump vertices / bones / bounding box
  extract_glb_textures.py       pull the model's own PBR maps out of the GLB (_D / _N / _MR)
  derive_normal.py              derive a normal map from baseColor (Sobel)
references/
  umd2-format.md                byte-level UMD2 layout
  troubleshooting.md            15-entry fault dictionary (incl. dead ends already ruled out)
  generation-spec.md            AI prompt spec / afk-rig details / cloud quotas
```

---

## Troubleshooting

Look up the symptom in [`references/troubleshooting.md`](./references/troubleshooting.md).
The three most commonly misdiagnosed ones:

| Symptom | Real cause |
|---|---|
| Legs torn off | pre-skin written as `Σ w·A⁻¹` (does not commute) — not a mesh problem |
| Engine hangs while parsing | self-built bone values — not bounding box or LOD |
| Model lying down / no head | an extra Z-up rotation — both sides are already Y-up |

---

## Validation

The generalized scripts here were checked with a **byte-level regression**: the same model was run
through both the original in-project pipeline and this repository's scripts, and the two `.mdl`
files came out identical (`737,082` bytes, `identical: True`).

That check also caught a real bug: `lower_arms`' arm bone set **must not include the clavicle**,
otherwise the shoulder line gets twisted along with the arms (29,417 bytes differed from the
reference output).

---

## License

MIT — see [LICENSE](./LICENSE).
