# 2026-10-19 B/C/E 材料独立核验

执行者 A/独立复核；基线 `56b8ea765850aea3ce3b24ea863cbc36a7e09048`；D 不在本次范围。未改生产代码、共享接口或日常运行数据库。原件只读归档于 materials/inbox，逐文件 SHA-256 见各成员 manifest。

## 实际执行

```powershell
./.venv/Scripts/python.exe -B -X utf8 docs/evidence/2026-10-19-material-review.py
./.venv/Scripts/python.exe -B -X utf8 -m unittest tests.test_budget tests.test_subscription_scan -v
```

- [独立检查 JSON](2026-10-19-material-review.json)：164项检查通过，0失败。它包含原JSON复核、重新执行、参考算法、算术和保护探查，**不是164个独立产品功能**。
- C实测：预算16例、订阅12例；另4例只验证当前未实现入口拒绝，不算商户/取消/提醒通过。
- R03-A：22例调用当前billing实现通过；R01的10例和R03-B的8例是未接入生产的参考复算。
- 新增E年付7例通过；B七个资金情形算术守恒通过（不冒称merchant.order运行测试）。
- R03-B反例重现原提前取整误报；修订参考的原8例及10000/10003/10004分边界通过。反例“成功重现错误”不代表原件正确。
- 现有专项 unittest：**28项通过，0失败、0错误、0跳过**，含N1页面、并发、回滚、未知阻断与持久化。测试出现Streamlit裸线程上下文提示，测试结果OK；未再次全量跑356项。

## 验收脚本首轮修正

A首次校验151通过、2失败，均在同一JAC-1-16原记录/复跑记录：A断言把内部effect当成公开action。实际审计是action=budget.reserve/budget.release、details.effect=reserve/release_reserved；按bank/budget.py查明后修正校验，同时断言金额30000和posting_key存在。该失败是A检查器问题，未归责C，也未修改业务代码。后续增加R03-B反例与B手算，最终164项通过。

## 范围限制

补测数据为明确的模拟流水，每个网关用例使用临时SQLite库；直接插入订阅测试流水不代表真实历史记账链路已验收。原脚本由A完整阅读后，通过runpy非main执行，防止覆盖成员JSON；实际执行者为A，不能冒充C原执行日志。运行库哈希保存在JSON并比对一致。

本轮只审核现有材料与补证。R01/R03-B数据接入、merchant.order、card_id、生日、取消/提醒等未实现功能仍按BLOCKERS跟踪。资金规则、权限和公共接口判定不交给外部模型。

后续实施说明：本文件记录56b8ea7基线下的材料验收。当天之后商户、取消/提醒和计划已进入实现，旧材料脚本中“未实现探查”的预期仅适用于该基线，不能直接当新版本回归。新版本以[开发验收](../2026-10-19可开工任务验收.md)为准，原记录不改写。
