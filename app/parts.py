"""Parts of the exploded-view drawing, for the 3D viewer: where each part is, its front-to-back order, its requirements.

One Claude vision call per run finds each drawn part: a name, a bounding box (0-1000 on both axes of the image),
its order along the assembly axis (0 = nearest the viewer), the callout text next to it if any, and which part of
the product profile it is. The result is cached as out/runs/<id>/08_parts.json and can be corrected with PUT. Each
part also lists the ids of the requirements that concern it, matched in code on the requirement's `applies_to`
and text, so the viewer can show them.
"""

from __future__ import annotations

import base64
import json
import re

from pydantic import BaseModel, Field

from app import runs

DRAWING = re.compile(r"^exploded_view\.(png|jpe?g)$", re.I)
STOP = set("the a an of and or for with to on in part parts module modules assembly unit".split())


class Box(BaseModel):
    x0: int = Field(description="left edge, 0-1000 of the image width")
    y0: int = Field(description="top edge, 0-1000 of the image height")
    x1: int = Field(description="right edge, 0-1000 of the image width")
    y1: int = Field(description="bottom edge, 0-1000 of the image height")


class DrawnPart(BaseModel):
    name: str = Field(description="short name of the part, e.g. 'Outer lens'")
    profile_part: str = Field(description="the matching name from the product profile's parts list, or empty")
    boxes: list[Box] = Field(description="one tight box per drawn instance of the part (three separate screws = "
                                         "three boxes; usually one), around the drawn part only: not its callout "
                                         "text or leader line")
    depth: int = Field(description="order along the assembly / explosion axis: 0 = the part nearest the viewer "
                                   "(outermost), increasing towards the back")
    callout: str = Field(description="text written next to the part in the drawing, if any; else empty")


class DrawnParts(BaseModel):
    parts: list[DrawnPart]
    axis_dx: float = Field(description="direction in which the parts are exploded, as an image vector from the "
                                       "frontmost part towards the rearmost part: x component (right = positive)")
    axis_dy: float = Field(description="same, y component (down = positive)")


SYSTEM = """You read exploded-view drawings of vehicle components. Find every separate part drawn in the image
(lens, gasket, reflector, circuit board, housing, brackets, fasteners …) and give each a tight bounding box around
the drawn part itself, excluding callout labels, leader lines and dimension text. Several identical small parts
(for example three screws) may be one part with a box around all of them. Order the parts along the assembly
axis: depth 0 is the outermost part, nearest the viewer of the assembled product. Use the product profile's part
names where a drawn part is one of them."""


def drawing(run_id: str):
    folder = runs.run_dir(run_id) / "input"
    if not folder.is_dir():
        return None
    return next((p for p in sorted(folder.iterdir()) if DRAWING.match(p.name)), None)


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z][a-z\-]{2,}", (text or "").lower()) if w not in STOP}


def _attach_requirements(run_id: str, parts: list[dict]) -> list[dict]:
    """Requirements whose applies_to (or, failing that, text) shares the part's distinctive words."""
    reqs = runs.requirements(run_id)
    for p in parts:
        key = _words(p["name"]) | _words(p.get("profile_part", ""))
        ids = []
        for r in reqs:
            target = _words(r.get("applies_to") or "") or _words(r["text"])
            if key and key & target:
                ids.append(r["id"])
        p["requirements"] = ids
    return parts


def load(run_id: str) -> dict | None:
    data = runs.read_json(run_id, "08_parts.json")
    if data:
        data["parts"] = _attach_requirements(run_id, data["parts"])
    return data


def save(run_id: str, data: dict) -> dict:
    clean = {"axis": data.get("axis", [1.0, 1.0]), "image": data.get("image"),
             "parts": [{k: p.get(k) for k in ("name", "profile_part", "box", "boxes", "depth", "callout")}
                       for p in data.get("parts", [])]}
    (runs.run_dir(run_id) / "08_parts.json").write_text(json.dumps(clean, indent=1, ensure_ascii=False))
    return load(run_id)


def detect(run_id: str) -> dict:
    from pipeline.llm import ask
    img = drawing(run_id)
    if img is None:
        raise FileNotFoundError("This analysis has no exploded-view drawing.")
    profile = runs.read_json(run_id, "01_profile.json") or {}
    names = [p.get("name", "") for p in profile.get("parts", [])]
    media = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": media,
                                     "data": base64.standard_b64encode(img.read_bytes()).decode()}},
        {"type": "text", "text": f"Product profile parts: {', '.join(names) or 'unknown'}.\n"
                                 "Find the drawn parts, their boxes (0-1000) and their order along the assembly axis."},
    ]
    found = ask(DrawnParts, SYSTEM, content, effort="medium")
    parts = []
    for p in found.parts:
        inst = []
        for b in p.boxes:
            x0, x1 = sorted((max(0, min(1000, b.x0)), max(0, min(1000, b.x1))))
            y0, y1 = sorted((max(0, min(1000, b.y0)), max(0, min(1000, b.y1))))
            if x1 - x0 >= 3 and y1 - y0 >= 3:
                inst.append([x0, y0, x1, y1])
        if not inst:
            continue
        union = [min(b[0] for b in inst), min(b[1] for b in inst), max(b[2] for b in inst), max(b[3] for b in inst)]
        parts.append({"name": p.name, "profile_part": p.profile_part, "depth": p.depth, "callout": p.callout,
                      "box": union, "boxes": inst})
    parts.sort(key=lambda p: p["depth"])
    for i, p in enumerate(parts):  # dense order 0..n-1 even if the model repeated or skipped numbers
        p["depth"] = i
    return save(run_id, {"axis": [found.axis_dx, found.axis_dy], "image": img.name, "parts": parts})


class FixedBox(BaseModel):
    part: int = Field(description="the number drawn on the box")
    boxes: list[Box] = Field(description="corrected tight box(es) for that part, 0-1000; one per instance")


class Fixes(BaseModel):
    fixes: list[FixedBox] = Field(description="every part, with its corrected boxes (unchanged ones repeated)")


REFINE = """The image shows an exploded-view drawing with numbered coloured boxes, one per part, drawn by a first
pass. Many are too large or shifted. For every numbered part, return a corrected tight box around that part's own
lines only: not the neighbouring parts, not callout text, not leader lines or the dash-dot assembly lines. Keep
one box per instance (separate screws get separate boxes)."""


def refine(run_id: str) -> dict:
    """Second vision pass: show the model its own numbered boxes and let it correct them."""
    import cv2
    from pipeline.llm import ask

    data = runs.read_json(run_id, "08_parts.json")
    img = cv2.imread(str(drawing(run_id)))
    H, W = img.shape[:2]
    colours = [(40, 40, 198), (0, 108, 239), (50, 125, 46), (192, 101, 21), (154, 27, 106), (55, 64, 93), (87, 20, 173)]
    for i, p in enumerate(data["parts"]):
        for b in p.get("boxes") or [p["box"]]:
            x0, y0, x1, y1 = int(b[0] * W / 1000), int(b[1] * H / 1000), int(b[2] * W / 1000), int(b[3] * H / 1000)
            cv2.rectangle(img, (x0, y0), (x1, y1), colours[i % len(colours)], 5)
            cv2.putText(img, str(i), (x0 + 8, y0 + 48), cv2.FONT_HERSHEY_SIMPLEX, 1.6, colours[i % len(colours)], 4)
    ok, buf = cv2.imencode(".png", img)
    legend = "\n".join(f"{i}: {p['name']}" for i, p in enumerate(data["parts"]))
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.standard_b64encode(buf.tobytes()).decode()}},
               {"type": "text", "text": f"Numbered parts:\n{legend}\nReturn corrected boxes for every part."}]
    fixes = ask(Fixes, REFINE, content, effort="medium")
    for f in fixes.fixes:
        if 0 <= f.part < len(data["parts"]) and f.boxes:
            inst = []
            for b in f.boxes:
                x0, x1 = sorted((max(0, min(1000, b.x0)), max(0, min(1000, b.x1))))
                y0, y1 = sorted((max(0, min(1000, b.y0)), max(0, min(1000, b.y1))))
                if x1 - x0 >= 3 and y1 - y0 >= 3:
                    inst.append([x0, y0, x1, y1])
            if inst:
                p = data["parts"][f.part]
                p["boxes"] = inst
                p["box"] = [min(b[0] for b in inst), min(b[1] for b in inst), max(b[2] for b in inst),
                            max(b[3] for b in inst)]
    return save(run_id, data)


# ------------------------------------------------------------------------------------------- cut-outs per part

def cutouts(run_id: str) -> dict:
    """Cut each part's own strokes out of the drawing as a transparent PNG (out/runs/<id>/parts/<i>.png).

    Boxes overlap, so a plain rectangle would carry pieces of neighbouring parts. Instead: erase the callout
    column and the leader lines that run to it, drop the dash-dot assembly lines, split the rest into connected
    strokes and give each stroke to the smallest part box that holds it. Returns the crop box (pixels) per part.
    """
    import cv2
    import numpy as np

    data = runs.read_json(run_id, "08_parts.json")
    img_path = drawing(run_id)
    if not data or img_path is None:
        raise FileNotFoundError("Detect the parts first.")
    gray = cv2.imread(str(img_path), cv2.IMREAD_GRAYSCALE)
    H, W = gray.shape
    ink = (gray < 170).astype(np.uint8)
    px = lambda b: [int(b[0] * W / 1000), int(b[1] * H / 1000), int(b[2] * W / 1000), int(b[3] * H / 1000)]
    boxes = [px(p["box"]) for p in data["parts"]]
    instances = [(i, px(b)) for i, p in enumerate(data["parts"]) for b in (p.get("boxes") or [p["box"]])]
    cut = min(W, max(b[2] for b in boxes) + int(0.04 * W))  # the callout column starts right of all parts
    # leader lines: long straight segments that reach into the callout column
    lines = cv2.HoughLinesP(ink * 255, 1, np.pi / 720, threshold=120, minLineLength=int(0.12 * W), maxLineGap=6)
    erase = np.zeros_like(ink)
    for x1, y1, x2, y2 in (lines.reshape(-1, 4) if lines is not None else []):  # (N,1,4) in OpenCV 4, (N,4) in 5
        if max(x1, x2) >= cut - int(0.02 * W):
            cv2.line(erase, (int(x1), int(y1)), (int(x2), int(y2)), 1, thickness=7)
    ink[erase > 0] = 0
    ink[:, cut:] = 0
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    owner = np.full(n, -1)
    held = np.zeros(n)
    skip = np.zeros(n, bool)
    for c in range(1, n):
        x, y, w, h, area = stats[c]
        long_side, short_side = max(w, h), max(1, min(w, h))
        if area < 25 or (long_side < 0.035 * W and long_side / short_side > 3.5 and area < 600):
            skip[c] = True
            continue  # specks and the dashes of dash-dot assembly lines
        best, best_key = None, None
        for i, b in instances:
            ix = max(0, min(x + w, b[2]) - max(x, b[0]))
            iy = max(0, min(y + h, b[3]) - max(y, b[1]))
            frac = ix * iy / (w * h)
            # among boxes that hold most of the stroke: the one whose centre is nearest, relative to its size
            # (in an exploded view a part's own strokes cluster round its own centre; boxes are only approximate)
            cx, cy = x + w / 2, y + h / 2
            bx, by, bd = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, max(1.0, ((b[2] - b[0]) ** 2 + (b[3] - b[1]) ** 2) ** 0.5)
            key = -(((cx - bx) ** 2 + (cy - by) ** 2) ** 0.5) / bd
            if frac >= 0.6 and (best_key is None or key > best_key):
                best, best_key = i, key
        owner[c] = -1 if best is None else best
        held[c] = max((max(0, min(x + w, b[2]) - max(x, b[0])) * max(0, min(y + h, b[3]) - max(y, b[1]))) / (w * h)
                      for _, b in instances)
    assigned = owner[labels]
    assigned[labels == 0] = -1
    # strokes of overlapping parts touch and merge into one component: split those pixel by pixel, each pixel
    # going to the nearest part centre (relative to box size) among the boxes that contain it
    merged = np.flatnonzero((held < 0.85) & ~skip)
    merged = merged[merged > 0]
    if len(merged):
        ys, xs = np.nonzero(np.isin(labels, merged))
        best_d = np.full(len(xs), np.inf)
        pick = np.full(len(xs), -1)  # pixels outside every box stay unassigned
        for i, b in instances:
            inside = (xs >= b[0]) & (xs < b[2]) & (ys >= b[1]) & (ys < b[3])
            bx, by = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            bd = max(1.0, ((b[2] - b[0]) ** 2 + (b[3] - b[1]) ** 2) ** 0.5)
            d = np.hypot(xs - bx, ys - by) / bd
            better = inside & (d < best_d)
            best_d[better], pick[better] = d[better], i
        assigned[ys, xs] = pick
    for i in range(len(boxes)):
        # a part left with little of the ink in its box claims it back: a small part (a retro-reflector insert whose
        # strokes touch a gasket) takes it from anyone, a large part only takes ink nobody owns
        x0, y0, x1, y1 = boxes[i]
        inside = ink[y0:y1, x0:x1] > 0
        if not inside.sum() or (assigned[y0:y1, x0:x1][inside] == i).mean() >= 0.3:
            continue
        small = (x1 - x0) * (y1 - y0) < 0.03 * W * H
        for j, b in instances:
            if j == i:
                region = assigned[b[1]:b[3], b[0]:b[2]]
                take = ink[b[1]:b[3], b[0]:b[2]] > 0
                if not small:
                    take &= region < 0
                region[take] = i
    folder = runs.run_dir(run_id) / "parts"
    folder.mkdir(exist_ok=True)
    crops = []
    soft = cv2.dilate(ink, np.ones((3, 3), np.uint8))  # keep anti-aliased edges of the strokes
    for i in range(len(boxes)):
        mine = (assigned == i).astype(np.uint8)
        mine = cv2.dilate(mine, np.ones((3, 3), np.uint8)) & soft
        ys, xs = np.nonzero(mine)
        if len(xs) == 0:  # nothing assigned: fall back to the plain box
            x0, y0, x1, y1 = boxes[i]
            mine = np.zeros_like(ink)
            mine[y0:y1, x0:x1] = soft[y0:y1, x0:x1]
            ys, xs = np.nonzero(mine)
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
        alpha = ((255 - gray[y0:y1, x0:x1].astype(np.int32)) * mine[y0:y1, x0:x1]).clip(0, 255).astype(np.uint8)
        rgba = np.dstack([np.full_like(alpha, 23), np.full_like(alpha, 26), np.full_like(alpha, 32), alpha])
        cv2.imwrite(str(folder / f"{i}.png"), cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
        crops.append([x0, y0, x1, y1])
    data["crops"] = crops
    data["size"] = [W, H]
    (runs.run_dir(run_id) / "08_parts.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))
    return {"size": [W, H], "crops": crops}
