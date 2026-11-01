"""Independent UX acceptance against real services and isolated SQLite banks."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest as NativeAppTest
from tests.ui_support import AppTest
from app.money import parse_yuan, yuan_text
from app.presentation import cancel_transfer_for_edit
from app.workflow import demo_payees
from bank.auth import AuthService
from bank.gateway import BankGateway
from bank.plan_store import PlanStore
from bank.seed import demo_path, initialize
from bank.store import Store
from agent.controller import Conversation
from shared import ServiceError

ROOT=Path(__file__).resolve().parents[1]


class MoneyTests(unittest.TestCase):
    def test_exact_values_and_no_rounding(self):
        for text, cents in [('200.01',20001),('0.01',1),('90071992547409.91',9007199254740991)]:
            self.assertEqual(parse_yuan(text),cents)
        for text in ('',None,True,'0','-1','1.001','1.000','1e2','NaN','inf','+1','90071992547409.92'):
            with self.subTest(value=text),self.assertRaises(ValueError):parse_yuan(text)
        self.assertEqual(parse_yuan('0',allow_zero=True),0)
        self.assertEqual(yuan_text(20001),'200.01元')


class BankConventionPages(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.temp=Path(temp.name)
        root_patch=patch('bank.seed.PROJECT_ROOT',self.temp)
        root_patch.start();self.addCleanup(root_patch.stop)

    def page(self,name='main.py',native=False):
        cls=NativeAppTest if native else AppTest
        page=cls.from_file(str(ROOT/'app'/name),default_timeout=15).run()
        self.assertFalse(page.exception)
        return page

    def count(self,flow,table):
        with flow.gateway.store.connection() as c:
            return c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]

    def prepare(self,p,amount='200.01'):
        flow=p.session_state['conversation']
        people=demo_payees(flow)
        p.selectbox(key='direct_payee').select(people[0])
        p.text_input(key='direct_amount').set_value(amount)
        p.text_input(key='direct_memo').set_value('本次备注')
        p.run()
        p.button(key='direct_transfer_submit').click().run()
        self.assertFalse(p.exception)
        return flow

    def test_welcome_requires_explicit_choice_before_business(self):
        p=self.page(native=True)
        self.assertEqual([h.value for h in p.title],['欢迎使用银行助手'])
        self.assertNotIn('conversation',p.session_state)
        self.assertFalse(p.chat_input)
        p.button(key='welcome_demo').click().run()
        self.assertFalse(p.exception)
        self.assertIn('conversation',p.session_state)
        self.assertTrue(any('体验示例账户' in c.value for c in p.caption))

    def test_setup_pending_cannot_be_bypassed_by_sample_flag(self):
        store=Store(demo_path());initialize(store)
        auth=AuthService(store,seal=lambda b:b,unseal=lambda b:b)
        auth.begin_setup('ux-user','test-password-12345','test-password-12345')
        p=NativeAppTest.from_file(str(ROOT/'app/main.py'),default_timeout=15)
        p.session_state['demo_experience']=True
        with patch('app.auth_ui.service',return_value=auth):p.run()
        self.assertFalse(p.exception)
        self.assertNotIn('conversation',p.session_state)
        self.assertFalse(any(b.key=='welcome_demo' for b in p.button))

    def test_manual_transfer_has_no_recipient_or_amount_defaults(self):
        p=self.page()
        self.assertIsNone(p.selectbox(key='direct_payee').value)
        self.assertEqual(p.text_input(key='direct_amount').value,'')
        self.assertTrue(p.button(key='direct_transfer_submit').disabled)
        flow=self.prepare(p)
        self.assertEqual(flow.request['params']['amount_minor'],20001)
        self.assertEqual(flow.request['params']['memo'],'本次备注')
        self.assertEqual(flow.result['status'],'awaiting_confirmation')
        self.assertIn('200.01',p.button(key='transfer_approve').label)
        self.assertEqual(self.count(flow,'transfers'),0)
        p.button(key='transfer_approve').click().run();p.run()
        self.assertEqual(flow.result['status'],'succeeded')
        self.assertEqual(self.count(flow,'transfers'),1)
        receipt=flow.gateway.transfer_receipt(flow.result['data']['transaction_id'],principal_id=flow.principal_id)
        self.assertEqual(receipt['amount_minor'],20001)
        with self.assertRaises(ServiceError):
            flow.gateway.transfer_receipt(receipt['transaction_id'],principal_id='demo_user_li')

    def test_three_decimal_transfer_does_not_create_operation(self):
        p=self.page();flow=self.prepare(p,'200.001')
        self.assertIsNone(flow.request)
        self.assertEqual(self.count(flow,'operations'),0)
        self.assertTrue(any('最多两位' in item.value for item in p.warning))

    def test_edit_cancels_old_authorization_before_new_request(self):
        p=self.page();flow=self.prepare(p)
        old=deepcopy(flow.request);challenge=deepcopy(flow.result['next_action'])
        p.button(key='transfer_edit').click().run()
        self.assertFalse(p.exception)
        self.assertIsNone(flow.request)
        self.assertEqual(p.text_input(key='direct_amount').value,'200.01')
        self.assertEqual(p.text_input(key='direct_memo').value,'本次备注')
        p.text_input(key='direct_amount').set_value('201.02').run()
        p.button(key='direct_transfer_submit').click().run()
        self.assertEqual(flow.request['params']['amount_minor'],20102)
        self.assertNotEqual(flow.request['idempotency_key'],old['idempotency_key'])
        payload=dict(schema_version=old['schema_version'],challenge_id=challenge['challenge_id'],
                     operation_digest=challenge['operation_digest'],decision='approve')
        try:flow.gateway.authorize(payload,principal_id=flow.principal_id)
        except ServiceError:pass
        self.assertEqual(self.count(flow,'transfers'),0)
        p.button(key='transfer_approve').click().run()
        self.assertEqual(self.count(flow,'transfers'),1)

    def test_unknown_transfer_cannot_be_edited_or_replaced(self):
        p=self.page();flow=self.prepare(p)
        authorize=flow.gateway.authorize
        def lost(*args,**kwargs):
            authorize(*args,**kwargs)
            raise TimeoutError('isolated lost reply')
        with patch.object(flow.gateway,'authorize',side_effect=lost):
            p.button(key='transfer_approve').click().run()
        original=deepcopy(flow.request)
        self.assertEqual(flow.result['status'],'unknown')
        with self.assertRaises(ValueError):cancel_transfer_for_edit(flow)
        self.assertEqual(flow.request,original)
        self.assertFalse(any(b.key=='transfer_edit' for b in p.button))
        self.assertEqual(self.count(flow,'transfers'),1)

    def test_budget_form_is_submittable_and_uses_exact_yuan(self):
        p=self.page('pages/7_预算管理.py');flow=p.session_state['action_flow']
        self.assertFalse(p.button(key='budget_reserve').disabled)
        p.text_input(key='budget_amount').set_value('200.01')
        p.text_input(key='budget_purpose').set_value('生日用途')
        p.button(key='budget_reserve').click().run()
        self.assertFalse(p.exception)
        self.assertEqual(flow.request['params']['amount_minor'],20001)
        self.assertEqual(self.count(flow,'reservations'),0)
        p.button(key='action_approve').click().run()
        self.assertEqual(self.count(flow,'reservations'),1)
        self.assertEqual(self.count(flow,'transfers'),0)

    def test_appointment_requires_recipient_and_stays_unpaid(self):
        p=self.page('pages/6_定时转账.py');flow=p.session_state['action_flow']
        self.assertIsNone(p.selectbox(key='schedule_payee').value)
        self.assertEqual(p.text_input(key='schedule_amount').value,'')
        p.button(key='schedule_submit').click().run()
        self.assertIsNone(flow.request)
        p.selectbox(key='schedule_payee').select(demo_payees(flow)[0])
        p.text_input(key='schedule_amount').set_value('200.01')
        p.button(key='schedule_submit').click().run()
        self.assertEqual(flow.request['params']['amount_minor'],20001)
        p.button(key='action_approve').click().run()
        self.assertEqual(flow.result['status'],'scheduled')
        self.assertEqual(self.count(flow,'transfers'),0)
        self.assertTrue(any('超过5分钟' in c.value for c in p.caption))

    def test_questionnaire_requires_answers_and_recovers_profile(self):
        p=self.page('pages/9_理财.py')
        self.assertTrue(p.button(key='wealth_assess').disabled)
        for i in range(1,6):
            self.assertIsNone(p.selectbox(key='answer_q'+str(i)).value)
            p.selectbox(key='answer_q'+str(i)).select('b')
        p.run();p.button(key='wealth_assess').click().run()
        self.assertFalse(p.exception)
        profile=p.session_state['risk_profile_id']
        fresh=self.page('pages/9_理财.py')
        self.assertEqual(fresh.session_state['risk_profile_id'],profile)
        self.assertTrue(fresh.button(key='wealth_assess').disabled)
        self.assertTrue(any('已有风险评估' in i.value for i in fresh.info))

    def test_product_selection_survives_navigation(self):
        p=self.page()
        p.switch_page('pages/2_产品对比.py').run()
        ids=p.multiselect(key='product_ids').value
        p.selectbox(key='product_purchase_choice').select(ids[1]).run()
        p.button(key='product_to_wealth').click().run()
        self.assertFalse(p.exception)
        self.assertEqual(p.selectbox(key='wealth_product').value['product_id'],ids[1])

    def test_birthday_saved_and_restored_on_same_page_without_payment(self):
        p=self.page('pages/8_生日与商户.py');flow=p.session_state['action_flow']
        resources=flow.resources
        birthday=(flow.gateway.clock().astimezone(timezone(timedelta(hours=8))).date()+timedelta(days=32)).replace(day=15)
        p.date_input(key='birthday_date').set_value(birthday)
        p.date_input(key='birthday_budget_day').set_value(birthday.replace(day=1))
        p.multiselect(key='birthday_items').set_value(resources['items'][:2])
        p.selectbox(key='merchant_address').select(resources['addresses'][0])
        p.text_input(key='birthday_budget_text').set_value('1000.00')
        p.button(key='birthday_review').click().run()
        self.assertFalse(p.exception)
        self.assertEqual(PlanStore(flow.gateway.store).list(flow.principal_id),[])
        p.button(key='birthday_save_workflow').click().run()
        self.assertFalse(p.exception)
        plans=PlanStore(flow.gateway.store).list(flow.principal_id)
        self.assertEqual(len(plans),1)
        self.assertEqual(self.count(flow,'reservations'),0)
        self.assertEqual(self.count(flow,'merchant_orders'),0)
        fresh=self.page('pages/8_生日与商户.py')
        runner=fresh.session_state['birthday_progress_runner']
        self.assertEqual(runner.trace_id,plans[0]['plan_id'])
        fresh.button(key='birthday_progress_next').click().run()
        self.assertFalse(fresh.exception)
        self.assertEqual(self.count(flow,'operations'),0)

    def test_subscription_read_keeps_confirmation_and_human_money(self):
        p=self.page('pages/5_订阅管理.py');flow=p.session_state['action_flow']
        p.button(key='subscription_list').click().run()
        self.assertEqual(flow.result['status'],'awaiting_confirmation')
        self.assertFalse(any('商户' in d.value.columns for d in p.dataframe))
        self.assertFalse([m for m in p.metric if m.label=='金额'])
        p.button(key='action_approve').click().run()
        self.assertFalse(p.exception)
        amounts=[m.value for m in p.metric if m.label=='金额']
        self.assertEqual(sorted(amounts), ['1,200.00元','25.00元'])
        self.assertTrue(all(str(x).endswith('元') for x in amounts))
        self.assertEqual(self.count(flow,'subscription_charges'),0)

    def test_home_pending_verifies_original_phone_operation_in_place(self):
        import pyotp
        store=Store(demo_path());initialize(store)
        ts=[1800000000]
        auth=AuthService(store,clock=lambda:ts[0],seal=lambda b:b,unseal=lambda b:b)
        auth.bootstrap('ux-user','test-password-12345')
        token=auth.login('ux-user','test-password-12345')
        otp=pyotp.parse_uri(auth.enrollment(token))
        auth.finish_enrollment(token,otp.at(ts[0]));ts[0]+=30
        gw=BankGateway(store,auth=auth,session_token=token,clock=lambda:datetime.fromtimestamp(ts[0],timezone.utc))
        with self.assertRaises(ServiceError):
            gw.latest_risk_profile(principal_id='demo_user_li')
        flow=Conversation(gw,principal_id='demo_user_a')
        flow.start_action('transfer.create',dict(account_id='demo_account_a',payee_id='demo_payee_li',amount_minor=120001))
        key=flow.request['idempotency_key']
        p=NativeAppTest.from_file(str(ROOT/'app/main.py'),default_timeout=15)
        p.session_state['auth_token']=token
        with patch('app.auth_ui.service',return_value=auth),patch('app.demo.gateway',return_value=gw):
            p.run();p.button(key='home_pending_open').click().run()
            self.assertFalse(p.exception)
            pending=p.session_state['conversation']
            self.assertEqual(pending.request['idempotency_key'],key)
            code_input=next(w for w in p.text_input if str(w.key).startswith('home_pending_totp_'))
            p.text_input(key=code_input.key).set_value('abcdef')
            p.button(key='home_pending_verify').click().run()
            self.assertEqual(self.count(pending,'transfers'),0)
            self.assertEqual(pending.request['idempotency_key'],key)
            p.text_input(key=code_input.key).set_value(otp.at(ts[0]))
            p.button(key='home_pending_verify').click().run();p.run()
            self.assertFalse(p.exception)
            self.assertEqual(self.count(pending,'transfers'),1)
            self.assertEqual(pending.result['status'],'succeeded')

