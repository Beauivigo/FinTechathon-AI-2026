from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
import tests.test_merchant as helper
from shared import ServiceError


class DeliveryPageTests(unittest.TestCase):
    setUp=helper.MerchantTests.setUp
    balance=helper.MerchantTests.balance

    def page(self,path):
        page=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app/main.py'),default_timeout=10).run()
        page.switch_page('pages/'+path).run();self.assertFalse(page.exception)
        return page

    def test_birthday_plan_reserve_order_restart_release(self):
        with patch('bank.seed.demo_path',return_value=self.store.path),patch('app.demo.gateway',return_value=self.g):
            p=self.page('8_生日与商户.py');p.button(key='birthday_plan').click().run()
            self.assertFalse(p.exception);self.assertEqual(self.balance(),(200000,200000,0))
            p.button(key='birthday_reserve').click().run();p.button(key='action_approve').click().run()
            self.assertFalse(p.exception);self.assertEqual(self.balance(),(200000,164300,35700))
            p.button(key='merchant_order').click().run();self.assertFalse(p.exception)
            self.assertTrue(any('CARD-E001' in m.value for m in p.markdown))
            self.assertEqual(self.balance(),(200000,164300,35700))
            p.button(key='action_approve').click().run();self.assertFalse(p.exception)
            self.assertEqual(self.balance(),(180100,164300,15800))
            p=self.page('8_生日与商户.py')
            p.button(key='merchant_release').click().run();p.button(key='action_approve').click().run()
            self.assertFalse(p.exception);self.assertEqual(self.balance(),(180100,180100,0))

    def test_merchant_lost_reply_blocks_page_until_recovery(self):
        with patch('bank.seed.demo_path',return_value=self.store.path),patch('app.demo.gateway',return_value=self.g):
            p=self.page('8_生日与商户.py');p.button(key='birthday_reserve').click().run();p.button(key='action_approve').click().run()
            p.button(key='merchant_order').click().run();authorize=self.g.authorize
            def lost(*args,**kwargs):
                authorize(*args,**kwargs);raise ServiceError('RESULT_UNKNOWN','模拟回执丢失')
            with patch.object(self.g,'authorize',side_effect=lost): p.button(key='action_approve').click().run()
            self.assertFalse(p.exception);self.assertTrue(p.button(key='merchant_order').disabled)
            self.assertTrue(p.button(key='merchant_release').disabled)
            p.button(key='action_query').click().run();self.assertFalse(p.exception)
            self.assertFalse(p.button(key='merchant_release').disabled)
            self.assertEqual(len(self.g.list_orders(principal_id='demo_user_a')),1)

    def test_subscription_reminder_and_cancel_from_page(self):
        with patch('bank.seed.demo_path',return_value=self.store.path),patch('app.demo.gateway',return_value=self.g):
            p=self.page('5_订阅管理.py');p.button(key='subscription_list').click().run();p.button(key='action_approve').click().run()
            p.button(key='subscription_remind').click().run();p.button(key='action_approve').click().run();self.assertFalse(p.exception)
            p.button(key='subscription_list').click().run();p.button(key='action_approve').click().run()
            p.button(key='subscription_cancel').click().run();p.button(key='action_approve').click().run()
            p.button(key='subscription_check_reminders').click().run();self.assertFalse(p.exception)
            self.assertEqual(p.session_state['reminder_snapshot'][0]['state'],'cancelled')

    def test_card_application_page_only_receives(self):
        with self.store.connection(write=True) as c: c.execute("UPDATE cards SET card_state='frozen'")
        with patch('bank.seed.demo_path',return_value=self.store.path),patch('app.demo.gateway',return_value=self.g):
            p=self.page('3_卡片管理.py');p.button(key='card_apply').click().run();p.button(key='action_approve').click().run()
            self.assertFalse(p.exception);self.assertTrue(any('受理不代表已发卡' in s.value for s in p.success))
            with self.store.connection() as c: self.assertEqual(c.execute('SELECT count(*) FROM cards').fetchone()[0],3)


if __name__=='__main__': unittest.main()
