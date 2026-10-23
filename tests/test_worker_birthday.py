"""Independent H4/H5 acceptance with a clock and real temporary SQLite ledger."""
import unittest
from datetime import datetime,date,timezone
from bank.gateway import BankGateway
from bank.worker import Worker
from bank.birthday import create_plan
from bank.plan_store import PlanStore
from bank.merchant import SHANGHAI
from agent.controller import Conversation
from agent.model_bank import BankPlanRunner
from tests import test_model_bank as fixture

class WorkerBirthdayTests(unittest.TestCase):
    setUp=fixture.ModelBankTests.setUp
    def make(self):
        self.now=datetime(2026,10,23,3,tzinfo=timezone.utc)
        self.g=BankGateway(self.store,clock=lambda:self.now)
        self.worker=Worker(self.store,clock=lambda:self.now)
        self.flow=Conversation(self.g,principal_id='demo_user_a')
        resources=self.g.get_context(principal_id='demo_user_a')
        self.items=[resources['items'][0],resources['items'][2]]
        self.total=sum(i['price_minor'] for i in self.items)
        self.assertEqual(self.total,35700)
        return create_plan(self.flow,birthday=date(2026,11,20),budget_minor=100000,account_id='demo_account_a',
            item_ids=[i['item_id'] for i in self.items],address_id=resources['addresses'][0]['delivery_address_id'],
            budget_at=datetime(2026,11,1,9,tzinfo=SHANGHAI),purchase_at=datetime(2026,11,18,9,tzinfo=SHANGHAI))
    def restore(self,pid):return BankPlanRunner.restore(Conversation(self.g,principal_id='demo_user_a'),PlanStore(self.store),pid)
    def test_background_time_gates_and_end_to_end_budget_orders_release(self):
        pid=self.make();self.worker.tick()
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM operations').fetchone()[0],0)
        self.now=datetime(2026,11,1,9,tzinfo=SHANGHAI);self.worker.tick()
        r=self.restore(pid);self.assertEqual(r.flow.result['status'],'awaiting_confirmation')
        r.flow.decide(True);r._sync();self.worker.tick()
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM merchant_orders').fetchone()[0],0)
        self.now=datetime(2026,11,18,9,tzinfo=SHANGHAI)
        for _ in self.items:
            self.worker.tick();r=self.restore(pid)
            self.assertEqual(r.flow.result['status'],'awaiting_verification')
            r.flow.decide(True,simulate_verification=True);r._sync()
        self.worker.tick();r=self.restore(pid);self.assertEqual(r.flow.request['action'],'budget.release')
        r.flow.decide(True);r._sync();self.worker.tick()
        with self.store.connection() as c:
            row=c.execute("SELECT balance_minor,available_minor,reserved_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()
            self.assertEqual(tuple(row),(200000-self.total,200000-self.total,0))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM merchant_orders').fetchone()[0],len(self.items))
        self.worker.tick()
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM merchant_orders').fetchone()[0],len(self.items))
    def test_two_workers_and_restart_keep_unique_pending_operation(self):
        pid=self.make();self.now=datetime(2026,11,1,9,tzinfo=SHANGHAI);self.worker.tick()
        second=Worker(self.store,clock=lambda:self.now);self.assertFalse(second.tick())
        self.now=self.now.replace(minute=1);self.assertTrue(second.tick())
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM operations').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM reservations').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM notifications').fetchone()[0],1)
    def test_pause_survives_worker_restart(self):
        pid=self.make();r=self.restore(pid);r.paused=True;r.checkpoint('paused')
        self.now=datetime(2026,11,18,9,tzinfo=SHANGHAI);self.worker.tick()
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM operations').fetchone()[0],0)

    def test_worker_cannot_mint_proof_or_approve_even_in_demo_mode(self):
        from shared import ServiceError
        self.make()
        self.flow.start_action('transfer.create',dict(account_id='demo_account_a',payee_id='demo_payee_li',amount_minor=120000))
        ch=self.flow.result['next_action'];worker=BankGateway(self.store,trusted_worker=True,clock=self.g.clock)
        with self.assertRaises(ServiceError):worker.issue_demo_proof(ch['challenge_id'],principal_id='demo_user_a')
        with self.assertRaises(ServiceError):worker.authorize(dict(schema_version='1.1',challenge_id=ch['challenge_id'],operation_digest=ch['operation_digest'],decision='approve'),principal_id='demo_user_a')
