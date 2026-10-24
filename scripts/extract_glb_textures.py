#!/usr/bin/env python3
"""Extract a model's own PBR textures out of a GLB (baseColor / normal / metallic-roughness).

Why this exists
---------------
AI-generated models **already ship with their own PBR textures**. Pulling them out
costs nothing, while re-generating textures through an image API burns a very scarce
quota (e.g. 5 submissions/day). Always try this first; only fall back to generated
textures when the GLB really has none.

Usage
-----
    python extract_glb_textures.py model.glb [--out-dir DIR] [--name hero_gunner]
                                   [--max-side 2048]

Writes ``<name>_D.png`` / ``<name>_N.png`` / ``<name>_MR.png`` (whichever the
material actually references).

Notes
-----
* Only textures **referenced by a material** are extracted; unused images (some
  exporters embed an unused AO map) are skipped.
* Only the **first material** is scanned — generated models normally have exactly one.
* Output is always PNG (lossless; no generation loss across repeated edits).
* Sides larger than ``--max-side`` are downscaled (mobile VRAM).
* External file URIs are not supported (embedded / data-URI only).

Requires: Pillow
"""
from __future__ import annotations

import base64
import io
import json
import struct
import sys
from pathlib import Path

MAX_SIDE = 2048

# material slot -> output suffix
SLOTS = (("baseColorTexture", "_D"),
         ("normalTexture", "_N"),
         ("metallicRoughnessTexture", "_MR"))


def read_glb(path):
    data = Path(path).read_bytes()
    magic, _ver, length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:  # "glTF"
        raise SystemExit("not a GLB file: %s" % path)
    off = 12
    gltf = None
    bin_data = b""
    while off + 8 <= length:
        clen, ctype = struct.unpack_from("<II", data, off)
        body = data[off + 8: off + 8 + clen]
        if ctype == 0x4E4F534A:  # JSON
            gltf = json.loads(body.decode("utf-8"))
        elif ctype == 0x004E4942:  # BIN
            bin_data = body
        off += 8 + clen
    if gltf is None:
        raise SystemExit("GLB has no JSON chunk: %s" % path)
    return gltf, bin_data


def buffer_bytes(gltf, bin_data, index):
    bv = gltf["bufferViews"][index]
    start = bv.get("byteOffset", 0)
    return bin_data[start: start + bv["byteLength"]]


def image_bytes(gltf, bin_data, img_index):
    img = gltf["images"][img_index]
    if "bufferView" in img:
        return buffer_bytes(gltf, bin_data, img["bufferView"]), \
            (img.get("mimeType") or "image/png")
    uri = img.get("uri", "")
    if uri.startswith("data:"):
        head, payload = uri.split(",", 1)
        return base64.b64decode(payload), head.split(";")[0][5:]
    raise SystemExit("externally referenced textures are not supported: %s" % uri[:60])


def collect_slots(gltf):
    """Return [(suffix, image_index), ...] for the first material that has any."""
    for mat in gltf.get("materials", []):
        pbr = mat.get("pbrMetallicRoughness", {})
        found = []
        for key, tag in SLOTS:
            tex = pbr.get(key)
            if not tex:
                continue
            idx = tex.get("index")
            if idx is None:
                continue
            entry = gltf["textures"][idx]
            if "source" not in entry:
                continue
            found.append((tag, entry["source"]))
        if found:
            return found
    return []


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    src = Path(argv[0])
    out_dir = None
    name = None
    max_side = MAX_SIDE
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--out-dir" and i + 1 < len(argv):
            out_dir = Path(argv[i + 1])
        elif a == "--name" and i + 1 < len(argv):
            name = argv[i + 1]
        elif a == "--max-side" and i + 1 < len(argv):
            max_side = int(argv[i + 1])
        i += 1

    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("Pillow is required: pip install pillow")

    gltf, bin_data = read_glb(src)
    name = name or src.stem
    out_dir = out_dir or src.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    slots = collect_slots(gltf)
    if not slots:
        raise SystemExit("no referenced PBR textures found in %s" % src.name)

    for tag, img_idx in slots:
        raw, mime = image_bytes(gltf, bin_data, img_idx)
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        w, h = im.size
        if max(w, h) > max_side:
            k = max_side / float(max(w, h))
            im = im.resize((int(w * k), int(h * k)), Image.LANCZOS)
        dst = out_dir / ("%s%s.png" % (name, tag))
        im.save(dst)
        print("  %-4s %dx%d -> %s" % (tag, im.size[0], im.size[1], dst.name))

    have = {tag for tag, _ in slots}
    if "_N" not in have:
        print("  note: no normalTexture in this GLB - run derive_normal.py on the _D map")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
