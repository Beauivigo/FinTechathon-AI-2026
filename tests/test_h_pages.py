"""Real Streamlit login/binding, saved-plan recovery and background controls; offline only."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import pyotp
from streamlit.testing.v1 import AppTest
from bank.store import Store
from bank.seed import initialize
from bank.auth import AuthService
from bank.gateway import BankGateway
from tests import test_closeout_acceptance as fixture

ROOT=Path(__file__).resolve().parents[1]
class HPageTests(unittest.TestCase):
    def test_real_login_binding_and_logout_page(self):
        import streamlit as st
        with TemporaryDirectory() as td:
            store=Store(Path(td)/'bank.sqlite3');initialize(store)
            import os
            auth=AuthService(store) if os.name=='nt' else AuthService(store,seal=lambda b:b,unseal=lambda b:b)
            auth.bootstrap('local-user','local-test-password-123')
            def gateway():return BankGateway(store,auth=auth,session_token=st.session_state.get('auth_token'))
            with patch('app.auth_ui.service',return_value=auth),patch('app.demo.gateway',side_effect=gateway):
                app=AppTest.from_file(str(ROOT/'app/main.py'),default_timeout=15).run()
                self.assertFalse(app.exception)
                next(i for i in app.text_input if i.label=='用户名').set_value('local-user')
                next(i for i in app.text_input if i.label=='登录密码').set_value('local-test-password-123')
                next(b for b in app.button if b.label=='登录').click().run()
                self.assertFalse(app.exception)
                token=app.session_state['auth_token'];uri=auth.enrollment(token)
                next(i for i in app.text_input if i.label=='手机当前动态码').set_value(pyotp.parse_uri(uri).now())
                next(b for b in app.button if b.label=='完成绑定').click().run()
                self.assertFalse(app.exception);self.assertEqual(auth.principal(token),'demo_user_a')
                app.button(key='auth_logout').click().run()
                self.assertFalse(app.exception)
                with self.assertRaises(Exception):auth.principal(token)

class RecoveryPageTests(unittest.TestCase):
    setUp=fixture.CloseoutPageTests.setUp
    page=fixture.CloseoutPageTests.page
    send=fixture.CloseoutPageTests.send
    prepare=fixture.CloseoutPageTests.prepare
    approve=fixture.CloseoutPageTests.approve
    def test_fresh_page_restores_full_plan_and_original_payment(self):
        from tests.test_model_dialogue import proposal
        self.send('给李明转20元',proposal('transfer',{'payee_query':'李明','amount_text':'20元'}))
        self.prepare();self.approve()
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0],1)
        fresh=self.page();fresh.button(key='restore_plan').click().run()
        self.assertFalse(fresh.exception)
        self.assertEqual(fresh.session_state['model_runner'].tasks[0]['status'],'completed')
        self.assertEqual(fresh.session_state['model_dialogue'].history,[])
        self.assertEqual(fresh.session_state['model_dialogue'].checkpoints,[])
        fresh.run()
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0],1)
