"""python -m material_check --input INPUT.xlsx --output-dir NEW_DIR"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

from .reader import MAX_FILE_BYTES, ReadError, read_workbook
from .report import build_report, compare_reports, markdown


def main(argv=None):
    parser = argparse.ArgumentParser(description='只读检查 C 资料；不导入账本。输出目录必须不存在。')
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--previous', type=Path, help='可选：同版本旧 report.json，比较问题变化')
    args = parser.parse_args(argv)
    created = False
    paths = []
    try:
        if args.output_dir.exists():
            raise ValueError('输出目录已存在，请换一个新目录；不会复用或覆盖旧报告')
        runtime = Path(__file__).resolve().parents[1] / 'runtime'
        if args.output_dir.resolve().is_relative_to(runtime):
            raise ValueError('报告不能写入业务 runtime 目录')
        parsed = read_workbook(args.input)
        report = build_report(parsed)
        # 金额复核完成后再次确认输入仍为同一份，而不只检查 Excel 解析期间。
        with args.input.open('rb') as stream:
            changed = sha256(stream.read(MAX_FILE_BYTES+1)).hexdigest() != parsed['source_sha256']
        if changed:
            parsed['source_changed'] = True
            report = build_report(parsed)
        if args.previous:
            with args.previous.open(encoding='utf-8') as stream:
                previous = json.load(stream)
            report['changes'] = compare_reports(previous, report)
        outputs = {'report.json': json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n',
                   'report.md': markdown(report)}
        args.output_dir.mkdir(parents=True, exist_ok=False)
        created = True
        for name, content in outputs.items():
            path = args.output_dir / name
            with path.open('x', encoding='utf-8') as stream:
                paths.append(path)
                stream.write(content)
        print(f"检查完成：{args.output_dir / 'report.md'}；材料尚未批准导入。")
        return 0 if report['executed_checks_status'] == 'passed' else 1
    except (OSError, ValueError, ReadError, KeyError, TypeError) as exc:
        # 只清理本次新建的两个文件；从不删除已有报告或递归删除目录。
        if created:
            for path in paths:
                try:
                    path.unlink()
                except OSError:
                    pass
            try:
                args.output_dir.rmdir()
            except OSError:
                pass
        print(f'检查未完成，不产生有效新结论：{exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
