# FinTechathon-AI-2026

2026 FinTechathon 银行 AI 智能体项目，目标覆盖全部六个场景。

**统一使用 main。** 已有持久化模拟银行、转账/定时/AA、账单、理财评估与申赎、卡片、订阅、预算及商户六场景基础。“AI银行助手”接入DeepSeek理解需求、追问和编排；新增完整计划持久恢复、Windows本机登录与手机TOTP、独立后台检查和生日到期待办。付款仍需确认或验证，银行与商户业务仍为模拟。默认接口v1.1，真实TOTP交易支持v1.2，存储v15；用户本人手机绑定仍待验收。

## 常用文档

- [下一阶段分工与实施计划](docs/下一阶段分工与实施计划/README.md#user-in-app-flow)：U1—U5补齐程序内首次设置、验证恢复、后台控制与日常启动；当前仅规划，成员无需新材料。
- [BLOCKERS：待补材料与开发阻塞](docs/BLOCKERS.md)：哪些能开工，哪些还缺业务规则。
- [成员资料勘误](docs/成员资料勘误.md)：最后轮错误已修订或A代补；当前无需成员补交，原件缺口和大小错误保留追溯。
- [最新开发验收与使用](docs/2026-10-23项目本体补齐与验收.md)：计划恢复、TOTP、后台与生日联测证据，以及本机初始化、手机绑定和后台启动方法；用户本人手机验证尚未签收。
- [接口约定与接入指南](docs/接口约定与接入指南.md)：共享v1.1接口与接入要求。
- [开发与需求阻塞处理规则](docs/开发与需求阻塞处理规则.md)：资料缺失、勘误和补测的处理约定。

A负责技术交付；按最新路由，后续项目源码开发（含身份、权限、安全及资金）交DeepSeek隔离实施，Codex独立审查、验收并修正最终缺陷；共享契约变更须有明确授权。B—E负责业务资料，当前无需新增补交。历史方案和证据保留原时点。

## 启动页面

需要 Python 3.10+。Windows PowerShell 在项目根目录首次运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-app.txt -r requirements-materials.txt
.\.venv\Scripts\python.exe -m streamlit run app/main.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

已有 .venv 和依赖时只运行最后一条，或运行“启动A演示.ps1”。若无 py，使用已安装的 Python 完整路径创建虚拟环境。打开 [本地页面](http://127.0.0.1:8501)，首次可从首页进入使用指南，或从左侧“AI 银行助手”办理业务。用 Ctrl+C 结束服务。

当前版本的真实手机验证先在本机终端运行 `.\.venv\Scripts\python.exe -m bank.auth` 初始化一次账号，离线保管恢复码，再到页面登录扫码。后台另开终端运行 `.\.venv\Scripts\python.exe -m bank.worker`；关闭网页仍检查任务，关闭后台或电脑关机则停止。**程序内建号向导和后台启停尚未实现，已列入U阶段计划**；不能把以上管理员操作当作完整的普通用户流程。详细步骤见最新验收文档，不向AI或GitHub提供密码、二维码、动态码和恢复码。

在AI助手左侧展开“模型设置”，选择DeepSeek、智谱GLM、通义千问、豆包或Kimi，再保存对应服务商的独立API密钥。支持自定义模型ID，桌宠配置复用仅限DeepSeek。独立密钥按Windows当前用户加密保存，修改只影响后续新请求；部署环境变量优先。详见[配置与使用](docs/AI模型接入与验收.md#2026-10-23-国内多服务商配置)。

## 检查项目

H阶段：**592项通过，0失败、0错误、0跳过**，5项干净启动/后台检查通过，验收前后日常库与密钥配置未变。见[验收摘要](docs/evidence/h0-h6-20261023-r4/full.json)及[范围说明](docs/2026-10-23项目本体补齐与验收.md)。用户本人手机绑定不在自动化通过范围。未新增真实模型调用；新增服务商实测仍见BLOCKERS。

安装页面与资料检查依赖后运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-app.txt -r requirements-materials.txt
.\.venv\Scripts\python.exe -B -X utf8 scripts/verify_h_core.py --output docs/evidence/本次本地验收
.\.venv\Scripts\python.exe -B -X utf8 -m examples.contract_demo
```

## 接口与运行边界

验收输出必须使用新目录，避免覆盖旧证据。共享接口默认 `schema_version="1.1"`，真实TOTP交易使用受控兼容版本`1.2`；字段、版本和样例以接口指南为准。

AA收款单不代表已收款，卡片申请仅受理。后台进程运行时自动检查转账、商户和提醒，生日完整计划可按时准备预算与订单待办；付款仍需当前确认/验证，未实现长期无人值守扣费或真实配送。首次初始化本机身份后启用真实密码登录与手机TOTP；此前为演示验证。通知为持久站内待办，没有短信、邮箱或Windows弹窗送达。

运行库位于被Git忽略的 `runtime/`。重置会清除模拟交易，未知结果应先查询原操作。

模型仅输出有界声明式计划，不执行任意生成代码；工具、身份、授权和审计由可信代码控制。连续5次异常草稿停止本会话，核实后通过“新需求”重开；银行验证锁定与原操作恢复独立处理。已验证的是逻辑隔离边界，未提供独立OS沙箱或生产银行安全认证。
