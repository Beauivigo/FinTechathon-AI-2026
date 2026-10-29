"""Revise D's wealth-management slide (13): fix the suitability logic, show the
questionnaire -> score -> level chain, and put the verified worked example in.
"""
import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "言行_FinTechathon_2026_24页_原稿.pptx")
DST = Path(sys.argv[2] if len(sys.argv) > 2 else "言行_FinTechathon_2026_24页_D修订.pptx")

DARK = RGBColor(0x17, 0x30, 0x2F)
GREEN = RGBColor(0x1F, 0x63, 0x50)
RED = RGBColor(0xA3, 0x2A, 0x35)

prs = Presentation(str(SRC))
slide = prs.slides[12]


def leaves(shapes):
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from leaves(shape.shapes)
        else:
            yield shape


def by_text(text):
    for shape in leaves(slide.shapes):
        if shape.has_text_frame and shape.text_frame.text.strip() == text:
            return shape
    raise KeyError(text)


def set_text(shape, text):
    paragraph = shape.text_frame.paragraphs[0]
    paragraph.runs[0].text = text
    for run in paragraph.runs[1:]:
        run._r.getparent().remove(run._r)


def drop(shape):
    shape._element.getparent().remove(shape._element)


def add_line(left, top, width, height, text, size, color, bold=False):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = box.text_frame
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    paragraph.alignment = PP_ALIGN.LEFT
    run = paragraph.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return box


# 1. The section label now names the questionnaire -> score -> level chain.
label = by_text("风险等级 R1–R5")
set_text(label, "问卷 5 题 → 总分 5–20 → 风险档")
label.width = int(Inches(6.2))

# 2. Each level carries its score band.
levels = {"保守": "保守 5–8", "稳健": "稳健 9–12", "平衡": "平衡 13–16",
          "成长": "成长 17–18", "进取": "进取 19–20"}
for old, new in levels.items():
    shape = by_text(old)
    set_text(shape, new)
    center = shape.left + shape.width / 2
    shape.width = int(Inches(1.05))
    shape.left = int(center - Inches(1.05) / 2)

# 3. Replace the misleading "R2 ≠ R4" with the actual ordering rule.
for text in ("R2 用户", "R4 产品", "R2 ≠ R4", "拒绝"):
    drop(by_text(text))
add_line(0.85, 4.58, 11.6, 0.26, "允许：用户档位 ≥ 产品档位（R3 用户可买 R2 产品）", 10.5, GREEN, bold=True)
add_line(0.85, 4.86, 11.6, 0.26, "拒绝：用户档位 < 产品档位（R2 用户买 R4 产品）；不进入确认/强验证，不扣款", 10.5, RED, bold=True)

# 4. The fee panel states the verified example instead of describing it.
set_text(by_text("费用到分；份额 4 位；0.5 分进位可复算"), "500 元 → 费 50 分 → 份额 499.5")

captions = [shape for shape in leaves(slide.shapes)
            if shape.has_text_frame and shape.text_frame.text.strip() == "可执行、可复算"]
captions.sort(key=lambda shape: shape.left)
print("captions at", [round(shape.left.inches, 2) for shape in captions])

fee_caption, boundary_caption = captions[1], captions[2]
set_text(fee_caption, "0.5 分进位 · 份额 4 位")
fee_caption.width = int(Inches(1.9))

# 5. The boundary panel keeps the no-yield-promise fact.
set_text(boundary_caption, "不承诺收益")

prs.save(str(DST))
print("saved", DST)
