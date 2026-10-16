"""固定版本的 C 资料只读服务；不创建数据库账户或执行交易。"""
from pathlib import Path
from hashlib import sha256
from copy import deepcopy

from billing import analyze
from shared import ServiceError, ContractError
from shared.contracts import ACTIONS, RECORDS, fields
from .reader import read_workbook, ReadError
from .checks import check_material, KINDS
from .reconcile import minor_value, source_day

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ROOT / 'materials/reviewed/2026-10-16'
SOURCE = MATERIALS / '账单数据与手算_C_V1.3_复核修订.xlsx'
DECISION = MATERIALS / '资金规则与功能复核_B_V1.2_复核修订.xlsx'
SOURCE_SHA = '496c6d27614add230f03b1440ebf155c9f813051ce04fdc493ef52269ec06d04'
DECISION_SHA = '4bb55f65c0548be5102b0757aec278be90c2fa1aef6f8c172fc04f28043be99b'
PRINCIPAL = 'PRIN-C01'
MAPPING = {'ACC-001': 'ACCT-C001', 'CH-001': 'ACCT-C001',
           'ACC-002': 'ACCT-C002', 'CH-002': 'ACCT-C002', 'ACC-003': 'ACCT-C003'}
ACCOUNTS = {'ACCT-C001': '工资储蓄卡（含微信渠道）',
            'ACCT-C002': '日常储蓄卡（含支付宝渠道）', 'ACCT-C003': '信用卡（只读）'}


def _authorize(principal_id, account_id=None):
    if principal_id != PRINCIPAL or (account_id is not None and account_id not in ACCOUNTS):
        raise ServiceError('FORBIDDEN', '无权查询该资料账户。')


def accounts(*, principal_id):
    _authorize(principal_id)
    return dict(ACCOUNTS)


def _convert(parsed):
    """保留原报告；只对本批已明确的退款缺关联允许只读统计。"""
    validation = check_material(parsed)
    errors = [i for i in validation['issues'] if i['level'] == 'error'
              and not (i['code'] == 'PARTY_MISSING' and i['record_id'] in {'LX-004', 'LX-015'})]
    if errors or validation['skipped_checks']:
        raise ServiceError('UPSTREAM_ERROR', '资料字段不完整，未生成部分账单。')
    transactions, sources = [], {}
    for row in parsed['ledger_rows']:
        f = row['fields']
        v = {k: c['value'] for k, c in f.items()}
        aid = MAPPING.get(v['账户编号'])
        if aid is None:
            raise ServiceError('UPSTREAM_ERROR', '未批准的账户映射。')
        day = source_day(f['日期']).isoformat()
        t = dict(transaction_id=v['流水号'], account_id=aid,
                 amount_minor=abs(minor_value(v['金额（元）'])),
                 direction={'收入': 'credit', '支出': 'debit'}[v['方向']],
                 kind=KINDS[v['交易类型']], category=v['消费分类'],
                 occurred_at=day+'T00:00:00+08:00')
        fields(t, *RECORDS['transaction'])
        transactions.append(t)
        sources[t['transaction_id']] = dict(sheet=row['sheet'], row=row['row'],
            source_date=day, date_precision='day', source_account=v['账户编号'],
            source_party=v['收款人/关联方'], source_reference=v['原交易关联编号'],
            payee_id='PAYEE-C001' if t['transaction_id'] in {'LX-003', 'LX-011'} else None)
    return transactions, sources


def query(params, *, principal_id):
    """内部只读入口；身份由本地演示服务选择，不能来自模型/查询表单。"""
    _authorize(principal_id)
    try:
        fields(params, ACTIONS['bill.analyze']['required'])
        if params['start_date'] > params['end_date']:
            raise ContractError('日期范围倒置')
    except ContractError as exc:
        raise ServiceError('INVALID_REQUEST', '账单查询条件无效。') from exc
    _authorize(principal_id, params['account_id'])
    try:
        if sha256(DECISION.read_bytes()).hexdigest() != DECISION_SHA:
            raise ReadError('B 决议版本已变化，需重新复核。')
        parsed = read_workbook(SOURCE)
        if parsed['source_changed'] is not False or parsed['source_sha256'] != SOURCE_SHA:
            raise ReadError('C 资料版本已变化，需重新复核。')
        transactions, provenance = _convert(parsed)
        bill = analyze(params, transactions)  # 验证整个快照；不静默跳过坏行。
    except (OSError, ReadError, ContractError, ValueError, KeyError, TypeError) as exc:
        raise ServiceError('UPSTREAM_ERROR', '资料不可用或版本已变化，请核对本地 B/C 文件。') from exc
    selected = [t for t in transactions if t['account_id'] == params['account_id']
                and params['start_date'] <= provenance[t['transaction_id']]['source_date'] <= params['end_date']]
    return deepcopy(dict(bill=bill, transactions=selected,
        provenance={t['transaction_id']: provenance[t['transaction_id']] for t in selected},
        refund_minor=sum(t['amount_minor'] for t in selected if t['kind']=='refund' and t['direction']=='credit'),
        source_sha256=SOURCE_SHA, decision_sha256=DECISION_SHA))
