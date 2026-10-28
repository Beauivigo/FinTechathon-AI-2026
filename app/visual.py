'''纯展示视觉系统：共享设计 token、本地静态样式与柔彩色模块表面。

本模块只包含固定常量与展示调用：不读取会话状态、不访问账务、模型、授权、
持久化或导航接口，也不接收业务数据。全部样式为本地静态 CSS 文本，没有
外链资源、字体下载、脚本执行或第三方依赖；不拼接任何业务数据或用户输入到
HTML。唯一的展示写入是 `st.html` 中的静态 `<style>` 块。

三种模块配色共用同一套文字、主操作、状态与间距 token：

- `core`：奶油黄 `#FFFFD0` + 天空蓝 `#D8EFFF`，账户、首页与核心业务。
- `ai`：薄荷绿 `#DDFFC2` + 浅紫 `#EEE1FF`，AI 助手、智能分析与计划。
- `bill`：蜜桃粉 `#FFEAE1` + 冰蓝 `#B6FAFF`，账单、预算与财务洞察。

页面通过 `apply()` 注入一次静态样式，再用 `band()` / `card()` 或
`card_key()` 显式声明模块表面。所有表面都由开发者写定的固定 key 决定，
不依赖 DOM 顺序、nth-child 或生成类名，也不隐藏任何现有控件。
'''

from __future__ import annotations

import re

import streamlit as st

__all__ = (
    'COLORS',
    'MODULES',
    'SPACING',
    'RADIUS',
    'SHADOW',
    'FONT_STACK',
    'CONTENT_WIDTH',
    'KEY_PREFIX',
    'STYLE',
    'card_key',
    'band',
    'card',
    'apply',
)

KEY_PREFIX = 'vx'
_KINDS = ('band', 'card')

#: 共用语义色。三种模块底色只改变背景气氛，不改变文字、主操作与风险语义。
COLORS = {
    'ink': '#1F3336',
    'ink_strong': '#17302F',
    'ink_soft': '#3B5257',
    'muted': '#54686B',
    'primary': '#21605F',
    'primary_strong': '#1B4F4E',
    'primary_soft': '#E7F3EF',
    'success': '#1F6350',
    'warning': '#7A5714',
    'danger': '#A32A35',
    'paper': '#FBFDFB',
    'surface': '#FFFFFF',
    'line': '#DCE8E4',
}

#: 用户指定的模块原色。`wash_*` 是对应原色的低透明度静态渐变底。
MODULES = {
    'core': {
        'label': '账户与核心业务',
        'base': '#FFFFD0',
        'accent': '#D8EFFF',
        'wash_base': 'rgba(255,255,208,.55)',
        'wash_accent': 'rgba(216,239,255,.6)',
    },
    'ai': {
        'label': 'AI 与智能分析',
        'base': '#DDFFC2',
        'accent': '#EEE1FF',
        'wash_base': 'rgba(221,255,194,.5)',
        'wash_accent': 'rgba(238,225,255,.55)',
    },
    'bill': {
        'label': '账单与财务洞察',
        'base': '#FFEAE1',
        'accent': '#B6FAFF',
        'wash_base': 'rgba(255,234,225,.58)',
        'wash_accent': 'rgba(182,250,255,.45)',
    },
}

SPACING = {
    'xxs': '4px',
    'xs': '8px',
    'sm': '12px',
    'md': '16px',
    'lg': '24px',
    'xl': '32px',
    'xxl': '48px',
    'xxxl': '64px',
    'gutter': '24px',
}

RADIUS = {'control': '12px', 'card': '22px', 'band': '30px', 'pill': '999px'}

SHADOW = {
    'line': '0 1px 2px rgba(20,58,58,.04)',
    'card': '0 1px 2px rgba(20,58,58,.04), 0 18px 36px -32px rgba(20,58,58,.42)',
    'band': '0 24px 48px -36px rgba(25,60,62,.45), inset 0 1px 0 rgba(255,255,255,.95)',
    'focus': '0 0 0 3px rgba(33,96,95,.16)',
}

FONT_STACK = (
    'IBM Plex Sans, Noto Sans SC, Source Han Sans SC, '
    'PingFang SC, Microsoft YaHei, sans-serif'
)
CONTENT_WIDTH = '1320px'


def _surface(system: str, kind: str) -> str:
    '''Return the static CSS selector for one module surface family.'''
    return f'[class*="st-key-{KEY_PREFIX}-{system}-{kind}-"]'


def _surface_list(kind: str) -> str:
    '''Return the comma-joined selector covering all three modules.'''
    return ',\n'.join(_surface(system, kind) for system in ('core', 'ai', 'bill'))


_MOTION_SURFACES = ',\n'.join((
    _surface_list('card'),
    '[class*="st-key-vx-tile-"]',
    '[class*="st-key-vx-bankcard-"]',
    '[class*="st-key-vx-activity-list-"]',
    '[class*="st-key-vx-step-"]',
    '[class*="st-key-vx-motion-"]',
    '.st-key-vx-auth-entry',
    '[data-testid="stForm"]',
    '[data-testid="stMetric"]',
    '[data-testid="stExpander"]',
    '[data-testid="stExpanderDetails"]',
    '[data-testid="stChatMessage"]',
    '[data-testid="stDataFrame"]',
    '[data-testid="stTable"]',
    '[data-testid="stAlertContainer"]',
))


STYLE = (
    '''
/* ============================================================
   1. 基础排版：本地字体回退、深色正文、轻盈页底
   ============================================================ */
html, body, .stApp, [data-testid="stAppViewContainer"] {{
  font-family: {font};
  color: {ink};
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}}
[data-testid="stAppViewContainer"] {{
  background:
    radial-gradient(1200px 560px at 6% -12%, rgba(255,255,208,.5) 0%, rgba(255,255,208,0) 62%),
    radial-gradient(1040px 540px at 100% -8%, rgba(216,239,255,.55) 0%, rgba(216,239,255,0) 60%),
    radial-gradient(900px 520px at 50% 104%, rgba(238,225,255,.26) 0%, rgba(238,225,255,0) 66%),
    linear-gradient(180deg, #FCFEFD 0%, #F6FBF9 100%);
  background-repeat: no-repeat;
}}
[data-testid="stHeader"] {{
  background: transparent;
}}
[data-testid="stMainBlockContainer"] {{
  max-width: {width} !important;
  padding-top: 4rem !important;
  padding-bottom: 3.5rem !important;
  padding-left: clamp(1rem, 3vw, 2.75rem) !important;
  padding-right: clamp(1rem, 3vw, 2.75rem) !important;
  overflow-x: clip;
}}
[data-testid="stVerticalBlock"] {{
  scrollbar-width: thin;
}}
[data-testid="stMain"] {{ scroll-padding-top: 5rem; }}
button, input, textarea, summary, [role="tab"], a {{ scroll-margin-top: 5rem; }}
[class*="st-key-vx-record-"] {{
  padding: 16px 0 !important;
  border-bottom: 1px solid {line};
}}
[class*="st-key-vx-record-"] h3 {{ margin: 0 !important; }}
[data-testid="stColumn"],
[data-testid="stHorizontalBlock"] > div {{
  min-width: 0;
}}
[data-testid="stMarkdownContainer"] {{
  overflow-wrap: break-word;
  word-break: normal;
}}
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li {{
  color: {ink} !important;
  line-height: 1.7;
  font-size: 1rem;
}}
[data-testid="stMarkdownContainer"] strong {{
  color: {ink_strong} !important;
  font-weight: 650;
}}
[data-testid="stMarkdownContainer"] a {{
  color: {primary_strong} !important;
  text-underline-offset: 2px;
}}
[data-testid="stCaptionContainer"] {{
  color: {muted} !important;
  opacity: 1 !important;
  font-size: 0.875rem !important;
  line-height: 1.6 !important;
}}
h1, h2, h3, h4, h5 {{
  font-family: {font} !important;
  color: {ink_strong} !important;
  letter-spacing: -0.012em;
  font-weight: 700 !important;
  line-height: 1.25 !important;
}}
h1 {{ font-size: clamp(1.7rem, 1.35rem + 0.8vw, 2.15rem) !important; }}
h2 {{ font-size: clamp(1.22rem, 1.06rem + 0.45vw, 1.5rem) !important; }}
h3 {{ font-size: clamp(1.06rem, 1rem + 0.2vw, 1.2rem) !important; font-weight: 650 !important; }}
hr {{ border-color: {line} !important; }}
[data-testid="stCode"], [data-testid="stJson"] {{
  border-radius: {radius_control} !important;
  border: 1px solid {line} !important;
  max-width: 100%;
}}
[data-testid="stCode"] pre, [data-testid="stJson"] pre {{
  white-space: pre-wrap !important;
  overflow-wrap: anywhere;
}}
label {{
  color: {ink_soft} !important;
  font-weight: 600 !important;
}}

/* ============================================================
   2. 模块表面：重点区域用轻玻璃与柔彩渐变，其余保持留白
   ============================================================ */
{card_list} {{
  border-radius: {radius_card} !important;
  border: 1px solid rgba(219,234,229,.95) !important;
  background: linear-gradient(158deg, rgba(255,255,255,.96) 0%, rgba(255,255,255,.84) 100%) !important;
  box-shadow: {shadow_card} !important;
  padding: 18px 20px !important;
}}
{band_list} {{
  border-radius: {radius_band} !important;
  border: 1px solid rgba(255,255,255,.92) !important;
  background: linear-gradient(135deg, rgba(255,255,255,.94) 0%, rgba(250,253,251,.84) 100%) !important;
  box-shadow: {shadow_band} !important;
  padding: 24px 28px !important;
}}
:is({band_list}) [data-testid="stCaptionContainer"] {{
  font-size: 0.94rem !important;
  color: {ink_soft} !important;
}}
:is({band_list}) h1 {{
  margin-bottom: 0.15rem !important;
}}
@supports (backdrop-filter: blur(12px)) {{
  {card_list} {{
    backdrop-filter: blur(13px) saturate(1.05);
    -webkit-backdrop-filter: blur(13px) saturate(1.05);
  }}
  {band_list} {{
    backdrop-filter: blur(16px) saturate(1.08);
    -webkit-backdrop-filter: blur(16px) saturate(1.08);
  }}
}}
/* 无 backdrop-filter 或禁用模糊时，表面仍为足够不透明的浅色卡片，文字保持清晰 */

/* 核心业务：奶油黄 → 天空蓝 */
[class*="st-key-{key}-core-band-"] {{
  background:
    radial-gradient(120% 165% at 0% 0%, {core_base} 0%, rgba(255,255,208,0) 58%),
    radial-gradient(130% 175% at 100% 0%, {core_accent} 0%, rgba(216,239,255,0) 62%),
    linear-gradient(135deg, rgba(255,255,255,.93) 0%, rgba(250,253,251,.84) 100%) !important;
}}
[class*="st-key-{key}-core-card-"] {{
  background:
    radial-gradient(120% 150% at 0% 0%, rgba(255,255,208,.4) 0%, rgba(255,255,208,0) 56%),
    radial-gradient(120% 150% at 100% 0%, rgba(216,239,255,.42) 0%, rgba(216,239,255,0) 58%),
    linear-gradient(158deg, rgba(255,255,255,.96) 0%, rgba(255,255,255,.85) 100%) !important;
}}
/* AI 与智能分析：薄荷绿 → 浅紫 */
[class*="st-key-{key}-ai-band-"] {{
  background:
    radial-gradient(120% 165% at 0% 0%, {ai_base} 0%, rgba(221,255,194,0) 58%),
    radial-gradient(130% 175% at 100% 0%, {ai_accent} 0%, rgba(238,225,255,0) 62%),
    linear-gradient(135deg, rgba(255,255,255,.93) 0%, rgba(251,253,252,.84) 100%) !important;
  border-color: rgba(224,242,231,.95) !important;
}}
[class*="st-key-{key}-ai-card-"] {{
  background:
    radial-gradient(120% 150% at 0% 0%, rgba(221,255,194,.46) 0%, rgba(221,255,194,0) 56%),
    radial-gradient(120% 150% at 100% 0%, rgba(238,225,255,.5) 0%, rgba(238,225,255,0) 58%),
    linear-gradient(158deg, rgba(255,255,255,.96) 0%, rgba(255,255,255,.85) 100%) !important;
  border-color: rgba(214,236,224,.95) !important;
}}
/* 账单与财务洞察：蜜桃粉 → 冰蓝 */
[class*="st-key-{key}-bill-band-"] {{
  background:
    radial-gradient(120% 165% at 0% 0%, {bill_base} 0%, rgba(255,234,225,0) 58%),
    radial-gradient(130% 175% at 100% 0%, {bill_accent} 0%, rgba(182,250,255,0) 62%),
    linear-gradient(135deg, rgba(255,255,255,.93) 0%, rgba(252,254,254,.84) 100%) !important;
  border-color: rgba(226,238,240,.95) !important;
}}
[class*="st-key-{key}-bill-card-"] {{
  background:
    radial-gradient(120% 150% at 0% 0%, rgba(255,234,225,.5) 0%, rgba(255,234,225,0) 56%),
    radial-gradient(120% 150% at 100% 0%, rgba(182,250,255,.4) 0%, rgba(182,250,255,0) 58%),
    linear-gradient(158deg, rgba(255,255,255,.96) 0%, rgba(255,255,255,.85) 100%) !important;
  border-color: rgba(219,236,238,.95) !important;
}}

/* ============================================================
   3. 按钮：主操作深青绿填充，次要白玻璃，第三级轻描边
   ============================================================ */
[data-testid^="stBaseButton"] {{
  border-radius: {radius_control} !important;
  min-height: 44px;
  font-weight: 620 !important;
  letter-spacing: 0.01em;
  transition: background-color .16s ease, border-color .16s ease,
              box-shadow .16s ease, color .16s ease;
}}
[data-testid="stBaseButton-primary"],
[data-testid="stBaseButton-primaryFormSubmit"] {{
  background: linear-gradient(135deg, #2A6E6C 0%, {primary_strong} 100%) !important;
  border: 1px solid rgba(21,66,66,.5) !important;
  color: #FFFFFF !important;
  box-shadow: 0 1px 2px rgba(20,58,58,.12), 0 12px 24px -16px rgba(20,58,58,.85) !important;
}}
[data-testid="stBaseButton-primary"] p,
[data-testid="stBaseButton-primaryFormSubmit"] p {{
  color: #FFFFFF !important;
}}
[data-testid="stBaseButton-primary"]:hover,
[data-testid="stBaseButton-primaryFormSubmit"]:hover {{
  background: linear-gradient(135deg, #2F7A77 0%, #1E5655 100%) !important;
  box-shadow: 0 2px 4px rgba(20,58,58,.16), 0 16px 28px -16px rgba(20,58,58,.9) !important;
}}
[data-testid="stBaseButton-secondary"],
[data-testid="stBaseButton-secondaryFormSubmit"] {{
  background: rgba(255,255,255,.94) !important;
  border: 1px solid #CBE0DA !important;
  color: {ink} !important;
  box-shadow: 0 1px 2px rgba(20,58,58,.05) !important;
}}
[data-testid="stBaseButton-secondary"]:hover,
[data-testid="stBaseButton-secondaryFormSubmit"]:hover {{
  background: #F3FAF7 !important;
  border-color: #9FC8BE !important;
}}
[data-testid="stBaseButton-secondary"] p,
[data-testid="stBaseButton-secondaryFormSubmit"] p {{
  color: {ink} !important;
}}
[data-testid="stBaseButton-tertiary"],
[data-testid="stBaseButton-tertiaryFormSubmit"] {{
  background: transparent !important;
  border: 1px solid transparent !important;
  color: {primary_strong} !important;
  min-height: 40px;
}}
[data-testid="stBaseButton-tertiary"] p,
[data-testid="stBaseButton-tertiaryFormSubmit"] p {{
  color: {primary_strong} !important;
}}
[data-testid="stBaseButton-tertiary"]:hover,
[data-testid="stBaseButton-tertiaryFormSubmit"]:hover {{
  background: rgba(33,96,95,.08) !important;
  border-color: rgba(33,96,95,.16) !important;
}}
[data-testid^="stBaseButton"]:focus-visible,
[data-testid="stFormSubmitButton"]:focus-visible,
[data-testid="stPageLink"] a:focus-visible,
a:focus-visible, button:focus-visible, summary:focus-visible,
input:focus-visible, textarea:focus-visible, [role="tab"]:focus-visible {{
  outline: 2px solid {primary} !important;
  outline-offset: 2px !important;
}}
[data-testid^="stBaseButton"]:disabled {{
  opacity: .55 !important;
  cursor: not-allowed !important;
}}
[data-testid="stPageLink"] a {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 44px;
  padding: 8px 16px;
  border-radius: {radius_control};
  border: 1px solid #D6E7E2;
  background: rgba(255,255,255,.86);
  color: {primary_strong} !important;
  font-weight: 600;
  text-decoration: none !important;
}}
[data-testid="stPageLink"] a:hover {{
  background: #F0FAF6;
  border-color: #A9CEC5;
}}

/* ============================================================
   4. 数据与状态：金额不清零、不省略；状态保留文字与颜色双重信号
   ============================================================ */
[data-testid="stMetric"] {{
  border-radius: 18px !important;
  border: 1px solid rgba(216,235,229,.95) !important;
  background: linear-gradient(160deg, rgba(255,255,255,.97) 0%, rgba(248,253,251,.86) 100%) !important;
  box-shadow: {shadow_line} !important;
  padding: 14px 16px !important;
  min-width: 0;
}}
[data-testid="stMetricLabel"] {{
  color: {muted} !important;
  font-size: 0.82rem !important;
  letter-spacing: 0.03em;
}}
[data-testid="stMetricValue"] {{
  color: {ink_strong} !important;
  font-weight: 700 !important;
  font-variant-numeric: tabular-nums;
  font-feature-settings: "tnum" 1;
  line-height: 1.28 !important;
  font-size: clamp(1.3rem, 1.05rem + 0.6vw, 1.7rem) !important;
  white-space: normal !important;
  overflow-wrap: break-word;
  overflow: visible !important;
  text-overflow: clip !important;
}}
[data-testid="stMetricValue"] [data-testid="stMarkdownContainer"],
[data-testid="stMetricValue"] [data-testid="stMarkdownContainer"] p {{
  color: inherit !important;
  font-size: inherit !important;
  line-height: inherit !important;
  white-space: normal !important;
  overflow: visible !important;
  text-overflow: clip !important;
  overflow-wrap: anywhere !important;
}}
.st-key-vx-core-card-home-account [data-testid="stMetric"] {{
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
  padding: 0 !important;
}}
.st-key-vx-core-card-home-account [data-testid="stMetricValue"] {{
  font-size: clamp(2rem, 1.6rem + 1.2vw, 2.75rem) !important;
  line-height: 1.18 !important;
}}
[data-testid="stAlertContainer"] {{
  border-radius: 16px !important;
  border: 1px solid rgba(214,232,226,.9) !important;
  box-shadow: 0 1px 2px rgba(20,58,58,.04) !important;
}}
[data-testid="stAlertContentInfo"] {{
  background: linear-gradient(135deg, rgba(216,239,255,.5) 0%, rgba(255,255,255,.92) 100%) !important;
  color: {ink_strong} !important;
}}
[data-testid="stAlertContentSuccess"] {{
  background: linear-gradient(135deg, rgba(221,255,194,.55) 0%, rgba(255,255,255,.92) 100%) !important;
  color: {success} !important;
}}
[data-testid="stAlertContentWarning"] {{
  background: linear-gradient(135deg, rgba(255,247,222,.95) 0%, rgba(255,255,255,.9) 100%) !important;
  color: {warning} !important;
  border-left: 3px solid #B07A12 !important;
}}
[data-testid="stAlertContentError"] {{
  background: linear-gradient(135deg, rgba(255,238,238,.95) 0%, rgba(255,255,255,.9) 100%) !important;
  color: {danger} !important;
  border-left: 3px solid {danger} !important;
}}
[data-testid="stAlertContentInfo"] p,
[data-testid="stAlertContentSuccess"] p,
[data-testid="stAlertContentWarning"] p,
[data-testid="stAlertContentError"] p {{
  color: inherit !important;
  font-size: 0.92rem !important;
}}
[data-testid="stAlertContainer"] a {{ color: inherit !important; }}

/* ============================================================
   5. 标签页、展开器、表单控件与表格
   ============================================================ */
[data-testid="stTabs"] [role="tablist"] {{
  gap: 4px;
  border-bottom: 1px solid {line};
  background: transparent;
  padding-bottom: 2px;
}}
[data-testid="stTabs"] [role="tab"] {{
  color: {ink_soft} !important;
  font-weight: 600 !important;
  border-radius: 12px 12px 0 0 !important;
  min-height: 44px;
  padding-left: 14px !important;
  padding-right: 14px !important;
}}
[data-testid="stTabs"] [role="tab"]:hover {{
  background: rgba(33,96,95,.06) !important;
  color: {ink_strong} !important;
}}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {{
  color: {ink_strong} !important;
  background: linear-gradient(180deg, rgba(216,239,255,.32) 0%, rgba(255,255,255,0) 100%) !important;
  box-shadow: inset 0 -3px 0 0 {primary} !important;
}}
[data-testid="stExpander"] {{
  border-radius: 16px !important;
}}
[data-testid="stExpander"] details {{
  border-radius: 16px !important;
  border: 1px solid {line} !important;
  background: rgba(255,255,255,.78) !important;
}}
[data-testid="stExpander"] summary {{
  font-weight: 600 !important;
  color: {ink_strong} !important;
  min-height: 44px;
  align-items: center;
}}
[data-testid="stExpander"] summary:hover {{
  background: rgba(33,96,95,.05) !important;
}}
[data-testid="stForm"] {{
  border-radius: 20px !important;
  border: 1px solid rgba(219,234,229,.95) !important;
  background: linear-gradient(158deg, rgba(255,255,255,.97) 0%, rgba(255,255,255,.88) 100%) !important;
  box-shadow: {shadow_card} !important;
  padding: 18px 20px !important;
}}
[data-testid="stTextInputRootElement"],
[data-testid="stTextAreaRootElement"],
[data-testid="stNumberInputContainer"],
[data-testid="stDateInputField"],
[data-testid="stDateTimeInputField"],
[data-testid="stTimeInput"] {{
  border-radius: {radius_control} !important;
  border-color: #D5E6E1 !important;
  background-color: rgba(255,255,255,.94) !important;
  min-height: 44px;
}}
[data-testid="stSelectbox"] > div {{
  border-radius: {radius_control} !important;
}}
[data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stTextAreaRootElement"]:focus-within,
[data-testid="stNumberInputContainer"]:focus-within,
[data-testid="stDateInputField"]:focus-within {{
  border-color: {primary} !important;
  box-shadow: {shadow_focus} !important;
}}
[data-testid="stDataFrame"], [data-testid="stTable"] {{
  border-radius: 16px !important;
  border: 1px solid {line} !important;
  max-width: 100%;
  overflow: hidden;
}}
[data-testid="stTable"] {{ overflow-x: auto; }}
[data-testid="stDataFrame"] [data-testid="stElementToolbar"] {{ border-radius: 12px; }}
[data-testid="stProgressBarTrack"] {{
  background: #E6F1ED !important;
  border-radius: 999px !important;
}}
[data-testid="stProgressBarTrack"] > div {{
  background: linear-gradient(90deg, #7FC9AE 0%, #7FB9E8 100%) !important;
  border-radius: 999px !important;
}}
[data-testid="stSpinner"] {{ color: {primary} !important; }}
[data-testid="stChatMessage"] {{
  border-radius: 18px !important;
  border: 1px solid rgba(221,235,230,.9) !important;
  background: rgba(255,255,255,.8) !important;
  padding: 12px 14px !important;
}}
[data-testid="stChatMessageContent"] p {{ color: {ink} !important; }}
[data-testid="stChatInput"] {{
  border-radius: 20px !important;
}}
[data-testid="stChatInputTextArea"] {{
  border-radius: 18px !important;
  border-color: #D5E6E1 !important;
  background: rgba(255,255,255,.96) !important;
}}
[data-testid="stChatInputTextArea"]:focus-within {{
  border-color: {primary} !important;
  box-shadow: {shadow_focus} !important;
}}

/* ============================================================
   6. 侧栏：浅色玻璃导航，当前页为主按钮
   ============================================================ */
[data-testid="stSidebar"] {{
  background: linear-gradient(180deg, rgba(255,255,255,.97) 0%, rgba(245,250,248,.94) 100%) !important;
  border-right: 1px solid rgba(219,234,229,.9) !important;
}}
[data-testid="stSidebarUserContent"] {{ padding-top: 0.5rem; }}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {{ letter-spacing: -0.01em; }}
[data-testid="stSidebar"] [data-testid^="stBaseButton"] {{
  justify-content: flex-start !important;
  text-align: left;
}}
[data-testid="stSidebar"] [data-testid="stExpander"] details {{
  background: rgba(255,255,255,.66) !important;
}}
[data-testid="stSidebar"] [data-testid="stMetric"] {{
  background: linear-gradient(160deg, rgba(255,255,208,.34) 0%, rgba(216,239,255,.32) 100%) !important;
}}

/* ============================================================
   7. 响应式：320 / 390 / 768 / 1024 / 1440 与 200% 缩放
   ============================================================ */
@media (max-width: 1024px) {{
  [data-testid="stMainBlockContainer"] {{
    max-width: 100% !important;
    padding-left: 1.5rem !important;
    padding-right: 1.5rem !important;
  }}
}}
@media (max-width: 768px) {{
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]) {{
    flex-direction: column !important;
    align-items: stretch !important;
  }}
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
    width: 100% !important;
    flex: 1 1 auto !important;
  }}
  .st-key-vx-core-card-home-services [data-testid="stHorizontalBlock"] {{
    display: grid !important;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }}
  .st-key-vx-core-card-home-services [data-testid="stColumn"] {{
    width: 100% !important;
    min-width: 0 !important;
  }}
  [data-testid="stMainBlockContainer"] {{
    padding-top: 4rem !important;
    padding-left: 0.9rem !important;
    padding-right: 0.9rem !important;
  }}
  h1 {{ font-size: 1.55rem !important; }}
  h2 {{ font-size: 1.18rem !important; }}
  {card_list} {{
    padding: 15px 15px !important;
    border-radius: 18px !important;
  }}
  {band_list} {{
    padding: 18px 17px !important;
    border-radius: 22px !important;
  }}
  [data-testid="stMetric"] {{ padding: 12px 13px !important; }}
  [data-testid="stMetricValue"] {{ font-size: 1.32rem !important; }}
  [data-testid="stTabs"] [role="tab"] {{
    padding-left: 10px !important;
    padding-right: 10px !important;
    font-size: 0.9rem !important;
  }}
  [data-testid="stHorizontalBlock"] {{ gap: 0.6rem !important; }}
}}
@media (max-width: 420px) {{
  [data-testid="stMainBlockContainer"] {{
    padding-left: 0.7rem !important;
    padding-right: 0.7rem !important;
  }}
  h1 {{ font-size: 1.42rem !important; }}
  {card_list} {{ padding: 13px 13px !important; }}
  {band_list} {{ padding: 16px 14px !important; }}
  [data-testid="stMetricValue"] {{ font-size: 1.2rem !important; }}
}}

 /* ============================================================
   7. 业务对象：账户/银行卡/预算柔彩块，流水紧凑清单
   ============================================================ */
/* 页面标题只承担定位；色彩集中在有真实信息的业务对象上。 */
:is({band_list}) {{
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
  backdrop-filter: none !important;
  padding: 4px 0 18px !important;
  border-radius: 0 !important;
}}
.st-key-vx-core-card-main, .st-key-vx-ai-card-main, .st-key-vx-bill-card-main {{
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
  padding: 0 !important;
  backdrop-filter: none !important;
}}
[class*="st-key-vx-tile-"], [class*="st-key-vx-bankcard-"] {{
  border: 1px solid rgba(208,226,220,.9) !important;
  border-radius: 24px !important;
  padding: 24px !important;
  box-shadow: 0 10px 26px -25px rgba(30,65,60,.5) !important;
  background: #FFFFFF !important;
  position: relative;
  overflow: hidden;
}}
[class*="st-key-vx-tile-core-"], [class*="st-key-vx-bankcard-core-"],
.st-key-vx-core-card-home-account {{
  background:
    radial-gradient(ellipse at 4% 0%, {core_base} 0%, rgba(255,255,208,0) 70%),
    radial-gradient(ellipse at 100% 90%, {core_accent} 0%, rgba(216,239,255,0) 72%),
    linear-gradient(145deg, #FFFFFF, #F4FAF8) !important;
}}
[class*="st-key-vx-tile-ai-"], .st-key-vx-ai-card-home-entry {{
  background:
    radial-gradient(ellipse at 0% 10%, {ai_base} 0%, rgba(221,255,194,0) 70%),
    radial-gradient(ellipse at 100% 90%, {ai_accent} 0%, rgba(238,225,255,0) 72%),
    linear-gradient(145deg, #FFFFFF, #F8FBF9) !important;
}}
[class*="st-key-vx-tile-bill-"] {{
  background:
    radial-gradient(ellipse at 0% 5%, {bill_base} 0%, rgba(255,234,225,0) 70%),
    radial-gradient(ellipse at 100% 90%, {bill_accent} 0%, rgba(182,250,255,0) 72%),
    linear-gradient(145deg, #FFFFFF, #F7FCFB) !important;
}}
[class*="st-key-vx-bankcard-"] {{
  min-height: 238px;
  padding: 28px !important;
  background-size: 100% 100%;
}}
:is([class*="st-key-vx-tile-"], [class*="st-key-vx-bankcard-"])
[data-testid="stMetric"] {{
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
  padding: 4px 0 8px !important;
}}
:is([class*="st-key-vx-tile-"], [class*="st-key-vx-bankcard-"])
[data-testid="stMetricValue"] {{
  font-size: clamp(1.6rem, 2vw, 2rem) !important;
  font-weight: 650 !important;
  letter-spacing: -.035em;
  font-variant-numeric: tabular-nums;
}}
[class*="st-key-vx-activity-list-"] {{
  background: rgba(255,255,255,.9) !important;
  border: 1px solid {line} !important;
  border-radius: 22px !important;
  padding: 8px 22px !important;
}}
[class*="st-key-vx-activity-bill-"], [class*="st-key-vx-activity-core-"],
[class*="st-key-vx-activity-ai-"] {{
  padding: 16px 0 !important;
  border-bottom: 1px solid {line};
}}
[class*="st-key-vx-activity-"] h3 {{
  margin: 0 !important;
  font-variant-numeric: tabular-nums;
  white-space: normal !important;
  overflow-wrap: anywhere;
}}
[class*="st-key-vx-step-"] {{
  border-left: 3px solid #B7D4AE;
  padding: 16px 18px !important;
  margin: 8px 0 !important;
  background: linear-gradient(115deg, rgba(221,255,194,.3), rgba(238,225,255,.3));
  border-radius: 4px 18px 18px 4px;
}}
.st-key-vx-core-card-home-account, .st-key-vx-ai-card-home-entry {{
  padding: 28px !important;
  min-height: 286px;
}}
.st-key-vx-core-card-home-services,
.st-key-vx-core-card-home-pending, .st-key-vx-bill-card-home-recent {{
  background: rgba(255,255,255,.86) !important;
}}
/* 首次设置、登录与恢复是独立入口，没有业务导航，避免空侧栏占位。 */
body:has(.st-key-vx-auth-entry) [data-testid="stSidebar"],
body:has(.st-key-vx-auth-entry) [data-testid="stSidebarCollapsedControl"] {{
  display: none !important;
}}
body:has(.st-key-vx-auth-entry) [data-testid="stMainBlockContainer"] {{
  max-width: 920px !important;
  margin-inline: auto !important;
  padding-top: clamp(4rem, 9vh, 7rem) !important;
}}
.st-key-vx-auth-entry [data-testid="stForm"] {{
  box-shadow: none !important;
  padding: 24px !important;
}}

/* 克制的动效：短暂入场、可交互对象反馈，不循环、不改变操作语义。 */
@keyframes vx-arrive {{
  from {{ opacity: .45; transform: translateY(7px); }}
  to {{ opacity: 1; transform: translateY(0); }}
}}
@keyframes vx-reveal {{
  from {{ opacity: .45; }}
  to {{ opacity: 1; }}
}}
[data-testid="stMain"] :is({motion_list}) {{
  animation: vx-arrive .34s cubic-bezier(.2,.7,.2,1) both;
}}
/* 嵌套表面仅淡入，避免父子位移叠加；侧栏不参与业务块入场。 */
[data-testid="stMain"] :is({motion_list}) :is({motion_list}) {{
  animation-name: vx-reveal;
}}
/* Streamlit 保留未激活标签的 DOM。清除隐藏态动画，显示时自然重播。 */
[data-testid="stMain"] [data-testid="stTabPanel"][data-inert="true"] :is({motion_list}),
[data-testid="stMain"] [data-testid="stExpanderDetails"][inert],
[data-testid="stMain"] [data-testid="stExpanderDetails"][inert] :is({motion_list}) {{
  animation-name: none !important;
}}
[data-testid^="stBaseButton"] {{
  transition: transform .14s ease, background-color .18s ease,
              border-color .18s ease, box-shadow .18s ease !important;
}}
[data-testid^="stBaseButton"]:active:not(:disabled) {{ transform: translateY(1px); }}
[data-testid="stProgressBarTrack"] > div {{ transition: width .32s ease; }}
@media (hover: hover) {{
  .st-key-vx-core-card-home-services button:hover {{ transform: translateY(-2px); }}
  [data-testid="stPageLink"] a {{ transition: background-color .18s ease, border-color .18s ease; }}
}}
@media (max-width: 768px) {{
  [class*="st-key-vx-tile-"], [class*="st-key-vx-bankcard-"] {{
    padding: 20px !important;
    border-radius: 20px !important;
  }}
  [class*="st-key-vx-activity-list-"] {{ padding: 6px 16px !important; }}
  .st-key-vx-core-card-home-account, .st-key-vx-ai-card-home-entry {{
    padding: 22px !important;
    min-height: 0;
  }}
  .st-key-vx-auth-entry [data-testid="stForm"] {{ padding: 16px !important; }}
}}

/* ============================================================
   8. 减少动态效果：尊重 prefers-reduced-motion，不做循环动画
   ============================================================ */
@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.001ms !important;
    scroll-behavior: auto !important;
  }}
}}
'''.format(
        font=FONT_STACK,
        width=CONTENT_WIDTH,
        key=KEY_PREFIX,
        card_list=_surface_list('card'),
        band_list=_surface_list('band'),
        motion_list=_MOTION_SURFACES,
        radius_control=RADIUS['control'],
        radius_card=RADIUS['card'],
        radius_band=RADIUS['band'],
        shadow_card=SHADOW['card'],
        shadow_band=SHADOW['band'],
        shadow_line=SHADOW['line'],
        shadow_focus=SHADOW['focus'],
        ink=COLORS['ink'],
        ink_strong=COLORS['ink_strong'],
        ink_soft=COLORS['ink_soft'],
        muted=COLORS['muted'],
        primary=COLORS['primary'],
        primary_strong=COLORS['primary_strong'],
        success=COLORS['success'],
        warning=COLORS['warning'],
        danger=COLORS['danger'],
        line=COLORS['line'],
        core_base=MODULES['core']['base'],
        core_accent=MODULES['core']['accent'],
        ai_base=MODULES['ai']['base'],
        ai_accent=MODULES['ai']['accent'],
        bill_base=MODULES['bill']['base'],
        bill_accent=MODULES['bill']['accent'],
    )
)


def _page_wash(system: str) -> str:
    '''Return the static page wash rule for one module palette.'''
    module = MODULES[system]
    return (
        '[data-testid="stAppViewContainer"]{{background:'
        'radial-gradient(1200px 560px at 6% -12%, {base} 0%, rgba(255,255,255,0) 62%),'
        'radial-gradient(1040px 540px at 100% -8%, {accent} 0%, rgba(255,255,255,0) 60%),'
        'linear-gradient(180deg, #FCFEFD 0%, #F6FBF9 100%);'
        'background-repeat:no-repeat;}}'
    ).format(base=module['wash_base'], accent=module['wash_accent'])


def card_key(system: str, name: str, *, kind: str = 'card') -> str:
    '''Build the stable widget key (and CSS class) for one visual surface.

    Args:
        system: `core`, `ai` or `bill`; unknown values fall back to `core`.
        name: literal developer-chosen surface name, e.g. `home-account`.
        kind: `card` for content cards, `band` for the page header band.

    Returns:
        A key such as `vx-core-card-home-account`. The matching style is
        derived from the fixed prefix only, so no DOM order is involved.
    '''
    module = system if system in MODULES else 'core'
    family = kind if kind in _KINDS else 'card'
    slug = re.sub(r'[^a-z0-9]+', '-', str(name).strip().lower()).strip('-')
    return f'{KEY_PREFIX}-{module}-{family}-{slug or "surface"}'


def band(system: str = 'core', name: str = 'header'):
    '''Return a bordered container styled as the page header band.'''
    return st.container(border=True, key=card_key(system, name, kind='band'), gap='xsmall')


def card(system: str = 'core', name: str = 'main', *, gap: str = 'small'):
    '''Return a bordered container styled as one module surface card.'''
    return st.container(border=True, key=card_key(system, name), gap=gap)


def apply(system: str = 'core') -> None:
    '''Insert the fixed local style sheet plus the current module page wash.

    Only static module constants are inserted; no business value, label or
    user input is ever concatenated into the style block.
    '''
    module = system if system in MODULES else 'core'
    st.html(
        '<style>\n'
        + STYLE
        + '\n/* 当前模块页底气氛 */\n'
        + _page_wash(module)
        + '\n</style>'
    )
