# 银行助手 UI 设计与演示

状态：2026-10-21 已完成十个页面的展示接线。主题、分组侧栏、首页账户摘要、转账状态、理财双区布局均已接入，见 [UI界面验收](2026-10-21UI界面验收.md)。

## 设计方向

**受众**：参加 FinTechathon 演示与验收的业务人员、运营人员和评审，以及需要核对现有页面中真实模拟数据的操作者。

**主要任务**：用中文快速确认当前页面、已有数据和下一步入口；带着规则识别出的草稿进入对应业务流程核对；在有后端数据时清楚区分已有值、空值和未知状态。

**视觉定位**：可核对的账户工作台，而不是营销落地页。整体用纸白背景、深墨标题、细边框和黄铜色单一强调色，结构接近柜台核对单：先看页面与入口，再看真实概要、状态和业务区域，最后看边界说明。

### 信息层级
- `render_page_header` 提供唯一页面标题和一句可选说明，再用原生分隔线结束页头。
- `render_app_nav` 只提供三个主导航入口；当前页按钮 disabled 且为主按钮，避免误跳。
- 概要、详情、状态和进度只显示调用方传入的值；空值显示占位，未知状态保持灰色「状态未知」。
- 业务控件放进 `business_section`，原有 key、标签和执行顺序不变。
- 页尾用 `render_scope_note` 说明草稿与真实业务的边界。

### 桌面与移动
- 桌面端：页头、三项主导航、最多三列概要，再向下排列业务区域；正文保持较短行宽，不铺满超宽屏。
- 移动端：只依赖原生容器和少量列，让内容自然重排；不设置固定像素宽度，不隐藏控件，不在窄区域再嵌套多列。
- 首页保留原有 `st.columns([1.5,1])` 的比例和顺序，不重排原有主体。
- 控件使用原生可见标签、键盘焦点和帮助文本；不添加 CSS 覆盖或隐藏功能。
- 页面文案使用动作明确的短句，按钮名称与产生的状态保持一致。

### 自审与删减
- 删去渐变、装饰性阴影、外链图片、外链字体、emoji、静态截图、无意义数字和重复卡片。
- 只保留一个黄铜强调色、一个清晰页头、原生按钮与输入控件的可见标签。
- 不添加自定义动画，减少动效敏感问题；状态用文字加 badge，颜色不是唯一信号。
- 不堆静态画面，也不为了演示效果伪造业务数值或成功回执。

## 设计 token 与主题配置

### 核心色板

| 名称 | 色值 | 用途 |
| --- | --- | --- |
| `ink` | `#020617` | 正文与标题 |
| `navy` | `#0F172A` | 辅助深色 |
| `paper` | `#F8FAFC` | 页面背景 |
| `surface` | `#FFFFFF` | 内容表面 |
| `muted` | `#475569` | 次要说明 |
| `border` | `#E2E8F0` | 边界 |
| `brass` | `#A16207` | 主操作与少量强调 |
| `danger` | `#B91C1C` | 失败或危险状态 |

### `THEME_TOKENS`
`THEME_TOKENS` 的键和值固定为：
- `primaryColor`：`#A16207`
- `backgroundColor`：`#F8FAFC`
- `secondaryBackgroundColor`：`#FFFFFF`
- `textColor`：`#020617`
- `borderColor`：`#E2E8F0`
- `baseRadius`：`0.5rem`
- `buttonRadius`：`0.5rem`
- `showWidgetBorder`：`True`
- `headingFontSizes`：`['2rem', '1.5rem', '1.25rem', '1.125rem', '1rem', '0.875rem']`

### 已接入的主题配置

主题已由 Codex 接入 .streamlit/config.toml，并配置本机字体回退链。没有新增 CSS、外链字体或依赖；不包含服务端或安全参数。

```toml
[theme]
primaryColor = '#A16207'
backgroundColor = '#F8FAFC'
secondaryBackgroundColor = '#FFFFFF'
textColor = '#020617'
borderColor = '#E2E8F0'
baseRadius = '0.5rem'
buttonRadius = '0.5rem'
showWidgetBorder = true
headingFontSizes = ['2rem', '1.5rem', '1.25rem', '1.125rem', '1rem', '0.875rem']

[theme.sidebar]
backgroundColor = '#FFFFFF'
secondaryBackgroundColor = '#F1F5F9'
```

`SIDEBAR_THEME_TOKENS` 固定为：`backgroundColor` 为 `#FFFFFF`，`secondaryBackgroundColor` 为 `#F1F5F9`。

### 字体
`FONT_STACK` 为 `IBM Plex Sans, Noto Sans SC, Source Han Sans SC, PingFang SC, Microsoft YaHei, sans-serif`。这些候选都来自本机回退链，不加载外部字体；缺少某个字体时交给平台继续回退。

### 可访问性
- `ink` 对 `paper` 的目标对比度不低于 7:1，白色对 `brass`、`muted` 对 `surface`、`danger` 对 `surface` 的目标对比度不低于 4.5:1。
- badge 同时显示中文文字，不依赖颜色单独传达状态。
- 按钮保留原生键盘焦点和可见标签；当前页通过 disabled 与主按钮双重表达。
- 未知状态额外显示 warning 文本，不是只在颜色上变化。
- 冻结测试包含 token 合法性、本地字体与对比度断言；这不代替真实浏览器验收。

## 组件 API

`app/ui.py` 是纯展示模块，只允许标准库的 `__future__`、`contextlib`、`typing` 和已有 `streamlit`；不导入 agent、银行、权限、持久化、Conversation、资金换算或授权执行，不访问会话状态或缓存。

### 展示函数
- `render_page_header(title, subtitle=None)`：调用 `st.title`；空标题回退「银行助手」，副标题非空时用 `st.caption`，最后调用 `st.divider`。
- `render_app_nav(current_key, *, keys=PRIMARY_NAV_KEYS, key_prefix='app_nav')`：在 `st.columns` 中最多渲染三个原生按钮。按钮 key 为 `app_nav_<key>`，宽度使用 stretch；当前页 disabled、主按钮，其他页 secondary。仅在点击非当前页时调用 `st.switch_page`；路径移除仓库相对的 app/ 前缀，以匹配主脚本目录。
- `render_overview(items, *, columns=3, title=None)`：只接收 label、value、help，最多三列；空列表只显示「暂无」说明且不生成 metric，空值显示「—」。
- `render_details(items, *, title=None)`：接受 mapping、二元组或 label/value mapping 列表；空值显示「未填写」，完全无数据只显示「暂无」说明。
- `render_status(state, *, title=None, detail=None)`：用 `st.badge` 显示 `STATUS_META`；未识别状态显示「状态未知」、灰色并显示 warning，任何状态都不调用成功或错误提示。
- `render_progress(steps, *, title=None)`：接受 label/status mapping 或二元组；未知步骤保持「状态未知」和灰色，空列表只显示「暂无」说明。
- `render_empty_state(title, *, description=None, action_label=None, action_key=None)`：用带边框的原生容器展示空状态；同时提供 label 和稳定 key 时才生成按钮，并返回该按钮的真实布尔结果，自身不执行业务动作。
- `business_section(title, *, description=None)`：上下文管理器，用 `st.container(border=True)` 包裹原业务区域并 yield 该容器；内部原控件保留原 key、标签与顺序。
- `render_scope_note(extra=None)`：用 `st.caption` 说明展示草稿边界，可附加一句调用方提供的说明。

### 页面与 current_key 映射

| current_key | 页面 | 文件 | 主导航 |
| --- | --- | --- | --- |
| `home` | 首页与转账 | `app/main.py` | 是 |
| `accounts` | 资料账单 | `app/pages/1_资料账单.py` | 是 |
| `products` | 产品对比 | `app/pages/2_产品对比.py` | 否 |
| `cards` | 卡片管理 | `app/pages/3_卡片管理.py` | 否 |
| `aa` | AA 收款 | `app/pages/4_AA收款.py` | 否 |
| `subscriptions` | 订阅管理 | `app/pages/5_订阅管理.py` | 否 |
| `scheduled` | 定时转账 | `app/pages/6_定时转账.py` | 否 |
| `budget` | 预算管理 | `app/pages/7_预算管理.py` | 否 |
| `merchant` | 生日与商户 | `app/pages/8_生日与商户.py` | 否 |
| `wealth` | 理财 | `app/pages/9_理财.py` | 是 |

`PRIMARY_NAV_KEYS` 为 `('home', 'accounts', 'wealth')`。`NAV_ITEMS` 是 `(current_key, 路径)` 的固定顺序序列，中文标签由 `NAV_LABELS` 提供。

### 状态映射

| 状态 key | badge 文字 | badge 颜色 |
| --- | --- | --- |
| `draft` | 草稿 | gray |
| `ready` | 可核对 | blue |
| `pending` | 待确认 | orange |
| `running` | 进行中 | blue |
| `blocked` | 受阻 | red |
| `failed` | 未成功 | red |
| `done` | 已完成 | green |
| `unknown` | 状态未知 | gray |

不在表中的状态也显示「状态未知」与灰色，并触发 `render_status` 的 warning。

### 数据和边界语义
- 余额、流水、收益、回执、业务状态与能力都只能由现有后端或原流程传入，组件不推断、不生成、不美化。
- 传入的 HTML 形式字符串只交给原生文本组件安全显示，不执行脚本，也不使用不安全 HTML。
- 空列表、空值、未知状态和未决结果均有明确中性展示。
- 模块不使用会话状态、缓存或页面副作用；所有展示调用都可重复执行。

## Codex 最终接线（已完成）

### 接线原则
- 只增加展示调用和原生容器，不重写页面业务逻辑，不改变资金、权限、认证、安全、确认或强验证代码。
- 保留原有控件 key、标签和执行顺序；保留首页原有 `st.columns([1.5,1])`。
- 原页面读取数据的顺序、确认按钮回调和页面跳转保持原样；展示层只消费已经得到的字段。
- 不新增业务规则，不在 UI 中补算金额、收益、成功回执或状态。
- .streamlit/config.toml 已由 Codex 按上面 token 接入，未新增 CSS 或依赖。

### 通用步骤
1. Codex 在最终阶段按设计 token 创建主题配置，保留 pages 页面路径，以统一分组侧栏取代重复的默认菜单。
2. 在页面加入 `from app import ui`，放在已有页面导入之后。
3. 在页面主体最前面调用 `ui.render_page_header(...)` 和 `ui.render_app_nav(<current_key>)`。
4. 只把现有后端已经给出的真实字段传给 `ui.render_overview`、`ui.render_details`、`ui.render_status` 或 `ui.render_progress`。
5. 用 `with ui.business_section(...):` 包住原有表单和结果区，内部控件保持原 key、标签与执行顺序。
6. 无数据时用 `ui.render_empty_state`，按钮只返回点击布尔值，真实刷新仍由原页面执行。
7. 在页面末尾调用 `ui.render_scope_note`，不改原有确认、权限或强验证流程。

统一页头和导航示例：

```python
from app import ui

ui.render_page_header('资料账单', '核对现有资料与账单数据')
ui.render_app_nav('accounts')
```

### 首页
首页负责首页与转账，current_key 为 `home`。原有两列主体必须保留比例和顺序。

```text
app/main.py
├─ ui.render_page_header('首页与转账', '...')
├─ ui.render_app_nav('home')
├─ 原 st.columns([1.5,1])，保持原比例与原顺序
│  ├─ 左列：原转账入口、账单分析控件与结果
│  └─ 右列：原侧栏控件与说明
├─ ui.render_overview(后端已有字段)
├─ ui.render_scope_note()
```

### 转账
转账在 `app/main.py` 的原有业务区域内接线，current_key 仍为 `home`。

```text
app/main.py 的转账业务区
├─ 原转账输入控件（原 key、标签、顺序不变）
├─ 原确认与强验证按钮及其回调（不移动、不改写）
├─ ui.render_status(原流程返回的真实状态)
├─ ui.render_progress(原流程真实步骤)
└─ ui.render_empty_state(...) 仅在原流程返回空草稿时提示
```

`ui.render_status` 和 `ui.render_progress` 只显示原流程给出的状态，不执行转账、不生成回执，也不会自动付款。

### 理财
理财页 current_key 为 `wealth`。概要、状态和业务区域都使用原页面已有数据。

```text
app/pages/9_理财.py
├─ ui.render_page_header('理财', '...')
├─ ui.render_app_nav('wealth')
├─ ui.render_overview(现有持仓与风险概要字段)
├─ ui.render_status(原流程真实状态)
├─ with ui.business_section('理财风险评估', ...)：原控件
├─ with ui.business_section('购买理财产品', ...)：原控件
├─ with ui.business_section('赎回理财', ...)：原控件
├─ with ui.business_section('查理财持仓', ...)：原控件
└─ ui.render_scope_note()
```

找不到原字段时传 None 或空列表，让组件显示「未填写」「—」或「暂无」；不要补造持仓、收益或成功状态。

### 其余页面
所有页面采用相同顺序：页头、当前页导航、真实概要/状态、原业务区域、边界说明。current_key 使用上面的映射表。

```text
任意页面
├─ ui.render_page_header(页面标题, 可选说明)
├─ ui.render_app_nav(本页 current_key)
├─ ui.render_overview / ui.render_details / ui.render_status（仅真实数据）
├─ with ui.business_section(...)：原有控件与回调
├─ ui.render_progress 或 ui.render_empty_state（按真实状态选择）
└─ ui.render_scope_note()
```

保留 pages/ 文件及 URL；原生按钮提供十页完整分组导航，由 client.showSidebarNavigation 关闭重复的默认菜单。没有隐藏业务入口，不用 CSS 隐藏原功能。

### 独立加载与默认导航
- 各页面被 `AppTest.from_file` 直接加载时，`ui` 不依赖会话状态或全局初始化，因此可在原页面导入后直接调用。
- 从首页正常导航进入页面时，同样按 current_key 渲染；主导航按钮只在点击非当前页时调用 `st.switch_page`。
- 保留原有页面顺序、原控件 key、标签、帮助文本和确认流程；页面内跳转仍由现有逻辑负责。
- 最终实际 AppTest 与浏览器验收由 Codex 独立执行。

## 真实能力与边界

- 当前聊天是有限中文规则解析和草稿导航，不是完整自由对话模型。
- 确认和模拟强验证仍由原流程执行；组件只展示与核对草稿。
- 不会自动付款、不会自动取消，也不会执行任何授权动作。
- AA 只创建收款单，卡片申请只受理；不宣称完成了扣款、发卡或资金到账。
- 定时业务与订阅提醒为手动检查；后台自动扣费与自动配送不宣称可用。
- 订阅续费资金方案仍待 B/E 确定，与 UI 展示相互独立。
- 不新增业务规则，不改共享接口、资金、权限、认证或安全逻辑。
- 不伪造余额、流水、收益、成功回执、用户或模型能力；所有业务数值由现有后端传入。
- 不调用资金、权限、银行、持久化、Conversation 或授权执行接口；不访问会话状态和缓存。
- 空数据、未知状态、未决状态和 HTML 形式字符串均可安全呈现。
- 字体使用本地回退链，不发起外部字体或图片请求。

## 本地启动与验证

环境使用已安装的 Streamlit 1.65.0，不安装新依赖。从仓库根启动：

```bash
streamlit run app/main.py
```

组件冻结测试使用标准库 unittest 和最小 fake streamlit，不启动真实页面；本阶段测试只覆盖这些标准库断言，不冒充真实 AppTest、Streamlit 页面或浏览器结果：

```bash
python -m unittest discover -s tests -p test_ui_components.py -v
```

导入检查：

```bash
python -c 'from app import ui'
```

fake 组件测试会检查原生调用、稳定 key、空值中性展示、未知状态不伪装成功和文档契约，但它不冒充真实 AppTest 或浏览器结果。最终由 Codex 独立使用 AppTest 检查页面独立加载与正常导航，并执行实际 Streamlit 与浏览器验收；真实的确认、权限与强验证仍由原流程负责，UI 不会自动付款。


## 实际交付与验证

- 全量485项通过：469项原有测试、12项DeepSeek组件测试、4项Codex真实页面验收。普通沙盒343项通过；AppTest及硬链接能力测试经正式工具执行。
- 自动中间测试因unittest启动参数不兼容失败，冻结测试及重试日志保留，未宣称它们通过。Codex独立审查并修正导航路径、完成页面接线，最终结论来自实际验收。
- render_sidebar_nav 已用于全部十页；首页的转账示例、演示设置、草稿导航、流水账单采用渐进展开。
- 37个核心业务文件统一换行后内容一致；用户授权同步后，这37个业务核心文件与日常库仍保持原始字节哈希。确认、强验证、未知结果恢复和幂等行为保留。
- 浏览器实际进入十个页面，并完成模拟转账200元、理财申购100元的确认流程。1280/1024/390像素无主内容横向溢出，手机确认按钮及键盘焦点可用。
- 独立工作区完成审查后，经用户明确授权将25个交付文件同步到主目录，并在主目录重新通过485项完整回归；补存本次同步状态与验收记录，未提交或推送。详见[主目录同步验收](2026-10-21UI界面验收.md#主目录同步验收)。
