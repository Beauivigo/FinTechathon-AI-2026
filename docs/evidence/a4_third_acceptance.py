"""review 验收包装：复用项目验收器，独立命名证据，不覆盖历史 S3 记录。"""
from pathlib import Path
import json
import subprocess
import sys

root=Path(__file__).resolve().parents[2]
label=sys.argv[1]
if label not in ('core','pages','full'):
    raise SystemExit('label must be core/pages/full')
paths=[root/'docs/evidence'/('2026-10-19-S3.'+ext) for ext in ('json','txt')]
previous={p:p.read_bytes() if p.exists() else None for p in paths}
command=[sys.executable,'-B','-X','utf8','scripts/acceptance.py','S3',*sys.argv[2:],'--date','2026-10-19']
try:
    code=subprocess.run(command,cwd=root).returncode
    for p in paths:
        if p.exists():
            target=p.with_name('2026-10-19-A4-S3-'+label+p.suffix)
            target.write_bytes(p.read_bytes())
            if p.suffix=='.json':
                record=json.loads(target.read_text(encoding='utf-8'))
                record['wrapper_command']=' '.join(sys.argv)
                target.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
finally:
    for p,data in previous.items():
        if data is None:
            p.unlink(missing_ok=True)
        else:
            p.write_bytes(data)
raise SystemExit(code)
