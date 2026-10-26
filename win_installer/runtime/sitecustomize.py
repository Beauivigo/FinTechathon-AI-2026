"""Configure installed storage before Streamlit selects any page or worker module."""
import os
import sys

try:
    from paths import configure
    configure()
except Exception:
    # CPython normally ignores sitecustomize exceptions. Never continue with
    # the developer/source storage location if installed path validation failed.
    if sys.stderr is not None:
        print('言行用户数据目录无效，未启动。', file=sys.stderr)
    os._exit(78)
