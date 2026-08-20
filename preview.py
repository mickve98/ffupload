"""
Turn sliced gcode into compact per-layer geometry for the on-screen preview.

Only extruding moves are kept — travel moves would clutter the picture without
telling you anything about the print. Coordinates are rounded to 0.1 mm and
handed over as flat arrays, which keeps the payload small enough to send to a
phone.
"""

import re

MOVE = re.compile(r"([XYZEF])(-?\d*\.?\d+)")


def parse(path, max_segments=24000, max_layers=400):
    x = y = z = 0.0
    e = 0.0
    absolute_xyz = True
    absolute_e = True
    layers = {}

    with open(path, "r", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line or line[0] == ";":
                continue
            semi = line.find(";")
            if semi != -1:
                line = line[:semi].rstrip()

            code = line[:3].upper()

            if code.startswith("G90"):
                absolute_xyz = True
                continue
            if code.startswith("G91"):
                absolute_xyz = False
                continue
            if code.startswith("M82"):
                absolute_e = True
                continue
            if code.startswith("M83"):
                absolute_e = False
                continue

            if code.startswith("G92"):
                for axis, value in MOVE.findall(line):
                    v = float(value)
                    if axis == "E":
                        e = v
                    elif axis == "X":
                        x = v
                    elif axis == "Y":
                        y = v
                    elif axis == "Z":
                        z = v
                continue

            if not (code.startswith("G0") or code.startswith("G1")):
                continue

            nx, ny, nz, de = x, y, z, 0.0
            for axis, value in MOVE.findall(line):
                v = float(value)
                if axis == "X":
                    nx = v if absolute_xyz else x + v
                elif axis == "Y":
                    ny = v if absolute_xyz else y + v
                elif axis == "Z":
                    nz = v if absolute_xyz else z + v
                elif axis == "E":
                    de = (v - e) if absolute_e else v
                    e = v if absolute_e else e + v

            extruding = de > 0.0 and (nx != x or ny != y)
            if extruding:
                key = round(nz, 2)
                layers.setdefault(key, []).append(
                    (round(x, 1), round(y, 1), round(nx, 1), round(ny, 1)))

            x, y, z = nx, ny, nz

    if not layers:
        return {"layers": [], "bed": 220, "truncated": False}

    heights = sorted(layers)
    truncated = False

    # Too many layers to scrub through comfortably: sample them evenly.
    if len(heights) > max_layers:
        step = len(heights) / max_layers
        heights = [heights[int(i * step)] for i in range(max_layers)]
        truncated = True

    total = sum(len(layers[h]) for h in heights)
    keep_every = 1
    if total > max_segments:
        keep_every = (total // max_segments) + 1
        truncated = True

    out = []
    for h in heights:
        segs = layers[h][::keep_every]
        flat = []
        for a, b, c, d in segs:
            flat += [a, b, c, d]
        out.append({"z": h, "s": flat})

    return {"layers": out, "bed": 220, "truncated": truncated,
            "segments": total, "shown": sum(len(l["s"]) // 4 for l in out)}
