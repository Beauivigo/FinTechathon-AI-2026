"""回归：subscription.list 确认门等未决操作必须可核对、确认、拒绝、过期收口。

只使用独立临时模拟银行；不读取或修改宿主 runtime 演示库，不产生真实扣款。
不依赖 Streamlit AppTest（Windows MXC 无回环），页面接线由静态断言与 Codex
独立 AppTest/视觉验收覆盖。
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import unittest

from agent.controller import UNRESOLVED, Conversation, reconcile_session_flows
from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from shared import ServiceError, adapt_request, adapt_result
from app import pending_view

USER = 'demo_user_a'
OTHER = 'demo_user_li'


class PendingResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'pending.sqlite3')
        initialize(self.store)
        self.now = datetime(2026, 10, 17, 3, tzinfo=timezone.utc)
        self.gateway = BankGateway(self.store, clock=lambda: self.now)

    def flow(self, principal=USER):
        return Conversation(self.gateway, principal_id=principal)

    def request(self, action, params):
        return adapt_request(action, params, request_id='req_' + uuid4().hex,
                             trace_id='trace_' + uuid4().hex,
                             idempotency_key='operation_' + uuid4().hex)

    def listing(self, principal=USER, account='demo_account_a'):
        request = self.request('subscription.list', dict(account_id=account))
        result = self.gateway.execute(request, principal_id=principal)
        self.assertEqual(result['status'], 'awaiting_confirmation')
        return request

    def count(self, table):
        with self.store.connection() as c:
            return c.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]

    def balance(self):
        with self.store.connection() as c:
            row = c.execute("SELECT balance_minor, available_minor, reserved_minor"
                            " FROM accounts WHERE account_id='demo_account_a'").fetchone()
        self.assertEqual(row[0], row[1] + row[2])
        return tuple(row)

    def test_pending_read_blocks_new_business_but_is_listed_for_resolution(self):
        original = self.listing()
        flow = self.flow()
        self.assertFalse(flow.pending)
        self.assertTrue(flow.blocked)
        items = pending_view.unresolved_items(flow)
        self.assertEqual([(item['action'], item['status']) for item in items],
                         [('subscription.list', 'awaiting_confirmation')])
        self.assertEqual(items[0]['key'], original['idempotency_key'])
        self.assertIn('查看订阅', items[0]['action_label'])
        self.assertIn('等待确认', items[0]['status_label'])
        self.assertTrue(pending_view.needs_attention(flow))
        self.assertEqual(set(UNRESOLVED), {'awaiting_confirmation', 'awaiting_verification',
                                           'running', 'unknown'})
        before = self.count('operations')
        with self.assertRaises(ValueError):
            flow.start_action('card.freeze', dict(card_id='CARD-E001'))
        self.assertIn('不要重复提交', flow.submit('给李明转200元'))
        self.assertIsNone(flow.request)
        self.assertEqual(self.count('operations'), before)
        self.assertEqual(self.balance(), (200000, 200000, 0))
        self.assertEqual(self.count('ledger_entries'), 0)

    def test_confirm_from_a_second_page_object_unblocks_every_view_and_cards(self):
        original = self.listing()
        creator = self.flow()
        creator.recover(action='subscription.list', idempotency_key=original['idempotency_key'])
        self.assertTrue(creator.pending)
        resolver = self.flow()
        self.assertTrue(resolver.blocked)
        resolver.recover(action='subscription.list', idempotency_key=original['idempotency_key'])
        self.assertEqual(resolver.result['status'], 'awaiting_confirmation')
        result = resolver.decide(True)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual([row['subscription_id'] for row in result['data']['subscriptions']],
                         ['SUB-E001', 'SUB-E003'])
        self.assertFalse(resolver.blocked)
        self.assertEqual(pending_view.unresolved_items(resolver), ())
        # 另一个页面对象仍持有旧的待确认快照，必须按权威结果收口。
        self.assertTrue(creator.pending)
        self.assertTrue(pending_view.needs_attention(creator))
        self.assertFalse(creator.settle())
        self.assertEqual(creator.result['status'], 'succeeded')
        self.assertFalse(creator.pending)
        self.assertFalse(pending_view.needs_attention(creator))
        # 全新会话直接看到权威状态：不再有待办。
        fresh = self.flow()
        self.assertFalse(fresh.blocked)
        self.assertEqual(fresh.unresolved, ())
        # 解除阻塞后卡片冻结（强验证）可以正常办理。
        result = fresh.start_action('card.freeze', dict(card_id='CARD-E001'))
        self.assertEqual(result['status'], 'awaiting_verification')
        self.assertEqual(fresh.decide(True, simulate_verification=True)['data']['card_state'], 'frozen')
        self.assertFalse(fresh.blocked)
        self.assertEqual(self.balance(), (200000, 200000, 0))
        self.assertEqual(self.count('ledger_entries'), 0)

    def test_rejecting_pending_read_cancels_without_reading_or_moving_money(self):
        original = self.listing()
        flow = self.flow()
        flow.recover(action='subscription.list', idempotency_key=original['idempotency_key'])
        result = flow.decide(False)
        self.assertEqual(result['status'], 'cancelled')
        self.assertNotIn('subscriptions', result.get('data') or {})
        self.assertFalse(flow.blocked)
        self.assertEqual(self.balance(), (200000, 200000, 0))
        self.assertEqual(self.count('ledger_entries'), 0)
        self.assertEqual(self.count('subscriptions'), 2)
        self.assertFalse(self.flow().blocked)

    def test_expired_pending_read_settles_as_failed_and_unblocks(self):
        original = self.listing()
        flow = self.flow()
        flow.recover(action='subscription.list', idempotency_key=original['idempotency_key'])
        self.now += timedelta(minutes=10)
        result = flow.decide(True)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['code'], 'AUTH_EXPIRED')
        self.assertNotIn('subscriptions', result.get('data') or {})
        self.assertFalse(flow.blocked)
        self.assertFalse(self.flow().blocked)
        self.assertEqual(self.balance(), (200000, 200000, 0))
        self.assertEqual(self.count('ledger_entries'), 0)

    def test_unknown_result_stays_blocked_and_a_missing_index_is_not_proof(self):
        request = self.request('transfer.create', dict(account_id='demo_account_a',
                                                       payee_id='demo_payee_li', amount_yuan=200))
        pending = self.gateway.execute(request, principal_id=USER)
        self.assertEqual(pending['status'], 'awaiting_confirmation')
        unknown = adapt_result(request, dict(status='unknown',
            error=dict(code='RESULT_UNKNOWN', message='待核实')), source='gateway')
        with self.store.connection(write=True) as c:
            c.execute('UPDATE operations SET result_json=? WHERE principal_id=? AND action=? AND idempotency_key=?',
                      (json.dumps(unknown), USER, request['action'], request['idempotency_key']))
        flow = self.flow()
        flow.recover(action='transfer.create', idempotency_key=request['idempotency_key'])
        self.assertEqual(flow.result['status'], 'unknown')
        self.assertTrue(flow.blocked)
        with patch.object(self.gateway, 'list_operations', return_value=[]):
            self.assertTrue(flow.settle())
        with patch.object(self.gateway, 'execute',
                          side_effect=ServiceError('NOT_FOUND', '未找到本次查询。')):
            message = flow.recover()
        self.assertIn('不能据此认定未扣款', message)
        self.assertEqual(flow.result['status'], 'unknown')
        self.assertTrue(flow.blocked)
        self.assertEqual(self.balance(), (200000, 200000, 0))

    def test_resolving_one_operation_does_not_bypass_another(self):
        listing = self.listing()
        transfer = self.request('transfer.create', dict(account_id='demo_account_a',
                                                        payee_id='demo_payee_li', amount_yuan=200))
        self.assertEqual(self.gateway.execute(transfer, principal_id=USER)['status'],
                         'awaiting_confirmation')
        flow = self.flow()
        self.assertEqual(len(flow.unresolved), 2)
        self.assertEqual(len(pending_view.unresolved_items(flow)), 2)
        flow.recover(action='transfer.create', idempotency_key=transfer['idempotency_key'])
        self.assertEqual(flow.decide(True)['status'], 'succeeded')
        self.assertEqual(self.balance(), (180000, 180000, 0))
        self.assertTrue(flow.blocked)
        self.assertEqual([item['action'] for item in pending_view.unresolved_items(flow)],
                         ['subscription.list'])
        with self.assertRaises(ValueError):
            flow.start_action('card.freeze', dict(card_id='CARD-E001'))
        flow.recover(action='subscription.list', idempotency_key=listing['idempotency_key'])
        self.assertEqual(flow.decide(True)['status'], 'succeeded')
        self.assertFalse(flow.blocked)
        self.assertEqual(self.balance(), (180000, 180000, 0))

    def test_pending_record_and_controls_are_scoped_to_its_owner(self):
        original = self.listing()
        other = self.flow(OTHER)
        self.assertFalse(other.blocked)
        self.assertEqual(pending_view.unresolved_items(other), ())
        with self.assertRaises(ServiceError):
            other.gateway.get_confirmed_read(original['idempotency_key'], principal_id=OTHER)
        result = other.start_action('card.freeze', dict(card_id='CARD-E001'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['code'], 'FORBIDDEN')
        self.assertTrue(self.flow().blocked)

    def test_repeated_confirm_and_recover_do_not_execute_twice(self):
        original = self.listing()
        flow = self.flow()
        flow.recover(action='subscription.list', idempotency_key=original['idempotency_key'])
        first = flow.decide(True)
        self.assertEqual(first['status'], 'succeeded')
        audits = self.count('audit_events')
        self.assertEqual(flow.decide(True), first)
        self.assertEqual(self.count('audit_events'), audits)
        flow.recover(action='subscription.list', idempotency_key=original['idempotency_key'])
        self.assertEqual(flow.result['status'], 'succeeded')
        self.assertFalse(flow.settle())
        self.assertEqual(self.count('challenges'), 1)
        self.assertEqual([row['subscription_id'] for row in flow.result['data']['subscriptions']],
                         ['SUB-E001', 'SUB-E003'])
        self.assertEqual(self.balance(), (200000, 200000, 0))

    def test_reconcile_settles_stale_sibling_page_objects(self):
        original = self.listing()
        creator = self.flow()
        creator.recover(action='subscription.list', idempotency_key=original['idempotency_key'])
        resolver = self.flow()
        resolver.recover(action='subscription.list', idempotency_key=original['idempotency_key'])
        resolver.decide(True)
        current = self.flow()
        state = dict(conversation=current, action_flow=creator)
        self.assertFalse(reconcile_session_flows(state, current))
        self.assertFalse(creator.blocked)
        self.assertEqual(creator.result['status'], 'succeeded')
        self.assertFalse(current.blocked)

    def test_pages_wire_the_shared_resolution_surface(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('app/main.py', 'app/model_bank_panel.py', 'app/pages/3_卡片管理.py'):
            text = (root / name).read_text(encoding='utf-8')
            self.assertIn('pending_view.render_unresolved', text, name)
        panel = (root / 'app/pending_view.py').read_text(encoding='utf-8')
        for label in ('查看并处理待办', '确认待办操作', '拒绝待办操作', '查询待办结果'):
            self.assertIn(label, panel)


if __name__ == '__main__':
    unittest.main()
