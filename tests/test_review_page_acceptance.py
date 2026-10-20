"""Codex独立复核：真实页面导航、恢复、披露与输入精度。"""
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import unittest
from streamlit.testing.v1 import AppTest
from agent.routing import route
from shared import adapt_request

ROOT=Path(__file__).resolve().parents[1]


class ReviewPageTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root_patch=patch('bank.seed.PROJECT_ROOT',Path(temp.name));self.root_patch.start();self.addCleanup(self.root_patch.stop)

    def page(self,name):
        p=AppTest.from_file(str(ROOT/'app'/name),default_timeout=15).run()
        self.assertFalse(p.exception)
        return p

    def request(self,action,params):
        return adapt_request(action,params,request_id=uuid4().hex,trace_id='review',idempotency_key=uuid4().hex)

    def snapshot(self,flow):
        with flow.gateway.store.connection() as c:
            return {t:[tuple(row) for row in c.execute('SELECT * FROM '+t)]
                    for t in ('accounts','holdings','wealth_orders','risk_profiles','ledger_entries','operations','subscription_charges')}

    def test_four_wealth_intents_open_real_page_without_writes(self):
        for text in ('风险评估','账户 demo_account_a 申购 WP-C02 500元','账户 demo_account_a 赎回10份','查看账户 demo_account_a 的持仓'):
            p=self.page('main.py');flow=p.session_state['conversation'];before=self.snapshot(flow)
            p.text_input(key='router_input').set_value(text)
            p.button(key='router_identify').click().run()
            p.button(key='router_open').click().run()
            self.assertFalse(p.exception)
            self.assertIn('理财操作',[t.value for t in p.title])
            self.assertEqual(self.snapshot(flow),before)
            self.assertTrue(any('理财' in t.value or '风险评估' in t.value for t in p.text))

    def test_fresh_session_can_recover_and_verify_wealth_purchase(self):
        p=self.page('pages/9_理财.py');flow=p.session_state['action_flow'];gw=flow.gateway
        profile=gw.execute(self.request('wealth.assess',dict(questionnaire_version='wpq_v1',answers={f'q{i}':f'q{i}_a' for i in range(1,6)})),principal_id=flow.principal_id)
        request=self.request('wealth.purchase',dict(account_id='demo_account_a',product_id='WP-C01',risk_profile_id=profile['data']['risk_profile_id'],amount_minor=10000))
        gw.execute(request,principal_id=flow.principal_id)
        p=self.page('pages/9_理财.py')
        p.button(key='action_recover').click().run()
        flow=p.session_state['action_flow'];self.assertEqual(flow.request['idempotency_key'],request['idempotency_key'])
        self.assertEqual(flow.result['status'],'awaiting_verification')
        self.assertTrue(any('100.00' in item.value for item in p.markdown))
        p.button(key='action_approve').click().run();self.assertEqual(flow.result['status'],'succeeded')
        self.assertFalse(p.exception)

    def test_charge_recovery_displays_original_bound_amount_and_card(self):
        p=self.page('pages/5_订阅管理.py');flow=p.session_state['action_flow'];gw=flow.gateway
        with gw.store.connection(write=True) as c:
            c.execute("UPDATE subscriptions SET subscription_state='active',next_charge_date=? WHERE subscription_id='SUB-E001'",(gw.clock().astimezone(timezone.utc).date().isoformat(),))
        request=self.request('subscription.charge',dict(subscription_id='SUB-E001'))
        self.assertEqual(gw.execute(request,principal_id=flow.principal_id)['status'],'awaiting_verification')
        with gw.store.connection(write=True) as c:c.execute("UPDATE subscriptions SET amount_minor=4000 WHERE subscription_id='SUB-E001'")
        p=self.page('pages/5_订阅管理.py');p.button(key='action_recover').click().run()
        self.assertFalse(p.exception)
        content=' '.join(m.value for m in p.markdown)
        self.assertIn('25.00',content);self.assertIn('CARD-E001',content)

    def test_questionnaire_has_real_option_text(self):
        p=self.page('pages/9_理财.py')
        options=' '.join(str(x) for widget in p.selectbox for x in widget.options).replace(' ','')
        self.assertIn('不到1年',options)
        self.assertIn('不能接受亏损',options)

    def test_holdings_selection_updates_default_units(self):
        p=self.page('pages/9_理财.py');flow=p.session_state['action_flow']
        with flow.gateway.store.connection(write=True) as c:
            c.executemany("INSERT INTO holdings VALUES (?, 'demo_user_a','demo_account_a','WP-C01',?,'2026-10-19',0,'open')",[('review-h1','2.5000'),('review-h2','7.5000')])
        p.run();holdings=p.session_state['action_flow'].resources['holdings']
        p.selectbox(key='wealth_holding').select(holdings[1]).run()
        value=next(w.value for w in [*p.number_input,*p.text_input] if w.label.startswith('赎回份额'))
        self.assertEqual(Decimal(str(value)),Decimal(holdings[1]['units']))

    def test_bad_wealth_units_and_multi_actions_never_ready(self):
        for text in ('账户 demo_account_a 赎回-10份','账户 demo_account_a 赎回0份',
                     '账户 demo_account_a 赎回1.23456份','账户 demo_account_a 买理财 WP-C01 100元然后赎回10份'):
            with self.subTest(text=text):self.assertNotEqual(route(text)['status'],'ready')
