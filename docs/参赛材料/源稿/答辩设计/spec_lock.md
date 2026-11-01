<!-- ppt-master-schema: spec-lock/v1 -->
# Execution Lock

## canvas
- viewBox: 0 0 1280 720
- format: PPT 16:9

## communication
- primary_language: zh-CN
- audience: 比赛评委与答辩听众，兼顾技术、安全、产品
- objective: 通过核实的实现和证据，让听众理解可信执行主干、六场景价值并区分已完成与未验证事项。
- core_message: 模型提出计划，可信程序掌握授权与执行；六场景形成可追溯的本地模拟闭环。

## mode
- mode: custom
- mode_references: narrative
- mode_behavior: 从入口分散提出问题，由模型与可信程序的边界给出解法，六场景提供具体实例，验收与演示回收主张；判断式标题推动叙事。

## visual_style
- visual_style: custom
- visual_style_references: glassmorphism, soft-rounded
- visual_style_behavior: 浅色多站渐变形成连续光场，少量半透明白色大面板提供空间层次；统一柔和圆角、深色标题和正文。glassmorphism仅贡献光场及薄边层次，soft-rounded贡献亲和轮廓；画布由用户指定为浅色，按页面任务变化关系图、路径、主数字与截图组合。

## colors
- background: #F8FBFA
- secondary_bg: #FFFFFF
- primary: #21605F
- accent: #FFFFD0
- secondary_accent: #D8EFFF
- body_text: #183B3A
- secondary_text: #466460
- divider: #D5E5E0
- mint: #DDFFC2
- lilac: #EEE1FF
- peach: #FFEAE1
- ice: #B6FAFF
- success: #247657
- warning: #7A5B12
- danger: #A64153

## typography
- font_family: Microsoft YaHei, Arial, sans-serif
- title_family: Microsoft YaHei, Arial, sans-serif
- body_family: Microsoft YaHei, Arial, sans-serif
- body: 24
- title: 44
- subtitle: 28
- label: 20
- annotation: 14
- hero: 88
- chapter: 72

## icons
- library: none
- inventory: none

## images
- p14: images/image_5daba817b45aff1d.png | source=user | crop=no-crop
- p19: images/image_0e3ceb2a470bbfc4.png | source=user | crop=no-crop
- p20: images/image_643568a83421dd24.png | source=user | crop=no-crop
- p21: images/image_f236390284a9e9df.jpg | source=user | crop=no-crop

## page_rhythm
- P01: breathing
- P02: dense
- P03: anchor
- P04: anchor
- P05: dense
- P06: dense
- P07: dense
- P08: dense
- P09: breathing
- P10: dense
- P11: dense
- P12: anchor
- P13: dense
- P14: anchor
- P15: dense
- P16: anchor
- P17: breathing
- P18: dense
- P19: anchor
- P20: anchor
- P21: anchor
- P22: dense
- P23: dense
- P24: breathing

## pptx_structure
- mode: flat

## forbidden
- `mask`, `<style>`, `class`, external CSS, `<foreignObject>`, `textPath`, `@font-face`, `<animate*>`, `<set>`, `<script>` / event attributes, `<iframe>`
- HTML named entities in text; write typography as raw Unicode and escape XML reserved characters
