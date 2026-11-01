"""UX final acceptance: continuous operations, AI gating and display helpers.

Runnable checks use isolated temporary banks only. Page tests use the repository
AppTest fixture; they are skipped automatically when the platform blocks
loopback (qualified MXC), and review runs them in final acceptance.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from agent.controller import Conversation
from agent.model_dialogue import DialogueSession
from agent.model_runtime import TrustedModelCaller
from agent.model_client import ModelError
from app import pending_view
from app.presentation import (apply_subscription_state, card_available_actions,
                              card_unavailable_reasons, dialogue_controls, dialogue_note,
                              failure_note, missing_text, model_error_guidance,
                              schedule_display, subscription_snapshot,
                              subscription_state_label)
from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from shared import ServiceError, adapt_request

USER = 'demo_user_a'
ROOT = Path(__file__).resolve().parents[1]
try:
    from tests.ui_support import AppTest
except ImportError:  # pragma: no cover - AppTest is part of the app extra
    AppTest = None


class FakeModel:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, messages):
        self.calls.append(messages)
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def proposal(intent, slots):
    return {'reply': '请核对草稿。', 'action': 'propose', 'intent': intent, 'slots': slots}


class DialogueControlTests(unittest.TestCase):
    def test_paused_and_handoff_offer_a_continue_or_manual_path(self):
        paused = dialogue_controls('paused')
        self.assertFalse(paused['send'])
        self.assertTrue(paused['resume'])
        self.assertTrue(paused['clear'])
        handoff = dialogue_controls('handoff')
        self.assertFalse(handoff['send'])
        self.assertFalse(handoff['handoff'])
        self.assertTrue(handoff['clear'])
        locked = dialogue_controls('locked')
        self.assertFalse(locked['send'])
        self.assertFalse(locked['back'])
        self.assertFalse(locked['pause'])
        self.assertTrue(locked['clear'])
        proposal_state = dialogue_controls('proposal')
        self.assertTrue(proposal_state['send'])
        self.assertTrue(proposal_state['pause'])
        self.assertTrue(proposal_state['handoff'])
        self.assertFalse(dialogue_controls('proposal', allow_back=False)['back'])
        self.assertIn('继续草稿', dialogue_note('paused'))
        self.assertIn('手动', dialogue_note('handoff'))
        self.assertIn('仍可提问', dialogue_note('clarify', write_blocked=True))

    def test_paused_or_handoff_submit_is_explained_without_a_model_call(self):
        model = FakeModel(proposal('balance', {'account_id': 'demo_account_a'}))
        session = DialogueSession(model)
        session.submit('查余额')
        self.assertEqual(len(model.calls), 1)
        session.pause()
        snap = session.submit('再查一次')
        self.assertEqual(len(model.calls), 1)
        self.assertIn('暂停', snap['error'])
        session.resume()
        self.assertIsNone(session.snapshot()['error'])
        session.handoff()
        snap = session.submit('继续付款')
        self.assertEqual(len(model.calls), 1)
        self.assertIn('手动', snap['error'])

    def test_missing_fields_use_readable_names(self):
        text = missing_text(['t1:amount_text', 't1:payee_query', 't2:execute_at'])
        self.assertIn('金额', text)
        self.assertIn('收款人', text)
        self.assertIn('预约时间', text)


class FakeGateway:
    def get_context(self, *, principal_id):
        return dict(accounts=[], cards=[], products=[], holdings=[], items=[], addresses=[])

    def clock(self):
        return datetime(2026, 10, 28, 3, tzinfo=timezone.utc)


class FakeFlow:
    principal_id = USER

    def __init__(self):
        self.gateway = FakeGateway()


class WorkingClient:
    last_metadata = {'model': 'fake-model', 'credential_source': 'configured'}

    def __call__(self, messages):
        return '{"reply":"草稿","action":"clarify","intent":"balance","slots":{}}'


class TrustedCallerTests(unittest.TestCase):
    messages = [{'role': 'system', 'content': 'system'}]

    def test_stale_error_is_cleared_before_building_the_client(self):
        calls = []

        def factory():
            calls.append(1)
            if len(calls) == 1:
                raise ModelError('请先配置模型密钥。')
            return WorkingClient()

        caller = TrustedModelCaller(FakeFlow(), client_factory=factory)
        with self.assertRaises(ModelError):
            caller(self.messages)
        self.assertIn('请先配置模型密钥', caller.last_error)
        self.assertEqual(caller.last_metadata, {})
        text = caller(self.messages)
        self.assertEqual(text, '{"reply":"草稿","action":"clarify","intent":"balance","slots":{}}')
        self.assertIsNone(caller.last_error)
        self.assertEqual(caller.last_metadata['model'], 'fake-model')

    def test_client_construction_error_does_not_echo_previous_metadata(self):
        caller = TrustedModelCaller(FakeFlow(), client_factory=lambda: WorkingClient())
        caller(self.messages)
        self.assertTrue(caller.last_metadata)

        def broken():
            raise ModelError('模型配置无效。')

        caller.client_factory = broken
        with self.assertRaises(ModelError):
            caller(self.messages)
        self.assertEqual(caller.last_metadata, {})
        self.assertIn('模型配置无效', caller.last_error)


class PresentationHelperTests(unittest.TestCase):
    def test_failure_note_uses_reviewed_codes_and_never_raw_backend_text(self):
        note = failure_note({'status': 'failed', 'error': {'code': 'INSUFFICIENT_FUNDS',
                                                           'message': 'SQLITE raw detail'}})
        self.assertIn('余额不足', note)
        self.assertIn('不会自动重试', note)
        self.assertNotIn('SQLITE', note)
        self.assertEqual(failure_note({'status': 'succeeded'}), '')
        unknown = failure_note({'error': {'code': 'SOMETHING_NEW', 'message': 'raw'}})
        self.assertIn('本次操作未完成', unknown)
        self.assertNotIn('raw', unknown)

    def test_card_actions_follow_the_state_machine(self):
        self.assertIn('card.freeze', card_available_actions('active'))
        self.assertIn('card.report_loss', card_available_actions('active'))
        self.assertNotIn('card.unfreeze', card_available_actions('active'))
        self.assertIn('card.unfreeze', card_available_actions('frozen'))
        self.assertIn('card.report_loss', card_available_actions('frozen'))
        self.assertNotIn('card.freeze', card_available_actions('frozen'))
        self.assertIn('card.release_loss', card_available_actions('lost'))
        for state in ('active', 'frozen', 'lost'):
            self.assertEqual(card_available_actions(state)[-1], 'card.set_limit')
        self.assertTrue(any('解冻' in reason for reason in card_unavailable_reasons('active')))
        self.assertTrue(any('解挂' in reason for reason in card_unavailable_reasons('active')))

    def test_subscription_snapshot_keeps_confirmed_rows_and_receipt_updates(self):
        result = {'data': {'subscriptions': [
            {'subscription_id': 'SUB-E001', 'subscription_state': 'candidate'},
            {'subscription_id': 'SUB-E003', 'subscription_state': 'active'}]}}
        snapshot = subscription_snapshot(result, queried_at='2026-10-28 12:00:00 北京时间')
        self.assertEqual(len(snapshot['rows']), 2)
        self.assertEqual(snapshot['queried_at'], '2026-10-28 12:00:00 北京时间')
        apply_subscription_state(snapshot, 'SUB-E001', 'dismissed')
        self.assertEqual(snapshot['rows'][0]['subscription_state'], 'dismissed')
        self.assertEqual(snapshot['rows'][1]['subscription_state'], 'active')
        self.assertEqual(subscription_state_label('dismissed'), '已排除')
        self.assertEqual(subscription_state_label('rejected'), '已排除')

    def test_schedule_display_separates_appointment_and_payment(self):
        self.assertIn('待到期', schedule_display('scheduled', None)[0])
        self.assertIn('已完成', schedule_display('dispatched', 'succeeded')[0])
        self.assertIn('待你确认', schedule_display('dispatched', 'awaiting_confirmation')[0])
        self.assertIn('待身份验证', schedule_display('dispatched', 'awaiting_verification')[0])
        self.assertIn('待核实', schedule_display('dispatched', 'NOT_FOUND')[0])
        self.assertIn('待核实', schedule_display('dispatched', 'unknown')[0])
        self.assertNotIn('待本人处理', ' '.join(schedule_display('dispatched', status)[0]
            for status in (None, 'NOT_FOUND', 'succeeded')))

    def test_model_error_guidance_distinguishes_common_causes(self):
        self.assertIn('尚未配置', model_error_guidance('请先配置模型密钥。'))
        self.assertIn('配置无效', model_error_guidance('模型密钥无效，请重新配置。'))
        self.assertIn('余额不足', model_error_guidance('模型账户余额不足。'))
        self.assertIn('限流', model_error_guidance('模型服务限流，请稍后手动重试。'))
        self.assertIn('超时', model_error_guidance('模型连接超时或不可用，本次未生成新计划。'))
        self.assertNotIn('raw-secret', model_error_guidance('raw-secret'))


class ConversationGateTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / 'ux.sqlite3')
        initialize(self.store)
        self.now = datetime(2026, 10, 28, 3, tzinfo=timezone.utc)
        self.gateway = BankGateway(self.store, clock=lambda: self.now)

    def pending_reserve(self):
        request = adapt_request('budget.reserve', dict(account_id='demo_account_a',
            amount_minor=35700, purpose='生日预算'), request_id='req_' + uuid4().hex,
            trace_id='trace_' + uuid4().hex, idempotency_key='operation_' + uuid4().hex)
        result = self.gateway.execute(request, principal_id=USER)
        self.assertEqual(result['status'], 'awaiting_confirmation')
        return request

    def test_pending_operation_keeps_dialogue_and_hides_dead_submission(self):
        self.pending_reserve()
        flow = Conversation(self.gateway, principal_id=USER)
        self.assertTrue(flow.blocked)
        self.assertTrue(pending_view.needs_attention(flow))
        labels = [item['action_label'] for item in pending_view.unresolved_items(flow)]
        self.assertIn('预留预算', labels)
        # New business submission is the only hard stop.
        with self.assertRaises(ValueError):
            flow.start_action('budget.reserve', dict(account_id='demo_account_a',
                                                     amount_minor=100, purpose='新业务'))
        # Draft-only dialogue still works and never writes the bank.
        before = len(self.gateway.list_operations(principal_id=USER))
        session = DialogueSession(FakeModel(proposal('balance', {'account_id': 'demo_account_a'})))
        snap = session.submit('先解释一下我当前能做哪些查询')
        self.assertEqual(snap['phase'], 'proposal')
        self.assertEqual(len(self.gateway.list_operations(principal_id=USER)), before)

    def test_recovered_pending_operation_needs_no_new_form(self):
        self.pending_reserve()
        second = Conversation(self.gateway, principal_id=USER)
        items = pending_view.unresolved_items(second)
        self.assertEqual(len(items), 1)
        second.recover(action=items[0]['action'], idempotency_key=items[0]['key'])
        self.assertTrue(second.pending)
        self.assertEqual(second.request['params']['amount_minor'], 35700)
        self.assertEqual(second.request['params']['purpose'], '生日预算')
        self.assertTrue(second.blocked)
        self.assertEqual(len(self.gateway.list_operations(principal_id=USER)), 1)


class SubscriptionJourneyTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / 'sub.sqlite3')
        initialize(self.store)
        self.gateway = BankGateway(self.store, clock=lambda: datetime(2026, 10, 28, tzinfo=timezone.utc))

    def test_confirmed_read_snapshot_survives_management_receipt(self):
        read = adapt_request('subscription.list', dict(account_id='demo_account_a'),
            request_id='req_' + uuid4().hex, trace_id='trace_' + uuid4().hex,
            idempotency_key='operation_' + uuid4().hex)
        pending = self.gateway.execute(read, principal_id=USER)
        self.assertEqual(pending['status'], 'awaiting_confirmation')
        challenge = pending['next_action']
        result = self.gateway.authorize(dict(schema_version='1.1',
            challenge_id=challenge['challenge_id'], operation_digest=challenge['operation_digest'],
            decision='approve'), principal_id=USER)
        snapshot = subscription_snapshot(result, queried_at='2026-10-28 12:00:00 北京时间')
        self.assertEqual([row['subscription_id'] for row in snapshot['rows']],
                         ['SUB-E001', 'SUB-E003'])
        review = adapt_request('subscription.review', dict(subscription_id='SUB-E001', decision='reject'),
            request_id='req_' + uuid4().hex, trace_id='trace_' + uuid4().hex,
            idempotency_key='operation_' + uuid4().hex)
        pending = self.gateway.execute(review, principal_id=USER)
        challenge = pending['next_action']
        result = self.gateway.authorize(dict(schema_version='1.1',
            challenge_id=challenge['challenge_id'], operation_digest=challenge['operation_digest'],
            decision='approve'), principal_id=USER)
        self.assertEqual(result['data']['subscription_state'], 'dismissed')
        apply_subscription_state(snapshot, 'SUB-E001', result['data']['subscription_state'])
        self.assertEqual(snapshot['rows'][0]['subscription_state'], 'dismissed')
        self.assertEqual(snapshot['rows'][1]['subscription_state'], 'active')
        # A fresh read is still a yellow gate: no silent re-read.
        again = adapt_request('subscription.list', dict(account_id='demo_account_a'),
            request_id='req_' + uuid4().hex, trace_id='trace_' + uuid4().hex,
            idempotency_key='operation_' + uuid4().hex)
        self.assertEqual(self.gateway.execute(again, principal_id=USER)['status'],
                         'awaiting_confirmation')


def _page_test(test):
    """Mark AppTest cases so loopback-less environments can see them skipped."""
    return unittest.skipIf(AppTest is None, 'AppTest unavailable')(test)


@unittest.skipIf(AppTest is None, 'AppTest unavailable')
class UxPageTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        root_patch = patch('bank.seed.PROJECT_ROOT', self.temp)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def page(self, name, native=False):
        from streamlit.testing.v1 import AppTest as NativeAppTest
        cls = NativeAppTest if native else AppTest
        page = cls.from_file(str(ROOT / 'app' / name), default_timeout=15).run()
        self.assertFalse(page.exception)
        return page

    @_page_test
    def test_birthday_pending_operation_visible_after_navigation(self):
        page = self.page('main.py')
        page.switch_page('pages/8_生日与商户.py').run()
        flow = page.session_state['action_flow']
        flow.start_action('budget.reserve', dict(account_id='demo_account_a',
            amount_minor=35700, purpose='生日预算'))
        page.run()
        self.assertFalse(page.exception)
        self.assertTrue(flow.pending)
        self.assertIn('生日预算', ' '.join(t.value for t in page.text))
        page.switch_page('main.py').run()
        page.switch_page('pages/8_生日与商户.py').run()
        self.assertFalse(page.exception)
        # Widget form state is gone after navigation, but the original pending
        # operation and its handle must still be visible without refilling.
        self.assertTrue(any(b.key in {'action_approve', 'birthday_progress_pending_approve'}
                            for b in page.button))
        self.assertTrue(any('生日预算' in t.value or '预留' in t.value for t in page.text))
        self.assertEqual(flow.result['status'], 'awaiting_confirmation')
        with self.store_connection(flow) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM reservations').fetchone()[0], 0)

    def store_connection(self, flow):
        return flow.gateway.store.connection()

    @_page_test
    def test_subscription_list_stays_after_review(self):
        page = self.page('pages/5_订阅管理.py')
        flow = page.session_state['action_flow']
        page.button(key='subscription_list').click().run()
        page.button(key='action_approve').click().run()
        self.assertFalse(page.exception)
        amounts = [m.value for m in page.metric if m.label=='金额']
        self.assertEqual(sorted(amounts), ['1,200.00元','25.00元'])
        page.selectbox(key='subscription_decision').select('reject').run()
        page.button(key='subscription_review').click().run()
        page.button(key='action_approve').click().run()
        self.assertFalse(page.exception)
        self.assertEqual([m.value for m in page.metric if m.label=='金额'], amounts)
        self.assertTrue(any('badge[已排除]' in m.value for m in page.markdown))
        # Management controls still exist from the retained snapshot.
        self.assertTrue(any(b.key=='subscription_list' for b in page.button))
        self.assertTrue(flow.result['status'] == 'succeeded')

    @_page_test
    def test_ai_paused_and_handoff_send_is_disabled_with_a_path_forward(self):
        output = proposal('transfer', {'account_id': 'demo_account_a',
                                       'payee_query': '李明', 'amount_text': '20元'})
        with patch('agent.model_client.ModelClient.__call__',
                   return_value=json.dumps(output, ensure_ascii=False)) as model:
            page = self.page('pages/0_AI银行助手.py')
            page.text_area(key='bank_model_input').set_value('给李明转20元')
            page.button(key='bank_model_send').click().run()
            self.assertEqual(model.call_count, 1)
            self.assertEqual(page.text_area(key='bank_model_input').value, '')
            page.button(key='bank_model_pause').click().run()
            self.assertTrue(page.button(key='bank_model_send').disabled)
            self.assertTrue(any(b.key=='bank_model_resume' for b in page.button))
            page.button(key='bank_model_resume').click().run()
            page.button(key='bank_model_handoff').click().run()
            self.assertTrue(page.button(key='bank_model_send').disabled)
            self.assertTrue(any(b.key=='bank_model_manual_transfer' for b in page.button))
            self.assertEqual(model.call_count, 1)
            flow = page.session_state['model_bank_flow']
            self.assertEqual(flow.operations, [])
            self.assertIsNone(flow.request)

    @_page_test
    def test_green_query_has_no_recovery_control(self):
        output = proposal('balance', {'account_id': 'demo_account_a'})
        with patch('agent.model_client.ModelClient.__call__',
                   return_value=json.dumps(output, ensure_ascii=False)):
            page = self.page('pages/0_AI银行助手.py')
            page.text_area(key='bank_model_input').set_value('看看余额')
            page.button(key='bank_model_send').click().run()
            self.assertFalse(page.exception)
            self.assertFalse(any(b.key == 'action_query' for b in page.button))
            self.assertTrue(any('只读查询' in c.value for c in page.caption))

    @_page_test
    def test_pending_operation_keeps_ai_input_usable_but_blocks_preparation(self):
        with patch('agent.model_client.ModelClient.__call__') as model:
            page = self.page('pages/0_AI银行助手.py')
            flow = page.session_state['model_bank_flow']
            flow.start_action('budget.reserve', dict(account_id='demo_account_a',
                amount_minor=35700, purpose='待办测试'))
            page.run()
            self.assertFalse(page.exception)
            self.assertFalse(page.text_area(key='bank_model_input').disabled)
            self.assertFalse(page.button(key='bank_model_send').disabled)
            self.assertTrue(any(b.key=='model_pending_open' for b in page.button))
            self.assertFalse(any(b.key=='model_prepare_step' for b in page.button))
            self.assertEqual(model.call_count, 0)
            self.assertTrue(flow.blocked)


if __name__ == '__main__':
    unittest.main()
