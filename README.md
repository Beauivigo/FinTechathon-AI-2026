# FinTechathon-AI-2026

2026 FinTechathon 银行 AI 智能体项目，目标覆盖全部六个场景。

**统一使用 main。** 当前已实现持久化模拟银行、单笔与定时转账、账单统计、仓库内C资料只读接入、产品对比、卡片四种状态操作与限额、AA收款单、订阅确认查询及核实/排除、预算预留与无订单关联预留的释放。共用确认/模拟强验证、幂等、审计和恢复；共享JSON接口仍为v1.1。新库使用E模拟卡与固定订阅样例，旧卡保留；产品为工程模拟数据。AA不执行付款，定时转账到期需手动检查并重新确认。自动订阅识别、问卷评分、理财申赎、六场景路由和生日计划仍待开发。

## 常用文档

- [下一阶段分工与实施计划](docs/下一阶段分工与实施计划/README.md)：当前每人做什么、交什么、怎样验收；下一轮分派继续更新这里。
- [BLOCKERS：待补材料与开发阻塞](docs/BLOCKERS.md)：哪些能开工，哪些还缺业务规则。
- [成员资料勘误](docs/成员资料勘误.md)：顶部是待成员处理清单，下方保留历史修正记录。
- [最新开发验收](docs/N1预算预留与释放验收.md)：已完成范围、复现命令、测试证据与限制。
- [接口约定与接入指南](docs/接口约定与接入指南.md)：共享v1.1接口与接入要求。
- [开发与需求阻塞处理规则](docs/开发与需求阻塞处理规则.md)：资料缺失、勘误和补测的处理约定。

A负责全部编码与自动化测试，B—E负责各自业务规则、数据和材料。历史方案与资料仍保留在仓库，首页只展示当前常用入口。

## 启动页面

需要 Python 3.10+。Windows PowerShell 在项目根目录首次运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-app.txt -r requirements-materials.txt
.\.venv\Scripts\python.exe -m streamlit run app/main.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

已有 .venv 和依赖时只运行最后一条，或运行“启动A演示.ps1”。若无 py，使用已安装的 Python 完整路径创建虚拟环境。打开 [本地页面](http://127.0.0.1:8501)，输入“给李明转200元”或“给小王转钱”。用 Ctrl+C 结束服务。

## 检查项目

最新本地验收（2026-10-17）：**342项测试通过，0失败、0错误、0跳过**，含实际Streamlit页面交互；日常演示库未改动。详见[N1验收与证据](docs/N1预算预留与释放验收.md)。

安装页面与资料检查依赖后运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-app.txt -r requirements-materials.txt
.\.venv\Scripts\python.exe -B -X utf8 scripts/acceptance.py full
.\.venv\Scripts\python.exe -B -X utf8 -m examples.contract_demo
```

## 接口与运行边界

共享接口保持 `schema_version="1.1"`。修改接口时由A统一更新代码、文档、样例和测试，业务负责人复核语义。

AA收款单不代表已收到付款；定时任务需手动检查并在到期时重新授权，程序关闭时不会自动执行。当前使用模拟银行与模拟验证，未接入真实银行或真实身份验证。

运行库位于被Git忽略的 `runtime/`。重置会清除模拟交易，未知结果应先查询原操作。
