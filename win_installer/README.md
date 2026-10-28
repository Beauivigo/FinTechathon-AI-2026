# 言行 Windows 安装包

产品版本 **2.0.0**，面向 Windows 10/11 x64、当前用户安装。普通使用者只需运行发布页中的安装器，无需 Python、pip、桌宠或开发环境。模型调用需自行配置服务商密钥；所有银行数据为模拟。

2.0.0 交付柔彩 UI 视觉系统（A/B/C 三套模块配色）与统一组件样式，不新增业务功能，不改资金、授权、认证、模型调用或共享接口。从 1.0.x 覆盖升级沿用数据目录、身份、账本、模型配置与快捷方式约定，不重置日常数据。

快捷方式统一位于 **桌面 `other` 文件夹**：启动言行、停止言行、卸载言行。安装器不在桌面根目录或开始菜单新增入口；卸载只删除本应用三个快捷方式，保留 `other` 及其他文件。

用户数据位于 `%LOCALAPPDATA%\言行\data`，与安装源文件分开。升级及静默卸载保留数据；交互卸载只有明确选择删除、且路径/链接校验通过后才清理本应用数据。页面只监听回环地址，默认端口 8511；端口占用时停止，不终止其他程序。

## 1.0.x → 2.0.0 覆盖升级

- 先在桌面 `other` 中运行“停止言行”，退出本应用。
- 直接运行 2.0.0 安装器覆盖安装；安装目录、卸载登记（`HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\言行`）与三个快捷方式沿用既有约定。
- 数据目录不在安装目录内，安装器不写入、不重置；账号、密码、手机绑定、账本、流水、计划、通知、预算预留与模型配置保留。
- 安装器检测到应用仍在运行会中止安装并提示，不会强制结束进程。

## 发布载荷范围

包含：应用运行源码（`app`、`agent`、`bank`、`shared`、`billing`、`card`、`scenario`、`subscription`、`wealth`、`material_check`）、`.streamlit/config.toml`、`materials/reviewed/2026-10-16/` 两份已审核样例账单、独立 CPython 3.14.10、42 个固定 wheel、第三方许可与版本清单。

排除：开发机 `.venv`/`.cache`/`runtime`/`local_reports`、`tests`、`docs`、`scripts`、`examples`、`win_installer`、参赛材料、凭证与 secrets、日常账本与模型配置、Harness 诊断探针 `_vx_*.py`、Git 元数据。构建时按文件名与内容双重扫描，命中疑似凭证即失败退出。

## 在开发机复现构建

以下在仓库根目录的 PowerShell 执行。构建机需要可用 Python/pip，安装包使用官方 CPython 3.14.10 embeddable x64 和锁定的 42 个 wheel。编译器为官方 NSIS 3.12 便携包；不复用开发机 `.venv` 或 Codex 运行时。

```powershell
$toolRoot = Join-Path $PWD '.cache/installer-20261026/toolchain'
$wheelRoot = Join-Path $toolRoot 'wheels'
New-Item -ItemType Directory -Force -Path $wheelRoot | Out-Null
$lockedPackages = Get-Content win_installer/wheel-lock.json -Raw | ConvertFrom-Json
foreach ($package in $lockedPackages) {
    if (([uri]$package.url).Host -ne 'files.pythonhosted.org') { throw '非官方 wheel 来源' }
    $packageFile = Join-Path $wheelRoot $package.file
    Invoke-WebRequest -Uri $package.url -OutFile $packageFile
    if ((Get-FileHash -LiteralPath $packageFile).Hash.ToLower() -ne $package.sha256) { throw 'wheel SHA256 不符' }
}
$pythonZip = Join-Path $toolRoot 'python-3.14.10-embed-amd64.zip'
Invoke-WebRequest 'https://www.python.org/ftp/python/3.14.10/python-3.14.10-embed-amd64.zip' -OutFile $pythonZip
if ((Get-FileHash $pythonZip).Hash.ToLower() -ne '90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159') { throw 'Python SHA256 不符' }
$nsisZip = Join-Path $toolRoot 'nsis-3.12.zip'
Invoke-WebRequest 'https://downloads.sourceforge.net/project/nsis/NSIS%203/3.12/nsis-3.12.zip' -OutFile $nsisZip
if ((Get-FileHash $nsisZip).Hash.ToLower() -ne '56581f90db321581c5381193d796fffcf2d24b2f8fed2160a6c6a3baa67f2c4f') { throw 'NSIS SHA256 不符' }
Expand-Archive -LiteralPath $nsisZip -DestinationPath (Join-Path $toolRoot 'nsis') -Force
.\.venv\Scripts\python.exe -m win_installer.build_installer --source . --runtime-zip $pythonZip --wheels $wheelRoot --nsis (Join-Path $toolRoot 'nsis/nsis-3.12/makensis.exe') --output dist/yanxing-v2.0.0
```

只校验载荷、不生成安装器时追加 `--stage-only`；校验已有安装器与清单使用 `--verify --artifact ... --manifest ...`。输入来源及 SHA256 可分别对照 [Python 官方发布页](https://www.python.org/downloads/release/python-31410/)、[NSIS 官方发布文件](https://sourceforge.net/projects/nsis/files/NSIS%203/3.12/nsis-3.12.zip/download)和锁定清单内的 PyPI 元数据地址。若工具、固定 wheel、许可证或实际编译失败，构建明确失败，不生成占位安装器。

输出含真实 `.exe`、`manifest.json`、`SHA256SUMS.txt`、发布说明；安装目录内含应用源文件、独立运行时、资料账单资源、组件许可和版本清单。项目源码权利由团队保留，第三方组件遵循各自许可。

## 回归与独立验收

```powershell
.\.venv\Scripts\python.exe -m unittest win_installer.test_installer win_installer.test_runtime_paths -v
```

这些离线回归覆盖：载荷收集与排除（含 Harness 探针、secrets、Git 指针、日常数据）、疑似凭证失败关闭、清单确定性与内容绑定、许可证证据、数据保留的卸载计划、NSIS 中文脚本与快捷方式约定、工具/产物失败关闭，以及 2.0.0 版本在 README/安装说明/发布说明中的一致性。

`verify_windows.py` 必须从仓库根目录运行，仅接受本项目 `.cache/installer-20261026/` 内的一次性验收目录。按 `install → start → stop → upgrade → start → stop → uninstall` 运行各阶段；`--artifact` 指定本次安装器，`--work` 始终指定同一个全新测试目录。首次操作前应确认本机没有需要保留的同名安装登记或同名快捷方式。

该工具使用实际安装器、内置 Python、清空开发环境变量的子进程及独立用户数据，检查依赖/数据路径、重复启动、快捷方式目标、升级与默认卸载保留数据。实际浏览器页面和核心业务回归另行验收，不能用健康端点代替。

安装器未做发布者代码签名；本机干净目录验证不等于独立新电脑或全部 Windows 版本验证。本人手机签收、第二家模型在线验证、PPT/视频状态见项目 BLOCKERS。

## 历史版本

- 1.0.1：2026-10-28 从最终验收副本构建，记录见 [安装发布补充说明](../docs/参赛材料/07-安装发布补充说明.md) 与 [最终体验验收](../docs/2026-10-28全项目体验最终验收.md)。
- 1.0.0：2026-10-26 首个 Windows 发布，记录见 [安装包与GitHub发布验收](../docs/2026-10-26安装包与GitHub发布验收.md)。
- 2.0.0：当前发布准备，见 [v2.0.0发布说明](../docs/2026-10-28言行v2.0.0发布说明.md)。
