"""Diff slide text between deck versions and emit a markdown table."""
import json
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

VERSIONS = [
    ("原稿", "言行_FinTechathon_2026_24页_原稿.pptx"),
    ("v1", "言行_FinTechathon_2026_24页_D修订.pptx"),
    ("v2", "言行_FinTechathon_2026_24页_D修订_v2.pptx"),
    ("v3", "言行_FinTechathon_2026_24页_D修订_v3.pptx"),
]


def leaves(shapes):
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from leaves(shape.shapes)
        else:
            yield shape


def slide_texts(path):
    prs = Presentation(path)
    result = []
    for slide in prs.slides:
        texts = [shape.text_frame.text.replace("\x0b", "").strip()
                 for shape in leaves(slide.shapes)
                 if shape.has_text_frame and shape.text_frame.text.strip()]
        result.append(texts)
    return result


data = {name: slide_texts(path) for name, path in VERSIONS}

rows = []
for index in range(len(data["原稿"])):
    base = data["v2"][index]
    new = data["v3"][index]
    removed = [text for text in base if text not in new]
    added = [text for text in new if text not in base]
    for old, updated in zip(removed, added):
        rows.append((index + 1, old, updated))

Path("text_changes.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

lines = ["| 页 | 原文 | 改后 |", "| --- | --- | --- |"]
for page, old, new in rows:
    lines.append(f"| {page} | {old} | {new} |")
Path("text_changes.md").write_text("\n".join(lines), encoding="utf-8")
print("changes:", len(rows))
for page, old, new in rows[:8]:
    print(page, "|", old, "->", new)
