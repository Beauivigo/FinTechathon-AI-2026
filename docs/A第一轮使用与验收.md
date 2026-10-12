# A 第一轮使用与验收

唯一验收依据为[分工表](五人分工与实施计划.md)中“A 第一轮先交”：能启动的输入页面、一次 bill.analyze 或 transfer.create 的示例调用、缺参数追问示例。本轮选择 transfer.create，使用既有共享接口 v1.1。

## 1. 启动

当前电脑已在项目 .venv 安装页面依赖。在仓库根目录打开 PowerShell，运行：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/main.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

然后打开 http://127.0.0.1:8501 。也可运行仓库中的“启动A演示.ps1”。使用 Ctrl+C 结束页面服务。

换一台电脑首次运行时，需要 Python 3.10+；推荐与本次验收相同的 Python 3.12：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-app.txt
.\.venv\Scripts\python.exe -m streamlit run app/main.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

如没有 py，使用电脑上的 Python 完整路径创建 .venv。本电脑还可使用 Codex 内置 Python：

```powershell
& 'C:\Users\huang\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv .venv
```

页面模块需要 requirements-app.txt 中的 Streamlit；原 shared 契约库和非页面测试仍只依赖 Python 标准库。

## 2. 三项交付的验收操作

| 分工表要求 | 实际操作 | 应看到什么 |
|---|---|---|
| 能启动的输入页面 | 启动后打开本地地址 | 页面标题“把一笔转账说清楚”，有聊天输入、示例按钮、当前操作区域和演示标识 |
| 一次 transfer.create 示例调用 | 输入“给李明转200元” | 展示收款人/账户尾号/200元，返回 awaiting_confirmation；展开“查看接口调用示例”可见完整 v1.1 请求与响应 |
| 缺参数追问示例 | 重置后输入“给小王转钱” | 询问金额；输入“200元”后保留“小王”，展示两位同名收款人让用户选择，未选之前不生成转账请求 |

点击“确认此笔示例转账”后生成 sandbox/succeeded 的**模拟回执**；测试替身不扣款，不修改余额，不实现银行账本。待确认阶段 data 为空，不能提前显示成功。拒绝后显示已取消。

辅助检查：输入“给小王转两百”可验证中文金额与同名选择；输入“给李明转1200元”显示待强验证，只有明确的“模拟验证并继续”按钮可继续。该按钮只是测试替身，不验证真实身份。重复点击/页面重运行不应生成第二份回执。

输入识别采用有限离线规则，支持“给/向某人转金额”、已询问金额时的单独金额回复。支持数字金额（最多两位小数）和基本中文整数；“两百五”等省略单位、有歧义的金额会要求改用数字。其他业务或复杂复合指令会明确提示本轮支持范围。

## 3. 代码如何交接

| 文件 | 职责 |
|---|---|
| app/main.py | 页面、对话记录、收款人选择、确认/拒绝/模拟验证和结果展示 |
| agent/intent.py | 离线规则识别、缺金额保留草稿、金额歧义拒绝 |
| agent/controller.py | 从可信候选选择对象，用 adapt_request 构造请求，调用网关并校验响应，使用 result_text 展示 |
| agent/demo_gateway.py | 明确标记的收款人/转账测试替身，提供 get_context、execute、authorize；只生成示例回执 |
| tests/test_agent.py | 输入→追问→选择→请求→确认/拒绝的流程验收 |
| tests/test_app.py | Streamlit 实际脚本的页面启动与按钮/输入交互验收 |

B 后续提供同名 get_context/execute/authorize 入口时，在 app/main.py 的 new_conversation 中换成 B 的实现，principal_id 来自真实服务端登录会话。B 的 get_context 若有多账户，需先增加付款账户选择，不能静默选第一个；真实强验证另接 B 的验证界面，不能把 issue_demo_proof 用于真实后端。

本轮对话与测试替身记录在页面会话内；浏览器刷新/服务重启后重新开始。这不是金融业务持久化。未来接 B 时，从后端恢复原操作和授权记录，不依赖页面会话去保证扣款幂等。

## 4. 运行验收检查

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

安装页面依赖后，页面测试必须实际运行、不能跳过。未安装 Streamlit 的环境只执行标准库测试，并明确跳过页面测试；这种结果不能单独证明页面验收通过。

## 5. 本轮默认选择及原因

| 需要选择的问题 | 本轮采用 | 原因 |
|---|---|---|
| 先交账单还是转账 | transfer.create | 一条流程即可展示请求、追问、对象选择和确认状态 |
| 页面方式 | Python + Streamlit 1.65.0 | 与已讨论方案一致；一个页面可运行，不依赖其他成员完成前后端 |
| 是否接模型 API | 离线规则演示 | 模型供应商/密钥未定；第一轮验收要求可在无网络、无密钥条件下完成，并明确说明模式 |
| 是否等待 B | get_context/execute/authorize 测试替身 | 保持既有交接口径，先完成 A 的第一轮页面与流程 |
| 同名小王如何处理 | 按银行与尾号由用户选择 | 不自行生成或猜测收款人 ID |
| 真实扣款/账本/身份验证 | 后续接 B；本轮生成模拟回执 | 与分工表允许固定示例返回值的第一轮范围一致 |
| 是否实现生日、多场景编排 | 本轮不加入 | 用户明确第一轮交付是唯一验收标准 |
| 安装位置 | 项目自己的 .venv | 页面依赖不修改共享库或全局 Python 环境 |

后续阶段仍按分工表推进；本轮验收通过只代表上述三项交付完成。

## 6. 本地验收结果（2026-10-12）

- 已实际启动本地服务并在浏览器打开输入页面。
- 已验证“给李明转200元”生成完整 v1.1 请求，确认前无成功回执。
- 已验证“给小王转钱”先追问金额，补充200元后保留收款人并要求选择同名账户。
- 62项检查全部通过，其中3项为实际 Streamlit 页面交互检查，未跳过。
- 同时验证拒绝、模拟强验证、重复确认、错误响应关联及新错误输入不残留上一笔成功状态。

结论：分工表中 A 的第一轮三项交付通过本地验收。未将后续六场景/真实业务模块纳入本轮完成结论。
