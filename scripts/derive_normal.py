#!/usr/bin/env python3
"""Derive a tangent-space normal map from a base-color map (Sobel on luminance).

Most generated models ship a baseColor map but **no** normalTexture. Rather than
spending an image-generation quota on one, derive it locally: for skin, cloth and
rotting surfaces the luminance gradient is enough to read as volume under PBR
lighting (pores, cracks, weave).

Usage
-----
    python derive_normal.py IN_D.png [OUT_N.png] [--strength 2.0]

``--strength`` controls bumpiness. Image-generation channels only produce colour
maps, so this is the cheap path — and it costs no quota.

Requires: Pillow, numpy
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image


def derive_normal(src, dst, strength=2.0):
    gray = np.asarray(Image.open(src).convert("L"), dtype=np.float32) / 255.0
    # Sobel-ish central differences on the luminance field
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    gy[1:-1, :] = gray[2:, :] - gray[:-2, :]

    # normal = normalize(-s*gx, -s*gy, 1); tangent-space +Z points outward
    nx, ny, nz = -gx * strength, -gy * strength, np.ones_like(gray)
    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    nx, ny, nz = nx / norm, ny / norm, nz / norm
    rgb = np.stack([nx * 0.5 + 0.5, ny * 0.5 + 0.5, nz * 0.5 + 0.5], axis=-1)
    out = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8), "RGB")
    out.save(dst, optimize=True)
    return dst


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="base-color map, e.g. hero_D.png")
    ap.add_argument("dst", nargs="?", help="output normal map (default: <src stem>_N.png)")
    ap.add_argument("--strength", type=float, default=2.0)
    args = ap.parse_args()

    src = Path(args.src)
    if args.dst:
        dst = Path(args.dst)
    else:
        stem = src.stem
        for tag in ("_D", "_diffuse", "_basecolor", "_albedo"):
            if stem.lower().endswith(tag.lower()):
                stem = stem[: -len(tag)]
                break
        dst = src.with_name(stem + "_N.png")

    derive_normal(src, dst, args.strength)
    print("%s -> %s (%d KB)" % (src.name, dst.name, dst.stat().st_size // 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
