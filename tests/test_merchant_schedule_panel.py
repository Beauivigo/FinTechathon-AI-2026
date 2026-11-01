"""review真实Streamlit验收；可指定隔离worktree组件，不采用代理stub结果。"""
from datetime import date, time
from pathlib import Path
import os
import unittest
from tests.ui_support import AppTest

ROOT=Path(__file__).resolve().parents[1]
PANEL=Path(os.environ.get('MERCHANT_PANEL_UNDER_TEST',ROOT/'app/merchant_schedule_panel.py'))
PARAMS=dict(account_id='demo_account_a',reservation_id='reservation_demo',item_id='ITEM-BD-FLOWER-01',
    amount_minor=19900,deliver_on='2026-11-20',delivery_address_id='ADDR-BD-01',card_id='CARD-E001')


class MerchantPanelTests(unittest.TestCase):
    def page(self,params=PARAMS,tasks=None):
        script=f'''
import importlib.util
from datetime import datetime, timezone
import streamlit as st
spec=importlib.util.spec_from_file_location('merchant_panel_test', {str(PANEL)!r})
panel=importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)
st.session_state.setdefault('params',{params!r})
st.session_state.setdefault('tasks',{tasks or []!r})
st.session_state.setdefault('blocked',False)
st.session_state['events']=panel.merchant_schedule_panel(st.session_state['params'],st.session_state['tasks'],datetime(2026,10,19,tzinfo=timezone.utc),st.session_state['blocked'])
'''
        p=AppTest.from_string(script,default_timeout=15).run()
        self.assertFalse(p.exception)
        return p

    def task(self,tid='t1',state='dispatched',operation_status='awaiting_verification'):
        return dict(task_id=tid,state=state,operation_status=operation_status,
            spec=dict(run_at='2026-10-20T08:00:00+08:00',payload=dict(params=PARAMS,idempotency_key='execute_'+tid)))

    def test_two_step_confirmation_and_original_fields(self):
        p=self.page(); self.assertEqual(p.session_state['events'],[])
        self.assertTrue(p.button(key='msp_create').disabled)
        p.date_input(key='msp_run_date').set_value(date(2026,10,20)).run()
        p.button(key='msp_confirm').click().run()
        self.assertFalse(p.button(key='msp_create').disabled)
        p.button(key='msp_create').click().run()
        event=p.session_state['events'][0]
        self.assertEqual(event['type'],'merchant_schedule_create')
        self.assertEqual(event['params'],PARAMS)
        self.assertTrue(event['run_at'].endswith('+08:00'))
        key=event['idempotency_key']
        p.run(); self.assertEqual(p.session_state['events'],[])
        self.assertTrue(p.button(key='msp_create').disabled)
        p.button(key='msp_confirm').click().run();p.button(key='msp_create').click().run()
        self.assertEqual(p.session_state['events'][0]['idempotency_key'],key)

    def test_changed_date_time_or_params_invalidates_confirmation(self):
        p=self.page()
        for change in ('date','time','params'):
            p.button(key='msp_confirm').click().run()
            if change=='date': p.date_input(key='msp_run_date').set_value(date(2026,10,21)).run()
            elif change=='time': p.time_input(key='msp_run_time').set_value(time(12,0)).run()
            else:
                p.session_state['params']=PARAMS|{'item_id':'ITEM-BD-CAKE-01','amount_minor':15800}
                p.run()
            self.assertTrue(p.button(key='msp_create').disabled)
            self.assertEqual(p.session_state['events'],[])

    def test_blocked_revokes_confirmation_keeps_recovery(self):
        p=self.page(tasks=[self.task()]);p.button(key='msp_confirm').click().run()
        p.session_state['blocked']=True;p.run()
        self.assertTrue(p.button(key='msp_create').disabled)
        p.button(key='msp_restore').click().run()
        self.assertEqual(p.session_state['events'],[dict(type='merchant_schedule_restore',task_id='t1')])
        p.session_state['blocked']=False;p.run()
        self.assertTrue(p.button(key='msp_create').disabled)

    def test_no_reservation_still_check_cancel_restore_and_distinguish_status(self):
        p=self.page(params=None,tasks=[self.task()])
        self.assertTrue(p.button(key='msp_create').disabled)
        content=' '.join(x.value for x in p.markdown)
        self.assertIn('dispatched',content);self.assertIn('awaiting_verification',content)
        for key,kind in [('msp_check','check'),('msp_cancel','cancel'),('msp_restore','restore')]:
            p.button(key=key).click().run()
            expected=dict(type='merchant_schedule_'+kind)
            if kind!='check': expected['task_id']='t1'
            self.assertEqual(p.session_state['events'],[expected])
            self.assertFalse(p.exception)

    def test_cancel_requires_click_and_uses_current_target(self):
        p=self.page(tasks=[self.task('t1'),self.task('t2')])
        self.assertEqual(p.session_state['events'],[])
        p.selectbox(key='msp_task_id').select('t2').run()
        self.assertEqual(p.session_state['events'],[])
        p.button(key='msp_cancel').click().run()
        self.assertEqual(p.session_state['events'],[dict(type='merchant_schedule_cancel',task_id='t2')])

    def test_empty_tasks_missing_params_and_empty_date_are_safe(self):
        p=self.page(params=None);self.assertEqual(p.session_state['events'],[])
        p=self.page();p.date_input(key='msp_run_date').set_value(None).run()
        self.assertFalse(p.exception);self.assertTrue(p.button(key='msp_create').disabled)
