"""离线报告及保守的问题变化比较；不批准材料，不修改业务状态。"""
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import html
import json
import re

from .reconcile import reconcile_material
from .checks import table_rows

VERSION = 'material-report/2'


def key(parts):
    return sha256(json.dumps(parts, ensure_ascii=False).encode('utf-8')).hexdigest()[:24]


def build_report(parsed):
    result = reconcile_material(parsed)
    locations, targets = {}, set()
    for name in parsed['sheets']:
        records = table_rows(parsed, name) or []
        first_values = [str(next(iter(row.values()))['value']) for row in records if row]
        unique = Counter(first_values)
        for row in records:
            if not row:
                continue
            first = str(next(iter(row.values()))['value'])
            if unique[first] == 1 and first != 'None':
                for field, cell in row.items():
                    locations[(name, cell['cell'])] = [name, first, field]
    ids = Counter(r['fields']['流水号']['value'] for r in parsed['ledger_rows'])
    for row in parsed['ledger_rows']:
        tid = row['fields']['流水号']['value']
        if isinstance(tid, str) and ids[tid] == 1:
            for name, cell in row['fields'].items():
                target = ['小账本', tid, name]
                locations[('小账本', cell['cell'])] = target
                targets.add(key(target))
    issues = []
    for item in result['validation']['issues']:
        target = locations.get((item['sheet'], item['cell']))
        # 无唯一业务编号时不以移动后的行号认定已修复。
        identity = target or [item['sheet'], item['record_id'], item['cell']]
        issues.append(dict(item, issue_id=key([item['code'], identity]),
                           target=key(target) if target else None,
                           rule=item['code'], observation=item['message']))
    comparison_targets = set()
    comparison_counts = Counter()
    for item in result['comparisons']:
        # 手算纳入流水的 label 同月可重复，追加同一行的分类金额 label。
        label = item['label']
        if 'expected_ids' in item:
            row = re.sub(r'^[A-Z]+', '', item['cell'])
            amounts = [c['label'] for c in result['comparisons'] if c['sheet'] == item['sheet']
                       and 'expected_minor' in c and re.sub(r'^[A-Z]+', '', c['cell']) == row]
            label = [label, amounts]
        target = key(['comparison', item['sheet'], label])
        comparison_counts[target] += 1
        if item['status'] != 'unavailable':
            comparison_targets.add(target)
        if item['status'] != 'match':
            issues.append(dict(issue_id=key(['COMPARISON', target]), target=target,
                               code='COMPARISON', rule='独立复算与源答案完全一致（整数分/编号）',
                               level='needs_decision' if item['status']=='unavailable' else 'error',
                               sheet=item['sheet'], cell=item['cell'], record_id=None,
                               observation=dict(item), message=item['status'],
                               impact='源答案不能标为通过；符号不同不自动修正', blocker='C1-002'))
    skipped = (result['validation']['skipped_checks'] + result['unperformed_comparisons']
               + result['test_scope_checks'])
    comparison_targets = {target for target in comparison_targets if comparison_counts[target] == 1}
    if parsed.get('source_changed', False) is not False:
        comparison_targets.clear()
    ready = not issues and not skipped and result['arithmetic_complete']
    return dict(schema_version=VERSION, generated_at=datetime.now(timezone.utc).isoformat(),
                source=dict(path=parsed.get('source_path'), sha256=parsed['source_sha256'],
                            bytes=parsed['source_bytes'], changed=parsed.get('source_changed')),
                tool_status='completed', executed_checks_status='passed' if ready else 'findings',
                material_ready=False, issues=issues, skipped_checks=skipped,
                counts=dict(issue_levels=dict(Counter(i['level'] for i in issues)),
                            comparisons=dict(Counter(c['status'] for c in result['comparisons'])),
                            not_executed=len(skipped), checked_rows=result['validation']['checked_ledger_rows']),
                coverage=dict(ledger_targets=sorted(targets), comparison_targets=sorted(comparison_targets),
                              ledger_recheck_safe=not result['validation']['skipped_checks']
                              and not parsed['issues'] and parsed.get('source_changed', False) is False),
                result=result)


def compare_reports(previous, current):
    """消失仅表示已执行检查不再报告，绝不自动解除 BLOCKERS。"""
    for report in (previous, current):
        if not isinstance(report, dict) or report.get('schema_version') != VERSION or report.get('tool_status') != 'completed':
            raise ValueError('只能比较同版本、工具完整生成的报告')
        if (not isinstance(report.get('issues'), list)
                or any(not isinstance(i, dict) or not isinstance(i.get('issue_id'), str)
                       for i in report['issues'])
                or not isinstance(report.get('coverage'), dict)
                or not isinstance(report.get('source'), dict)
                or not isinstance(report.get('result'), dict)):
            raise ValueError('报告结构不完整，不能比较')
    def index(report):
        grouped = {}
        for issue in report['issues']:
            grouped.setdefault(issue['issue_id'], []).append(issue)
        return grouped
    old, new = index(previous), index(current)
    def covered(issue, report, original):
        coverage = report['coverage']
        if issue['code'] == 'COMPARISON':
            return issue['target'] in coverage['comparison_targets']
        def context(value):
            matches = [p['candidate'] for p in value['result']['preview']
                       if p['source_transaction_id'] == issue['record_id']]
            return [(p.get('kind'), p.get('direction')) for p in matches]
        return (issue['target'] is not None and coverage['ledger_recheck_safe']
                and issue['code'] != 'KNOWN_PARTY_CONFLICT'
                and issue['target'] in coverage['ledger_targets']
                and len(context(report)) == 1 and context(report) == context(original))
    changes = dict(previous_sha256=previous['source']['sha256'], current_sha256=current['source']['sha256'],
                   continued=[], appeared=[], disappeared=[], unassessed_previous=[], unassessed_current=[])
    for ident in sorted(old.keys() | new.keys()):
        if ident in old and ident in new:
            changes['continued'].extend(new[ident])
        elif ident in old:
            bucket = 'disappeared' if all(covered(i, current, previous) for i in old[ident]) else 'unassessed_previous'
            changes[bucket].extend(old[ident])
        else:
            bucket = 'appeared' if all(covered(i, previous, current) for i in new[ident]) else 'unassessed_current'
            changes[bucket].extend(new[ident])
    return changes


def markdown(report):
    def text(value):
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        return html.escape(value).replace('|', '&#124;').replace('\n', '<br>').replace('\r', '')
    lines = ['# C 资料只读检查报告', '', '工具已完成检查；材料未批准导入。金额单位：整数分。', '',
             f"报告版本：{VERSION}；生成时间：{report['generated_at']}",
             '输入：'+text(report['source']), '', '## 检查计数', '', text(report['counts']), '',
             'error＝确定错误；needs_decision＝待确认；match＝一致；sign_mismatch＝绝对值一致但符号不同；'
             'mismatch＝不一致；unavailable＝未能比较；not_executed＝未执行。', '',
             '## 独立复算', '', text(report['result']['scope']),
             text(report['result']['subtotal_label']), '合计：'+text(report['result']['totals']),
             '', '| 月份 | 毛支出（分） | 退款（分） | 分类金额（分） |', '|---|---:|---:|---|']
    for month, amounts in (report['result']['valid_row_subtotals'] or {}).items():
        lines.append('| '+' | '.join(text(v) for v in [month,amounts['gross_minor'],
                     amounts['refund_minor'],amounts['categories']])+' |')
    lines += ['', '## 问题定位', '', '| 编号/规则 | 等级 | 工作表/单元格/源编号 | 事实 | 影响/阻塞 |',
             '|---|---|---|---|---|']
    for item in report['issues']:
        lines.append('| '+' | '.join(text(v) for v in [item['issue_id']+' / '+item['rule'], item['level'],
                     [item['sheet'],item['cell'],item['record_id']],item['observation'],
                     [item['impact'],item['blocker']]])+' |')
    lines += ['', '## 比较明细（包含通过项）', '']
    lines += ['- '+text(c) for c in report['result']['comparisons']]
    lines += ['', '## 未执行项', '']
    lines += ['- '+text(c) for c in report['skipped_checks']] or ['无']
    lines += ['', '## 未参与完整金额复算的流水', '', text(report['result']['excluded_rows']),
              '', '## 转换预览（不能导入）', '']
    lines += ['- '+text(c) for c in report['result']['preview']]
    if 'changes' in report:
        lines += ['', '## 与旧报告的问题变化', '',
                  '消失仅表示本次已执行检查不再报告，不代表业务决议解除；无法确认的项目单独列出。', '']
        lines += ['- '+text(k)+'：'+text(v) for k,v in report['changes'].items()]
    return '\n'.join(lines)+'\n'
