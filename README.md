# FinTechathon-AI-2026

2026 FinTechathon 银行 AI 智能体项目。目标覆盖题目全部六个场景。

目前是团队协作与接口适配起点，尚未实现完整银行业务系统。

- [五人分工与实施计划（详细讨论稿，含逐人任务、交付与验收）](docs/五人分工与实施计划.md)
- [接口约定与接入指南](docs/接口约定与接入指南.md)
- [内部服务与开工验收](docs/内部服务与开工验收.md)：资源来源、资金交接、调度、网关入口与必须通过的验收项。
- [所有动作的完整 JSON 样例](examples/contracts.json)：26 个动作及确认/验证/结果未知示例，五人按同一格式接入。
- [内部服务 JSON 样例](examples/internal_contracts.json)：资源快照、五类资金动作及三种任务样例；均为格式示范。
- `shared/contracts.py`：六场景与公共查询动作、嵌套字段、返回状态、授权载荷格式与计划依赖校验。
- `shared/adapters.py`：字段别名、元/分换算、结果状态适配、统一用户文案。
- `shared/policy.py`：题目权限要求计算、操作摘要；不执行交易或验证身份。
- `shared/services.py`：内部服务函数约定、资源/资金/调度校验与卡片/订阅状态规则；后端仍需实现。

使用 Python 3.10+，无第三方依赖。在仓库根目录运行：

```text
python -X utf8 -m examples.contract_demo
python -m unittest discover -s tests -v
```

Demo 展示 900 元日累计 + 本次 120.50 元触发“待强验证”，不会执行真实或模拟扣款。五人都使用 AI 工具，A/B 侧重编程与集成，C/D/E 侧重业务、数据、测试与展示，同时参与各自模块开发。

当前共用版为 `schema_version="1.1"`，不接受旧 1.0 请求。五人应同步整个提交，不能只复制适配器：本次增加订阅依据与核实、解挂、内部服务协议，并禁止业务来源在状态更新中切换。当前实现的是契约和适配库，确认凭据校验、鉴权、事务扣款、幂等存储等仍需 B 的执行网关实现。

接入流程：找到自己的 JSON 样例 → 使用 `from shared import ...` 导入公共函数 → 编写业务模块 → 校验请求/结果 → 运行测试 → 合并。完整网络 JSON 使用 `loads_request/loads_result`，可拒绝重复字段和错误请求关联；本地模块使用 `adapt_request/adapt_result`。

修改样例源 `examples/module_examples.py` 后，用以下命令同步 JSON；测试会检查是否遗漏更新：

```text
python -X utf8 -m examples.module_examples --write
python -X utf8 -m examples.service_examples --write
```

已配置 GitHub Actions，在 push/PR 时用 Python 3.10、3.12 检查契约、样例和演示脚本。上传后以实际工作流结果为准。团队成员应同步同一提交版本，修改共享接口时一起更新代码、文档、样例和测试。

开工顺序：全员跑通上述测试 → A/B 按内部服务协议交最小可调用入口 → C/D/E 独立实现自己的模块 → 集成六场景及安全验收 → 再增加功能。第一版定时资金操作到期重新校验，缺少有效确认/强验证时等待用户；不承诺无人值守扣款。
