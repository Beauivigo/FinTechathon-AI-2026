"""Build a Windows x64 installer for the frozen 言行 application sources.

Design: a caller-supplied official CPython 3.14 Windows runtime plus pinned
wheels are installed into a portable payload; the frozen source tree is copied
(with no fixed file list), verified, and an NSIS installer is generated from a
strict payload manifest.  Real compilation is intentionally a separate step:
this module refuses to run when official tools are missing and never creates a
placeholder executable.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.parser import Parser
from hashlib import sha256
from pathlib import Path
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

BRAND = '言行'
PRODUCT_VERSION = '1.0.0'
DATA_DIR_NAME = '言行'
MANIFEST_SCHEMA = 1
MIN_INSTALLER_BYTES = 1_000_000
DEFAULT_INSTALL_DIR = '$LOCALAPPDATA\\Programs\\' + BRAND
DEFAULT_DATA_DIR = '$LOCALAPPDATA\\' + DATA_DIR_NAME + '\\data'
NSIS_DOWNLOAD = 'https://nsis.sourceforge.io/Download'
PYTHON_DOWNLOAD = 'https://www.python.org/downloads/windows/'

EXCLUDED_DIRS = {
    '.git', '.github', '.venv', 'venv', 'env', '__pycache__', '.pytest_cache',
    '.cache', 'runtime', 'local_reports', 'materials', 'docs',
    'tests', 'scripts', 'node_modules', 'win_installer', '项目要求-非代码',
    '参赛材料_Word_2026-10-24', 'examples',
    '.idea', '.vscode', 'dist', 'build',
}
EXCLUDED_ROOT_NAMES = {
    'README.md', 'AGENTS.md', 'requirements-app.txt', 'requirements-materials.txt',
    'launcher.py', '启动A演示.ps1', '启动银行助手.py', '.gitignore',
    '.gitattributes', '.env', '.env.local', 'test_model_security.py',
}
EXCLUDED_SUFFIXES = {
    '.pyc', '.pyo', '.log', '.db', '.sqlite', '.sqlite3', '.pem', '.key',
    '.p12', '.pfx', '.tmp', '.temp',
}
SCAN_SUFFIXES = {
    '.py', '.ps1', '.cmd', '.bat', '.json', '.txt', '.toml', '.cfg', '.ini',
    '.md', '.cs', '.css', '.html', '.js', '.yml', '.yaml',
}
SECRET_NAME_PATTERNS = (
    re.compile(r'^\.env(?:\..*)?$', re.I),
    re.compile(r'.*\.(?:pem|key|p12|pfx)$', re.I),
    re.compile(r'^id_(?:rsa|dsa|ec|ed25519)(?:\.pub)?$', re.I),
    re.compile(r'^credentials.*\.json$', re.I),
    re.compile(r'.*\.(?:sqlite3?|db)$', re.I),
)
SECRET_CONTENT_PATTERNS = (
    ('openai-style key', re.compile(r'sk-[A-Za-z0-9]{20,}')),
    ('private key block', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
    ('github token', re.compile(r'ghp_[A-Za-z0-9]{30,}')),
    ('aws access key', re.compile(r'AKIA[0-9A-Z]{16}')),
    ('google api key', re.compile(r'AIza[0-9A-Za-z_\-]{30,}')),
)


class BuildError(RuntimeError):
    """A build precondition, payload or tool problem; never silently swallowed."""


# ---------------------------------------------------------------------------
# source and payload inventory
# ---------------------------------------------------------------------------

def _is_link(path):
    try:
        return Path(path).is_symlink() or getattr(Path(path), 'is_junction', lambda: False)()
    except OSError:
        return False


def _safe_relative(path):
    if not isinstance(path, str) or not path or path.startswith(('/', '\\')):
        raise BuildError('载荷路径必须是相对路径: %r' % (path,))
    value = path.replace('\\', '/')
    if any(part in {'', '.', '..'} for part in value.split('/')):
        raise BuildError('载荷路径不得包含空段、. 或 ..: %r' % (path,))
    if any(ord(ch) < 32 for ch in value) or '"' in value:
        raise BuildError('载荷路径包含控制字符或引号: %r' % (path,))
    if re.match(r'^[A-Za-z]:', value):
        raise BuildError('载荷路径不能是绝对盘符路径: %r' % (path,))
    return value


def _file_entry(root, relative):
    path = Path(root) / Path(relative)
    data = path.read_bytes()
    return dict(path=relative, size=len(data), sha256=sha256(data).hexdigest())


def _secret_name(path):
    name = Path(path).name
    return any(pattern.match(name) for pattern in SECRET_NAME_PATTERNS)


def source_payload(source_root):
    """Return the runtime source files from a frozen source directory.

    The function is intentionally allow-by-default inside the application tree:
    unknown packages and new modules such as ``app/pending_view.py`` are
    included automatically.  Only clearly development, data, secret and build
    locations are excluded.
    """
    root = Path(source_root)
    if not root.is_dir():
        raise BuildError('冻结源码目录不存在: %s' % root)
    if not (root / 'app' / 'main.py').is_file():
        raise BuildError('冻结源码目录缺少 app/main.py: %s' % root)
    entries = []
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        keep_dirs = []
        for name in sorted(dirs):
            full = current_path / name
            if name in EXCLUDED_DIRS:
                continue
            if _is_link(full):
                raise BuildError('源码树不允许符号链接目录: %s' % full)
            keep_dirs.append(name)
        dirs[:] = keep_dirs
        for name in sorted(files):
            full = current_path / name
            relative = full.relative_to(root).as_posix()
            top_level = '/' not in relative
            if top_level and name in EXCLUDED_ROOT_NAMES:
                continue
            if full.suffix.lower() in EXCLUDED_SUFFIXES:
                continue
            if _secret_name(name):
                continue
            if relative == 'app/installed_main.py':
                continue
            if _is_link(full):
                raise BuildError('源码树不允许符号链接文件: %s' % full)
            _safe_relative(relative)
            entries.append(_file_entry(root, relative))
    for name in ('账单数据与手算_C_V1.3_复核修订.xlsx', '资金规则与功能复核_B_V1.2_复核修订.xlsx'):
        rel = 'materials/reviewed/2026-10-16/' + name
        if (root / rel).is_file():
            entries.append(_file_entry(root, rel))
    if not any(row['path'] == 'app/main.py' for row in entries):
        raise BuildError('冻结源码缺少 app/main.py')
    return sorted(entries, key=lambda row: row['path'])


def tree_sha256(entries):
    ordered = sorted((row['path'], row['sha256']) for row in entries)
    payload = json.dumps(ordered, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return sha256(payload).hexdigest()


def build_payload_manifest(source_root, entries, *, version=PRODUCT_VERSION, brand=BRAND):
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise BuildError('产品版本必须是 x.y.z: %r' % (version,))
    if not isinstance(brand, str) or not brand.strip() or '"' in brand or any(ord(ch) < 32 for ch in brand):
        raise BuildError('品牌名无效')
    seen = set()
    files = []
    for entry in entries:
        path = _safe_relative(entry.get('path'))
        if path in seen:
            raise BuildError('载荷清单存在重复路径: %s' % path)
        seen.add(path)
        size, digest = entry.get('size'), entry.get('sha256')
        if type(size) is not int or size < 0:
            raise BuildError('载荷文件大小无效: %s' % path)
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise BuildError('载荷文件哈希无效: %s' % path)
        files.append(dict(path=path, size=size, sha256=digest))
    files.sort(key=lambda row: row['path'])
    total = sum(row['size'] for row in files)
    return dict(schema=MANIFEST_SCHEMA, brand=brand, version=version,
                file_count=len(files), total_bytes=total,
                source_tree_sha256=tree_sha256(files), files=files)


def scan_secrets(source_root, entries):
    root = Path(source_root)
    for entry in entries:
        path = entry['path']
        if _secret_name(path):
            raise BuildError('载荷包含疑似凭证文件名: %s' % path)
        suffix = Path(path).suffix.lower()
        if suffix not in SCAN_SUFFIXES:
            continue
        full = root / Path(path)
        try:
            if full.stat().st_size > 8 * 1024 * 1024:
                continue
            text = full.read_text(encoding='utf-8', errors='ignore')
        except OSError as exc:
            raise BuildError('无法读取待扫描文件 %s: %s' % (path, exc)) from None
        for label, pattern in SECRET_CONTENT_PATTERNS:
            if pattern.search(text):
                raise BuildError('载荷包含疑似%s，已停止: %s' % (label, path))


def payload_mb(entries):
    return max(1, math.ceil(sum(row['size'] for row in entries) / (1024 * 1024)))


def read_requirements(source_root):
    path = Path(source_root) / 'requirements-app.txt'
    if not path.is_file():
        raise BuildError('冻结源码缺少 requirements-app.txt')
    lines = []
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        lowered = line.lower()
        if any(token in lowered for token in ('-e', 'git+', 'http://', 'https://', 'file:', '-r ', '--')):
            raise BuildError('requirements-app.txt 不允许可编辑、路径或网络依赖: %s' % line)
        if ';' in line or '@' in line or '==' not in line:
            raise BuildError('requirements-app.txt 必须使用固定版本 ==: %s' % line)
        lines.append(line)
    if not lines:
        raise BuildError('requirements-app.txt 为空')
    return lines


# ---------------------------------------------------------------------------
# third-party licences and removal plan
# ---------------------------------------------------------------------------

def _dist_metadata(dist_info):
    metadata = dist_info / 'METADATA'
    if not metadata.is_file():
        return None
    try:
        return Parser().parsestr(metadata.read_text(encoding='utf-8', errors='ignore'))
    except (OSError, ValueError):
        return None


def _safe_component(value):
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '_', str(value)).strip('._')
    return cleaned[:80] or 'package'


def license_bundle(site_packages, licenses_dir):
    site = Path(site_packages)
    out = Path(licenses_dir)
    if not site.is_dir():
        raise BuildError('site-packages 不存在: %s' % site)
    packages, files, missing = [], [], []
    for folder in sorted(site.iterdir(), key=lambda item: item.name):
        if not folder.is_dir() or not folder.name.endswith('.dist-info'):
            continue
        metadata = _dist_metadata(folder)
        name = metadata.get('Name') if metadata else folder.name[:-len('.dist-info')].split('-')[0]
        version = metadata.get('Version') if metadata else ''
        name = str(name or '').strip()
        version = str(version or '').strip()
        if not name:
            continue
        license_text = ''
        if metadata:
            values = [metadata.get('License-Expression'), metadata.get('License')]
            for value in values:
                text = str(value or '').strip()
                if text and text.upper() not in {'UNKNOWN', 'NONE'}:
                    license_text = text
                    break
        sources = []
        if metadata:
            for field in ('Home-page', 'Project-URL', 'Download-URL'):
                for value in metadata.get_all(field) or []:
                    value = str(value).strip()
                    if value and value not in sources:
                        sources.append(value)
        license_files = []
        wanted = []
        if metadata:
            wanted.extend(str(value).strip() for value in metadata.get_all('License-File') or [])
        for candidate in folder.iterdir():
            if candidate.is_file() and re.match(r'(?i)(license|copying|notice|authors?)(?:[._-].*)?$', candidate.name):
                wanted.append(candidate.name)
        for item in sorted(set(wanted)):
            candidate = folder / item
            if not candidate.is_file():
                candidate = folder / 'licenses' / item
            if not candidate.is_file() or any(part in {'..', ''} for part in Path(item).parts):
                continue
            target = out / _safe_component(name) / Path(item).name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            license_files.append(str(target.relative_to(out)).replace('\\', '/'))
        if not license_files and not license_text:
            missing.append(name)
            continue
        metadata_copy = out / _safe_component(name) / 'METADATA'
        if metadata is not None:
            metadata_copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(folder / 'METADATA', metadata_copy)
            files.append(str(metadata_copy.relative_to(out)).replace('\\', '/'))
        files.extend(license_files)
        packages.append(dict(name=name, version=version, license=license_text or 'see licence files',
                             source=' ; '.join(sources), license_files=license_files))
    if missing:
        raise BuildError('以下第三方包缺少许可证文本或元数据证据: %s' % ', '.join(sorted(set(missing))))
    if not packages:
        raise BuildError('未在 site-packages 找到任何第三方许可证证据')
    lines = ['言行 第三方组件许可与来源清单', '=' * 44,
             '本文件由构建工具从已安装 wheel 的 METADATA 与许可证文件自动汇总。',
             '完整许可证文本位于同目录下的各组件文件夹；来源字段原样取自包元数据。', '']
    for row in packages:
        lines.append('- %s %s | %s | %s' % (row['name'], row['version'], row['license'], row['source'] or '未提供来源字段'))
        for file in row['license_files']:
            lines.append('    文件: ' + file)
    notices = '\n'.join(lines) + '\n'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'THIRD-PARTY-NOTICES.txt').write_text(notices, encoding='utf-8')
    return dict(packages=packages, files=sorted(set(files)), notices=notices)


def removal_plan(manifest, *, data_mode='preserve'):
    if data_mode not in {'preserve', 'clear'}:
        raise BuildError('data_mode 必须是 preserve 或 clear')
    files = []
    directories = set()
    for row in manifest.get('files', []):
        path = _safe_relative(row.get('path'))
        files.append(dict(path=path))
        parent = Path(path).parent.as_posix()
        while parent and parent != '.':
            directories.add(parent)
            parent = Path(parent).parent.as_posix()
    ordered_dirs = sorted(directories, key=lambda value: (-value.count('/'), value))
    data_targets = [] if data_mode == 'preserve' else [
        DEFAULT_DATA_DIR,
        '$INSTDIR\\runtime',
        '$INSTDIR\\.cache',
    ]
    return dict(data_mode=data_mode, files=files,
                directories=ordered_dirs, data_targets=data_targets,
                preserve_unknown=True)


# ---------------------------------------------------------------------------
# NSIS script generation (generated at build time; never committed as .nsi)
# ---------------------------------------------------------------------------

NSIS_TEMPLATE = r'''Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"
!include "WinVer.nsh"

!define BRAND "@BRAND@"
!define PRODUCT_VERSION "@VERSION@"
!define PRODUCT_VERSION_4 "@VERSION4@"
!define DATA_DIR "@DATA_DIR@"

; 内置运行时与入口（严格载荷清单另行校验）: python\python.exe

Name "${BRAND} ${PRODUCT_VERSION}"
OutFile "@OUTFILE@"
InstallDir "@INSTALL_DIR@"
InstallDirRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\@BRAND@" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "${BRAND} ${PRODUCT_VERSION}"
ShowInstDetails show
ShowUninstDetails show

!define MUI_ABORTWARNING
!define MUI_LICENSEPAGE_TEXT_TOP "请阅读本应用的来源、第三方许可与安全说明。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\python\pythonw.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS "$\"$INSTDIR\launcher\launch.py$\""
!define MUI_FINISHPAGE_RUN_TEXT "立即启动言行"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "@LICENSE_FILE@"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
VIProductVersion "@VERSION4@"
VIAddVersionKey /LANG=${LANG_SimpChinese} "ProductName" "${BRAND}"
VIAddVersionKey /LANG=${LANG_SimpChinese} "FileDescription" "${BRAND} 本地模拟银行演示"
VIAddVersionKey /LANG=${LANG_SimpChinese} "FileVersion" "${PRODUCT_VERSION}"
VIAddVersionKey /LANG=${LANG_SimpChinese} "ProductVersion" "${PRODUCT_VERSION}"
VIAddVersionKey /LANG=${LANG_SimpChinese} "CompanyName" "言行演示项目"



Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP|MB_OK "言行只支持 Windows 10/11 x64；当前系统不是 x64，安装已停止。"
    Quit
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP|MB_OK "言行需要 Windows 10 或 Windows 11；当前系统版本不受支持，安装已停止。"
    Quit
  ${EndIf}
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\@BRAND@" "InstallLocation"
  ${If} $0 != ""
    StrCpy $INSTDIR $0
  ${EndIf}
  IfFileExists "$INSTDIR\python\pythonw.exe" 0 init_done
  ExecWait '"$INSTDIR\python\pythonw.exe" -B "$INSTDIR\launcher\stop.py" --check --quiet' $0
  ${If} $0 == 2
    MessageBox MB_ICONSTOP|MB_OK "言行正在运行。请先关闭言行（桌面 other 文件夹中的“停止言行”），再继续安装或升级。"
    Quit
  ${ElseIf} $0 == 3
    MessageBox MB_ICONSTOP|MB_OK "无法核验当前言行进程状态；为避免影响其他程序，安装已停止。请先关闭言行后重试。"
    Quit
  ${EndIf}
init_done:
FunctionEnd

Section "安装言行" SEC_INSTALL
  SetShellVarContext current
  ; AvailableFreeSpace: query the selected destination volume using GetDiskFreeSpaceExW.
  ${GetRoot} "$INSTDIR" $2
  System::Call 'kernel32::GetDiskFreeSpaceExW(w "$2\", *l .r1, p 0, p 0)i.r0'
  ${If} $0 == 0
    Abort "无法检查可用磁盘空间。"
  ${EndIf}
  System::Int64Op $1 < @REQUIRED_BYTES@
  Pop $0
  ${If} $0 == 1
    Abort "磁盘空间不足。"
  ${EndIf}
  ClearErrors
  CreateDirectory "$INSTDIR"
  IfErrors 0 install_dir_ok
  MessageBox MB_ICONSTOP|MB_OK "无法创建安装目录。请选择可写目录（建议使用默认的当前用户目录）。"
  Abort
install_dir_ok:
@INSTALL_FILES@

  CreateDirectory "$DESKTOP\other"
  SetOutPath "$INSTDIR"
  CreateShortCut "$DESKTOP\other\启动言行.lnk" "$INSTDIR\python\pythonw.exe" "-B $\"$INSTDIR\launcher\launch.py$\"" "$INSTDIR\python\pythonw.exe" 0
  CreateShortCut "$DESKTOP\other\停止言行.lnk" "$INSTDIR\python\pythonw.exe" "-B $\"$INSTDIR\launcher\stop.py$\" --quiet" "$INSTDIR\python\pythonw.exe" 0
  CreateShortCut "$DESKTOP\other\卸载言行.lnk" "$INSTDIR\uninstall.exe"
  IfFileExists "$DESKTOP\other\启动言行.lnk" 0 shortcut_failed
  IfFileExists "$DESKTOP\other\停止言行.lnk" 0 shortcut_failed
  IfFileExists "$DESKTOP\other\卸载言行.lnk" shortcut_done 0
shortcut_failed:
  Abort "无法在桌面 other 文件夹创建快捷方式，请检查该文件夹权限。"
shortcut_done:
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\@BRAND@" "DisplayName" "${BRAND}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\@BRAND@" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\@BRAND@" "Publisher" "言行演示项目"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\@BRAND@" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\@BRAND@" "DisplayIcon" "$INSTDIR\python\pythonw.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\@BRAND@" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\@BRAND@" "QuietUninstallString" "$\"$INSTDIR\uninstall.exe$\" /S"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\@BRAND@" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\@BRAND@" "NoRepair" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\@BRAND@" "EstimatedSize" @ESTIMATED_KB@
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\@BRAND@" "URLInfoAbout" "言行本地演示"
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  IfFileExists "$INSTDIR\python\pythonw.exe" 0 uninstall_continue
  ExecWait '"$INSTDIR\python\pythonw.exe" -B "$INSTDIR\launcher\stop.py" --quiet' $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP|MB_OK "无法安全停止言行（或无法核验其进程），卸载已停止。请先在桌面 other 文件夹运行“停止言行”后重试。"
    Abort
  ${EndIf}
uninstall_continue:
  IfSilent preserve_data
  MessageBox MB_YESNO|MB_DEFBUTTON2|MB_ICONQUESTION "是否同时删除本应用的本地数据（账号、待办、模型配置与日志）？默认保留，选择“否”不影响再次安装或升级。" IDNO preserve_data
  ExecWait '"$INSTDIR\python\python.exe" -B "$INSTDIR\launcher\stop.py" --validate-data --quiet' $0
  ${If} $0 != 0
    Abort "数据目录包含链接或路径无效；未删除数据。"
  ${EndIf}
  RMDir /r "@DATA_DIR@"
preserve_data:
@UNINSTALL_FILES@
@UNINSTALL_DIRS@
  Delete "$DESKTOP\other\启动言行.lnk"
  Delete "$DESKTOP\other\停止言行.lnk"
  Delete "$DESKTOP\other\卸载言行.lnk"
  ; other 是用户共享文件夹，卸载不删除它或其中任何其他文件。
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\@BRAND@"

  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
'''


def _win_path(path):
    return str(path).replace('/', '\\')


def _nsis_quote(value):
    text = str(value)
    if '"' in text or any(ord(ch) < 32 for ch in text):
        raise BuildError('NSIS 参数包含引号或控制字符: %r' % text)
    return '"' + text + '"'


def generate_nsis(context):
    manifest = context['manifest']
    version = context['version']
    brand = context['brand']
    payload_dir = Path(context['payload_dir']).resolve()
    output_path = Path(context['output_path']).resolve()
    license_file = context.get('license_file')
    if license_file is None:
        license_file = payload_dir / '许可与第三方说明.txt'
    license_file = Path(license_file).resolve()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', str(version)):
        raise BuildError('NSIS 版本必须是 x.y.z')
    version4 = str(version) + '.0'

    groups = {}
    for row in manifest['files']:
        relative = _safe_relative(row['path'])
        parent = Path(relative).parent.as_posix()
        if parent == '.':
            parent = ''
        groups.setdefault(parent, []).append(relative)
    install_lines, uninstall_lines = [], []
    for parent in sorted(groups):
        target = '$INSTDIR' + (('\\' + parent.replace('/', '\\')) if parent else '')
        install_lines.append('  SetOutPath ' + _nsis_quote(target))
        for relative in sorted(groups[parent]):
            source = payload_dir / Path(relative)
            install_lines.append('  ; payload: ' + relative.replace('/', '\\'))
            install_lines.append('  File "/oname=%s" %s' % (Path(relative).name.replace('"', ''), _nsis_quote(_win_path(source))))
            uninstall_lines.append('  Delete ' + _nsis_quote(target + '\\' + Path(relative).name))
    plan = removal_plan(manifest)
    uninstall_dirs = ['  RMDir "$INSTDIR\\' + directory.replace('/', '\\') + '"'
                      for directory in plan['directories']]
    replacements = {
        '@BRAND@': str(brand),
        '@VERSION@': str(version),
        '@VERSION4@': version4,
        '@DATA_DIR@': DEFAULT_DATA_DIR,
        '@OUTFILE@': _win_path(output_path),
        '@INSTALL_DIR@': DEFAULT_INSTALL_DIR,
        '@LICENSE_FILE@': _win_path(license_file),
        '@REQUIRED_BYTES@': str((payload_mb(manifest['files']) * 2 + 200) * 1048576),
        '@PAYLOAD_MB@': str(int(context.get('payload_mb') or payload_mb(manifest['files']))),
        '@ESTIMATED_KB@': str(max(1, int(manifest['total_bytes']) // 1024)),
        '@INSTALL_FILES@': '\n'.join(install_lines),
        '@UNINSTALL_FILES@': '\n'.join(uninstall_lines),
        '@UNINSTALL_DIRS@': '\n'.join(uninstall_dirs),
        '@INSTDIR@': DEFAULT_INSTALL_DIR,
    }
    script = NSIS_TEMPLATE
    for key, value in replacements.items():
        script = script.replace(key, value)
    return script


# ---------------------------------------------------------------------------
# tool and artifact guards
# ---------------------------------------------------------------------------

def require_makensis(path=None):
    if path is not None:
        candidate = Path(path)
        if not candidate.is_file():
            raise BuildError('NSIS 编译器不存在: %s。请从官方 %s 获取 NSIS 3.x 并重试。'
                             % (candidate, NSIS_DOWNLOAD))
        return str(candidate)
    found = shutil.which('makensis') or shutil.which('makensis.exe')
    if not found:
        raise BuildError('未找到 NSIS 编译器 makensis。请从官方 %s 安装 NSIS 3.x，'
                         '或使用 --nsis 指定 makensis.exe；本工具不会自行下载或伪造安装器。'
                         % NSIS_DOWNLOAD)
    return found


def compile_installer(nsi_path, artifact_path, *, makensis, runner=None, timeout=900):
    nsi = Path(nsi_path)
    artifact = Path(artifact_path)
    if not nsi.is_file():
        raise BuildError('生成的 NSIS 脚本不存在: %s' % nsi)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    if artifact.exists():
        artifact.unlink()
    runner = runner or subprocess.run
    command = [str(makensis), '/V2', str(nsi)]
    try:
        result = runner(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BuildError('NSIS 编译超时；未生成安装器。') from None
    except OSError as exc:
        raise BuildError('无法启动 NSIS 编译器: %s' % exc) from None
    if getattr(result, 'returncode', 1) != 0:
        if artifact.exists():
            artifact.unlink()
        detail = (getattr(result, 'stderr', '') or getattr(result, 'stdout', '') or '')[-1200:]
        raise BuildError('NSIS 编译失败，未生成安装器。编译器输出：%s' % detail.strip())
    if not artifact.is_file() or artifact.stat().st_size < MIN_INSTALLER_BYTES:
        if artifact.exists():
            artifact.unlink()
        raise BuildError('NSIS 未生成有效的非空安装器（缺失或小于 %d 字节）。' % MIN_INSTALLER_BYTES)
    with artifact.open('rb') as handle:
        magic = handle.read(2)
    if magic != b'MZ':
        artifact.unlink()
        raise BuildError('编译结果不是 Windows 可执行文件；已删除伪安装器。')
    return artifact


def verify_artifact(artifact, artifact_manifest, *, min_bytes=MIN_INSTALLER_BYTES):
    path = Path(artifact)
    if not path.is_file():
        raise BuildError('安装器不存在: %s' % path)
    data = path.read_bytes()
    if len(data) < min_bytes:
        raise BuildError('安装器过小，疑似空文件或占位: %d 字节' % len(data))
    if data[:2] != b'MZ':
        raise BuildError('安装器不是 Windows PE 可执行文件')
    expected = artifact_manifest.get('installer_sha256') if isinstance(artifact_manifest, dict) else None
    expected_size = artifact_manifest.get('installer_size') if isinstance(artifact_manifest, dict) else None
    digest = sha256(data).hexdigest()
    if expected is not None and digest != expected:
        raise BuildError('安装器哈希与清单不一致')
    if expected_size is not None and int(expected_size) != len(data):
        raise BuildError('安装器大小与清单不一致')
    return dict(path=str(path), size=len(data), sha256=digest)


def validate_runtime_zip(path):
    candidate = Path(path)
    if not candidate.is_file():
        raise BuildError('未提供官方 CPython 3.14 Windows runtime zip: %s' % candidate)
    if candidate.suffix.lower() != '.zip' or not zipfile.is_zipfile(candidate):
        raise BuildError('Python runtime 必须是官方 Windows embeddable zip: %s' % candidate)
    with zipfile.ZipFile(candidate) as archive:
        names = {item.filename.replace('\\', '/') for item in archive.infolist()}
    for required in ('python.exe', 'pythonw.exe', 'LICENSE.txt'):
        if required not in names:
            raise BuildError('Python runtime zip 缺少 %s；请使用官方 3.14.10 Windows embeddable 包。' % required)
    return candidate


def validate_wheels(path):
    folder = Path(path)
    if not folder.is_dir():
        raise BuildError('wheels 目录不存在: %s' % folder)
    wheels = sorted(folder.glob('*.whl'))
    if not wheels:
        raise BuildError('wheels 目录没有 .whl；请先按 requirements-app.txt 准备 3.14 win_amd64 wheel。')
    return wheels


def validate_build_config(config):
    root = Path(config.source_root)
    entries = source_payload(root)
    requirements = read_requirements(root)
    validate_runtime_zip(config.runtime_zip)
    validate_wheels(config.wheels_dir)
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', str(config.version)):
        raise BuildError('产品版本必须是 x.y.z')
    if not str(config.brand).strip() or '"' in str(config.brand):
        raise BuildError('品牌名无效')
    require_makensis(config.makensis)
    return dict(source_entries=entries, requirements=requirements)


# ---------------------------------------------------------------------------
# full build
# ---------------------------------------------------------------------------

@dataclass
class BuildConfig:
    source_root: Path
    runtime_zip: Path
    wheels_dir: Path
    output_dir: Path
    makensis: Path = None
    build_python: str = sys.executable
    version: str = PRODUCT_VERSION
    brand: str = BRAND
    keep_work: Path = None


def _extract_runtime(zip_path, target):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename.replace('\\', '/')
            if name.startswith('/') or any(part in {'', '.', '..'} for part in name.split('/')):
                raise BuildError('Python runtime zip 含不安全路径: %r' % info.filename)
            archive.extract(info, target)
    return target


def _python_version(runtime_python):
    try:
        result = subprocess.run([str(runtime_python), '-c',
            'import sys;print("%d.%d.%d"%sys.version_info[:3])'],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError('无法执行便携 Python 运行时: %s' % exc) from None
    if result.returncode != 0:
        raise BuildError('便携 Python 运行时启动失败: %s' % (result.stderr or '')[-800:])
    version = (result.stdout or '').strip()
    if not re.fullmatch(r'3\.14\.[0-9]+', version):
        raise BuildError('便携 Python 必须是 3.14.x，实际为 %r' % version)
    return version


def _install_wheels(config, python_dir, site_packages, requirements):
    lock = json.loads((Path(__file__).parent / 'wheel-lock.json').read_text(encoding='utf-8'))
    for row in lock:
        wheel = Path(config.wheels_dir) / row['file']
        if not wheel.is_file() or sha256(wheel.read_bytes()).hexdigest() != row['sha256']:
            raise BuildError('离线 wheel 与已核验的官方 PyPI 锁定清单不符: ' + row['file'])
    if {p.name for p in Path(config.wheels_dir).glob('*.whl')} != {r['file'] for r in lock}:
        raise BuildError('离线 wheel 目录包含锁定清单外的包')
    command = [str(config.build_python), '-m', 'pip', 'install', '--no-index',
               '--find-links', str(config.wheels_dir), '--target', str(site_packages),
               '--only-binary=:all:', '--platform', 'win_amd64', '--implementation', 'cp',
               '--python-version', '3.14', '--no-compile', '--no-warn-script-location',
               '-r', str(Path(config.source_root) / 'requirements-app.txt'),
               '-r', str(Path(config.source_root) / 'requirements-materials.txt')]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError('安装 wheel 失败: %s' % exc) from None
    if result.returncode != 0:
        raise BuildError('pip 安装失败（离线 wheel；构建不会访问网络）：%s'
                         % (result.stderr or result.stdout or '')[-1600:])
    if not any(site_packages.iterdir()):
        raise BuildError('site-packages 为空，依赖安装未产生文件')
    # Dependencies are pinned by requirements-app.txt plus the supplied wheel set.
    return requirements


def _write_pth(python_dir, app_dir_name='app'):
    files = sorted(Path(python_dir).glob('python3*._pth'))
    if not files:
        raise BuildError('官方 embeddable Python 缺少 python3xx._pth；请使用 3.14 Windows embeddable 包')
    pth = files[0]
    entries = ['python314.zip', '.', 'Lib\\site-packages', '..\\' + app_dir_name, '..\\launcher', 'import site']
    pth.write_text('\n'.join(entries) + '\n', encoding='utf-8')
    return pth


def _license_page(payload_root, licence_result, python_license):
    lines = ['言行 1.0.0 来源、许可与安全说明', '=' * 40, '',
             '本安装包是本地模拟银行竞赛演示应用。不连接真实银行或真实资金。',
             '模型 API 需用户自行配置合法凭证与网络；安装包不包含任何密钥。',
             'NSIS 安装器运行代码许可：licenses/nsis/COPYING.txt。项目源码权利由项目团队保留。',
             '', '项目源码说明：本应用源码随安装包内版本清单记录；第三方组件与许可见下。',
             '']
    if python_license and Path(python_license).is_file():
        lines += ['Python 运行时许可：licenses/python/LICENSE.txt', '']
    lines.append(licence_result['notices'])
    (Path(payload_root) / '许可与第三方说明.txt').write_text('\ufeff' + '\n'.join(lines), encoding='utf-8')


def _wheel_inventory(wheels):
    rows = []
    for wheel in sorted(p.name for p in Path(wheels).glob('*.whl')):
        path = Path(wheels) / wheel
        rows.append(dict(name=wheel, sha256=sha256(path.read_bytes()).hexdigest()))
    return rows


def _scan_tree(root):
    root = Path(root)
    entries = []
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in sorted(dirs):
            if _is_link(current_path / name):
                raise BuildError('payload 不允许目录链接: %s' % (current_path / name))
        for name in sorted(files):
            full = current_path / name
            if _is_link(full):
                raise BuildError('payload 不允许文件链接: %s' % full)
            relative = full.relative_to(root).as_posix()
            _safe_relative(relative)
            entries.append(_file_entry(root, relative))
    return sorted(entries, key=lambda row: row['path'])


def assemble_payload(config, work_dir=None):
    if not Path(config.source_root).is_dir():
        raise BuildError('冻结源码目录不存在: %s' % config.source_root)
    validated = validate_build_config(config)
    source_entries = validated['source_entries']
    source_manifest = build_payload_manifest(config.source_root, source_entries,
                                             version=config.version, brand=config.brand)
    work = Path(work_dir) if work_dir is not None else Path(tempfile.mkdtemp(prefix='言行-build-'))
    payload = work / 'payload'
    python_dir = payload / 'python'
    app_dir = payload / 'app'
    launcher_dir = payload / 'launcher'
    try:
        runtime_hash = sha256(Path(config.runtime_zip).read_bytes()).hexdigest()
        if runtime_hash != '90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159':
            raise BuildError('官方 Python 3.14.10 x64 运行时 SHA256 不匹配')
        _extract_runtime(config.runtime_zip, python_dir)
        runtime_python = python_dir / 'python.exe'
        python_version = _python_version(runtime_python)
        _pth = _write_pth(python_dir)
        site = python_dir / 'Lib' / 'site-packages'
        site.mkdir(parents=True, exist_ok=True)
        _install_wheels(config, python_dir, site, validated['requirements'])
        for entry in source_entries:
            source = Path(config.source_root) / Path(entry['path'])
            target = app_dir / Path(entry['path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if sha256(target.read_bytes()).hexdigest() != entry['sha256']:
                raise BuildError('源码复制后哈希不一致: %s' % entry['path'])
        runtime_source = Path(__file__).resolve().parent / 'runtime'
        launcher_dir.mkdir(parents=True, exist_ok=True)
        for name in ('launch.py', 'stop.py', 'paths.py'):
            shutil.copy2(runtime_source / name, launcher_dir / name)
        shutil.copy2(runtime_source / 'sitecustomize.py', python_dir / 'sitecustomize.py')
        if not (app_dir / 'app' / 'main.py').is_file():
            raise BuildError('打包后的原生应用入口缺失')
        docs_source = Path(__file__).resolve().parent / 'payload_docs'
        docs_dir = payload / 'docs'
        docs_dir.mkdir(parents=True, exist_ok=True)
        for doc in sorted(docs_source.glob('*.txt')):
            shutil.copy2(doc, docs_dir / doc.name)
        licences = payload / 'licenses'
        licences.mkdir(parents=True, exist_ok=True)
        python_license = python_dir / 'LICENSE.txt'
        if not python_license.is_file():
            raise BuildError('便携 Python 缺少 LICENSE.txt，无法完成许可交付')
        (licences / 'python').mkdir(parents=True, exist_ok=True)
        shutil.copy2(python_license, licences / 'python' / 'LICENSE.txt')
        licence_result = license_bundle(site, licences / 'third-party')
        compiler_license = Path(require_makensis(config.makensis)).resolve().parent / 'COPYING'
        if not compiler_license.is_file():
            raise BuildError('NSIS 官方编译器目录缺少 COPYING')
        (licences / 'nsis').mkdir(exist_ok=True)
        shutil.copy2(compiler_license, licences / 'nsis' / 'COPYING.txt')
        _license_page(payload, licence_result, python_license)
        entries_before_version = _scan_tree(payload)
        version_info = dict(
            brand=str(config.brand), version=str(config.version), manifest_schema=MANIFEST_SCHEMA,
            build_utc=datetime.now(timezone.utc).isoformat(),
            source_tree_sha256=source_manifest['source_tree_sha256'],
            payload_tree_sha256=tree_sha256(entries_before_version),
            python=python_version, requirements=validated['requirements'],
            wheels=_wheel_inventory(config.wheels_dir),
            install_data_dir='%LOCALAPPDATA%\\' + str(config.brand) + '\\data',
            source_version_note='源码树哈希按相对路径+sha256排序计算；禁止用开发机venv替代。',
        )
        (payload / 'version.json').write_text(
            json.dumps(version_info, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        entries = _scan_tree(payload)
        # Vendor wheels are verified against official PyPI hashes separately;
        # scan our source, launcher and generated files without mistaking font data for keys.
        scan_secrets(payload, [row for row in entries if not row["path"].startswith("python/")])
        manifest = build_payload_manifest(payload, entries,
                                          version=config.version, brand=config.brand)
        manifest['source_tree_sha256'] = source_manifest['source_tree_sha256']
        manifest['payload_tree_sha256'] = tree_sha256(entries)
        nsi_text = generate_nsis(dict(
            payload_dir=payload, output_path=Path(config.output_dir) / (
                '%s-%s-x64-setup.exe' % (config.brand, config.version)),
            manifest=manifest, version=config.version, brand=config.brand,
            data_dir=DEFAULT_DATA_DIR, payload_mb=payload_mb(entries),
            license_file=payload / '许可与第三方说明.txt'))
        nsi_path = work / 'installer.nsi'
        nsi_path.write_text('\ufeff' + nsi_text, encoding='utf-8')
        return dict(work=work, payload=payload, manifest=manifest, nsi=nsi_path,
                    source_manifest=source_manifest, version_info=version_info,
                    licence_result=licence_result)
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise


def build_installer(config):
    validated = validate_build_config(config)
    output = Path(config.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    result = assemble_payload(config, work_dir=output / '.build-work')
    try:
        artifact = output / ('%s-%s-x64-setup.exe' % (config.brand, config.version))
        makensis = require_makensis(config.makensis)
        compile_installer(result['nsi'], artifact, makensis=makensis)
        digest = sha256(artifact.read_bytes()).hexdigest()
        artifact_manifest = dict(
            schema=MANIFEST_SCHEMA, brand=str(config.brand), version=str(config.version),
            installer_file=artifact.name, installer_sha256=digest,
            installer_size=artifact.stat().st_size,
            payload_manifest=result['manifest'],
            source_tree_sha256=result['source_manifest']['source_tree_sha256'],
            payload_tree_sha256=result['manifest']['payload_tree_sha256'],
            version_info=result['version_info'],
            build_utc=datetime.now(timezone.utc).isoformat(),
            build_tool_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
            wheel_inventory=result['version_info']['wheels'],
            unsigned=True,
            note='未签名；真实安装/启动/卸载/升级验收由 Codex 在宿主执行。',
        )
        verify_artifact(artifact, artifact_manifest)
        (output / 'manifest.json').write_text(
            json.dumps(artifact_manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (output / 'SHA256SUMS.txt').write_text(
            '%s  %s\n' % (digest, artifact.name), encoding='utf-8')
        notes = ['言行 %s Windows x64 安装包' % config.version, '',
                 '构建时间：' + artifact_manifest['build_utc'],
                 '源码树哈希：' + artifact_manifest['source_tree_sha256'],
                 '载荷文件数：%d' % result['manifest']['file_count'],
                 'Python：' + result['version_info']['python'],
                 '安装器未签名；SmartScreen 可能提示未知发布者。',
                 '安装/升级默认保留 %LOCALAPPDATA%\\言行\\data；卸载默认也保留。',
                 '真实编译、安装、启动、停止、卸载与最小升级验收由 Codex 独立执行。']
        (output / 'RELEASE-NOTES.txt').write_text('\n'.join(notes) + '\n', encoding='utf-8')
        return dict(artifact=str(artifact), manifest=str(output / 'manifest.json'),
                    sha256=digest, size=artifact.stat().st_size,
                    payload_files=result['manifest']['file_count'])
    finally:
        shutil.rmtree(Path(config.output_dir) / '.build-work', ignore_errors=True)


def _parser():
    parser = argparse.ArgumentParser(description='构建言行 Windows x64 安装包（真实编译需 NSIS/Python runtime/wheels）。')
    parser.add_argument('--source', required=False, help='Codex 冻结的完整源码目录')
    parser.add_argument('--runtime-zip', required=False, help='官方 CPython 3.14.10 Windows embeddable x64 zip')
    parser.add_argument('--wheels', required=False, help='requirements-app.txt 对应的离线 win_amd64 wheel 目录')
    parser.add_argument('--output', required=False, help='输出目录（安装器与清单）')
    parser.add_argument('--nsis', default=None, help='makensis.exe 路径；缺省在 PATH 查找')
    parser.add_argument('--python', default=sys.executable, help='构建机 Python（用于 pip --target）')
    parser.add_argument('--version', default=PRODUCT_VERSION)
    parser.add_argument('--brand', default=BRAND)
    parser.add_argument('--stage-only', action='store_true', help='只组装并校验 payload，不生成安装器（诊断用）')
    parser.add_argument('--verify', action='store_true', help='校验已有安装器与 manifest')
    parser.add_argument('--artifact', help='--verify 的安装器路径')
    parser.add_argument('--manifest', help='--verify 的 manifest.json 路径')
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.verify:
            if not args.artifact or not args.manifest:
                parser.error('--verify 需要 --artifact 与 --manifest')
            value = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
            result = verify_artifact(args.artifact, value)
            print('VERIFIED ' + json.dumps(result, ensure_ascii=False))
            return 0
        missing = [name for name in ('source', 'runtime_zip', 'wheels', 'output')
                   if not getattr(args, name.replace('-', '_'))]
        if missing:
            parser.error('构建需要 --source/--runtime-zip/--wheels/--output')
        config = BuildConfig(source_root=Path(args.source), runtime_zip=Path(args.runtime_zip),
                             wheels_dir=Path(args.wheels), output_dir=Path(args.output),
                             makensis=Path(args.nsis) if args.nsis else None,
                             build_python=args.python, version=args.version, brand=args.brand)
        if args.stage_only:
            result = assemble_payload(config, work_dir=Path(args.output) / '.stage-work')
            target = Path(args.output) / 'payload-manifest.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result['manifest'], ensure_ascii=False, indent=2) + '\n',
                              encoding='utf-8')
            print('STAGE-ONLY 未生成安装器；payload=%s manifest=%s files=%d'
                  % (result['payload'], target, result['manifest']['file_count']))
            return 0
        summary = build_installer(config)
        print('BUILT ' + json.dumps(summary, ensure_ascii=False))
        return 0
    except BuildError as exc:
        print('构建失败: ' + str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print('构建失败（文件/工具错误）: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
