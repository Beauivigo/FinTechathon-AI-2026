"""Loopback-only isolated identity acceptance entry for the user's own phone test.

This is not a second bank or a model entry point.  It starts the real app with a
fixed throwaway SQLite path under the OS temporary directory, so the daily
``runtime/sandbox.sqlite3`` identity is never read or changed.  There is no
argument, environment variable or URL to choose another database path.

Runbook: docs/2026-10-26本人手机安全验收单.md
"""
from hashlib import sha256
import json
import runpy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SURFACE = 'fintech-phone-acceptance-' + sha256(str(ROOT.resolve()).encode()).hexdigest()[:12]


def _is_symlink(path):
    try:
        return Path(path).is_symlink() or getattr(Path(path), 'is_junction', lambda: False)()
    except OSError:
        return False


def _hard_linked(path):
    try:
        return Path(path).stat().st_nlink > 1
    except OSError:
        return False


def isolated_database(*, root=None, temp_dir=None):
    """Return the fixed isolated database path; reject the daily runtime and links."""
    root = Path(root if root is not None else ROOT).resolve()
    temp = Path(temp_dir if temp_dir is not None else tempfile.gettempdir()).absolute()
    raw_folder = temp / SURFACE
    if any(_is_symlink(p) for p in (raw_folder, temp, *temp.parents)):
        raise RuntimeError('隔离数据目录不得使用符号链接或目录联接。')
    folder = raw_folder.resolve()
    runtime = (root / 'runtime').resolve()
    if folder == runtime or folder.is_relative_to(runtime) or runtime.is_relative_to(folder):
        raise RuntimeError('隔离数据目录不能位于项目日常runtime内。')
    if folder.is_relative_to(root) or root.is_relative_to(folder):
        raise RuntimeError('隔离数据目录不能位于项目目录内。')
    if _is_symlink(folder) or _is_symlink(folder.parent):
        raise RuntimeError('隔离数据目录不得使用符号链接。')
    database = folder / 'sandbox.sqlite3'
    if database.exists():
        if _is_symlink(database):
            raise RuntimeError('隔离数据库不得使用符号链接。')
        if _hard_linked(database):
            raise RuntimeError('隔离数据库不得使用硬链接。')
    return database


def check_database(database):
    """Read-only status for the isolated identity; never prints secrets or codes."""
    path = Path(database)
    result = dict(path=str(path), exists=path.exists(), initialized=False,
                  setup_state=None, bound=None)
    if not result['exists']:
        return result
    try:
        from bank.auth import AuthService
        from bank.store import Store
        status = AuthService(Store(path)).account_status()
        result.update(initialized=bool(status.get('initialized')),
                      setup_state=status.get('setup_state'),
                      bound=bool(status.get('bound')))
    except Exception as exc:
        result['error'] = type(exc).__name__
    return result


def _in_streamlit():
    try:
        from streamlit.runtime import exists
        return bool(exists())
    except Exception:
        return False


def main(argv=None):
    values = list(sys.argv[1:] if argv is None else argv)
    database = isolated_database()
    if values == ['--check']:
        print(json.dumps(check_database(database), ensure_ascii=False))
        return 0
    if values and not _in_streamlit():
        raise SystemExit('只支持 --check；不接受数据库路径或其他参数。')
    if not _in_streamlit():
        raise SystemExit('请在Streamlit中按本人手机验收单启动；或使用 --check 只读查看隔离身份状态。')
    database.parent.mkdir(parents=True, exist_ok=True)
    from bank import seed

    def isolated_demo_path():
        folder = database.parent
        if _is_symlink(folder) or _is_symlink(database) or _hard_linked(database):
            raise RuntimeError('隔离数据目录不得使用符号链接。')
        return database

    # This dedicated Streamlit process keeps all pages on the same isolated
    # identity, including aliases imported before this entry and page switches.
    from app import auth_ui, demo, ui
    seed.demo_path = auth_ui.demo_path = demo.demo_path = isolated_demo_path
    for key in ui._HOME_VIEWS:
        label, _ = ui._NAV_LOOKUP[key]
        ui._NAV_LOOKUP[key] = (label, 'app/phone_acceptance.py')
    print('隔离身份数据库：' + str(database))
    runpy.run_path(str(ROOT / 'app/main.py'), run_name='__main__')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
