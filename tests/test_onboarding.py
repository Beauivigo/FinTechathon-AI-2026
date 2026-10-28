"""Guide state and real pages: teaching must never change bank state."""
from copy import deepcopy
import unittest

from app.onboarding import GuideState, TOPICS, transition
from tests import test_ui_demo_acceptance as acceptance


class GuideStateTests(unittest.TestCase):
    def test_all_topics_complete_backtrack_replay_and_close(self):
        initial = GuideState()
        self.assertEqual(transition(initial, 'next'), initial)
        self.assertEqual(transition(initial, 'start', 'not-a-topic'), initial)
        for topic, (_, steps, _, _) in TOPICS.items():
            with self.subTest(topic=topic):
                state = transition(initial, 'start', topic)
                self.assertEqual(transition(state, 'back').step, 0)
                state = transition(state, 'next')
                self.assertEqual(transition(state, 'back').step, 0)
                for _ in range(len(steps) - 1):
                    state = transition(state, 'next')
                self.assertTrue(state.completed)
                self.assertEqual(state.step, len(steps) - 1)
                self.assertEqual(transition(state, 'next'), state)
                self.assertEqual(transition(state, 'replay'), GuideState(True, True, topic))
                closed = transition(state, 'close')
                self.assertFalse(closed.opened)
                self.assertTrue(closed.dismissed)
                self.assertIsNone(transition(closed, 'open').topic)


class GuidePageTests(unittest.TestCase):
    setUp = acceptance.UiDemoAcceptanceTests.setUp
    page = acceptance.UiDemoAcceptanceTests.page

    def bank_state(self, flow):
        with flow.gateway.store.connection() as connection:
            names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {name: [tuple(row) for row in connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"')]
                    for name in names}

    def test_transfer_exercise_edits_and_replays_without_any_bank_write(self):
        page = self.page()
        flow = page.session_state['conversation']
        before = self.bank_state(flow)
        for key in ('bank_guide_open', 'bank_guide_topic_transfer', 'bank_guide_next'):
            page.button(key=key).click().run()
            self.assertFalse(page.exception)
            self.assertEqual(self.bank_state(flow), before)
        page.text_input(key='bank_guide_payee').set_value('教学收款人').run()
        page.number_input(key='bank_guide_amount').set_value(450.0).run()
        page.button(key='bank_guide_next').click().run()
        self.assertIn('收款人：教学收款人', [item.value for item in page.text])
        self.assertTrue(any('450.00' in item.value for item in page.text))
        page.button(key='bank_guide_back').click().run()
        self.assertEqual(page.text_input(key='bank_guide_payee').value, '教学收款人')
        for _ in range(3):
            page.button(key='bank_guide_next').click().run()
            self.assertFalse(page.exception)
            self.assertEqual(self.bank_state(flow), before)
            self.assertIsNone(flow.request)
        self.assertTrue(page.session_state['_bank_guide_v1'].completed)
        page.button(key='bank_guide_replay').click().run()
        self.assertEqual(page.session_state['_bank_guide_v1'].step, 0)
        page.button(key='bank_guide_close').click().run()
        self.assertFalse(page.session_state['_bank_guide_v1'].opened)
        self.assertEqual(self.bank_state(flow), before)

    def test_guide_skip_close_and_reopen_preserve_pending_transfer(self):
        page = self.page()
        flow = page.session_state['conversation']
        page.chat_input[0].set_value('给李明转200元').run()
        self.assertEqual(flow.result['status'], 'awaiting_confirmation')
        before = self.bank_state(flow)
        request, result = deepcopy(flow.request), deepcopy(flow.result)
        page.button(key='ui_menu_home').click().run()
        self.assertEqual(page.session_state['home_workspace'], '首页')
        for key in ('bank_guide_skip', 'bank_guide_open', 'bank_guide_topic_bills',
                    'bank_guide_close', 'bank_guide_open'):
            page.button(key=key).click().run()
            self.assertFalse(page.exception)
            self.assertEqual(flow.request, request)
            self.assertEqual(flow.result, result)
            self.assertEqual(self.bank_state(flow), before)

    def test_guided_destination_navigates_without_executing_business(self):
        for topic, destination in (('account', '首页'), ('transfer', '转账与收款'), ('bills', '收支明细')):
            with self.subTest(topic=topic):
                page = self.page()
                flow = page.session_state['conversation']
                before = self.bank_state(flow)
                page.button(key='bank_guide_open').click().run()
                page.button(key='bank_guide_topic_' + topic).click().run()
                for _ in TOPICS[topic][1]:
                    page.button(key='bank_guide_next').click().run()
                page.button(key='bank_guide_destination').click().run()
                self.assertFalse(page.exception)
                self.assertEqual(page.session_state['home_workspace'], destination)
                self.assertFalse(page.session_state['_bank_guide_v1'].opened)
                self.assertIsNone(flow.request)
                self.assertEqual(self.bank_state(flow), before)

    def test_guide_preserves_unknown_result_and_the_original_recovery_gate(self):
        page = self.page()
        flow = page.session_state['conversation']
        page.chat_input[0].set_value('给李明转200元').run()
        authorize = flow.gateway.authorize

        def lost_reply(payload, *, principal_id):
            authorize(payload, principal_id=principal_id)
            raise TimeoutError('Test lost reply after commit')

        flow.gateway.authorize = lost_reply
        page.button(key='transfer_approve').click().run()
        self.assertEqual(flow.result['status'], 'unknown')
        before, request = self.bank_state(flow), deepcopy(flow.request)
        page.button(key='ui_menu_home').click().run()
        for key in ('bank_guide_open', 'bank_guide_topic_transfer', 'bank_guide_next',
                    'bank_guide_close', 'bank_guide_open'):
            page.button(key=key).click().run()
            self.assertFalse(page.exception)
            self.assertEqual(flow.request, request)
            self.assertEqual(flow.result['status'], 'unknown')
            self.assertTrue(page.chat_input[0].disabled)
            self.assertEqual(self.bank_state(flow), before)
        page.button(key='home_service_transfer').click().run()
        next(item for item in page.button if item.label == '查询原操作结果').click().run()
        self.assertEqual(flow.result['status'], 'succeeded')
        self.assertEqual(len(self.bank_state(flow)['transfers']), 1)

    def test_home_view_shortcuts_and_ai_entry_only_navigate(self):
        page = self.page()
        flow = page.session_state['conversation']
        before = self.bank_state(flow)
        for key, tab in (('transfer', '转账与收款'), ('bills', '收支明细'), ('home', '首页')):
            if key == 'home':
                page.button(key='ui_menu_home').click().run()
            else:
                if page.session_state['home_workspace'] != '首页':
                    page.button(key='ui_menu_home').click().run()
                page.button(key='home_service_' + key).click().run()
            self.assertFalse(page.exception)
            self.assertEqual(page.session_state['home_workspace'], tab)
            self.assertEqual(self.bank_state(flow), before)
        page.button(key='home_assistant').click().run()
        self.assertFalse(page.exception)
        self.assertIn('AI银行助手', [item.value for item in page.title])
        self.assertEqual(self.bank_state(flow), before)
        page.button(key='ui_menu_home').click().run()
        self.assertEqual(page.session_state['home_workspace'], '首页')
        self.assertEqual(self.bank_state(flow), before)


if __name__ == '__main__':
    unittest.main()
