"""Codex独立对话验收；假模型只用于离线边界，不计真实模型通过。"""
from copy import deepcopy
import json
import unittest
from agent.model_dialogue import DialogueSession


class FakeModel:
    def __init__(self,*outputs):self.outputs=list(outputs);self.calls=[]
    def __call__(self,messages):
        self.calls.append(deepcopy(messages));value=self.outputs.pop(0)
        if isinstance(value,Exception):raise value
        return value


def proposal(intent,slots):return {'reply':'请核对业务草稿。','action':'propose','intent':intent,'slots':slots}


class DialogueTests(unittest.TestCase):
    def test_missing_followup_modification_and_context(self):
        model=FakeModel(proposal('transfer',{'account_id':'demo_account_a','payee_query':'李明'}),
            proposal('transfer',{'amount_text':'100元'}),proposal('transfer',{'amount_text':'200元'}))
        session=DialogueSession(model)
        self.assertEqual(session.submit('给李明转钱')['missing'],['amount_text'])
        self.assertEqual(session.submit('100元')['phase'],'proposal')
        last=session.submit('改成200元')
        self.assertEqual(last['fields']['payee_query'],'李明')
        self.assertEqual(last['fields']['amount_text'],'200元')
        self.assertEqual([m['role'] for m in model.calls[-1]],['system','user','assistant','user','assistant','user'])
        self.assertNotIn('executed',last['proposal'])

    def test_six_scenes_are_proposals_or_missing_information(self):
        cases=[('balance',{'account_id':'demo_account_a'}),('bill',{'account_id':'demo_account_a','period':'2026-10'}),
            ('transfer',{'account_id':'demo_account_a','payee_query':'李明','amount_text':'20元'}),
            ('products',{'product_ids':['WP-C01','WP-C02']}),('card',{'card_operation':'挂失','card_id':'CARD-E001'}),
            ('subscription',{'subscription_operation':'取消','subscription_id':'SUB-E003'}),('birthday',{'birthday':'2026-11-20'})]
        for intent,slots in cases:
            with self.subTest(intent=intent):
                snap=DialogueSession(FakeModel(proposal(intent,slots))).submit('办理这项业务')
                self.assertEqual(snap['phase'],'proposal')
                self.assertEqual(snap['proposal']['status'],'draft')

    def test_dag_sorted_and_model_completion_not_trusted(self):
        model=FakeModel({'reply':'计划草稿','action':'plan','slots':{'account_id':'demo_account_a'},'tasks':[
            {'id':'pay','intent':'transfer','slots':{'payee_query':'李明','amount_text':'20元'},'depends_on':['balance'],'status':'completed'},
            {'id':'balance','intent':'balance','slots':{},'depends_on':[]}]})
        snap=DialogueSession(model).submit('先查余额再转20元给李明')
        self.assertEqual(snap['phase'],'plan')
        self.assertEqual([t['id'] for t in snap['tasks']],['balance','pay'])
        self.assertTrue(all(t['status']=='pending' for t in snap['tasks']))

    def test_bad_graph_unknown_fields_and_fake_success_fail_closed(self):
        invalid=[{'reply':'转账成功','action':'propose','intent':'balance','slots':{'account_id':'a'}},
            {'reply':'草稿','action':'execute','intent':'balance','slots':{}},
            {'reply':'草稿','action':'propose','intent':'balance','slots':{'confirmed':'true'}},
            '{"action":"unsupported","action":"propose"}',
            {'reply':'草稿','action':'plan','tasks':[{'id':'a','intent':'balance','slots':{},'depends_on':['a']}]}]
        for value in invalid:
            with self.subTest(value=value):
                session=DialogueSession(FakeModel(value));snap=session.submit('处理请求')
                self.assertEqual(snap['phase'],'error')
                self.assertIsNone(snap['proposal']);self.assertEqual(snap['history'],[])

    def test_pause_resume_back_and_handoff_make_no_model_calls(self):
        model=FakeModel(proposal('balance',{'account_id':'demo_account_a'}),proposal('bill',{'account_id':'demo_account_a','period':'2026-10'}))
        session=DialogueSession(model)
        session.submit('查余额');session.pause();session.submit('转账')
        self.assertEqual(len(model.calls),1)
        session.resume();session.submit('改查账单');session.back()
        self.assertEqual(session.snapshot()['intent'],'balance')
        session.handoff();session.submit('转账')
        self.assertEqual(len(model.calls),2)
        session.clear();self.assertEqual(session.snapshot()['phase'],'idle')

    def test_history_is_bounded_and_error_does_not_reuse_old_proposal(self):
        model=FakeModel(*[proposal('balance',{'account_id':'demo_account_a'}) for _ in range(6)],RuntimeError('raw error'))
        session=DialogueSession(model,max_history=4)
        for _ in range(6):session.submit('查余额')
        self.assertEqual([m['role'] for m in model.calls[-1]],['system','user','assistant','user','assistant','user'])
        snap=session.submit('修改')
        self.assertEqual(snap['phase'],'error');self.assertIsNone(snap['proposal'])
        self.assertNotIn('raw error',snap['error'])

    def test_otp_or_secret_is_not_sent_to_model_or_saved_in_history(self):
        model=FakeModel()
        session=DialogueSession(model)
        self.assertEqual(session.submit('验证码是123456')['phase'],'error')
        self.assertEqual(model.calls,[])
        self.assertEqual(session.snapshot()['history'],[])

    def test_assessment_never_invents_answers(self):
        snap=DialogueSession(FakeModel(proposal('wealth_assess',{'questionnaire_version':'wpq_v1'}))).submit('我比较保守，帮我评估')
        self.assertEqual(snap['phase'],'clarify');self.assertIn('answers',snap['missing'])

    def test_semantic_operation_alias_and_explicit_green_comparison(self):
        snap=DialogueSession(FakeModel(proposal('subscription',{'subscription_operation':'cancel','subscription_id':'SUB-E003'}))).submit('取消SUB-E003')
        self.assertEqual(snap['fields']['subscription_operation'],'取消')
        value={'reply':'需要确认这两个产品吗','action':'clarify','intent':'products','slots':{'product_ids':['WP-C01','WP-C02']}}
        snap=DialogueSession(FakeModel(value)).submit('比较WP-C01和WP-C02，不要购买')
        self.assertEqual(snap['phase'],'proposal')
        for text in ['比较产品','不要比较WP-C01和WP-C02','比较WP-C010和WP-C02']:
            snap=DialogueSession(FakeModel(value)).submit(text)
            self.assertEqual(snap['phase'],'clarify')
