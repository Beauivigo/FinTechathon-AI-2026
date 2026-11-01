# 银行助手 UI 设计与演示

当前交付：已按用户授权同步主项目并完成v2.0.0构建和覆盖安装；[最新验收](2026-10-28言行v2.0.0发布说明.md)。下方首轮与反馈补修为同步前记录。

状态：2026-10-28，隔离候选已完成柔彩视觉系统与业务对象布局调整，独立复核 正在进行最终独立验收，全量回归与浏览器结论待本轮验收记录补齐。尚未同步主目录、提交或推送。本文件描述当前候选实现；10 月 21 日、10 月 23 日的数字与截图属于历史证据，见 [UI界面验收](2026-10-21UI界面验收.md) 与 [银行操作统一验收](2026-10-28银行操作统一验收.md)。

本文件记录设计 token、共享组件、页面接线、真实能力边界与验证方式。它不替代浏览器视觉验收，也不宣称未经实际运行的结论。

## 设计方向

**受众**：参加 FinTechathon 演示与验收的业务人员、运营人员与评审，以及需要核对本机模拟数据的操作者。

**主要任务**：用中文快速确认当前页面、已有数据和下一步入口；带着规则识别出的草稿进入对应业务流程核对；在有后端数据时清楚区分已有值、空值和未知状态。

**视觉定位**：calm fintech，现代、轻盈、温柔、清透且可信的 AI 金融应用。以账户、银行卡、产品、订阅和预算这些业务对象组织页面，把柔彩放到有实际内容的块上；标题和外围结构保持安静，让金额、状态与下一步操作先被看见。沿用现有架构、原生控件与业务流程，通过留白、字体层级、低饱和多层渐变、细边框和统一圆角提升完成度。

设计判断为 `DESIGN_VARIANCE=5 / MOTION_INTENSITY=2 / VISUAL_DENSITY=3`：布局有适度变化，动效只用于入场与交互反馈，信息密度舒展。审美重点落在比例、对齐、层级和业务对象之间的关系，避免把所有数据套上同一种展开器。

### 真实银行参照与本项目取舍

2026 年 10 月 5 日的 [Apple Developer Monzo 设计故事](https://developer-rno.apple.com/news/?id=1q3azf1j) 展示了账户卡、快捷操作与近期活动同屏的银行首页，并强调设计一致性、性能和可识别的文字操作。当前 [Monzo 官方首页帮助](https://monzo.com/ie/help/using-monzo/help-home-screen) 明确把账户放在顶部，将最新及即将发生的付款放进下方 Activity 区域，并为 Pots 保留独立区域。

据此，本项目采用“业务对象块 + 紧凑活动流水”的信息结构。这里借鉴的是内容层级和对象组织方式；配色、排版与交互仍按本项目的桌面 Streamlit 环境和用户指定的三套柔彩设计。

### 块、流水与表格的分工

| 内容 | 默认呈现 | 保留的核对能力 |
| --- | --- | --- |
| 账户、银行卡、产品、订阅、预算 | 柔彩业务块；名称、关键金额、状态与主要属性直接可读 | 原业务表单、操作和明细保持原流程 |
| 近期交易、收支明细、提醒、预约与订单 | 紧凑 Activity 清单，时间/对象与金额/状态分层排列 | 技术编号和来源留在明细位置 |
| AI 任务和已保存计划 | Steps 步骤块，沿现有顺序展示依赖与状态 | 原确认、恢复、强验证与执行入口 |
| 产品参数、当前流水对账、历史账单对账 | 概览与原生表格并列为标签页 | 只有这三处显式使用 `table_view=True` |

不把所有表格折叠，也不把每一笔流水做成高大的彩色卡片。账户等对象承担视觉识别，流水清单承担连续阅读，确有逐项比较需要的参数和对账表保留原生表格。展示层只接收已有字段，不补造余额、收益、进度或状态。

### 三种模块配色

三种配色属于同一套系统：文字、主操作、风险状态、圆角、间距与字体完全相同，只有背景气氛和局部点缀变化。

| 模块 | 用户指定原色 | 使用范围 |
| --- | --- | --- |
| B 核心业务 `core` | 奶油黄 `#FFFFD0` + 天空蓝 `#D8EFFF` | 首页、账户概览、转账、卡片、AA、预约、订阅、生日计划、理财、我的及通用表单 |
| A AI 与智能分析 `ai` | 薄荷绿 `#DDFFC2` + 浅紫 `#EEE1FF` | AI 银行助手、智能理解、计划与待办准备区域 |
| C 账单与财务洞察 `bill` | 蜜桃粉 `#FFEAE1` + 冰蓝 `#B6FAFF` | 当前账单、历史样例分析、预算管理、近期交易与财务图表 |

### 信息层级

- `render_page_header` 提供唯一页面标题和一句可选说明；页头保留 `band` 结构标记，但当前样式为通透的无边框标题区，色彩集中在下方业务对象。
- `render_app_nav` 只提供最多三个主导航入口；当前页按钮 disabled，其余按钮为第三级按钮，避免误跳。
- 概要、详情、状态和进度只显示调用方传入的值；空值显示占位，未知状态保持灰色「结果待核实」。
- 业务控件放在 `business_section` 或页面卡片中，原有 key、标签和执行顺序不变。
- 资金金额使用等宽数字、清晰货币单位和较大字号；余额、金额、状态和验证码都是深色文字，不使用浅色或渐变文字。

### 桌面与移动

- 桌面优先：主内容最大宽度 `1320px`，左右留白随视口在 `1rem`–`2.75rem` 之间收放，超宽屏不铺满。
- 响应式验收目标覆盖 `1440`、`1024`、`768`、`390`、`320` 与桌面 200% 缩放，保持同一阅读顺序。样式在 `1024` 以下取消业务页最大宽度，`768` 与 `420` 以下进一步收紧内边距与字号；实际表现待最终浏览器记录。
- 金额允许在 `¥` 与数字之间换行，不截断、不省略、不缩成不可读的小字。
- 宽表格、代码与接口示例应在自身区域内滚动或换行；最终验收检查整页水平溢出。
- 触控目标约 `44px`：按钮最小高度、标签页高度与展开器摘要高度都按此设置。
- 焦点可见：键盘焦点使用 `2px` 深青绿描边并带 `2px` 外偏移。

### 自审与删减

- 渐变与轻玻璃主要用于账户、银行卡、AI 入口和业务对象块；页头及外围容器保持通透，避免层层嵌套的卡片边框。
- 默认保留约 `0.34s` 的短暂入场（轻微上移与透明度变化）、按钮按压、常用服务悬停，以及约 `0.14s`–`0.32s` 的交互/进度过渡。不使用循环漂浮或持续发光。
- 仅当系统请求 `prefers-reduced-motion: reduce` 时，动画与过渡降到近零时长；这是可访问性例外，不是默认关闭动效。
- 状态始终是“文字 + 颜色”双重信号；颜色不是唯一信息。
- 不按 `nth-child` 或 DOM 顺序区分模块；模块表面由固定容器 key 决定。身份入口页以 `vx-auth-entry` 标记精确移除空业务侧栏及其折叠按钮；正常业务导航与控件不受此规则影响。
- 不堆静态画面，也不为了演示效果伪造业务数值或成功回执。

## 设计 token 与主题配置

### 核心色板 `PALETTE`

`app/ui.py` 的 `PALETTE` 与 `app/visual.py` 的 `COLORS` 共用同一组语义值：

| 名称 | 色值 | 用途 |
| --- | --- | --- |
| `ink` | `#1F3336` | 正文、表单与普通文字 |
| `ink_strong` | `#17302F` | 标题、余额与金额等重点数据 |
| `paper` | `#FBFDFB` | 页面底色 |
| `surface` | `#FFFFFF` | 卡片与内容表面 |
| `muted` | `#54686B` | 次要说明、辅助标签 |
| `border` | `#DCE8E4` | 细边框与分隔线 |
| `primary` | `#21605F` | 主操作、焦点与当前页 |
| `danger` | `#A32A35` | 风险、失败与不可逆提示 |

`app/visual.py` 的 `COLORS` 还提供 `ink_soft`、`primary_strong`、`primary_soft`、`success`、`warning` 和 `line`。状态语义固定为成功 `#1F6350`、警告 `#7A5714`、风险 `#A32A35`，三种模块配色共用，不各自定义状态色。

### `THEME_TOKENS`

`THEME_TOKENS` 的键和值固定为：
- `primaryColor`：`#21605F`
- `backgroundColor`：`#FBFDFB`
- `secondaryBackgroundColor`：`#FFFFFF`
- `textColor`：`#1F3336`
- `borderColor`：`#DCE8E4`
- `baseRadius`：`0.75rem`
- `buttonRadius`：`0.75rem`
- `showWidgetBorder`：`True`
- `headingFontSizes`：`['2.15rem', '1.5rem', '1.25rem', '1.125rem', '1rem', '0.875rem']`

`SIDEBAR_THEME_TOKENS` 为 `backgroundColor` `#FFFFFF`、`secondaryBackgroundColor` `#F5FAF8`。

原生主题是基础，负责控件形状、原生状态与图表默认色板；配置如下。独立复核 最终审查已将隔离候选的 `.streamlit/config.toml` 与共用 token 同步，并补齐一致的状态和图表分类色；静态样式补充按钮、标签页、指标、提示、输入框、侧栏等表面。主目录的配置未覆盖，最终验收结果另见本轮独立报告。

```toml
[theme]
primaryColor = '#21605F'
backgroundColor = '#FBFDFB'
secondaryBackgroundColor = '#FFFFFF'
textColor = '#1F3336'
borderColor = '#DCE8E4'
baseRadius = '0.75rem'
buttonRadius = '0.75rem'
showWidgetBorder = true
headingFontSizes = ['2.15rem', '1.5rem', '1.25rem', '1.125rem', '1rem', '0.875rem']

[theme.sidebar]
backgroundColor = '#FFFFFF'
secondaryBackgroundColor = '#F5FAF8'
```

其余主题参数保持原有服务端与安全配置不变；本文件不记录任何服务端参数。

### 静态样式与表面 key

`app/visual.py` 是纯展示模块，承载可复用的设计 token 与一段固定的本地静态 CSS，不含业务依赖、脚本行为、外链资源或第三方库：

| 常量 | 内容 |
| --- | --- |
| `COLORS` | 共用语义色 |
| `MODULES` | 三种模块的标签、原色与低透明度页底 |
| `SPACING` | `4` / `8` / `12` / `16` / `24` / `32` / `48` / `64px` 与常规 `24px` 间距 |
| `RADIUS` | 控件 `12px`、内容卡片 `22px`、页头带基础 token `30px`、胶囊 `999px`；业务对象块使用 `24px`，小屏收至 `20px` |
| `SHADOW` | 细边框阴影、卡片阴影、页头带阴影与焦点环 |
| `FONT_STACK` | 本机字体回退链 |
| `CONTENT_WIDTH` | `1320px` 最大内容宽度 |
| `STYLE` | 固定样式文本：渐变、玻璃、按钮、指标、标签页、提示、表单、表格、侧栏、响应式与减少动态效果 |

样式通过 `apply()` 写入 `st.html` 的固定 `<style>` 块；该块只包含模块常量，不拼接业务数据、标签或用户输入。用户在本次任务中明确要求渐变、背景模糊、细阴影与克制微动效，因此这些静态样式是本项目的明确设计选择，优先于此前的单色约定。

基础模块表面只由显式容器 key 决定，命名规则为 `vx-<system>-card-<name>` 与 `vx-<system>-band-<name>`，由 `card_key()`、`card()`、`band()` 生成。`st.container(key=...)` 会把 key 写成 `st-key-` 前缀的稳定 class，因此样式选择器是 `[class*="st-key-vx-<system>-card-"]` 这类固定前缀，不依赖 DOM 顺序、`nth-child` 或框架生成的哈希类名。

业务记录还使用 `vx-tile-`、`vx-bankcard-`、`vx-activity-list-`、`vx-activity-` 和 `vx-step-` 固定前缀，分别对应对象块、银行卡、流水列表、流水项和计划步骤。`vx-auth-entry` 只标记尚未进入业务区的身份入口。

### 字体

`FONT_STACK` 为 `IBM Plex Sans, Noto Sans SC, Source Han Sans SC, PingFang SC, Microsoft YaHei, sans-serif`。全部是本机候选，不下载字体、不请求外部字体服务；缺字时交回平台回退。正文 `0.95rem` 左右、辅助 `0.85rem`，页标题约 `1.7rem`–`2.15rem`，余额与金额使用等宽数字，业务对象重点金额约 `1.6rem`–`2rem`，并随小屏收缩。

### 可访问性

- `ink` 对 `paper` 约 `12.96:1`，白色对 `primary` 约 `7.23:1`，`muted` 对 `surface` 约 `5.88:1`，`danger` 对 `surface` 约 `7.14:1`；静态测试对这些值设有下限断言；本轮最终执行结果另行记录。
- 模块原色上的文字仍是深墨色：三种原色对 `ink` 的对比度都在 `10:1` 以上；余额、金额、控件标签与验证码不使用浅色文字。
- badge 同时显示中文文字；当前页通过 disabled 与主按钮双重表达。
- 未知状态额外显示 warning 文本，不是只在颜色上变化。
- 键盘焦点在按钮、标签页、链接、输入框与展开器上均可见。
- 不支持 `backdrop-filter` 时，卡片与页头带仍使用高不透明度浅色背景与细边框，文字与边界保持清晰。

## 组件 API

`app/ui.py` 仍是纯展示模块，只允许标准库的 `__future__`、`contextlib`、`typing` 和已有 `streamlit`；不导入 agent、银行、权限、持久化、Conversation、资金换算或授权执行，不访问会话状态或缓存。`app/home_view.py` 同样只接收调用方传入的 dataclass 并返回 action_id。

### 展示函数

- `render_page_header(title, subtitle=None)`：调用 `st.title`；空标题回退「银行助手」，副标题非空时用 `st.caption`。
- `render_sidebar_nav(current_key, *, hide_home_views=False)`：侧栏分组导航，当前页为 `type='primary'`，其余为 `type='tertiary'`；key 固定为 `ui_menu_<key>`，仅在点击非当前页时切换页面。
- `render_app_nav(current_key, *, keys=PRIMARY_NAV_KEYS, key_prefix='app_nav')`：在横向容器中最多渲染三个原生按钮。按钮 key 为 `app_nav_<key>`，宽度使用 content；当前页 disabled，均为第三级按钮。仅在点击非当前页时调用 `st.switch_page`；路径移除仓库相对的 app/ 前缀，以匹配主脚本目录。
- `render_overview(items, *, columns=3, title=None)`：只接收 label、value、help，最多三列；空列表只显示「暂无」说明且不生成 metric，空值显示「—」。
- `render_details(items, *, title=None)`：接受 mapping、二元组或 label/value mapping 列表；空值显示「未填写」，完全无数据只显示「暂无」说明。
- `render_records(records, ..., layout='tiles', tone='core', table_view=False)`：复用已有字段渲染 `tiles`、`bankcards`、`activity` 或 `steps`，支持分组、摘要与调用方传入的进度值；默认直接显示内容。只有显式 `table_view=True` 才增加参数/对账标签页，保留原生 `st.dataframe`。该组件不调用业务服务，不修改记录。
- `render_status(state, *, title=None, detail=None)`：用 `st.badge` 显示 `STATUS_META`；未识别状态显示「结果待核实」、灰色并显示 warning，任何状态都不调用成功或错误提示。
- `render_progress(steps, *, title=None)`：接受 label/status mapping 或二元组；未知步骤保持「结果待核实」和灰色，空列表只显示「暂无」说明。
- `render_empty_state(title, *, description=None, action_label=None, action_key=None)`：用带边框的原生容器展示空状态；同时提供 label 和稳定 key 时才生成按钮，并返回该按钮的真实布尔结果，自身不执行业务动作。
- `business_section(title, *, description=None, key=None)`：上下文管理器，用 `st.container(border=True, key=None)` 包裹原业务区域并 yield 该容器；传 `key` 时由调用方指定视觉表面，控件 key、标签与顺序不变。
- `render_scope_note(extra=None)`：用 `st.caption` 说明展示草稿边界，可附加一句调用方提供的说明。

### 视觉模块 API

- `apply(system='core')`：注入固定静态样式与当前模块页底气氛；`system` 取 `core`、`ai`、`bill`，未知值回退 `core`。
- `card(system, name, *, gap='small')`：带边框容器，作为模块卡片；key 为 `vx-<system>-card-<name>`。
- `band(system='core', name='header')`：带边框容器，作为页头柔彩带；key 为 `vx-<system>-band-<name>`。
- `card_key(system, name, *, kind='card')`：只返回 key，用于把同一表面交给 `business_section` 等原生容器。
- `MODULES`、`COLORS`、`SPACING`、`RADIUS`、`SHADOW`、`FONT_STACK`、`CONTENT_WIDTH`、`STYLE`：可复用 token 与样式文本。

页面里的标准写法：

```python
from app import ui, visual

visual.apply('bill')
with visual.band('bill', 'ledger'):
    ui.render_page_header('收支明细')
    ui.render_app_nav('bills')
with visual.card('bill', 'ledger-flow'):
    ...原有控件与回调保持不动...
```

### 页面与 current_key 映射

| current_key | 页面 | 文件 | 侧栏 | 模块 |
| --- | --- | --- | --- | --- |
| `home` | 首页与转账 | `app/main.py` | 是 | B 核心 |
| `mine` | 我的 | `app/pages/10_我的.py` | 是 | B 核心 |
| `accounts` | 历史样例分析 | `app/pages/1_资料账单.py` | 分组 | C 账单 |
| `products` | 产品对比 | `app/pages/2_产品对比.py` | 分组 | B 核心 |
| `cards` | 卡片管理 | `app/pages/3_卡片管理.py` | 分组 | B 核心 |
| `aa` | AA 收款 | `app/pages/4_AA收款.py` | 分组 | B 核心 |
| `subscriptions` | 订阅管理 | `app/pages/5_订阅管理.py` | 分组 | B 核心 |
| `scheduled` | 预约转账 | `app/pages/6_定时转账.py` | 分组 | B 核心 |
| `budget` | 预算管理 | `app/pages/7_预算管理.py` | 分组 | C 账单与洞察 |
| `merchant` | 生日计划 | `app/pages/8_生日与商户.py` | 分组 | B 核心 |
| `wealth` | 理财 | `app/pages/9_理财.py` | 是 | B 核心 |
| `assistant` | AI 银行助手 | `app/pages/0_AI银行助手.py` | 是 | A AI |
| `transfer` | 转账与收款 | `app/main.py` | 是 | B 核心 + A 聊天 |
| `bills` | 收支明细 | `app/main.py` | 是 | C 账单 |

`PRIMARY_NAV_KEYS` 为 `('home',)`。`NAV_ITEMS` 是 `(current_key, 路径)` 的固定顺序序列，中文标签由 `NAV_LABELS` 提供。

### 状态映射

| 状态 key | badge 文字 | badge 颜色 |
| --- | --- | --- |
| `draft` | 草稿 | gray |
| `ready` | 可核对 | blue |
| `pending` | 待确认 | orange |
| `running` | 处理中 | blue |
| `blocked` | 受阻 | red |
| `failed` | 未成功 | red |
| `done` | 已完成 | green |
| `unknown` | 结果待核实 | gray |

业务状态 `awaiting_confirmation`、`awaiting_verification`、`succeeded`、`cancelled`、`needs_reconciliation` 也有对应中文标签。不在表中的状态同样显示「结果待核实」与灰色，并触发 `render_status` 的 warning。

### 数据和边界语义

- 余额、流水、收益、回执、业务状态与能力都只能由现有后端或原流程传入，展示层不推断、不生成、不美化。
- 传入的 HTML 形式字符串只交给原生文本组件安全显示，不执行脚本；样式块不含任何动态文本。
- 空列表、空值、未知状态和未决结果均有明确中性展示，不使用生成式占位数字。
- 组件不使用会话状态、缓存或页面副作用；所有展示调用都可重复执行。

## 最终接线与验收

### 接线原则

- 只增加展示调用、容器和排版，不重写页面业务逻辑，不改变资金、权限、认证、安全、确认或强验证代码。
- 保留全部控件 key、标签、禁用条件、回调顺序、导航语义与页面跳转；首页仍保留 `st.columns([1.5,1])` 这一比例的转账聊天与当前操作区（代码写作 `st.columns([1.5, 1], gap="large")`）。
- 不新增业务规则，不在 UI 中补算金额、收益、成功回执或状态。
- 三种模块只改变表面配色，不改语义：状态、风险、主操作和金额在 A/B/C 页面完全一致。

### 页面统一结构

1. 在页面已有导入之后加入 `from app import ui`，需要视觉表面时再加 `from app import visual`。
2. `st.set_page_config(...)` 之后、其它 Streamlit 命令之前调用 `visual.apply(<system>)`。
3. 用 `with visual.band(<system>, <name>):` 组织标题与页面导航；当前 band 样式通透，不做大面积彩色标题海报。
4. 表单与结果复用 `visual.card` / `business_section`；记录按内容使用 `ui.render_records` 的业务块、流水或步骤布局。内部控件保持原 key、标签与执行顺序，不机械地为所有区域重复套卡片。
5. 只把现有后端已经给出的真实字段传给 `ui.render_overview`、`ui.render_details`、`ui.render_status` 或 `ui.render_progress`。
6. 空数据仍由原页面决定文案与按钮，展示层只负责外观。

### 首页

`app/home_view.py` 严格纯展示：接收 `HomeData` 并返回 action_id，不访问会话状态、存储、服务、认证或导航。本轮只调整布局与表面：

```text
首页
├─ visual.band('core', 'home')：通透标题与「本地模拟项目」说明
├─ 待处理操作（原 pending_view，key 与业务不变）
├─ 使用指南（原 onboarding，未改流程）
├─ 账户概览（B）           AI 银行助手入口（A）
├─ 常用业务（B，四个常用 + 全部服务展开）
├─ 待处理事项（B）         近期交易（C，清单式三条）
└─ 使用说明展开器
```

- 账户卡片继续显示账户名称、可用余额与预留金额；助手卡片继续显示「打开 AI 银行助手」按钮。
- 常用业务与全部服务按钮的 key 仍为 `home_service_<action_id>`，顺序与目标页面不变；返回的 action_id 仍交给 `app/main.py` 处理。
- 近期交易由密集表格改为两列清单：左侧内容与时间，右侧金额与状态；仍只显示调用方传入的三条记录，缺失值显示「时间暂不可用」「金额暂不可用」「状态未知」。
- 不使用 `st.session_state`、`st.switch_page` 或任何业务调用。

### 转账与账单

- `app/main.py` 保留 `home_workspace` 四个标签、全部控件键、`direct_transfer`、确认/强验证按钮、待办恢复与回调。
- 页头与标签页说明保持通透；直接转账表单使用 B 核心语义，自然语言聊天使用 A AI 语义；当前操作与回执保留原有核对顺序。
- 收支明细以 C 体系的紧凑 Activity 清单为默认视图，并保留「对账表」标签页；账单图表仍用原生 `st.bar_chart`，只调整容器与配色语义。

### AI 助手与计划

- `app/pages/0_AI银行助手.py` 使用 A 模块带；`render_model_bank()` 内部把对话区与「当前步骤」包成 A 卡片，模型调用、计划保存、待办准备与确认逻辑完全不变。
- DeepSeek 初次交付时没有改写 `app/model_view.py`、`app/presentation.py`、`app/workflow.py`；这属于历史交付边界。当前 独立复核 最终修正已调整 `app/workflow.py` 的 AA 参与人展示，以及 `app/saved_plan_view.py` 的计划步骤展示；确认、验证码、恢复、执行条件与业务调用保持原实现。

### 身份入口与空侧栏修复

`app/auth_ui.py` 在欢迎、设置本机账号、续设恢复/绑定、登录/恢复、未绑定与身份数据不可用等入口分支使用纯展示容器 `vx-auth-entry`。`app/visual.py` 只在页面存在该标记时隐藏空业务侧栏及侧栏折叠按钮，将主内容居中并限制为 `920px` 最大宽度，解决设置页左侧整块空白占位。

示例体验或完整登录进入业务区后不生成该标记，正常侧栏导航继续显示。新增容器不改变 AuthService、凭证、设置/登录条件、密码/验证码规则、控件键或调用参数。认证页展示修正已做语法与去除纯展示包装后的 AST 对照，另有不实例化真实认证服务的模拟分支检查；浏览器表现仍以本轮最终验收记录为准。

### 独立加载与默认导航

- 各页面被 `AppTest.from_file` 直接加载时，`ui` 与 `visual` 都不依赖会话状态或全局初始化，可在原页面导入后直接调用。
- 从首页正常导航进入页面时，同样按 current_key 渲染；导航按钮只在点击非当前页时调用 `st.switch_page`。
- 保留原有页面顺序、原控件 key、标签、帮助文本和确认流程；页面内跳转仍由现有逻辑负责。
- 最终实际 AppTest 与浏览器验收由 独立执行。

## 真实能力与边界

- 界面组件本身不发起模型或银行调用；模型草稿、确认与强验证仍由原流程执行。
- 不会自动付款、不会自动取消，也不会执行任何授权动作。
- AA 只创建收款单，卡片申请只受理；不宣称完成了扣款、发卡或资金到账。
- 定时业务与订阅提醒为手动检查；后台自动扣费与自动配送不宣称可用。
- 订阅续费资金方案已按最新 BLOCKERS 收口，界面改版不改变既定路径。
- 不新增业务规则，不改共享接口、资金、权限、认证或安全逻辑。
- 不伪造余额、流水、收益、成功回执、用户或模型能力；所有业务数值由现有后端传入。
- 不调用资金、权限、银行、持久化、Conversation 或授权执行接口；展示层不访问会话状态和缓存（`app/main.py` 与页面自身的会话逻辑保持原样）。
- 空数据、未知状态、未决状态和 HTML 形式字符串均可安全呈现。
- 字体使用本地回退链，不发起外部字体或图片请求；样式不使用外链资源或脚本。

## 本地启动与验证

环境使用已安装的 Streamlit 1.65.0，不安装新依赖。从仓库根启动：

```bash
streamlit run app/main.py
```

组件契约与视觉静态测试使用标准库 unittest 和最小 fake streamlit，不启动真实页面：

```bash
python -m unittest discover -s tests -p test_ui_components.py -v
```

视觉系统与页面接线静态检查：

```bash
python -m unittest tests.test_visual_system -v
```

首页纯展示契约测试：

```bash
python -m unittest tests.test_home_view -v
```

导入检查：

```bash
python -c 'from app import ui'
```

这些检查覆盖：token 与对比度、样式不含外链与脚本、正常业务控件可见性、身份入口限定的空侧栏修复、默认动效与减少动态效果例外、无模糊回退、三个模块原色确实被使用、页面只经固定 key 声明表面且同一页面不重复、`app/ui.py` 与 `app/home_view.py` 的纯展示边界、稳定导航 key、未知状态不伪装成功、不可信文本不进入 HTML。另有 `tests/test_visual_system.py` 直接校验静态样式文本、`visual.apply` 只插入固定内容、每个页面在 `st.set_page_config` 之后注入一次样式并声明页头带与模块卡片。

fake 组件测试不冒充真实 AppTest 或浏览器结果。最终由 独立使用 AppTest 检查页面独立加载与正常导航，并在 1440、1024、768、390、320 像素和 200% 缩放下做浏览器视觉验收；真实的确认、权限与强验证仍由原流程负责，UI 不会自动付款。

## 实际交付与验证（本轮）

### 当前隔离候选

- 已实施：共用设计 token、原生主题同步、柔彩业务对象块、银行卡块、紧凑 Activity 流水、计划 Steps、首页服务与页面容器调整；保留产品参数与两组对账表，技术编号与来源留在明细位置。
- 已实施：身份入口居中与空侧栏修复；默认短入场、按钮按压、服务悬停和减少动态效果例外。
- 范围：页面、共享展示组件、静态样式及候选 `.streamlit/config.toml`。业务后端、资金规则、认证授权规则、模型调用逻辑和共享契约未改。主目录未同步，未提交或推送。
- 验收状态：独立复核 最终独立校验进行中；当前实施记录不等于全量回归或全视口浏览器验收已经通过。

### DeepSeek 初次交付的历史自测边界

以下仅记录 DeepSeek run `7631e138-a8dd-41d6-a3d2-3430a28f5fda` 初次提交时的报告，不代替 独立复核 修正后候选的最新结果：

- 当时报告组件测试 12 项、首页测试 9 项、视觉静态测试 11 项通过；46 个非 AppTest 模块中 43 个通过，2 个无 TestCase 的脚本未计入，1 个依赖硬链接的既有用例遇到执行环境权限限制。这些数字属于该次自测时点。
- 当时执行环境不能完成 loopback/AppTest 与真实浏览器渲染；这是 DeepSeek 自测环境的历史限制，不表示当前 独立复核 环境无法验收。
- 当时未改 `.streamlit/config.toml` 和部分混合流程文件；当前候选已经同步原生主题并完成必要的纯展示调整，不能继续沿用旧的“未修改”清单。

### 独立复核 首轮视觉验收（2026-10-28）

- 最终命令：`python -B -X utf8 scripts/verify_h_core.py --output docs/evidence/pastel-ui-blocks-final-20261028`，退出码 0；712 项回归，0 失败、0 错误、0 跳过，另 5 项干净启动与后台检查通过。测试期间源码、模拟账本与模型配置指纹不变，真实 HTTP 已拦截。见[完整结果](evidence/pastel-ui-blocks-final-20261028/full.json)。
- 浏览器：36 项主要页面/视口检查，以及 10 项有数据的业务块与设置页检查通过；覆盖 1440/1024/768/720/390/320px，确认无整页横向溢出或页面异常，键盘焦点为 2px 实线。设置页 1440px 下主区域从 x=260 到 x=1180 居中，390px 下表单可用；进入示例业务后侧栏恢复。实际检查了 3 张银行卡、2 个产品、5 条历史流水与末条记录。
- 正常动效实际计算值为 `vx-arrive / 0.34s`；减少动态效果时为 `0.000001s`。静态截图仅快进有限过渡以记录稳定布局，交付预览保持正常动效。见[业务块与动效结果](evidence/pastel-ui-browser-20261028/blocks-report.json)、[页面矩阵](evidence/pastel-ui-browser-20261028/matrix-report.json)和[截图索引](evidence/pastel-ui-browser-20261028/capture-index.json)。
- 静态审计：52 个后端文件逐字节一致；31 个界面的业务控件、服务调用及执行顺序保留，635 个分支关联事件已核对。见[审计记录](evidence/pastel-ui-browser-20261028/business-audit.json)。手机尺寸银行卡按单列纵向组织，保留源阅读顺序（2026年11月1日依据当前样式与截图纠正原记录表述）；历史流水只作已知类型/方向的中文显示映射，未知值原样回退。
- 尚未验证：真实桌面 200% 浏览器缩放。响应式视口检查不冒充缩放；本人手机、真实银行和新的在线模型调用也不在本轮 UI 验收内。
- 交付为[本机隔离预览](http://127.0.0.1:8512)，主目录源码尚未同步，没有提交或推送。本次独立验收对应 run `7631e138-a8dd-41d6-a3d2-3430a28f5fda`；保留同一原生 Session 和累计工作区。

### 导航与全块动效反馈补修（2026-10-28）

用户指出：主页面切到转账或收支明细后侧栏没有当前位置，动效仅覆盖首页的少数卡片与银行卡。此次直接补修同一候选，不发起新的模型调用。

- 主工作区隐藏单独的转账/收支侧栏链接时，其父级「首页」保持高亮；顶部标签精确指示当前子页。独立页面保留原分支高亮、控件键、路由和点击行为。
- 所有 A/B/C 业务卡片、信息块、银行卡、流水、计划步骤、表单、指标、详情、聊天、数据和提示表面使用统一入场规则。首次使用引导和欢迎页原生卡片补充固定动效 key，不改内容或状态逻辑。
- 隐藏标签和收起详情时清除入场动画，重新显示时重播；嵌套表面只淡入，避免位移叠加。侧栏不参与业务块入场；正常时长仍为 0.34 秒，减少动态效果设置保留。
- 独立全量命令：`python -B -X utf8 scripts/verify_h_core.py --output docs/evidence/pastel-ui-nav-motion-final-20261028`，713 项回归与 5 项干净启动/后台检查通过，0 失败/错误/跳过，源码/账本/模型配置指纹不变。[完整结果](evidence/pastel-ui-nav-motion-final-20261028/full.json)。
- 浏览器实测 22 种页面/展示状态、108 次业务块检查，无漏动效；16 次侧栏当前项核对、标签回切及详情展开的重播检查、减少动态效果检查通过，页面异常为 0。包括全部 11 个独立页面、主工作区四个标签、有数据的产品/历史账单、参数表与 390px 转账布局。[浏览器结果](evidence/pastel-ui-nav-motion-browser-20261028/browser.json)。
- 52 个后端文件仍字节一致；相对上一版已接受源码，认证/引导仅增加展示 key，UI 仅改变父级高亮，规范化后 AST 相同。[静态审计](evidence/pastel-ui-nav-motion-browser-20261028/business-audit.json)。

本次证据覆盖本反馈修正，首轮的页面矩阵与 200% 缩放未核实等边界按上文保留。实现仍在同一隔离预览，主项目未同步，未提交或推送。

## 2026-10-28 操作统一改造

以下为上一轮的路由说明，历史数字与图示保留原时点，详见 [银行操作统一验收](2026-10-28银行操作统一验收.md)。

主导航为首页、转账与收款、收支明细、理财、我的，AI银行助手保持全局入口。业务页默认只提供返回首页；显式传入 keys 仍支持原 render_app_nav 多入口 API。

| action_id | 按钮 | 最终落点 |
|---|---|---|
| transfer | 转账 | 主脚本转账与收款 |
| bills | 收支明细 | 主脚本收支明细 |
| cards | 卡片管理 | pages/3_卡片管理.py |
| aa | AA收款 | pages/4_AA收款.py |
| scheduled | 预约转账 | pages/6_定时转账.py |
| budget | 预算管理 | pages/7_预算管理.py |
| wealth | 理财 | pages/9_理财.py |
| products | 产品对比 | pages/2_产品对比.py |
| subscriptions | 订阅管理 | pages/5_订阅管理.py |
| merchant | 生日计划 | pages/8_生日与商户.py |
| mine | 我的 | app/pages/10_我的.py |

render_home 仍只返回动作；名称、余额和回执仍由可信调用方提供。金额统一用元，既有整数分、身份与授权机制不变。

## 历史记录

- 2026-10-21：十个页面完成展示接线，交付内容见 [UI界面验收](2026-10-21UI界面验收.md)；当时的纸白背景、黄铜强调色与“只用单一强调色、无渐变”的约定是本轮之前的历史状态，已被 2026-10-28 柔彩视觉系统取代。
- 2026-10-23：首页改版与首次使用指南见 [银行首页与首次使用指南](UI银行改版与引导.md)；页面标题后不再追加装饰分隔线。
- 2026-10-28：银行操作统一验收，见 [银行操作统一验收](2026-10-28银行操作统一验收.md)。
