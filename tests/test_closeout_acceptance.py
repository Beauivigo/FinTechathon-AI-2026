"""F2独立验收：实际Streamlit页面、确定性模型响应、真实临时模拟账本。"""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.ui_support import AppTest
from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from shared import ServiceError
from tests.test_model_dialogue import proposal
from agent.model_dialogue import INTENTS

PAGE=Path(__file__).resolve().parents[1]/'app/pages/0_AI银行助手.py'


def step(key,intent,slots,deps=()):
    return dict(id=key,title=key,intent=intent,slots=slots,depends_on=list(deps))


class CloseoutPageTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=Store(Path(temp.name)/'bank.sqlite3');initialize(self.store)
        self.now=datetime(2026,10,23,3,tzinfo=timezone.utc)
        self.gateway=BankGateway(self.store,clock=lambda:self.now)
        binding=patch('app.demo.gateway',return_value=self.gateway);binding.start();self.addCleanup(binding.stop)
        self.model_patch=patch('agent.model_client.ModelClient.__call__')
        self.model=self.model_patch.start();self.addCleanup(self.model_patch.stop)
        self.app=self.page()

    def page(self):
        app=AppTest.from_file(str(PAGE),default_timeout=15).run()
        self.assertFalse(app.exception);return app

    def rows(self,sql,args=()):
        with self.store.connection() as c:return [dict(row) for row in c.execute(sql,args)]

    def balance(self):
        row=self.rows("SELECT balance_minor,available_minor,reserved_minor FROM accounts WHERE account_id='demo_account_a'")[0]
        self.assertEqual(row['balance_minor'],row['available_minor']+row['reserved_minor'])
        return tuple(row.values())

    def send(self,text,value,*,new=True):
        if new and self.app.session_state['model_dialogue'].snapshot()['phase']!='idle':
            self.app.button(key='bank_model_clear').click().run()
        value=deepcopy(value)
        # 模拟模型从已提供的单一可信账户上下文填入账户，与真实调用协议一致。
        if value.get('action')=='plan':value.setdefault('slots',{})['account_id']='demo_account_a'
        elif 'account_id' in INTENTS.get(value.get('intent'),()):value.setdefault('slots',{}).setdefault('account_id','demo_account_a')
        self.model.return_value=json.dumps(value,ensure_ascii=False)
        self.app.text_area(key='bank_model_input').set_value(text)
        self.app.button(key='bank_model_send').click().run()
        self.assertFalse(self.app.exception)
        return self.app.session_state['model_bank_flow']

    def prepare(self):
        self.app.button(key='model_prepare_step').click().run()
        self.assertFalse(self.app.exception)
        return self.app.session_state['model_bank_flow']

    def approve(self):
        self.app.button(key='action_approve').click().run()
        self.assertFalse(self.app.exception)
        return self.app.session_state['model_bank_flow']

    def test_six_scenes_from_dialogue_through_page_gates_to_actual_ledger(self):
        # 转账：自然语言草稿到真实模拟扣款，重绘不重扣。
        self.send('给李明转20元',proposal('transfer',{'payee_query':'李明','amount_text':'20元'}))
        self.assertEqual(self.balance(),(200000,200000,0))
        self.assertEqual(self.prepare().result['status'],'awaiting_confirmation')
        self.approve();self.app.run();self.app.run()
        self.assertEqual(self.balance(),(198000,198000,0))
        self.assertEqual(len(self.rows('SELECT * FROM transfers')),1)
        # 生日：真实预留回执引用，独立确认/强验证，释放剩余预算。
        self.send('预留357元给生日，买199元鲜花，再释放剩余预算',dict(action='plan',tasks=[
            step('reserve','budget_reserve',{'amount_text':'357元','purpose':'生日预算'}),
            step('order','merchant_order',{'reservation_id':'@reserve.reservation_id','item_id':'ITEM-BD-FLOWER-01',
                'amount_text':'199元','deliver_on':'2026-11-20','delivery_address_id':'ADDR-BD-01'},['reserve']),
            step('release','budget_release',{'reservation_id':'@reserve.reservation_id'},['order'])]))
        self.prepare();self.approve();self.assertEqual(self.balance(),(198000,162300,35700))
        self.assertEqual(self.prepare().result['status'],'awaiting_verification')
        self.approve();self.assertEqual(self.balance(),(178100,162300,15800))
        self.prepare();self.approve();self.assertEqual(self.balance(),(178100,178100,0))
        self.assertEqual(len(self.gateway.list_orders(principal_id='demo_user_a')),1)
        # 账单：转账不算消费，鲜花消费按整数分精确核对。
        self.send('查2026年10月消费账单',proposal('bill',{'period':'2026-10'}))
        bill=self.app.session_state['model_runner'].receipts['t1']['data']
        self.assertEqual(bill['total_expense_minor'],19900)
        self.assertIn({'category':'鲜花','amount_minor':19900},bill['categories'])
        # 理财：明确逐题选择，使用真实风险档案回执，红门槛申购与赎回。
        answers={f'q{i}':f'q{i}_'+('c' if i<=3 else 'b') for i in range(1,6)}
        self.send('按明确问卷选项评估后购买WP-C02 500元',dict(action='plan',tasks=[
            step('assess','wealth_assess',{'questionnaire_version':'wpq_v1','answers':answers}),
            step('buy','wealth_purchase',{'product_id':'WP-C02','amount_text':'500元','risk_profile_id':'@assess.risk_profile_id'},['assess'])]))
        self.prepare();self.assertEqual(self.prepare().result['status'],'awaiting_verification')
        self.approve();self.assertEqual(self.balance(),(128100,128100,0))
        holding=self.rows("SELECT * FROM holdings WHERE product_id='WP-C02'")[0]
        self.assertEqual(holding['units'],'499.5000')
        self.now+=timedelta(days=7)
        self.send('赎回这笔持仓200份',proposal('wealth_redeem',{'holding_id':holding['holding_id'],'units_text':'200'}))
        self.assertEqual(self.prepare().result['status'],'awaiting_verification')
        self.approve();self.assertEqual(self.balance(),(148100,148100,0))
        # 卡片：挂失后解挂保持冻结，再单独解冻。冻结/解挂都不改账户余额。
        for text,op,state in [('卡丢了，挂失CARD-E001','挂失','lost'),('找回CARD-E001，解挂','解挂','frozen'),('解冻CARD-E001','解冻','active')]:
            self.send(text,proposal('card',{'card_id':'CARD-E001','card_operation':op}))
            self.prepare();flow=self.approve();self.assertEqual(flow.result['data']['card_state'],state)
        self.assertEqual(self.balance(),(148100,148100,0))
        # 订阅：核实不扣款，续费单独强验证，取消不退已发生消费。
        self.send('核实SUB-E001为订阅',proposal('subscription',{'subscription_id':'SUB-E001','subscription_operation':'核实'}))
        self.prepare();self.approve();self.assertEqual(self.balance(),(148100,148100,0))
        with self.store.connection(write=True) as c:c.execute("UPDATE subscriptions SET next_charge_date='2026-10-30' WHERE subscription_id='SUB-E001'")
        self.send('本期续费SUB-E001',proposal('subscription',{'subscription_id':'SUB-E001','subscription_operation':'续费'}))
        self.assertEqual(self.prepare().result['status'],'awaiting_verification')
        self.approve();self.assertEqual(self.balance(),(145600,145600,0))
        self.send('取消SUB-E001后续订阅',proposal('subscription',{'subscription_id':'SUB-E001','subscription_operation':'取消'}))
        self.prepare();self.assertEqual(self.approve().result['data']['subscription_state'],'cancelled')
        self.assertEqual(self.balance(),(145600,145600,0))
        self.assertEqual(len(self.rows('SELECT * FROM subscription_charges')),1)
        self.assertTrue(
            any('模型配置已保存，连接状态以实际调用为准' in item.value for item in self.app.caption)
            or any('尚未配置模型' in item.value for item in self.app.warning)
        )
        self.assertFalse(any(op['status'] in {'unknown','awaiting_confirmation','awaiting_verification'}
            for op in self.gateway.list_operations(principal_id='demo_user_a')))

    def test_followup_rejection_and_dependency_do_not_move_money(self):
        self.send('给李明转点钱',proposal('transfer',{'payee_query':'李明'}))
        self.assertEqual(self.app.session_state['model_dialogue'].snapshot()['phase'],'clarify')
        self.send('20元',proposal('transfer',{'amount_text':'20元'}),new=False)
        self.prepare();self.app.button(key='action_reject').click().run()
        self.assertFalse(self.app.exception);self.assertEqual(self.balance(),(200000,200000,0))
        self.assertTrue(self.app.session_state['model_runner'].paused)
        self.assertEqual(self.rows('SELECT * FROM transfers'),[])

    def test_lost_commit_receipt_recovered_in_fresh_page_without_duplicate_payment(self):
        flow=self.send('给李明转1200元',proposal('transfer',{'payee_query':'李明','amount_text':'1200元'}))
        self.prepare();original_key=flow.request['idempotency_key'];authorize=self.gateway.authorize
        def lost(*args,**kwargs):
            authorize(*args,**kwargs);raise ServiceError('RESULT_UNKNOWN','模拟提交后回执丢失')
        with patch.object(self.gateway,'authorize',side_effect=lost):self.approve()
        self.assertEqual(flow.result['status'],'unknown');self.assertEqual(self.balance(),(80000,80000,0))
        # The AI stays usable for reading/asking; only new business submission is gated.
        self.assertFalse(self.app.text_area(key='bank_model_input').disabled)
        self.assertFalse(self.app.button(key='bank_model_send').disabled)
        self.assertTrue(any(b.key in {'model_pending_open','action_query'} for b in self.app.button))
        calls=self.model.call_count
        self.app=self.page();self.app.button(key='action_recover').click().run()
        recovered=self.app.session_state['model_bank_flow']
        self.assertEqual(recovered.request['idempotency_key'],original_key)
        self.assertEqual(recovered.result['status'],'succeeded')
        self.app.button(key='action_query').click().run();self.app.run()
        self.assertEqual(self.model.call_count,calls);self.assertEqual(self.balance(),(80000,80000,0))
        self.assertEqual(len(self.rows('SELECT * FROM transfers')),1)

    def test_birthday_pause_handoff_then_release_unused_budget(self):
        self.send('预留357元生日预算，再买199元鲜花',dict(action='plan',tasks=[
            step('reserve','budget_reserve',{'amount_text':'357元','purpose':'生日'}),
            step('order','merchant_order',{'reservation_id':'@reserve.reservation_id','item_id':'ITEM-BD-FLOWER-01',
                'amount_text':'199元','deliver_on':'2026-11-20','delivery_address_id':'ADDR-BD-01'},['reserve'])]))
        self.prepare();self.approve();runner=self.app.session_state['model_runner']
        reservation=runner.receipts['reserve']['data']['reservation_id']
        self.app.button(key='bank_model_pause').click().run();self.app.run()
        self.assertTrue(runner.paused);self.assertEqual(self.balance(),(200000,164300,35700))
        self.app.button(key='bank_model_resume').click().run();self.assertFalse(runner.paused)
        self.app.button(key='bank_model_handoff').click().run();calls=self.model.call_count
        self.assertTrue(self.app.button(key='bank_model_send').disabled)
        self.assertTrue(any(b.key.startswith('bank_model_manual_') for b in self.app.button))
        self.assertEqual(self.model.call_count,calls);self.assertEqual(self.gateway.list_orders(principal_id='demo_user_a'),[])
        self.send('释放刚才未用的生日预算',proposal('budget_release',{'reservation_id':reservation}))
        self.prepare();self.approve();self.assertEqual(self.balance(),(200000,200000,0))
        self.assertEqual(self.gateway.list_orders(principal_id='demo_user_a'),[])

    def test_page_five_model_failures_lock_and_manual_new_task_recovers(self):
        self.model.return_value='invalid JSON'
        self.app.text_area(key='bank_model_input').set_value('查询余额')
        for _ in range(5):self.app.button(key='bank_model_send').click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state['model_dialogue'].snapshot()['phase'],'locked')
        self.assertTrue(self.app.button(key='bank_model_send').disabled)
        self.assertTrue(any(b.key=='bank_model_clear' for b in self.app.button))
        self.assertFalse(any(b.key in {'bank_model_back','bank_model_pause','bank_model_resume','bank_model_handoff'} for b in self.app.button))
        self.assertEqual(self.model.call_count,5)
        self.assertEqual(self.rows('SELECT * FROM operations'),[])
        self.send('查询余额',proposal('balance',{}))
        self.assertEqual(self.app.session_state['model_runner'].receipts['t1']['data']['balance_minor'],200000)

    def test_five_invalid_proofs_persist_after_page_restart_and_do_not_debit(self):
        flow=self.send('给李明转1200元',proposal('transfer',{'payee_query':'李明','amount_text':'1200元'}))
        self.prepare();n=flow.result['next_action']
        payload=dict(schema_version='1.1',challenge_id=n['challenge_id'],operation_digest=n['operation_digest'],decision='approve',proof_token='wrong-proof')
        for _ in range(4):
            with self.assertRaises(ServiceError):self.gateway.authorize(payload,principal_id='demo_user_a')
        self.assertEqual(self.gateway.authorize(payload,principal_id='demo_user_a')['status'],'blocked')
        self.app=self.page();self.app.button(key='action_recover').click().run()
        recovered=self.app.session_state['model_bank_flow']
        self.assertEqual(recovered.result['error']['code'],'SAFETY_LOCKED')
        self.assertEqual(self.balance(),(200000,200000,0));self.assertEqual(self.rows('SELECT * FROM transfers'),[])


if __name__=='__main__':unittest.main()
