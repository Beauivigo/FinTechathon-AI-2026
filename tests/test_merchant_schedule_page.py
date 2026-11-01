"""review独立联合页面验收：真实Streamlit、网关与临时SQLite。"""
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import unittest
from tests.ui_support import AppTest, fill_manual_birthday
from bank.merchant_scheduler import MerchantScheduler
from tests import test_merchant as helpers

ROOT=Path(__file__).resolve().parents[1]


class MerchantSchedulePageTests(unittest.TestCase):
    setUp=helpers.MerchantTests.setUp
    req=helpers.MerchantTests.req
    send=helpers.MerchantTests.send
    approve=helpers.MerchantTests.approve
    reserve=helpers.MerchantTests.reserve
    order=helpers.MerchantTests.order
    release=helpers.MerchantTests.release
    balance=helpers.MerchantTests.balance

    def page(self):
        mock=patch('app.demo.gateway',return_value=self.g);mock.start();self.addCleanup(mock.stop)
        p=AppTest.from_file(str(ROOT/'app/pages/8_生日与商户.py'),default_timeout=15).run()
        self.assertFalse(p.exception)
        fill_manual_birthday(p)
        return p

    def click(self,p,key):
        p.button(key=key).click().run();self.assertFalse(p.exception)

    def tasks(self): return MerchantScheduler(self.g).list_tasks(principal_id='demo_user_a')

    def register(self,p):
        self.click(p,'msp_confirm');self.click(p,'msp_create')
        self.assertEqual(len(self.tasks()),1)
        return self.tasks()[0]

    def test_register_due_verify_release_complete_page_flow(self):
        self.reserve();p=self.page();task=self.register(p)
        self.assertEqual(self.balance(),(200000,150000,50000))
        self.click(p,'msp_check');self.assertEqual(self.tasks()[0]['state'],'scheduled')
        # 同一采购与时点重新确认只会重放原登记。
        self.register(p)
        self.now+=timedelta(minutes=10);self.click(p,'msp_check')
        self.assertEqual(self.tasks()[0]['state'],'dispatched')
        self.assertEqual(self.balance(),(200000,150000,50000))
        self.assertTrue(p.button(key='msp_create').disabled)
        self.click(p,'msp_restore')
        flow=p.session_state['action_flow']
        self.assertEqual(flow.result['status'],'awaiting_verification')
        self.assertEqual(flow.request['idempotency_key'],task['spec']['payload']['idempotency_key'])
        self.click(p,'action_approve');self.assertEqual(flow.result['status'],'succeeded')
        self.assertEqual(self.balance(),(180100,150000,30100))
        self.click(p,'merchant_release');self.click(p,'action_approve')
        self.assertEqual(self.balance(),(180100,180100,0))
        self.assertTrue(any('succeeded' in m.value for m in p.markdown))

    def test_changed_draft_and_cancel_pending_without_late_charge(self):
        self.reserve();p=self.page();self.click(p,'msp_confirm')
        p.date_input(key='msp_run_date').set_value((self.now+timedelta(days=1)).date()).run()
        self.assertTrue(p.button(key='msp_create').disabled);self.assertEqual(self.tasks(),[])
        self.register(p);self.now+=timedelta(days=2);self.click(p,'msp_check');self.click(p,'msp_restore')
        flow=p.session_state['action_flow'];challenge=flow.result['next_action']
        self.click(p,'msp_cancel');self.assertEqual(flow.result['status'],'cancelled')
        self.assertEqual(self.balance(),(200000,150000,50000))
        result=self.g.authorize(dict(schema_version='1.1',challenge_id=challenge['challenge_id'],operation_digest=challenge['operation_digest'],decision='approve'),principal_id='demo_user_a')
        self.assertEqual(result['status'],'cancelled')

    def test_cancel_scheduled_release_and_no_reservation_history(self):
        self.reserve();p=self.page();self.register(p);self.click(p,'msp_cancel')
        self.assertEqual(self.tasks()[0]['state'],'cancelled')
        self.assertEqual(self.balance(),(200000,150000,50000))
        self.click(p,'merchant_release');self.click(p,'action_approve')
        self.assertTrue(p.button(key='msp_create').disabled)
        self.click(p,'msp_restore')
        self.assertFalse(p.session_state['action_flow'].needs_reconciliation)
        self.assertEqual(self.balance(),(200000,200000,0))

    def test_fully_spent_reservation_retains_order_recovery(self):
        self.reserve(19900);p=self.page();self.register(p)
        self.now+=timedelta(minutes=10);self.click(p,'msp_check');self.click(p,'msp_restore');self.click(p,'action_approve')
        self.assertEqual(self.balance(),(180100,180100,0))
        self.assertTrue(p.button(key='msp_create').disabled)
        self.click(p,'msp_restore');self.assertEqual(p.session_state['action_flow'].result['status'],'succeeded')
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT count(*) FROM merchant_orders').fetchone()[0],1)

    def test_reply_loss_can_recover_original_pending_from_page(self):
        self.reserve();p=self.page();task=self.register(p);self.now+=timedelta(minutes=10)
        original=self.g.execute
        def lost(request,**kwargs):
            result=original(request,**kwargs)
            if request['action']=='merchant.order': raise OSError('模拟受理后断线')
            return result
        with patch.object(self.g,'execute',side_effect=lost):self.click(p,'msp_check')
        self.assertEqual(self.tasks()[0]['state'],'unknown')
        self.assertTrue(p.button(key='msp_create').disabled)
        self.click(p,'msp_restore')
        flow=p.session_state['action_flow']
        self.assertEqual(flow.request['idempotency_key'],task['spec']['payload']['idempotency_key'])
        self.assertEqual(flow.result['status'],'awaiting_verification')
        self.assertEqual(self.balance(),(200000,150000,50000))
