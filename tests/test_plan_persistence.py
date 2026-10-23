"""Independent temporary-ledger failure/restart acceptance for H1/H2."""
import unittest
from copy import deepcopy
from agent.model_bank import BankPlanRunner, NeedsInput
from agent.controller import Conversation
from bank.plan_store import PlanStore
from tests import test_model_bank as fixture
from tests.test_model_bank import task
from unittest.mock import patch

class PersistenceTests(unittest.TestCase):
    setUp=fixture.ModelBankTests.setUp
    def test_last_holdings_query_is_persisted_as_completed(self):
        r=BankPlanRunner(self.flow,[task('wealth_holdings',key='holdings')],trace_id='plan_'+'d'*32,journal=PlanStore(self.store))
        r.advance(allow_write=True)
        restored=self.restored(r)
        self.assertEqual(restored.tasks[0]['status'],'completed')
        self.assertIsNone(restored.advance())
    def runner(self):
        return BankPlanRunner(self.flow,[task('transfer',{'payee_query':'李明','amount_text':'20元'},key='pay'),task('card',{'card_id':'CARD-E001','card_operation':'冻结'},key='card',deps=['pay'])],trace_id='plan_'+'a'*32,journal=PlanStore(self.store))
    def restored(self,old):
        fresh=Conversation(self.flow.gateway,principal_id='demo_user_a')
        return BankPlanRunner.restore(fresh,PlanStore(self.store),old.trace_id)
    def test_restart_after_payment_keeps_completed_step(self):
        r=self.runner();r.advance(allow_write=True);self.flow.decide(True);r._sync()
        restored=self.restored(r);restored.advance(allow_write=True)
        self.assertEqual(restored.flow.request['action'],'card.freeze')
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0],1)
    def test_crash_after_commit_before_checkpoint_queries_original(self):
        r=self.runner();r.advance(allow_write=True);self.flow.decide(True)
        restored=self.restored(r)
        self.assertEqual(restored.tasks[0]['status'],'completed')
        self.assertEqual(restored.flow.result['status'],'succeeded')
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0],1)
    def test_crash_before_dispatch_replays_only_saved_key(self):
        r=self.runner()
        with patch.object(self.flow,'start_action',side_effect=SystemExit):
            with self.assertRaises(SystemExit):r.advance(allow_write=True)
        restored=self.restored(r)
        self.assertEqual(restored.flow.result['status'],'awaiting_confirmation')
        self.assertEqual(restored.flow.request['idempotency_key'],r.tasks[0]['operation_key'])
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0],0)
    def test_other_user_and_stale_revision_are_rejected(self):
        r=self.runner();r.checkpoint();other=self.restored(r)
        r.paused=True;r.checkpoint()
        with self.assertRaises(NeedsInput):other.checkpoint()
        with self.assertRaises(Exception):PlanStore(self.store).load(r.trace_id,'demo_user_li')
    def test_journal_failure_stops_before_bank_call(self):
        r=self.runner()
        with patch.object(r.journal,'save',side_effect=OSError),patch.object(self.flow,'start_action') as call:
            with self.assertRaises(NeedsInput):r.advance(allow_write=True)
            call.assert_not_called()

    def test_scheduled_registration_does_not_complete_payment_dependency(self):
        from datetime import timedelta
        from bank.scheduler import Scheduler
        now=self.flow.gateway.clock();future=now+timedelta(minutes=2)
        tasks=[task('scheduled_transfer',{'payee_query':'李明','amount_text':'20元','execute_at':future.isoformat()},key='future'),task('balance',key='after',deps=['future'])]
        r=BankPlanRunner(self.flow,tasks,trace_id='plan_'+'b'*32,journal=PlanStore(self.store))
        r.advance(allow_write=True);self.flow.decide(True);r._sync()
        self.assertEqual(r.tasks[0]['status'],'awaiting_bank')
        self.assertEqual(r.tasks[1]['status'],'pending')
        self.flow.gateway.clock=lambda:future
        Scheduler(self.flow.gateway).run_once(principal_id='demo_user_a')
        r._sync();self.assertEqual(r.flow.result['status'],'awaiting_confirmation')
        self.flow.decide(True);r._sync();r.advance()
        self.assertEqual(r.tasks[1]['status'],'completed')

    def test_v13_migration_keeps_money_and_ledger(self):
        with self.store.connection(write=True) as c:
            c.execute('PRAGMA user_version=13')
        from bank.seed import initialize
        from bank.store import STORAGE_VERSION
        initialize(self.store);initialize(self.store)
        with self.store.connection() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],STORAGE_VERSION)
            self.assertEqual(c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0],200000)
