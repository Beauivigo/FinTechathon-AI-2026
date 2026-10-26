# 言行 Windows 安装包

产品版本 1.0.0，面向 Windows 10/11 x64、当前用户安装。普通使用者只需运行发布页中的安装器，无需 Python、pip、桌宠或开发环境。模型调用需自行配置服务商密钥；所有银行数据为模拟。

快捷方式统一位于 **桌面 `other` 文件夹**：启动言行、停止言行、卸载言行。安装器不在桌面根目录或开始菜单新增入口；卸载只删除本应用三个快捷方式，保留 `other` 及其他文件。

用户数据位于 `%LOCALAPPDATA%\言行\data`，与安装源文件分开。升级及静默卸载保留数据；交互卸载只有明确选择删除、且路径/链接校验通过后才清理本应用数据。页面只监听回环地址，默认端口 8511；端口占用时停止，不终止其他程序。

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
.\.venv\Scripts\python.exe -m win_installer.build_installer --source . --runtime-zip $pythonZip --wheels $wheelRoot --nsis (Join-Path $toolRoot 'nsis/nsis-3.12/makensis.exe') --output dist/yanxing-v1.0.0
```

输入来源及 SHA256 可分别对照 [Python 官方发布页](https://www.python.org/downloads/release/python-31410/)、[NSIS 官方发布文件](https://sourceforge.net/projects/nsis/files/NSIS%203/3.12/nsis-3.12.zip/download)和锁定清单内的 PyPI 元数据地址。若工具、固定 wheel、许可证或实际编译失败，构建明确失败，不生成占位安装器。

输出含真实 `.exe`、`manifest.json`、`SHA256SUMS.txt`、发布说明；安装目录内含应用源文件、独立运行时、资料账单资源、组件许可和版本清单。项目源码权利由团队保留，第三方组件遵循各自许可。

## 独立验收

`verify_windows.py` 必须从仓库根目录运行，仅接受本项目 `.cache/installer-20261026/` 内的一次性验收目录。按 `install → start → stop → upgrade → start → stop → uninstall` 运行各阶段；`--artifact` 指定本次安装器，`--work` 始终指定同一个全新测试目录。首次操作前应确认本机没有需要保留的同名安装登记或同名快捷方式。

该工具使用实际安装器、内置 Python、清空开发环境变量的子进程及独立用户数据，检查依赖/数据路径、重复启动、快捷方式目标、升级与默认卸载保留数据。实际浏览器页面和核心业务回归另行验收，不能用健康端点代替。

安装器未做发布者代码签名；本机干净目录验证不等于独立新电脑或全部 Windows 版本验证。本人手机签收、第二家模型在线验证、PPT/视频状态见项目 BLOCKERS。

安装工具的 13 项离线回归：

```powershell
.\.venv\Scripts\python.exe -m unittest win_installer.test_installer win_installer.test_runtime_paths -v
```
