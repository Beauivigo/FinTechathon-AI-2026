# FinTechathon-AI-2026

2026 FinTechathon 银行 AI 智能体项目，目标覆盖全部六个场景。

**统一使用 main。** 当前已包含共享接口 v1.1、A 第一轮页面和新版五人分工。页面使用离线规则与测试替身，只生成模拟回执，不扣款、不改变余额；六场景完整业务系统仍待后续开发。

## 从这里开始

- [A 第一轮页面：启动与验收](docs/A第一轮使用与验收.md)：不需要模型密钥，按步骤运行页面、转账示例和缺参追问。
- [五人详细分工与实施计划](docs/五人分工与实施计划.md)：每人任务、交付物、完成标准、材料模板和后续轮次。
- [本次项目审核记录](docs/项目审核记录.md)：发现的问题、修正和验证证据、尚未实现的范围。
- [接口约定与接入指南](docs/接口约定与接入指南.md)：调用链、动作、字段、金额与结果表述。
- [内部服务与开工验收](docs/内部服务与开工验收.md)：资源、资金、调度和 G01—G15 集成验收。

## 五人如何分工

A 负责全部程序、自动化测试、配置和技术修复；B—E 使用 AI 辅助整理规则、数据、独立验收和材料，不要求交代码。

| 成员 | 主责 | 交付重点 |
|---|---|---|
| A（你） | 全部编码与集成 | 页面、智能体、各业务模块、运行配置与技术事实 |
| B | 转账/公共资金规则、安全验收、需求跟踪 | 权限表、资金算例、安全证据 |
| C | 数据、账单口径、测试统筹、使用说明 | 小账本与手算、验收记录、缺陷回归 |
| D | 理财规则、技术材料、答辩 | 产品/问卷/申赎规则、文档、自评、PPT |
| E | 卡片/订阅规则、交互与演示 | 状态表、文案、生日素材、简介与视频 |

后续也直接在 main 完成可核验的改动和提交。旧 A 第一轮分支的成果已合入，历史保留，不需要切换分支才能运行页面。

## 启动页面

需要 Python 3.10+。Windows PowerShell 在项目根目录首次运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-app.txt
.\.venv\Scripts\python.exe -m streamlit run app/main.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

已有 .venv 和依赖时只运行最后一条，或运行“启动A演示.ps1”。若无 py，使用已安装的 Python 完整路径创建虚拟环境。打开 [本地页面](http://127.0.0.1:8501)，输入“给李明转200元”或“给小王转钱”。用 Ctrl+C 结束服务。其他系统的命令见[启动文档](docs/A第一轮使用与验收.md)。

## 检查项目

安装页面依赖后运行：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B -X utf8 -m examples.contract_demo
```

2026-10-13 本地审核：**70 项测试通过，包括 5 项实际 Streamlit 页面交互测试，未跳过。** 其中共享接口测试 47 项、调用流程测试 18 项。未安装页面依赖时页面测试会跳过，这不能作为页面验收通过；GitHub Actions 会先检查页面测试依赖，再在 Python 3.10、3.12 上执行全部测试和接口演示。

契约演示使用“当日累计 900 元 + 本次 120.50 元”验证待强验证。页面测试替身则把当日已用累计固定为 0，只演示本次金额分级，不提供真实累计账本。这两种固定样例不是同一个连续业务系统。

## 共享接口

- [全部动作 JSON 样例](examples/contracts.json)：26 个动作及确认/验证/未知结果。
- [内部服务 JSON 样例](examples/internal_contracts.json)：资源快照、资金动作和任务格式。
- `shared/contracts.py`：字段、状态、授权载荷和计划校验。
- `shared/adapters.py`：别名、元/分换算、结果适配和统一文案。
- `shared/policy.py`：权限要求和操作摘要，不验证身份或执行交易。
- `shared/services.py`：内部协议，不实现账本、网关或调度器。

共享库只依赖标准库。接口保持 `schema_version="1.1"`；完整网络 JSON 使用 `loads_request/loads_result`，本地模块使用 `adapt_request/adapt_result`。模型只能提出请求，写操作必须经过网关；source 标签不构成业务执行证明。

修改样例生成器后同步 JSON：

```text
python -X utf8 -m examples.module_examples --write
python -X utf8 -m examples.service_examples --write
```

接口变更由 A 同步更新代码、文档、样例和测试，业务负责人核语义，C 组织回归。B—E 可以交表格，由 A 编写导入及校验。

## 后续范围

A 第一轮三项验收保持有效。第二轮依次做：单笔转账与持久化底座 → 其他业务最小模块 → 意图路由和生日计划；第三轮完成定时/AA、预算/商户、调度恢复、生日执行和 G01—G15 集成验收。第一版定时资金操作到期重验权限，缺少有效确认/验证时等待用户。

当前没有真实银行接入、完整金融业务账本、业务持久化、完整模型路由、调度恢复或运行隔离。测试通过说明已实现范围的检查通过，不能据此宣称六场景或整体安全验收完成。
