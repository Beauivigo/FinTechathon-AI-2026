"""review补充按钮实点验收。仅可抛弃临时身份/账本；不触碰日常账户。"""
from pathlib import Path
from datetime import datetime,timedelta,timezone
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json,secrets,sys,unittest
import pyotp
from streamlit.testing.v1 import AppTest
from bank.store import Store
from bank.seed import initialize
from bank.gateway import BankGateway
from bank.auth import AuthService
from agent.controller import Conversation

ROOT=Path(__file__).resolve().parent/'acceptance-tree'

class ButtonCases(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.data=Path(temp.name);self.store=Store(self.data/'runtime/sandbox.sqlite3');initialize(self.store)
        self.now=datetime(2026,10,26,6,tzinfo=timezone.utc)
        self.ts=1800000000
        self.auth=AuthService(self.store,clock=lambda:self.ts)
        self.gateway=BankGateway(self.store,clock=lambda:self.now,auth=self.auth)
        for name,value in [('app.demo.gateway',self.gateway),('app.auth_ui.service',self.auth),('bank.seed.PROJECT_ROOT',self.data)]:
            p=patch(name,return_value=value) if name!='bank.seed.PROJECT_ROOT' else patch(name,value)
            p.start();self.addCleanup(p.stop)
        p=patch('agent.model_client.ModelClient.__call__');self.model=p.start();self.addCleanup(p.stop)

    def page(self,name='main.py',token=None):
        app=AppTest.from_file(str(ROOT/'app'/name),default_timeout=25)
        if token:app.session_state['auth_token']=token
        app.run();self.ok(app)
        if any(b.key=='welcome_demo' for b in app.button):self.click(app,'welcome_demo')
        return app
    def ok(self,app):self.assertFalse(app.exception)
    def click(self,app,key):
        aliases={'model_pending_approve':'action_approve','model_pending_reject':'action_reject',
                 'model_pending_query':'action_query','card_pending_verify':'action_approve',
                 'model_reconcile_payee':'action_query'}
        if not any(b.key==key for b in app.button):key=aliases.get(key,key)
        app.button(key=key).click().run();self.ok(app)
    def label(self,app,label):next(b for b in app.button if b.label==label).click().run();self.ok(app)
    def text(self,app,label,value):next(w for w in app.text_input if w.label==label).set_value(value)
    def money(self):
        with self.store.connection() as c:return c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0]
    def flow(self):return Conversation(self.gateway,principal_id='demo_user_a')
    def pending_read(self):
        flow=self.flow();flow.start_action('subscription.list',{'account_id':'demo_account_a'});return flow
    def send(self,app,text,intent,slots):
        value=dict(action='propose',intent=intent,slots={'account_id':'demo_account_a',**slots})
        self.model.return_value=json.dumps(value,ensure_ascii=False)
        app.text_area(key='bank_model_input').set_value(text);self.click(app,'bank_model_send')
    def enable_auth(self):
        self.name='button-check';self.password=secrets.token_urlsafe(20)
        self.codes=self.auth.bootstrap(self.name,self.password)
        token=self.auth.login(self.name,self.password);otp=pyotp.parse_uri(self.auth.enrollment(token))
        self.auth.finish_enrollment(token,otp.at(self.ts));self.ts+=30
        self.gateway.session_token=token;return token

    def test_pending_ai_query_reject_then_input_and_model_are_usable(self):
        original=self.pending_read();app=self.page('pages/0_AI银行助手.py')
        self.assertFalse(app.text_area(key='bank_model_input').disabled)
        self.click(app,'model_pending_open');self.click(app,'model_pending_query')
        self.assertTrue(any('业务账户' in t.value for t in app.text))
        self.click(app,'model_pending_reject')
        self.assertFalse(app.text_area(key='bank_model_input').disabled)
        self.send(app,'查余额','balance',{})
        self.assertEqual(self.model.call_count,1);self.assertEqual(self.money(),200000)
        self.assertEqual(app.session_state['model_runner'].receipts['t1']['data']['balance_minor'],200000)

    def test_home_confirm_clears_real_pending_count_and_card_can_freeze(self):
        self.pending_read();home=self.page();self.click(home,'home_pending_open');self.click(home,'home_pending_approve')
        self.assertFalse(any('有 1 项业务待处理' in w.value for w in home.warning))
        card=self.page('pages/3_卡片管理.py');self.assertFalse(card.button(key='card_submit').disabled)
        self.click(card,'card_submit');self.assertEqual(card.session_state['action_flow'].result['status'],'awaiting_verification')
        self.click(card,'card_pending_verify')
        self.assertEqual(card.session_state['action_flow'].result['data']['card_state'],'frozen')
        self.assertEqual(self.money(),200000)

    def test_expired_read_ends_without_reading_subscription_or_moving_money(self):
        self.pending_read();self.now+=timedelta(minutes=10)
        app=self.page('pages/0_AI银行助手.py');self.click(app,'model_pending_open');self.click(app,'model_pending_approve')
        flow=app.session_state['model_bank_flow']
        self.assertEqual(flow.result['error']['code'],'AUTH_EXPIRED')
        self.assertNotIn('subscriptions',flow.result.get('data',{}))
        self.assertFalse(app.text_area(key='bank_model_input').disabled);self.assertEqual(self.money(),200000)

    def test_cross_page_unknown_stays_locked_until_explicit_query(self):
        original=self.flow();original.start_action('transfer.create',dict(account_id='demo_account_a',payee_id='demo_payee_li',amount_minor=2000))
        authorize=self.gateway.authorize
        def lost(*a,**kw):authorize(*a,**kw);raise ConnectionError('fictional lost reply')
        with patch.object(self.gateway,'authorize',side_effect=lost):original.decide(True)
        self.assertTrue(original.needs_reconciliation)
        home=self.page();home.session_state['model_bank_flow']=original;home.run();self.ok(home)
        self.assertTrue(original.needs_reconciliation)
        self.click(home,'home_pending_source_2_query')
        self.assertFalse(original.needs_reconciliation);self.assertEqual(self.money(),198000)
        self.assertFalse(home.session_state['conversation'].blocked)

    def test_model_recipient_choice_automation_and_unavailable_preview(self):
        app=self.page('pages/0_AI银行助手.py')
        self.send(app,'给小王20元','transfer',dict(payee_query='小王',amount_text='20元'))
        self.click(app,'model_pick_payee');self.click(app,'model_prepare_step')
        self.assertEqual(app.session_state['model_bank_flow'].request['params']['payee_id'],'demo_payee_wang_1')
        with patch.object(self.gateway,'operation_payee',return_value=None):
            app.run();self.ok(app);self.assertTrue(app.button(key='action_approve').disabled)
            self.click(app,'model_reconcile_payee')
        app.run();self.ok(app);self.click(app,'model_pending_reject');self.assertEqual(self.money(),200000)

    def test_subscription_scan_and_due_charge_buttons(self):
        app=self.page('pages/5_订阅管理.py');self.click(app,'subscription_scan');self.click(app,'action_approve')
        self.assertEqual(app.session_state['action_flow'].result['status'],'succeeded')
        with self.store.connection(write=True) as c:
            c.execute("UPDATE subscriptions SET subscription_state='active',next_charge_date='2026-10-26' WHERE subscription_id='SUB-E001'")
        self.click(app,'subscription_list');self.click(app,'action_approve');self.click(app,'subscription_charge');self.click(app,'action_approve')
        self.assertEqual(self.money(),197500)
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM subscription_charges').fetchone()[0],1)



    def test_guide_choose_buttons_in_active_and_completed_states(self):
        from app.onboarding import TOPICS
        app=self.page();self.click(app,'bank_guide_open');self.click(app,'bank_guide_topic_transfer');self.click(app,'bank_guide_choose')
        self.click(app,'bank_guide_topic_transfer')
        for _ in TOPICS['transfer'][1]:self.click(app,'bank_guide_next')
        self.click(app,'bank_guide_choose');self.assertEqual(self.money(),200000)

    def test_all_navigation_instances_and_examples_are_real_clicks(self):
        from app.ui import NAV_ITEMS
        for key,*_ in NAV_ITEMS:
            app=self.page();button=next((b for b in app.button if b.key=='ui_menu_'+key),None)
            if button is not None and not button.disabled:button.click().run();self.ok(app)
        for key in ('transfer','bills','cards','aa','scheduled','budget','wealth','products'):
            app=self.page();self.click(app,'home_service_'+key)
        app=self.page();self.click(app,'home_service_wealth');self.click(app,'app_nav_home')
        for index in range(4):
            app=self.page();self.click(app,'example_'+str(index));flow=app.session_state['conversation']
            if flow.pending:flow.decide(False)
        self.assertEqual(self.money(),200000)

    def test_first_setup_ack_binding_and_recovery_buttons(self):
        name='ui-setup';password=secrets.token_urlsafe(20)
        with patch('app.auth_ui._setup_allowed',return_value=True):
            app=self.page();self.click(app,'demo_to_setup');self.text(app,'设置用户名',name);self.text(app,'设置密码（至少12字符）',password);self.text(app,'再次输入密码',password)
            self.label(app,'下一步：创建账号并生成恢复码')
            token=app.session_state['auth_token'];codes=self.auth.pending_recovery(token)
            next(w for w in app.checkbox if w.label=='我已将以上恢复码离线保存到安全位置').check()
            self.label(app,'我已保存，继续绑定手机')
            otp=pyotp.parse_uri(self.auth.enrollment(token));self.text(app,'手机当前动态码',otp.at(self.ts));self.label(app,'完成绑定');self.ts+=30
            self.assertEqual(self.auth.principal(token),'demo_user_a')
            self.click(app,'auth_logout')
            self.text(app,'恢复账号',name);self.text(app,'账号密码',password);self.text(app,'一枚未使用的恢复码',codes[0])
            self.label(app,'撤销原会话并重新绑定')
            self.assertFalse(self.auth.account_status()['bound']);self.assertEqual(self.money(),200000)

    def test_authenticated_background_notifications_and_exit_controls(self):
        token=self.enable_auth()
        with self.store.connection(write=True) as c:c.execute('INSERT INTO notifications VALUES (?,?,?,?,0)',('ui-notice','demo_user_a','独立测试通知',self.now.isoformat()))
        app=self.page(token=token);self.click(app,'inbox_read');self.click(app,'bg_refresh')
        from bank.background import BackgroundController
        controller=BackgroundController(self.store)
        self.addCleanup(lambda:controller.request_stop(wait=6))
        self.click(app,'bg_start')
        self.assertTrue(controller.status()['running'] or controller.status().get('starting'))
        self.click(app,'bg_stop');self.assertTrue(app.session_state['bg_last_stop']['requested'])
        self.assertTrue(app.session_state['bg_last_stop']['confirmed_stopped'])
        self.click(app,'exit_prepare');self.click(app,'exit_cancel');self.assertNotIn('exit_preview',app.session_state)
        self.click(app,'exit_prepare')
        with patch('app.lifecycle_panel.os._exit') as terminate:
            self.click(app,'exit_confirm');terminate.assert_called_once_with(0)
        self.assertEqual(self.money(),200000)

    def test_empty_state_component_action_contract(self):
        app=AppTest.from_string("from app.ui import render_empty_state\nimport streamlit as st\nif render_empty_state('验收空状态',action_label='重新查看',action_key='qa_empty'):\n st.session_state['qa_clicked']=True").run()
        self.click(app,'qa_empty');self.assertTrue(app.session_state['qa_clicked'])

    def test_wealth_redeem_uses_red_verification(self):
        flow=self.flow();flow.start_action('wealth.assess',dict(questionnaire_version='wpq_v1',answers={f'q{i}':f'q{i}_a' for i in range(1,6)}))
        profile=flow.result['data']['risk_profile_id']
        flow.start_action('wealth.purchase',dict(account_id='demo_account_a',product_id='WP-C01',risk_profile_id=profile,amount_minor=10000));flow.decide(True,simulate_verification=True)
        app=self.page('pages/9_理财.py');self.click(app,'wealth_redeem')
        self.assertEqual(app.session_state['action_flow'].result['status'],'awaiting_verification')
        before=self.money();self.click(app,'action_approve');self.assertGreater(self.money(),before)

    def test_cancel_an_unclaimed_schedule_preserves_balance(self):
        from app.workflow import demo_payees
        app=self.page('pages/6_定时转账.py');flow=app.session_state['action_flow']
        app.selectbox(key='schedule_payee').set_value(demo_payees(flow)[0])
        app.text_input(key='schedule_amount').set_value('20');app.run();self.click(app,'schedule_submit');self.click(app,'action_approve')
        self.assertTrue(app.button(key='schedule_cancel').disabled)
        app.checkbox(key='schedule_cancel_confirm').check().run();self.click(app,'schedule_cancel')
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT state FROM tasks').fetchone()[0],'cancelled')
        self.assertEqual(self.money(),200000)

    def test_saved_birthday_pause_resume_restore_and_clear_draft(self):
        from datetime import date
        from bank.plan_store import PlanStore
        app=self.page('pages/8_生日与商户.py');resources=app.session_state['action_flow'].resources
        app.date_input(key='birthday_date').set_value(date(2026,11,20))
        app.date_input(key='birthday_budget_day').set_value(date(2026,11,1))
        app.multiselect(key='birthday_items').set_value(resources['items'][:2])
        app.selectbox(key='merchant_address').set_value(resources['addresses'][0])
        app.text_input(key='birthday_budget_text').set_value('1000.00');self.click(app,'birthday_review')
        self.click(app,'birthday_edit');self.click(app,'birthday_review');self.click(app,'birthday_save_workflow')
        self.click(app,'birthday_progress_restore');self.click(app,'birthday_progress_pause')
        self.assertTrue(app.session_state['birthday_progress_runner'].paused)
        self.click(app,'birthday_progress_resume');self.assertFalse(app.session_state['birthday_progress_runner'].paused)
        self.click(app,'birthday_plan');self.click(app,'birthday_clear')
        self.assertEqual(len(PlanStore(self.store).list('demo_user_a')),1);self.assertEqual(self.money(),200000)

    def test_draft_back_and_setup_cancel_paths(self):
        app=self.page('pages/0_AI银行助手.py');self.send(app,'给李明20元','transfer',dict(payee_query='李明',amount_text='20元'))
        self.click(app,'bank_model_back');self.assertEqual(self.model.call_count,1);self.assertEqual(self.money(),200000)
        home=self.page();self.click(home,'demo_to_setup');self.click(home,'setup_cancel');self.click(home,'welcome_setup');self.click(home,'setup_cancel')
        self.assertFalse(home.exception);self.assertEqual(self.money(),200000)

    def test_settings_and_compact_navigation_and_local_setup_cancel(self):
        app=self.page();app.switch_page('pages/3_卡片管理.py').run();self.ok(app);self.click(app,'app_nav_home')
        self.assertIn('首页',[h.value for h in app.title])
        app=self.page();self.click(app,'home_assistant')
        # st.switch_page renders its destination in this run, but AppTest's
        # selected page for the next .run remains the old entrypoint unless
        # switch_page is also called on the testing driver.
        app.switch_page('pages/0_AI银行助手.py').run();self.ok(app)
        self.click(app,'model_open_settings')
        self.assertIn('我的',[h.value for h in app.title])
        app.switch_page('pages/10_我的.py').run();self.ok(app)
        with patch('app.auth_ui._setup_allowed',return_value=True):
            self.click(app,'mine_setup');self.click(app,'setup_cancel')
        self.assertEqual(self.money(),200000)

    def test_multistep_automation_only_prepares_and_does_not_pay(self):
        self.model.return_value=json.dumps(dict(reply='请核对计划草稿。',action='plan',slots={'account_id':'demo_account_a'},tasks=[
            dict(id='balance',intent='balance',slots={},depends_on=[]),
            dict(id='pay',intent='transfer',slots={'payee_query':'李明','amount_text':'20元'},depends_on=['balance'])]),ensure_ascii=False)
        app=self.page('pages/0_AI银行助手.py');app.text_area(key='bank_model_input').set_value('先查余额，再准备给李明转20元')
        self.click(app,'bank_model_send');self.click(app,'plan_automation')
        self.assertTrue(app.session_state['model_runner'].automation)
        self.assertEqual(self.money(),200000)

    def test_pending_origin_link_and_local_query_reject_controls(self):
        self.pending_read();page=self.page();self.click(page,'home_pending_goto')
        self.assertIn('订阅管理',[h.value for h in page.title])
        flow=page.session_state['action_flow'];self.assertTrue(flow.pending)
        component=AppTest.from_string("import streamlit as st\nfrom app.pending_view import render_unresolved\nrender_unresolved(st.session_state['flow'],key_prefix='component')")
        component.session_state['flow']=flow;component.run();self.ok(component)
        self.click(component,'component_query');self.click(component,'component_reject')
        self.assertEqual(flow.result['status'],'cancelled');self.assertEqual(self.money(),200000)

if __name__=='__main__':unittest.main()
