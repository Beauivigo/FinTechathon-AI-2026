"""独立只读资料页面，不初始化或查询运行账本。"""
from pathlib import Path
import sys
from datetime import date
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from shared import ServiceError

st.set_page_config(page_title='C 资料账单', layout='wide')
st.title('C 资料账单')
st.caption('仓库内 C V1.3 / B V1.2 复核修订件；B 仅采用已确认的只读账户映射。')
st.info('虚构历史资料，只读分析。此页使用独立模拟身份“用户 C”，不代表真实登录，不支持转账或余额查询。')
st.caption('日期仅精确到天，程序按上海午夜归属统计，非真实成交时间。退款原支付待补，不推断关联；已启用大额单笔提示（≥5000 元），其余异常规则未启用。')
try:
    from material_check import approved
except ImportError:
    st.error('资料依赖未安装，请安装 requirements-materials.txt。')
    st.stop()
names = approved.accounts(principal_id=approved.PRINCIPAL)
account = st.selectbox('资料账户', list(names), format_func=lambda x: names[x]+' · '+x, key='material_account')
start = st.date_input('开始日期（上海）', value=date(2026,8,1), key='material_start')
end = st.date_input('结束日期（上海）', value=date(2026,8,31), key='material_end')
valid = start is not None and end is not None and start <= end
if not valid:
    st.warning('请选择完整、顺序正确的日期范围。')
if st.button('查询资料账单', key='material_query', disabled=not valid):
    try:
        result = approved.query(dict(account_id=account, start_date=start.isoformat(), end_date=end.isoformat()),
                                principal_id=approved.PRINCIPAL)
    except ServiceError:
        st.error('本次未生成报告。请检查本地 B/C 资料存在、版本已复核且字段完整；不会展示旧结果。')
    else:
        bill = result['bill']
        expense, refund = bill['total_expense_minor'], result['refund_minor']
        st.metric('资料毛支出（元）', f"{expense // 100}.{expense % 100:02d}")
        st.metric('退款另列（元）', f"{refund // 100}.{refund % 100:02d}")
        st.write(bill['report'])
        if bill['categories']:
            st.dataframe([{'分类': r['category'], '金额（分）': r['amount_minor']} for r in bill['categories']], hide_index=True)
            st.bar_chart([{'分类': r['category'], '金额（分）': r['amount_minor']} for r in bill['categories']], x='分类', y='金额（分）')
        else:
            st.info('该期间没有计入毛支出的消费或订阅出账。')
        if bill['anomalies']:
            st.warning('大额单笔提示（只提示核实，不判欺诈、不拦截、不改账）')
            st.dataframe([{'流水编号': r['transaction_id'], '原因': r['reason']} for r in bill['anomalies']], hide_index=True)
        else:
            st.caption('本区间未发现大额单笔；其余异常规则未启用，空列表不代表没有异常。')
        if result['transactions']:
            st.dataframe([{'流水编号': t['transaction_id'], '记账日期': result['provenance'][t['transaction_id']]['source_date'],
                '类型': t['kind'], '方向': t['direction'], '金额（分）': t['amount_minor'],
                '来源行': result['provenance'][t['transaction_id']]['row']} for t in result['transactions']], hide_index=True)
        else:
            st.info('该期间没有流水。')
        st.caption('本次只读结果绑定 C '+result['source_sha256'][:12]+' / B '+result['decision_sha256'][:12]+'；来源行可回查小账本。')
