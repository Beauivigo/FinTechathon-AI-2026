# FinTechathon-AI-2026

2026 FinTechathon 银行 AI 智能体项目，目标覆盖全部六个场景。

**统一使用 main。** 当前已实现持久化模拟银行、单笔与定时转账、账单统计与大额单笔提示、C资料只读接入、产品对比、风险问卷评分与理财申购/赎回、卡片状态与限额、卡片申请受理、AA收款单、订阅识别/核实/取消/续费/本地提醒，以及预算预留、商户选卡下单、部分消费与剩余释放。新增“生日与商户”“理财”页面；首页“需求入口”支持有限中文规则识别、追问和草稿导航。共用确认/模拟强验证、幂等、审计和恢复；共享JSON接口为v1.1，存储为v12。完整自由对话编排及后台生日自动执行仍未完成。

## 常用文档

- [下一阶段分工与实施计划](docs/下一阶段分工与实施计划/README.md)：第四轮（10-19分派，计划截止10-20），所有人各加一轮；本体目标10-24，联调缓冲至10-26。
- [BLOCKERS：待补材料与开发阻塞](docs/BLOCKERS.md)：哪些能开工，哪些还缺业务规则。
- [成员资料勘误](docs/成员资料勘误.md)：顶部是待成员处理清单；10-19已核验B/C/E（D除外），E-11解除、C-07剩两项决定；下方保留历史修正记录。
- [最新开发验收](docs/A4第三步验收.md)：商户定时登记、到期待授权、取消与恢复已完成；426项全量回归通过。
- [接口约定与接入指南](docs/接口约定与接入指南.md)：共享v1.1接口与接入要求。
- [开发与需求阻塞处理规则](docs/开发与需求阻塞处理规则.md)：资料缺失、勘误和补测的处理约定。

A负责技术交付、代码审查与验收；后续合适的编码默认委派DeepSeek，资金、权限及共享契约由A处理。B—E负责业务规则、数据和材料。历史方案与资料仍保留在仓库，首页只展示当前常用入口。

## 启动页面

需要 Python 3.10+。Windows PowerShell 在项目根目录首次运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-app.txt -r requirements-materials.txt
.\.venv\Scripts\python.exe -m streamlit run app/main.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

已有 .venv 和依赖时只运行最后一条，或运行“启动A演示.ps1”。若无 py，使用已安装的 Python 完整路径创建虚拟环境。打开 [本地页面](http://127.0.0.1:8501)，输入“给李明转200元”或“给小王转钱”。用 Ctrl+C 结束服务。

## 检查项目

最新本地验收（2026-10-20）：**443项测试通过，0失败、0错误、0跳过**，包含理财评分/申购/赎回、公开订阅续费、理财页面与路由，以及原功能回归；日常演示库未改动。详见[理财页面与路由验收](docs/2026-10-19理财页面与路由验收.md)、[六场景联调](docs/2026-10-20六场景联调验收.md)与[收尾一致性证据](docs/evidence/2026-10-20-consistency.json)。此前426/445项验收按原报告保留。

安装页面与资料检查依赖后运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-app.txt -r requirements-materials.txt
.\.venv\Scripts\python.exe -B -X utf8 docs/evidence/a4_third_acceptance.py full
.\.venv\Scripts\python.exe -B -X utf8 -m examples.contract_demo
```

## 接口与运行边界

共享接口保持 `schema_version="1.1"`。修改接口时由A统一更新代码、文档、样例和测试，业务负责人复核语义。

AA收款单不代表已收到付款；定时任务需手动检查并在到期时重新授权，程序关闭时不会自动执行。卡片申请仅登记受理；订阅提醒需手动检查，尚无自动扣费引擎；生日页面不自动下单或发送后台通知。当前使用模拟银行与模拟验证，未接入真实银行或真实身份验证。

运行库位于被Git忽略的 `runtime/`。重置会清除模拟交易，未知结果应先查询原操作。
