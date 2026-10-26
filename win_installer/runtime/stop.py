"""Request a cooperative shutdown through the held local lock and run nonce."""
import argparse
import time
from paths import safe_data
from launch import lock, record


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--check', action='store_true')
    p.add_argument('--quiet', action='store_true')
    p.add_argument('--validate-data', action='store_true')
    args = p.parse_args()
    data = safe_data()
    if args.validate_data:
        import os
        for folder, dirs, files in os.walk(data, followlinks=False):
            from pathlib import Path
            for name in dirs + files:
                path = Path(folder) / name
                if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                    return 3
                if path.is_file() and path.stat().st_nlink > 1:
                    return 3
        return 0
    with lock() as owned:
        if owned:
            return 0
    if args.check:
        return 2
    value = record()
    if not value or not isinstance(value.get('nonce'), str):
        return 3
    (data / 'stop-request.txt').write_text(value['nonce'], encoding='utf-8')
    for _ in range(100):
        time.sleep(.2)
        with lock() as owned:
            if owned:
                return 0
    return 3


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit(3)
