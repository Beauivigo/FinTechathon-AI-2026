"""模型到银行网关的独立行为验收，全部使用临时模拟库。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime,timezone
from bank.store import Store
from bank.seed import initialize
from bank.gateway import BankGateway
from agent.controller import Conversation
from agent.model_bank import BankPlanRunner, NeedsInput, compile_task


def task(intent,fields=None,*,key='t1',deps=None):
    return {'id':key,'intent':intent,'fields':fields or {},'depends_on':deps or [],'status':'completed'}


class ModelBankTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'bank.sqlite3')
        initialize(self.store)
        self.flow=Conversation(BankGateway(self.store,clock=lambda:datetime(2026,10,21,3,0,tzinfo=timezone.utc)),principal_id='demo_user_a')

    def balance(self):
        with self.store.connection() as c:
            return c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0]

    def test_green_read_is_automatic_and_rerun_does_not_repeat(self):
        runner=BankPlanRunner(self.flow,[task('balance')])
        reply=runner.advance()
        self.assertEqual(reply['status'],'succeeded')
        self.assertEqual(reply['data']['balance_minor'],200000)
        self.assertEqual(runner.tasks[0]['status'],'completed')
        self.assertIsNone(runner.advance())
        self.assertEqual(self.balance(),200000)

    def test_yellow_write_keeps_confirmation_and_commits_once(self):
        runner=BankPlanRunner(self.flow,[task('transfer',{'payee_query':'李明','amount_text':'10元'})])
        self.assertIsNone(runner.advance())
        self.assertEqual(self.balance(),200000)
        result=runner.advance(allow_write=True)
        self.assertEqual(result['status'],'awaiting_confirmation')
        self.assertEqual(self.balance(),200000)
        self.flow.decide(True)
        runner.advance()
        self.assertEqual(self.balance(),199000)
        self.assertEqual(runner.receipts['t1']['status'],'succeeded')
        runner.advance(allow_write=True)
        self.assertEqual(self.balance(),199000)

    def test_red_transfer_cannot_be_approved_by_normal_confirmation(self):
        runner=BankPlanRunner(self.flow,[task('transfer',{'payee_query':'李明','amount_text':'1200元'})])
        self.assertEqual(runner.advance(allow_write=True)['status'],'awaiting_verification')
        with self.assertRaises(ValueError):self.flow.decide(True)
        self.assertEqual(self.balance(),200000)
        self.flow.decide(True,simulate_verification=True)
        runner.advance()
        self.assertEqual(self.balance(),80000)

    def test_dag_waits_for_preceding_bank_outcome_and_rejection_stops(self):
        runner=BankPlanRunner(self.flow,[task('balance',key='read'),task('transfer',{'payee_query':'李明','amount_text':'10元'},key='pay',deps=['read']),task('card',{'card_operation':'冻结','card_id':'CARD-E001'},key='freeze',deps=['pay'])])
        self.assertEqual(runner.advance()['status'],'succeeded')
        self.assertIsNone(runner.advance())
        runner.advance(allow_write=True)
        self.assertIsNone(runner.advance(allow_write=True))
        self.flow.decide(False)
        runner.advance(allow_write=True)
        self.assertTrue(runner.paused)
        self.assertEqual(runner.tasks[2]['status'],'pending')
        self.assertEqual(self.balance(),200000)

    def test_model_completion_flags_and_bad_graphs_are_not_trusted(self):
        runner=BankPlanRunner(self.flow,[task('balance')])
        self.assertEqual(runner.tasks[0]['status'],'pending')
        for tasks in [[task('balance',deps=['missing'])],[task('balance',deps=['t2']),task('balance',key='t2',deps=['t1'])]]:
            with self.assertRaises(NeedsInput):BankPlanRunner(self.flow,tasks)

    def test_principal_or_proof_injection_and_foreign_account_are_rejected(self):
        for field in ['principal_id','proof_token','confirmed','risk_score','authorization']:
            with self.assertRaises(NeedsInput):compile_task(self.flow,'balance',{field:'fake'})
        with self.assertRaises(NeedsInput):compile_task(self.flow,'balance',{'account_id':'demo_account_li'})
        self.assertEqual(self.balance(),200000)

    def test_ambiguous_payee_needs_trusted_choice(self):
        fields={'payee_query':'小王','amount_text':'10元'}
        with self.assertRaises(NeedsInput) as seen:compile_task(self.flow,'transfer',fields)
        self.assertEqual(len(seen.exception.choices),2)
        with self.assertRaises(NeedsInput):compile_task(self.flow,'transfer',fields,selected_payee='invented')
        action,params=compile_task(self.flow,'transfer',fields,selected_payee='demo_payee_wang_2')
        self.assertEqual((action,params['payee_id']),('transfer.create','demo_payee_wang_2'))

    def test_malformed_amount_or_questionnaire_is_not_invented(self):
        for amount in ['-20元','0元','20.001元','1e3元','20 00元']:
            with self.assertRaises(NeedsInput):compile_task(self.flow,'transfer',{'payee_query':'李明','amount_text':amount})
        with self.assertRaises(NeedsInput):compile_task(self.flow,'wealth_assess',{'questionnaire_version':'wpq_v1'})
        self.assertEqual(self.balance(),200000)

    def test_budget_to_order_uses_real_preceding_receipt_and_separate_gates(self):
        tasks=[task('budget_reserve',{'account_id':'demo_account_a','amount_text':'1000元','purpose':'生日'},key='reserve'),
            task('merchant_order',{'account_id':'demo_account_a','reservation_id':'@reserve.reservation_id','item_id':'ITEM-BD-FLOWER-01','amount_text':'199元','deliver_on':'2026-11-20','delivery_address_id':'ADDR-BD-01'},key='order',deps=['reserve'])]
        runner=BankPlanRunner(self.flow,tasks)
        self.assertEqual(runner.advance(allow_write=True)['status'],'awaiting_confirmation')
        self.flow.decide(True)
        self.assertIsNone(runner.advance())
        self.assertEqual(runner.advance(allow_write=True)['status'],'awaiting_verification')
        self.flow.decide(True,simulate_verification=True);runner.advance()
        self.assertEqual(self.balance(),180100)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT reserved_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0],80100)
        self.assertEqual(len(runner.receipts),2)

    def test_unrelated_or_model_forged_reservation_reference_is_not_used(self):
        runner=BankPlanRunner(self.flow,[task('merchant_order',{'account_id':'demo_account_a','reservation_id':'@unrelated.reservation_id','item_id':'ITEM-BD-FLOWER-01','amount_text':'199元','deliver_on':'2026-11-20','delivery_address_id':'ADDR-BD-01'})])
        runner.receipts['unrelated']={'status':'succeeded','data':{'reservation_id':'fake'}}
        with self.assertRaises(NeedsInput):runner.advance(allow_write=True)
        self.assertEqual(self.balance(),200000)

    def test_lost_receipt_blocks_plan_until_original_operation_is_recovered(self):
        runner=BankPlanRunner(self.flow,[task('transfer',{'payee_query':'李明','amount_text':'10元'})])
        runner.advance(allow_write=True)
        original=self.flow.gateway.authorize
        def lost(*args,**kwargs):
            original(*args,**kwargs)
            raise ConnectionError('simulated lost receipt')
        with patch.object(self.flow.gateway,'authorize',side_effect=lost):self.flow.decide(True)
        self.assertTrue(self.flow.needs_reconciliation)
        self.assertIsNone(runner.advance(allow_write=True))
        self.assertEqual(self.balance(),199000)
        self.flow.recover();runner.advance()
        self.assertEqual(runner.receipts['t1']['status'],'succeeded')
        self.assertEqual(self.balance(),199000)
