# PPT独立复核与视觉升级证据

2026-10-30，独立复核执行。源码基线：`e555721b4be4e8674ef56d9ec80b1e10487d5116`。本轮只改参赛材料和资料记录，未修改应用代码、资金规则、认证或模型调用。

- [交付PPTX](../../过期文档清理记录-20261101.md)：24页、16:9、24份讲者备注、原生可编辑文字与图形。原稿与原Word勘误保留原字节。
- [补充勘误](../../../参赛材料_Word_2026-10-24/PPT复核勘误_言行_FinTechathon_2026_20261030.md)：7项问题及依据，包括原辅助报告自身的错误。
- [原件清单](original-inventory.json)：原始24页正文、备注、4张内嵌截图的哈希及证据映射。
- [57项离线专项原始日志](targeted-tests.txt)：覆盖契约、授权展示、TOTP、理财申赎、订阅续费、后台和六场景。本轮没有重跑历史713项全量回归，也没有新的真实银行、本人手机或付费模型调用。
- [SVG质量门](svg-quality-report.json)：24/24通过，0错误、0警告。
- [PPTX导出检查](pptx-postflight-report.json)：原生导出通过，24页、24份备注。
- [PowerPoint渲染记录](native-render.json)：Microsoft PowerPoint 15.0，只读导出24张1600×900 PNG。
- [最终独立验收](final-verification.json)、[新版正文与备注](native-text-and-notes.json)：对正式交付副本验证ZIP、24页备注、可编辑对象、4张截图字节、无外部资源以及关键勘误文案。

实际运行的现有测试：

```powershell
& .\.venv\Scripts\python.exe -B -X utf8 -m unittest tests.test_contracts tests.test_authorization_display tests.test_real_auth tests.test_wealth_purchase tests.test_wealth_redeem tests.test_subscription_charge tests.test_u3_background tests.test_six_scenarios -v
```

初次沙箱运行因临时SQLite访问权限停止，未计为通过；通过官方执行机制原样重跑，57项通过，未修改测试断言。HTML实时预览缺少Flask，未启用；实际PowerPoint原生渲染已完成视觉检查。成员姓名保留原稿，正式报名表未提供独立对照。

逐页视觉核验联系表：

- [第1—4页](contact-1.jpg)
- [第5—8页](contact-2.jpg)
- [第9—12页](contact-3.jpg)
- [第13—16页](contact-4.jpg)
- [第17—20页](contact-5.jpg)
- [第21—24页](contact-6.jpg)

检查了全部24页，并放大复核累计授权和历史授权回执页；未发现正文溢出、遮挡或页面结构错位。详细设计规范、SVG源文件、讲者备注源稿和全分辨率渲染保存在本地`.cache/ppt-audit-20261030/`工作空间。
