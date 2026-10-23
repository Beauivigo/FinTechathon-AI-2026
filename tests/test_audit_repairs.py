"""Independent regression for audit AUD-01 and AUD-02."""
import unittest
from types import SimpleNamespace
from copy import deepcopy
from agent.controller import reconcile_session_flows
from agent.model_bank import BankPlanRunner, NeedsInput
from tests import test_model_bank as fixtures
from tests.test_model_bank import task

class AuditRepairTests(unittest.TestCase):
    setUp = fixtures.ModelBankTests.setUp

    def test_all_entry_pairs_block_and_unlock_after_reconciliation(self):
        keys = ('conversation','action_flow','model_bank_flow')
        for source in keys:
            for target in keys:
                if source == target: continue
                state = {k:SimpleNamespace(needs_reconciliation=False,result=None,external_blocked=False) for k in keys}
                state[source].needs_reconciliation=True
                self.assertTrue(reconcile_session_flows(state,state[target]))
                state[source].needs_reconciliation=False
                self.assertFalse(reconcile_session_flows(state,state[target]))

    def test_revision_preserves_paid_step_and_original_operation(self):
        steps=[task('transfer',{'payee_query':'李明','amount_text':'20元'},key='pay'),task('card',{'card_id':'CARD-E001','card_operation':'冻结'},key='card',deps=['pay'])]
        old=BankPlanRunner(self.flow,steps)
        old.advance(allow_write=True);self.flow.decide(True);old._sync()
        key=old.tasks[0]['operation_key']
        steps[1]['fields']['card_operation']='挂失'
        revised=BankPlanRunner(self.flow,steps);revised.inherit_completed(old)
        revised.advance(allow_write=True)
        self.assertEqual(self.flow.request['action'],'card.report_loss')
        self.assertEqual(revised.tasks[0]['operation_key'],key)
        with self.flow.gateway.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM transfers').fetchone()[0],1)

    def test_completed_step_cannot_be_changed_removed_or_renumbered(self):
        steps=[task('transfer',{'payee_query':'李明','amount_text':'20元'},key='pay')]
        old=BankPlanRunner(self.flow,steps);old.advance(allow_write=True);self.flow.decide(True);old._sync()
        for new in [[task('balance')],[task('transfer',{'payee_query':'李明','amount_text':'30元'},key='pay')],[task('transfer',steps[0]['fields'],key='new')]]:
            with self.assertRaises(NeedsInput):BankPlanRunner(self.flow,new).inherit_completed(old)
