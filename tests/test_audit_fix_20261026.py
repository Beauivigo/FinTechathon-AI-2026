"""Regressions for AUD-06, AUD-07 and A-NL-26 (temporary SQLite only)."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bank.worker as worker_module
from agent.controller import Conversation
from agent.model_bank import BankPlanRunner, NeedsInput, compile_task
from agent.model_dialogue import DialogueSession
from bank.background import BackgroundController
from bank.birthday import create_plan, schedule_ready, tasks_from_slots
from bank.gateway import BankGateway
from bank.merchant import SHANGHAI
from bank.plan_store import PlanStore
from bank.scheduler import Scheduler as RealScheduler
from bank.worker import Worker
from tests import test_model_bank as fixture


class RevisionConstraintTests(unittest.TestCase):
    """AUD-06: a model revision may not silently drop trusted time constraints."""

    def setUp(self):
        fixture.ModelBankTests.setUp(self)
        self.now = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
        self.gateway = BankGateway(self.store, clock=lambda: self.now)
        self.flow = Conversation(self.gateway, principal_id='demo_user_a')
        resources = self.gateway.get_context(principal_id='demo_user_a')
        self.items = [resources['items'][0], resources['items'][2]]
        self.plan_id = create_plan(self.flow, birthday=date(2026, 11, 20), budget_minor=100000,
            account_id='demo_account_a', item_ids=[i['item_id'] for i in self.items],
            address_id=resources['addresses'][0]['delivery_address_id'],
            budget_at=datetime(2026, 11, 1, 9, tzinfo=SHANGHAI),
            purchase_at=datetime(2026, 11, 18, 9, tzinfo=SHANGHAI))
        self.worker = Worker(self.store, clock=lambda: self.now)
        self.reserve_at = datetime(2026, 11, 1, 9, tzinfo=SHANGHAI).isoformat()
        self.purchase_at = datetime(2026, 11, 18, 9, tzinfo=SHANGHAI).isoformat()

    def snapshot(self):
        return PlanStore(self.store).load(self.plan_id, 'demo_user_a')

    def restore(self):
        return BankPlanRunner.restore(Conversation(self.gateway, principal_id='demo_user_a'),
            PlanStore(self.store), self.plan_id)

    def operations(self):
        with self.store.connection() as c:
            return [dict(r) for r in c.execute('SELECT action,result_json FROM operations ORDER BY rowid')]

    def revise(self, mutate):
        snap, _ = self.snapshot()
        tasks = deepcopy(snap['tasks'])
        mutate(tasks)
        fresh = Conversation(self.gateway, principal_id='demo_user_a')
        previous = BankPlanRunner.restore(fresh, PlanStore(self.store), self.plan_id)
        candidate = BankPlanRunner(fresh, tasks, trace_id=self.plan_id, journal=PlanStore(self.store))
        candidate.inherit_completed(previous)
        candidate.checkpoint('revision')
        return candidate

    def test_purpose_revision_keeps_time_gates_and_rejects_loss(self):
        self.worker.tick()
        self.assertEqual(self.operations(), [])
        candidate = self.revise(
            lambda tasks: next(t for t in tasks if t['id'] == 'reserve')['fields'].update(purpose='生日礼物预算'))
        self.assertEqual(candidate.tasks[0]['not_before'], self.reserve_at)
        self.assertTrue(all('not_before' in task for task in candidate.tasks))
        self.worker.tick()
        self.assertEqual(self.operations(), [])
        restored = self.restore()
        self.assertEqual(restored.tasks[0]['fields']['purpose'], '生日礼物预算')
        self.now = datetime(2026, 11, 1, 9, 0, tzinfo=SHANGHAI)
        self.worker.tick()
        self.assertEqual([r['action'] for r in self.operations()], ['budget.reserve'])
        snap, revision = self.snapshot()
        kept = [deepcopy(t) for t in snap['tasks'] if t['id'] not in ('order_2', 'release')]
        fresh = Conversation(self.gateway, principal_id='demo_user_a')
        previous = BankPlanRunner.restore(fresh, PlanStore(self.store), self.plan_id)
        candidate = BankPlanRunner(fresh, kept, trace_id=self.plan_id, journal=PlanStore(self.store))
        with self.assertRaises(NeedsInput):
            candidate.inherit_completed(previous)
        after, after_revision = self.snapshot()
        self.assertEqual((after, after_revision), (snap, revision))


class LeaseFencingTests(unittest.TestCase):
    """AUD-07: a superseded process cannot progress, and stop claims stay truthful."""

    def setUp(self):
        fixture.ModelBankTests.setUp(self)
        self.now = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
        self.spawned = []

    def spawn(self, owner):
        process = SimpleNamespace(pid=5000 + len(self.spawned))
        self.spawned.append((owner, process))
        return process

    def controller(self):
        return BackgroundController(self.store, clock=lambda: self.now.timestamp(),
            sleep=lambda seconds: None, spawn=self.spawn)

    def register_plan(self, trace, amount_text):
        flow = Conversation(BankGateway(self.store, clock=lambda: self.now), principal_id='demo_user_a')
        runner = BankPlanRunner(flow, [dict(id='pay', title='测试付款', intent='transfer',
            fields=dict(account_id='demo_account_a', payee_query='李明', amount_text=amount_text),
            depends_on=[])], trace_id=trace, journal=PlanStore(self.store))
        runner.automation = True
        runner.checkpoint('registered')
        return runner.trace_id

    def operations(self):
        with self.store.connection() as c:
            return [dict(r) for r in c.execute('SELECT action FROM operations ORDER BY rowid')]

    def test_expired_lease_is_not_a_confirmed_stop(self):
        controller = self.controller()
        controller.ensure_started()
        owner = self.spawned[0][0]
        self.assertTrue(Worker(self.store, clock=lambda: self.now, owner=owner).tick())
        self.now = self.now + timedelta(seconds=31)
        result = controller.request_stop(wait=0)
        self.assertFalse(result['confirmed_stopped'])
        self.assertEqual(result['stop_state'], 'unverified')
        self.assertFalse(controller.status()['running'])

    def test_taken_over_worker_cannot_advance_new_plan(self):
        self.register_plan('plan_' + '1' * 32, '10元')
        controller = self.controller()
        controller.ensure_started()
        old = Worker(self.store, clock=lambda: self.now, owner=self.spawned[0][0])
        entered = threading.Event()
        release = threading.Event()
        class GatedScheduler:
            def __init__(self, gateway, **kwargs):
                self.inner = RealScheduler(gateway, **kwargs)
            def run_once(self, *, principal_id):
                if threading.current_thread().name == 'old-worker':
                    entered.set()
                    release.wait(15)
                return self.inner.run_once(principal_id=principal_id)
        with patch.object(worker_module, 'Scheduler', GatedScheduler):
            thread = threading.Thread(target=old.tick, name='old-worker', daemon=True)
            thread.start()
            self.assertTrue(entered.wait(15))
            self.now = self.now + timedelta(seconds=31)
            newcomer = Worker(self.store, clock=lambda: self.now,
                owner='new-owner-token-0123456789abcdef')
            self.assertTrue(newcomer.tick())
            self.assertFalse(controller.request_stop(wait=0)['confirmed_stopped'])
            self.assertFalse(newcomer.tick())
            self.assertEqual(controller.status()['stop_state'], 'unverified')
            self.register_plan('plan_' + '2' * 32, '20元')
            before = len(self.operations())
            release.set()
            thread.join(15)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(self.operations()), before)
        snapshot, _ = PlanStore(self.store).load('plan_' + '2' * 32, 'demo_user_a')
        self.assertTrue(all(task['status'] == 'pending' for task in snapshot['tasks']))
        with self.store.connection() as c:
            self.assertEqual(tuple(c.execute("SELECT balance_minor,available_minor,reserved_minor "
                "FROM accounts WHERE account_id='demo_account_a'").fetchone()), (200000, 200000, 0))
        self.assertEqual(controller.status()['stop_state'], 'stopped')
        self.assertTrue(controller.request_stop(wait=0)['confirmed_stopped'])


class NaturalLanguageTests(unittest.TestCase):
    """A-NL-26: birthday schedule chain and transfer memo through the model protocol."""

    def test_budget_revision_preserves_custom_times(self):
        fields = dict(self.slots, budget_at='2026-11-05T14:00:00+08:00', purchase_at='2026-11-18T15:00:00+08:00')
        old = BankPlanRunner(self.flow, tasks_from_slots(self.flow, fields))
        revised = tasks_from_slots(self.flow, dict(self.slots, budget_text='1100元'), previous=old)
        self.assertEqual([t['not_before'] for t in old.tasks], [t['not_before'] for t in revised])
        explicit = tasks_from_slots(self.flow, dict(fields, budget_at='2026-11-06T14:00:00+08:00'), previous=old)
        self.assertEqual(explicit[0]['not_before'], '2026-11-06T14:00:00+08:00')
        with self.assertRaises(ValueError):
            tasks_from_slots(self.flow, dict(self.slots, birthday='2026-11-22'), previous=old)

    def test_audit_failure_cannot_confirm_retirement(self):
        controller = BackgroundController(self.store)
        with patch.object(self.store, 'connection', side_effect=RuntimeError('audit unavailable')):
            self.assertTrue(controller._unretired_owners())

    def setUp(self):
        fixture.ModelBankTests.setUp(self)
        self.now = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
        self.gateway = BankGateway(self.store, clock=lambda: self.now)
        self.flow = Conversation(self.gateway, principal_id='demo_user_a')
        resources = self.gateway.get_context(principal_id='demo_user_a')
        self.items = [resources['items'][0], resources['items'][2]]
        self.slots = dict(account_id='demo_account_a', birthday='2026-11-20',
            budget_text='1000.00元', item_ids=[i['item_id'] for i in self.items],
            delivery_address_id=resources['addresses'][0]['delivery_address_id'])
        self.worker = Worker(self.store, clock=lambda: self.now)
        self.reserve_at = datetime(2026, 11, 1, 9, tzinfo=SHANGHAI).isoformat()
        self.purchase_at = datetime(2026, 11, 18, 9, tzinfo=SHANGHAI).isoformat()

    def test_complete_birthday_plan_waits_for_each_authorization(self):
        snap = DialogueSession(lambda messages: {'reply': '草稿', 'action': 'propose',
            'intent': 'birthday', 'slots': deepcopy(self.slots)}).submit('11月20日生日，预算1000元买这两件')
        self.assertEqual(snap['phase'], 'proposal')
        self.assertTrue(schedule_ready(snap['fields']))
        partial = dict(self.slots, item_ids=None)
        snap = DialogueSession(lambda messages: {'reply': '草稿', 'action': 'propose',
            'intent': 'birthday', 'slots': partial}).submit('生日预算1000元')
        self.assertEqual(snap['phase'], 'clarify')
        self.assertIn('item_ids', ' '.join(snap['missing']))
        tasks = tasks_from_slots(self.flow, self.slots)
        self.assertEqual([t['id'] for t in tasks], ['reserve', 'order_1', 'order_2', 'release'])
        self.assertEqual((tasks[0]['not_before'], tasks[1]['not_before']), (self.reserve_at, self.purchase_at))
        runner = BankPlanRunner(self.flow, tasks, trace_id='plan_' + '6' * 32,
            journal=PlanStore(self.store))
        runner.automation = True
        runner.checkpoint('birthday_registered')
        self.worker.tick()
        self.assertEqual(self.count('operations'), 0)
        self.now = datetime(2026, 11, 1, 9, 0, tzinfo=SHANGHAI)
        self.worker.tick()
        with self.store.connection() as c:
            rows = [dict(r) for r in c.execute('SELECT action,result_json FROM operations')]
        self.assertEqual([r['action'] for r in rows], ['budget.reserve'])
        self.assertIn('awaiting_confirmation', rows[0]['result_json'])
        running = BankPlanRunner.restore(Conversation(self.gateway, principal_id='demo_user_a'),
            PlanStore(self.store), 'plan_' + '6' * 32)
        self.assertTrue(running.automation)
        self.assertEqual(running.tasks[0]['not_before'], self.reserve_at)
        running.flow.decide(True)
        running._sync()
        self.worker.tick()
        self.now = datetime(2026, 11, 18, 9, 0, tzinfo=SHANGHAI)
        self.worker.tick()
        with self.store.connection() as c:
            rows = [dict(r) for r in c.execute('SELECT action,result_json FROM operations')]
        self.assertEqual([r['action'] for r in rows], ['budget.reserve', 'merchant.order'])
        self.assertIn('awaiting_verification', rows[1]['result_json'])
        self.assertEqual(self.count('merchant_orders'), 0)

    def test_date_only_birthday_stays_read_only(self):
        fields = dict(account_id='demo_account_a', birthday='2026-11-20')
        snap = DialogueSession(lambda messages: {'reply': '草稿', 'action': 'propose',
            'intent': 'birthday', 'slots': fields}).submit('11月20日是我生日')
        self.assertEqual(snap['phase'], 'proposal')
        self.assertFalse(schedule_ready(snap['fields']))
        self.assertEqual(compile_task(self.flow, 'birthday', snap['fields'])[0], 'scenario.plan')

    def test_transfer_memo_survives_compile_execution_and_recovery(self):
        model_outputs = [
            {'reply': '草稿', 'action': 'propose', 'intent': 'transfer',
             'slots': {'account_id': 'demo_account_a', 'payee_query': '李明', 'amount_text': '20元'}},
            {'reply': '草稿', 'action': 'propose', 'intent': 'transfer', 'slots': {'memo': '生日快乐'}}]
        session = DialogueSession(lambda messages: model_outputs.pop(0))
        session.submit('给李明转20元')
        snap = session.submit('备注写生日快乐')
        action, params = compile_task(self.flow, 'transfer', snap['fields'])
        self.assertEqual((action, params['memo']), ('transfer.create', '生日快乐'))
        runner = BankPlanRunner(self.flow, [dict(id='t1', title='转账', intent='transfer',
            fields=deepcopy(snap['fields']), depends_on=[])], trace_id='plan_' + '4' * 32)
        self.assertEqual(runner.advance(allow_write=True)['status'], 'awaiting_confirmation')
        runner.flow.decide(True)
        runner._sync()
        fresh = Conversation(self.flow.gateway, principal_id='demo_user_a')
        fresh.recover(action='transfer.create', idempotency_key=runner.tasks[0]['operation_key'])
        self.assertEqual(fresh.request['params']['memo'], '生日快乐')
        action, params = compile_task(self.flow, 'scheduled_transfer', dict(
            account_id='demo_account_a', payee_query='李明', amount_text='20元',
            execute_at='2026-10-21T03:10:00+00:00', memo='生日快乐'))
        self.assertEqual((action, params['memo']), ('transfer.schedule', '生日快乐'))

    def test_scheduled_memo_survives_registration_restart_and_due_dispatch(self):
        execute_at = (self.now + timedelta(minutes=5)).isoformat()
        snap = DialogueSession(lambda messages: {'reply': '草稿', 'action': 'propose',
            'intent': 'scheduled_transfer', 'slots': dict(account_id='demo_account_a',
            payee_query='李明', amount_text='20元', execute_at=execute_at, memo='生日快乐')}
            ).submit('给李明定时转账20元，备注生日快乐')
        self.assertEqual(snap['phase'], 'proposal')
        runner = BankPlanRunner(self.flow, [dict(id='t1', title='定时转账',
            intent='scheduled_transfer', fields=deepcopy(snap['fields']), depends_on=[])],
            trace_id='plan_' + '7' * 32, journal=PlanStore(self.store))
        self.assertEqual(runner.advance(allow_write=True)['status'], 'awaiting_confirmation')
        self.assertEqual(runner.flow.request['params']['memo'], '生日快乐')
        runner.flow.decide(True)
        runner._sync()
        self.assertEqual(runner.tasks[0]['status'], 'awaiting_bank')
        with self.store.connection() as c:
            spec = json.loads(c.execute('SELECT spec_json FROM tasks').fetchone()['spec_json'])
        self.assertEqual(spec['payload']['params']['memo'], '生日快乐')
        restored = BankPlanRunner.restore(Conversation(self.gateway, principal_id='demo_user_a'),
            PlanStore(self.store), 'plan_' + '7' * 32)
        self.assertEqual(restored.flow.result['status'], 'scheduled')
        self.now = datetime.fromisoformat(execute_at)
        RealScheduler(self.gateway).run_once(principal_id='demo_user_a')
        restored._sync()
        self.assertEqual(restored.flow.result['status'], 'awaiting_confirmation')
        self.assertEqual(restored.flow.request['params']['memo'], '生日快乐')
        restored.flow.decide(True)
        restored._sync()
        self.assertEqual(restored.tasks[0]['status'], 'completed')
        RealScheduler(self.gateway).run_once(principal_id='demo_user_a')
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0], 1)
            self.assertEqual(tuple(c.execute("SELECT balance_minor,available_minor,reserved_minor "
                "FROM accounts WHERE account_id='demo_account_a'").fetchone()), (198000, 198000, 0))
            stored = c.execute("SELECT request_json FROM operations WHERE action='transfer.create'").fetchone()
        self.assertEqual(json.loads(stored['request_json'])['params']['memo'], '生日快乐')

    def count(self, table):
        with self.store.connection() as c:
            return c.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]


if __name__ == '__main__':
    unittest.main(verbosity=2)
