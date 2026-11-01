<!-- ppt-master-schema: design-spec/v1 -->
# 速发贝团队 · 言行 FinTechathon 柔彩升级 - Design Spec

## I. Project Information

| Item | Value |
| --- | --- |
| Project Name | 速发贝团队 · 言行 FinTechathon 柔彩升级 |
| Canvas Format | ppt169 — PPT 16:9 — 1280×720 |
| Page Count | 24 |
| Primary Language | zh-CN |
| Target Audience | 比赛评委与答辩听众，兼顾技术、安全、产品 |
| Communication Intent | 基于核实事实解释可信执行主干、六场景价值及本地模拟边界 |
| Desired Audience Outcome | 理解模型提议与程序授权执行的区别，记住亮点并准确区分完成和未验证事项 |
| Core Message / Ask / Action | 模型提出计划，可信程序掌握授权与执行；六场景形成可追溯的本地模拟闭环 |
| Delivery Context | 有主讲的9分钟比赛答辩，以流程图与截图讲解业务步骤；兼顾会后独立阅读 |
| Artifact Afterlife | 参赛材料、团队备稿，与技术安全文档和来源对应 |
| Reading Mode | balanced |
| Content Strategy | balanced default；用户要求纠正事实，保留原稿全部业务、控制与边界信息，可改写为适合演示的表达 |
| Design Style | 清透产品叙事；三组柔彩、浅色玻璃层、明确深色文字、可编辑关系图 |
| AI Image Acquisition Path | not applicable |
| Generation Mode | continuous |
| Spec Refinement | disabled |
| Speaker Notes | enabled — delegated Stage-2 policy；备注承接细节与来源 |
| Custom Animations | disabled — delegated Stage-2 policy |
| Narration Audio | disabled — delegated Stage-2 policy |
| Created Date | 2026-10-30 |

## II. Canvas Specification

| Property | Value |
| --- | --- |
| Format | PPT 16:9 |
| Dimensions | 1280×720 |
| viewBox | `0 0 1280 720` |
| Margins | 左右64px，上下32px |
| Content Area | 核心内容位于 x64—1216、y160—650；封面和章节可重组 |

## III. Visual Theme

### Theme Style

- **Mode**: custom
- **Mode References**: narrative
- **Mode Behavior**: 从入口分散提出问题，由模型与可信程序的边界给出解法，六场景提供具体实例，验收与演示回收主张；判断式标题推动叙事。
- **Visual style**: custom
- **Visual Style References**: glassmorphism, soft-rounded
- **Visual Style Behavior**: 浅色多站渐变形成连续光场，少量半透明白色大面板提供空间层次；统一柔和圆角、深色标题和正文。glassmorphism仅贡献光场及薄边层次，soft-rounded贡献亲和轮廓；画布由用户指定为浅色，按页面任务变化关系图、路径、主数字与截图组合。
- **Theme**: 三组配色属于同一产品，文字与字体角色始终相同。
- **Tone**: 清透、温柔、克制、专业、可信。

### Color Scheme

| Role | HEX | Purpose |
| --- | --- | --- |
| Background | #F8FBFA | 清透底色 |
| Secondary background | #FFFFFF | 主要内容表面 |
| Primary | #21605F | 结构和主强调 |
| Accent | #FFFFD0 | 核心奶油黄 |
| Secondary accent | #D8EFFF | 核心天空蓝 |
| Body text | #183B3A | 统一主文字 |
| Secondary text | #466460 | 来源与补充信息 |
| Divider | #D5E5E0 | 细边界 |
| Mint | #DDFFC2 | AI薄荷绿 |
| Lilac | #EEE1FF | AI浅紫 |
| Peach | #FFEAE1 | 洞察蜜桃粉 |
| Ice | #B6FAFF | 洞察冰蓝 |
| Success | #247657 | 明确成功/绿色分级文字 |
| Warning | #7A5B12 | 黄色分级文字 |
| Danger | #A64153 | 红色分级与风险文字 |

## IV. Typography System

### Font Plan

| Role | Character (Reference) | Primary | English if non-English | Fallback tail |
| --- | --- | --- | --- | --- |
| Title | 清晰现代无衬线 | Microsoft YaHei | Arial | sans-serif |
| Body | 中性易读无衬线 | Microsoft YaHei | Arial | sans-serif |

- **Title stack**: Microsoft YaHei, Arial, sans-serif
- **Body stack**: Microsoft YaHei, Arial, sans-serif

### Font Size Hierarchy

| Purpose | Anchor Size (px) |
| --- | ---: |
| Body | 24 |
| Title | 44 |
| Subtitle | 28 |
| Label | 20 |
| Annotation | 14 |
| Hero | 88 |
| Chapter | 72 |

## V. Layout Principles

### Deck-wide Direction

- **Hierarchy direction**: 每页先见主判断，再见关系或实物证据，最后见边界或来源。
- **Composition tendency**: 问题用不对称图文，流程用有向路径，架构用分层领域，实物证据用主截图，数字用明确阶段归属；宏观构图是Reference。
- **Cross-page continuity**: 同一任务线与光场复现，三组配色按核心、AI、洞察职责变化，不切换文字体系。
- **Spacing posture**: 章节和封面舒展，信息页有组织地提高密度，内容完整性由讲者备注补足。
- **Spacing anchors**: page margin 64px；block gap 24px；column gutter 32px；corner radius 24px；body leading 36px。

## VI. Icon Usage Specification

- **Primary bundled library**: none

| Icon Path | Suitable Scenarios |
| --- | --- |

## VIII. Image Resource List

| Filename | Dimensions | Ratio | Purpose | Type | Image pattern | Crop Policy | Acquire Via | Status | Reference | text_policy | page_role |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| image_5daba817b45aff1d.png | 1440×1000 | 1.44 | P14卡片状态实物 | Screenshot | 完整大截图与原生导读并置 | no-crop | user | Existing | 原稿P14，精确匹配源码预览；题注不得称实际安装版 | none | local |
| image_0e3ceb2a470bbfc4.png | 1440×1000 | 1.44 | P19首页上下文 | Screenshot | 完整首页与三条阅读提示 | no-crop | user | Existing | 原稿P19，四项常用业务与全部服务 | none | local |
| image_643568a83421dd24.png | 1440×1000 | 1.44 | P20转账表单 | Screenshot | 沿用首页的截图区域，改变导读重点 | no-crop | user | Existing | 原稿P20，导航动效源码预览 | none | local |
| image_f236390284a9e9df.jpg | 1280×720 | 1.777778 | P21历史体验回执 | Screenshot | 完整回执截图与放大字段互证 | no-crop | user | Existing | 原稿P21，历史预览，demo_otp，1200元模拟资金 | none | local |

三张用户配色照片只作为风格与HEX参考，不直接放上投影页。四张截图保留像素内容与比例；身份、来源和验证方式来自哈希及记录，不从视觉推断。未计划AI或网络素材。

## IX. Content Outline

#### Slide 01 - 言行：把意图变成可信任务

- **Audience move**: 认识项目 → 记住产品主张
- **Relationships**: order: 意图、计划、授权、执行、回执具有先后顺序
- **Composition**: 大字品牌与一条五站任务线形成轻盈的产品封面
- **Title**: 言行：把意图变成可信任务
- **Core message**: 一句话需求
- **Content**: 一句话需求／六场景／可信执行主干／本地模拟银行

#### Slide 02 - 银行不缺入口，缺的是可信的完成路径

- **Audience move**: 看到分散入口 → 理解完成路径的价值
- **Relationships**: link: 入口分散、步骤冗长、模型不可信共同造成完成困难；五站任务链回应问题
- **Composition**: 一侧三条短判断，另一侧连接成有起点与终点的任务线
- **Title**: 银行不缺入口，缺的是可信的完成路径
- **Core message**: 三个痛点
- **Content**: 三个痛点／用户要的是办成／状态、回执与可恢复下一步

#### Slide 03 - 模型提出计划，可信程序掌握执行

- **Audience move**: 把AI当执行者 → 区分模型建议与可信执行
- **Relationships**: contrast, link, order: 模型理解、追问、计划位于提议侧；资源、身份、权限和执行属于可信程序；两侧经校验衔接
- **Composition**: 两个不同材质的领域通过明确的校验接口连接
- **Title**: 模型提出计划，可信程序掌握执行
- **Core message**: 模型只提议
- **Content**: 模型只提议／身份、确认与授权证明不交给模型／可信程序执行

#### Slide 04 - 六个高频场景，共用一条可信主干

- **Audience move**: 看到六个功能 → 理解共享身份授权账本
- **Relationships**: parent, membership: 转账、账单、理财、卡片、订阅、生日共同依赖身份、授权、账本与审计
- **Composition**: 共享主干托起六个命名场景，层次清楚但不堆成后台菜单
- **Title**: 六个高频场景，共用一条可信主干
- **Core message**: 六场景与各自业务对象
- **Content**: 六场景与各自业务对象／共享可信主干／每场景保留规则与拒绝路径

#### Slide 05 - 四层协作，把不确定性留在模型层

- **Audience move**: 知道技术名词 → 看懂责任与信任边界
- **Relationships**: order, link: Streamlit体验层到模型认知层，再到可信编译及网关，最后持久事实与审计；各层承担不同职责
- **Composition**: 宽阔的架构分层搭配独立的安全约束侧栏
- **Title**: 四层协作，把不确定性留在模型层
- **Core message**: Streamlit
- **Content**: Streamlit／五家模型接口、有界JSON与DAG／PlanStore、策略认证、网关／SQLite v16与worker

#### Slide 06 - 五家可配置，在线证据逐家区分

- **Audience move**: 看见多模型 → 分清接入与实测
- **Relationships**: contrast, membership: DeepSeek有历史在线证据；GLM、通义千问、豆包、Kimi可配置而未在线验收；各家密钥独立
- **Composition**: 一个突出的已验证来源，其余四家作为对等的接口能力列示
- **Title**: 五家可配置，在线证据逐家区分
- **Core message**: 理解追问、提出有界计划
- **Content**: 理解追问、提出有界计划／不授予资金决策权／DPAPI、白名单、禁止重定向

#### Slide 07 - 转账授权，看含本笔的当日累计

- **Audience move**: 只看本笔金额 → 理解动态风险分级
- **Relationships**: contrast: 绿色执行、黄色确认、红色强验证；转账按同用户跨账户、上海执行日已付累计加本笔计算；执行事务内重算，并发待办可升级验证
- **Composition**: 三种明确标注的授权领域配一条跨领域的累计规则
- **Title**: 转账授权，看含本笔的当日累计
- **Core message**: 绿色查询仍校验归属
- **Content**: 绿色查询仍校验归属／黄色累计≤1000元／红色累计>1000元及申赎等／TOTP与体验模拟分开／挑战10分钟、5次失败锁定

#### Slide 08 - 五道防线，每层都有可检查的约束

- **Audience move**: 知道安全口号 → 看到约束及边界
- **Relationships**: membership: 身份、授权、模型、资金、审计共同约束任务；本机审计可被本机篡改
- **Composition**: 五个检查站并列展开，各自可读，底部单独给出安全边界
- **Title**: 五道防线，每层都有可检查的约束
- **Core message**: scrypt、TOTP、恢复码
- **Content**: scrypt、TOTP、恢复码／分级与挑战／有界JSON及白名单／整数分、事务、幂等／PlanStore与trace_id

#### Slide 09 - 业务与资金：六种对象，一套账本

- **Audience move**: 看完技术主干 → 进入业务对象
- **Relationships**: membership: 六场景属于同一可信执行体系，各业务有自身对象与约束
- **Composition**: 大字章节标题与六个轻量业务名构成呼吸页
- **Title**: 业务与资金：六种对象，一套账本
- **Core message**: 转账与资金转移
- **Content**: 转账与资金转移／账单与消费解释／理财与风险适配／卡片状态／订阅周期／生日预算

#### Slide 10 - 业务对象不同，执行边界同样清楚

- **Audience move**: 知道六场景名字 → 记住不同业务边界
- **Relationships**: membership, contrast: 六类业务各有对象；受理、建单、计划均不能等同资金执行完成
- **Composition**: 六个业务对象按信息关系分区，边界文字直接跟随对象
- **Title**: 业务对象不同，执行边界同样清楚
- **Core message**: 到期重授权
- **Content**: 到期重授权／异常规则不等于反欺诈／本地即时模拟记账／申请受理／每期强验证／预留不扣款

#### Slide 11 - 先把人认对，再把钱转对

- **Audience move**: 把转账视作按钮 → 理解核对和恢复
- **Relationships**: order: 识别、消歧、确认、授权、执行、回执按顺序发生；单笔、定时、AA使用同一可信主干
- **Composition**: 上方完整六站路径，下方以三种转账任务展示不同约束
- **Title**: 先把人认对，再把钱转对
- **Core message**: 跨账户当日累计
- **Content**: 跨账户当日累计／执行前复验卡状态与限额／定时到期重算／AA建单不等于收齐／unknown先查原操作

#### Slide 12 - 先分组，再把每一笔解释清楚

- **Audience move**: 看到流水 → 理解可解释账单
- **Relationships**: link, order: 已有流水按期间和类别分组；退款另列；大额提示是既定规则
- **Composition**: 从流水记录到解释结果的有向关系，保留充足留白
- **Title**: 先分组，再把每一笔解释清楚
- **Core message**: 月年窗口
- **Content**: 月年窗口／餐饮、出行、订阅／退款不混入当期支出／单笔大额提示／模拟数据

#### Slide 13 - 先评估风险，再允许申购

- **Audience move**: 把理财视作商品列表 → 理解风险适配与拒绝
- **Relationships**: order, contrast: 对比、评估、匹配、申购、持仓、赎回形成流程；R2用户与R4产品不匹配则拒绝
- **Composition**: 用一个不匹配的具体例子串起流程与拒绝路径
- **Title**: 先评估风险，再允许申购
- **Core message**: R1—R5
- **Content**: R1—R5／6个虚构产品／费用整数分、份额4位、HALF_UP／余额、起购、锁定与清仓边界／静态净值与本地即时模拟记账

#### Slide 14 - 卡片管理，让每一步都可核对

- **Audience move**: 看到卡片截图 → 理解状态与权限
- **Relationships**: membership, link: 申请是受理；限额、冻结解冻、挂失解挂受权限和状态检查
- **Composition**: 保留完整来源截图，并用放大的原生文字解释关键状态
- **Title**: 卡片管理，让每一步都可核对
- **Core message**: 三张示例卡
- **Content**: 三张示例卡／单笔限额／挂失卡不能支付／申请不等于审批发卡／截图为源码预览
- **Images**: image_5daba817b45aff1d.png；完整显示，原生解释文字保持可编辑。

#### Slide 15 - 把“以后”变成可撤销的计划

- **Audience move**: 把计划当付款 → 区分候选、预留、授权与完成
- **Relationships**: order, contrast: 订阅由候选到核实，再取消或逐期授权续费；生日由日期到预留、模拟下单与释放
- **Composition**: 两条不同时间链并置，每条的授权停顿明确可见
- **Title**: 把“以后”变成可撤销的计划
- **Core message**: 候选不等于已订阅
- **Content**: 候选不等于已订阅／取消不等于退款／每期续费红色／站内到期提醒／后台可准备待办但不批准付款

#### Slide 16 - 每一组测试数字，都有自己的出处

- **Audience move**: 被总数吸引 → 理解证据维度与阶段
- **Relationships**: order, contrast: 530、619、698、713分别为不同阶段的全量回归；5、18、35、22/108是不同维度或阶段
- **Composition**: 713作为主证据，历史节点与不同维度清晰分层，节点等距而不画数量条形
- **Title**: 每一组测试数字，都有自己的出处
- **Core message**: v2历史全量713且0失败0错误0跳过
- **Content**: v2历史全量713且0失败0错误0跳过／5清洁检查／18安装工具／35为1.0.1关键复核／22状态108业务块
- **Visualization**: 历史节点按阶段等距排列；数字作为文本标签，不以长度或面积编码数量。

#### Slide 17 - 从一句话，到可追溯的业务

- **Audience move**: 理解实现 → 准备观察演示闭环
- **Relationships**: order: 首页上下文、一句话、计划、授权、回执依次相连
- **Composition**: 章节大标题与同一条五站信任线呼应封面
- **Title**: 从一句话，到可追溯的业务
- **Core message**: 三张真实来源截图
- **Content**: 三张真实来源截图／演示与收尾／边界与团队

#### Slide 18 - 五个步骤，走完一条信任链

- **Audience move**: 准备看演示 → 知道每站看什么
- **Relationships**: order: 首页、自然语言、计划、授权、回执按业务步骤推进；失败与unknown走查询或历史截图兜底
- **Composition**: 步骤标签与任务阶段对应，兜底路径独立于成功路径
- **Title**: 五个步骤，走完一条信任链
- **Core message**: 首页与身份
- **Content**: 首页／一句话／计划／授权／回执；未知结果按原操作查询，说明历史截图范围

#### Slide 19 - 第一屏：我在哪，能做什么

- **Audience move**: 看到首页 → 理解账户上下文与入口
- **Relationships**: order: 首页先展示账户及余额，再提供AI入口、常用业务与全部服务
- **Composition**: 完整大截图占主视觉，左侧短提示给出阅读顺序
- **Title**: 第一屏：我在哪，能做什么
- **Core message**: 账户可用余额
- **Content**: 账户可用余额／AI入口／四项常用业务加全部服务／本地模拟声明／源码预览题注
- **Images**: image_0e3ceb2a470bbfc4.png；完整显示，原生解释文字保持可编辑。

#### Slide 20 - 第二屏：先补信息，再让用户确认

- **Audience move**: 把一句话当付款 → 理解补齐与确认
- **Relationships**: order: 收款人、金额、付款账户先补齐核对，再进入独立确认和执行
- **Composition**: 延续首页的图文关系，让变化发生在业务内容而非视觉框架
- **Title**: 第二屏：先补信息，再让用户确认
- **Core message**: 自然语言和表单共用核对确认结果
- **Content**: 自然语言和表单共用核对确认结果／缺信息先补齐／源码预览题注
- **Images**: image_643568a83421dd24.png；完整显示，原生解释文字保持可编辑。

#### Slide 21 - 第三屏：授权与验证方式都要留下

- **Audience move**: 看到红色完成 → 区分权限级别、验证与执行状态
- **Relationships**: contrast: 红色授权、demo_otp验证、1200元模拟金额、已完成状态同时留存；不能据此证明本人手机TOTP
- **Composition**: 历史截图与放大的四项回执字段互证，醒目标明模拟验证
- **Title**: 第三屏：授权与验证方式都要留下
- **Core message**: 历史本机预览
- **Content**: 历史本机预览／体验模拟验证／挑战绑定本次操作且10分钟／本机绑定账号才使用TOTP
- **Images**: image_f236390284a9e9df.jpg；完整显示，原生解释文字保持可编辑。

#### Slide 22 - 本地闭环已完成，生产能力有边界

- **Audience move**: 看到完成度 → 能准确复述未验证范围
- **Relationships**: contrast: 已实现本地闭环与待现场补验和产品边界分别列示
- **Composition**: 三种证据状态采用明确标题，颜色只作辅助
- **Title**: 本地闭环已完成，生产能力有边界
- **Core message**: 六场景、713、2.0.0
- **Content**: 六场景、713、2.0.0／本人手机、第二家模型、新电脑、200%／真实资金、发卡、真实申赎与生产安全不作承诺

#### Slide 23 - 速发贝团队 · 从规则到交付

- **Audience move**: 看到姓名 → 理解责任与可核查交付
- **Relationships**: membership: A技术、B资金、C规则验收、D理财、E卡片订阅协作；各角色以需求分工、交叉复核与统一验收协作
- **Composition**: 五位成员作为对等角色呈现，避免用装饰数字混淆交付数量
- **Title**: 速发贝团队 · 从规则到交付
- **Core message**: 速发贝团队，沿用五个原稿姓名
- **Content**: 速发贝团队，沿用五个原稿姓名／E两条记录覆盖三个流程／各角色有交付依据／按交付证据核对分工

#### Slide 24 - 模型提议，程序把关

- **Audience move**: 理解细节 → 记住可信闭环与边界
- **Relationships**: link, membership: 六场景、历史713回归、v2可安装支撑本地模拟可用；身份授权模型资金审计各有控制
- **Composition**: 回收封面的柔彩光场与任务线，以大字主张和三个事实收束
- **Title**: 模型提议，程序把关
- **Core message**: 模型不持有授权
- **Content**: 模型不持有授权／可信程序执行／安全自评五维／本地模拟可用、生产边界未证

## X. Speaker Notes Requirements

- **Generation**: enabled
- **Filename**: match each SVG filename under `notes/`
- **Content**: 依据最终可见SVG逐页说明，并保留原稿必要细节与源码/验收记录来源；各次测试按阶段分别引用，注明截图为源码或历史预览及demo_otp，姓名沿原稿待报名表核对。不虚构现场演示、本人手机、在线API或生产验收。
- **Total duration**: 按本次提交要求编排9分钟答辩，不另加5分钟现场演示；这是讲稿与节奏预算，不声称已完成真人计时。
- **Notes style**: 清晰、自然、证据充分；主讲者可根据答辩时长选讲。
- **Presentation purpose**: 解释可信执行主干、六场景价值与本地模拟边界。
