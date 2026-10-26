"""Trusted installed paths; never accept a data location from a web request."""
import os
from pathlib import Path

INSTALL = Path(__file__).resolve().parents[1]
SOURCE = INSTALL / 'app'
RAW_BASE = Path(os.environ['LOCALAPPDATA']).absolute()
# Windows MSIX may transparently virtualize LocalAppData without a junction.
# Canonicalize the trusted OS base, then reject links in both original and final paths.
RAW_DATA = RAW_BASE / '言行' / 'data'
DATA = RAW_DATA.resolve()


def safe_data():
    for path in (RAW_DATA, *RAW_DATA.parents, DATA, *DATA.parents):
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise RuntimeError('言行数据目录不允许链接或目录联接。')
    if DATA.resolve() != DATA:
        raise RuntimeError('言行数据目录无效。')
    return DATA


def configure():
    import sys
    sys.path.insert(0, str(SOURCE))
    data = safe_data()
    from bank import seed, store, background
    from agent import model_credentials
    seed.PROJECT_ROOT = store.PROJECT_ROOT = data
    background.PROJECT_ROOT = data
    store.DEFAULT_DB_PATH = data / 'runtime' / 'sandbox.sqlite3'
    model_credentials.PROFILE = data / '.cache' / 'bank-model' / 'profile.json'
    return data
