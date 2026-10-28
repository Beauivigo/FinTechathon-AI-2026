"""独立只读资料页面，不初始化或查询运行账本。"""
from pathlib import Path
import sys
from datetime import date
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from app.money import yuan_text
from shared import ServiceError

st.set_page_config(page_title='历史样例分析', layout='wide')
from app import ui
from app import visual

# 先注入固定静态样式，再渲染页面内容；st.set_page_config 已在本文件最前面调用。
visual.apply('bill')
ui.render_sidebar_nav('accounts')
with visual.band('bill', 'accounts'):
    ui.render_page_header('历史样例分析')
    ui.render_app_nav('accounts')

with visual.card('bill', 'main'):
    st.caption('只读历史资料，独立于当前登录账户。')
    with st.expander('资料与规则说明'):
        st.caption('C V1.3 / B V1.2 复核修订件；B 使用已确认的只读账户映射。')
        st.info('虚构历史资料，只读分析；不代表真实登录，不支持转账或余额查询。')
        st.caption('日期按上海午夜归属统计，非真实成交时间。退款原支付待补，不推断关联；大额单笔提示仅供核实，其余异常规则未启用。')
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
                with st.expander('查看分类金额明细', expanded=False):
                    st.dataframe([{'分类': r['category'], '金额（元）': yuan_text(r['amount_minor'])} for r in bill['categories']], hide_index=True)
                st.bar_chart([{'分类': r['category'], '金额（元）': r['amount_minor'] / 100} for r in bill['categories']], x='分类', y='金额（元）')
            else:
                st.info('该期间没有计入毛支出的消费或订阅出账。')
            if bill['anomalies']:
                st.warning('大额单笔提示（只提示核实，不判欺诈、不拦截、不改账）')
                ui.render_records([{'流水编号': r['transaction_id'], '原因': r['reason']} for r in bill['anomalies']], hide_index=True,
                                  title_field='原因', meta_fields=('流水编号',), limit=None, key='sample-alerts', layout='activity', tone='bill')
            else:
                st.caption('本区间未发现大额单笔；其余异常规则未启用，空列表不代表没有异常。')
            if result['transactions']:
                ui.render_records([{'流水编号': t['transaction_id'], '记账日期': result['provenance'][t['transaction_id']]['source_date'],
                    '类型': {'income':'收入', 'consumption':'消费', 'transfer':'转账', 'subscription':'订阅', 'refund':'退款'}.get(t['kind'], t['kind']),
                    '方向': {'credit':'入账', 'debit':'支出'}.get(t['direction'], t['direction']), '金额（元）': yuan_text(t['amount_minor']),
                    '来源行': result['provenance'][t['transaction_id']]['row']} for t in result['transactions']], hide_index=True,
                    title_field='类型', value_field='金额（元）', meta_fields=('记账日期', '方向'),
                    key='sample-transactions', layout='activity', tone='bill', group_field='记账日期',
                    table_view=True, details_label='对账表')
            else:
                st.info('该期间没有流水。')
            with st.expander('来源校验'):
                st.caption('本次只读结果绑定 C '+result['source_sha256'][:12]+' / B '+result['decision_sha256'][:12]+'；来源行可回查小账本。')
