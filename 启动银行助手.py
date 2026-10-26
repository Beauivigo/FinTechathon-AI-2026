"""双击打开本机银行助手（已配置Windows环境）。

不需要输入终端命令。程序会优先使用项目现有 .venv；已打开时只复用现有页面，
端口被其他程序占用时不会启动第二套服务或改动数据。正常退出请使用页面中的
“退出应用（不是关闭网页）”。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from launcher import main

if __name__ == '__main__':
    raise SystemExit(main())
