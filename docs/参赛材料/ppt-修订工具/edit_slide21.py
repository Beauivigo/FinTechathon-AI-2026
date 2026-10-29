"""Swap slide 21's outdated UI screenshot for the 2.0.0 capture.

Usage: python edit_slide21.py <src.pptx> <out.pptx> <screenshot.png>
"""
import sys
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

SRC = Path(sys.argv[1])
OUT = Path(sys.argv[2])
SHOT = Path(sys.argv[3])

prs = Presentation(str(SRC))
slide = prs.slides[20]


def leaves(shapes):
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from leaves(shape.shapes)
        else:
            yield shape


picture = next(shape for shape in leaves(slide.shapes) if shape.shape_type == MSO_SHAPE_TYPE.PICTURE)
image_part = picture.part.related_part(picture._element.blip_rId)
old = len(image_part.blob)
image_part._blob = SHOT.read_bytes()
print(f"replaced image part {image_part.partname}: {old} -> {len(image_part.blob)} bytes")

prs.save(str(OUT))
print("saved", OUT)
