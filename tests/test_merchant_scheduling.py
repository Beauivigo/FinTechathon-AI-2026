"""review 独立验收：真实网关/临时库，不使用委派自测结果。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4
import unittest

from bank.gateway import BankGateway
from bank.merchant_scheduler import MerchantScheduler
from bank.scheduler import Scheduler
from shared import ServiceError, adapt_result
import json
from tests import test_merchant as helpers


class MerchantSchedulingTests(unittest.TestCase):
    setUp = helpers.MerchantTests.setUp
    req = helpers.MerchantTests.req
    send = helpers.MerchantTests.send
    approve = helpers.MerchantTests.approve
    reserve = helpers.MerchantTests.reserve
    order = helpers.MerchantTests.order
    release = helpers.MerchantTests.release
    balance = helpers.MerchantTests.balance

    def register(self, rid=None, **kwargs):
        rid = rid or self.reserve()
        return MerchantScheduler(self.g).register(self.order(rid)['params'],
            (self.now+timedelta(hours=1)).isoformat(), uuid4().hex,
            principal_id='demo_user_a', confirmed=True, **kwargs)

    def due(self, task):
        self.now += timedelta(hours=1)
        MerchantScheduler(self.g).run_once(principal_id='demo_user_a')
        return self.send(task['spec']['payload'])

    def cancel(self, task):
        return MerchantScheduler(self.g).cancel(task['task_id'], principal_id='demo_user_a', confirmed=True)

    def test_registration_confirmation_replay_conflict_ownership(self):
        rid=self.reserve(); scheduler=MerchantScheduler(self.g); params=self.order(rid)['params']
        at=(self.now+timedelta(hours=1)).isoformat(); key=uuid4().hex
        with self.assertRaises(ServiceError): scheduler.register(params,at,key,principal_id='demo_user_a')
        task=scheduler.register(params,at,key,principal_id='demo_user_a',confirmed=True)
        self.assertEqual(task,scheduler.register(params,at,key,principal_id='demo_user_a',confirmed=True))
        with self.assertRaises(ServiceError): scheduler.register(params,at,key,principal_id='demo_user_li',confirmed=True)
        with self.assertRaises(ServiceError): scheduler.register(params|{'amount_minor':1},at,key,principal_id='demo_user_a',confirmed=True)
        self.assertEqual(self.balance(),(200000,150000,50000))
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM operations WHERE action='merchant.order'").fetchone()[0],0)

    def test_not_due_then_new_authorization_and_partial_release(self):
        rid=self.reserve(); task=self.register(rid); scheduler=MerchantScheduler(self.g)
        self.assertEqual(scheduler.run_once(principal_id='demo_user_a'),[])
        self.assertEqual(self.release(rid)['status'],'failed')
        pending=self.due(task)
        self.assertEqual(pending['status'],'awaiting_verification')
        self.assertEqual(self.balance(),(200000,150000,50000))
        self.assertEqual(scheduler.list_tasks(principal_id='demo_user_a')[0]['state'],'dispatched')
        self.assertEqual(self.approve(pending)['status'],'succeeded')
        self.assertEqual(self.approve(self.release(rid))['status'],'succeeded')
        self.assertEqual(self.balance(),(180100,180100,0))

    def test_cancel_scheduled_blocks_late_execution_and_releases(self):
        rid=self.reserve(); task=self.register(rid)
        self.assertEqual(self.cancel(task)['state'],'cancelled')
        self.assertEqual(self.cancel(task)['state'],'cancelled')
        self.assertEqual(self.approve(self.release(rid))['status'],'succeeded')
        self.assertEqual(self.due(task)['status'],'failed')
        self.assertEqual(self.balance(),(200000,200000,0))

    def test_cancel_pending_rejects_old_proof(self):
        rid=self.reserve(); task=self.register(rid); pending=self.due(task)
        n=pending['next_action']; proof=self.g.issue_demo_proof(n['challenge_id'],principal_id='demo_user_a')
        self.cancel(task)
        late=self.g.authorize(dict(schema_version='1.1',challenge_id=n['challenge_id'],
            operation_digest=n['operation_digest'],decision='approve',proof_token=proof),principal_id='demo_user_a')
        self.assertEqual(late['status'],'cancelled')
        self.assertEqual(self.approve(self.release(rid))['status'],'succeeded')
        self.assertEqual(self.balance(),(200000,200000,0))

    def test_committed_cannot_cancel_restart_no_duplicate(self):
        task=self.register(); pending=self.due(task); done=self.approve(pending)
        with self.assertRaises(ServiceError): self.cancel(task)
        self.g=BankGateway(self.store,clock=lambda:self.now)
        MerchantScheduler(self.g).run_once(principal_id='demo_user_a')
        self.assertEqual(self.send(task['spec']['payload']),done)
        with self.store.connection() as c: self.assertEqual(c.execute('SELECT count(*) FROM merchant_orders').fetchone()[0],1)

    def test_late_check_does_not_inherit_transfer_five_minute_expiry(self):
        task=self.register(); self.now+=timedelta(days=1)
        MerchantScheduler(self.g).run_once(principal_id='demo_user_a')
        pending=self.send(task['spec']['payload'])
        self.assertEqual(pending['status'],'awaiting_verification')
        self.assertEqual(self.balance(),(200000,150000,50000))

    def test_expired_authorization_never_charges_or_resurrects(self):
        task=self.register(); pending=self.due(task); self.now+=timedelta(minutes=10)
        with self.assertRaises(ServiceError): self.approve(pending)
        result=self.send(task['spec']['payload'])
        self.assertEqual(result['error']['code'],'AUTH_EXPIRED')
        self.assertEqual(self.balance(),(200000,150000,50000))
        self.assertEqual(self.approve(self.release(task['spec']['payload']['params']['reservation_id']))['status'],'succeeded')

    def test_card_change_before_due_and_after_challenge(self):
        task=self.register()
        with self.store.connection(write=True) as c: c.execute("UPDATE cards SET card_state='frozen',revision=revision+1 WHERE card_id='CARD-E001'")
        self.assertEqual(self.due(task)['status'],'failed')
        with self.store.connection(write=True) as c: c.execute("UPDATE cards SET card_state='active',revision=revision+1 WHERE card_id='CARD-E001'")
        task=self.register(); pending=self.due(task)
        with self.store.connection(write=True) as c: c.execute("UPDATE cards SET limit_minor=1,revision=revision+1 WHERE card_id='CARD-E001'")
        self.assertEqual(self.approve(pending)['error']['code'],'AUTH_INVALID')

    def test_unknown_reply_blocks_cancel_release_recovers_same_key(self):
        rid=self.reserve(); task=self.register(rid); self.now+=timedelta(hours=1)
        scheduler=MerchantScheduler(self.g); original=self.g.execute
        def lost(request,**kwargs):
            value=original(request,**kwargs)
            if request['action']=='merchant.order': raise OSError('模拟受理后回执丢失')
            return value
        with patch.object(self.g,'execute',side_effect=lost): scheduler.run_once(principal_id='demo_user_a')
        self.assertEqual(scheduler.list_tasks(principal_id='demo_user_a')[0]['state'],'unknown')
        self.assertEqual(self.approve(self.release(rid))['status'],'succeeded')
        self.assertEqual(self.balance(),(200000,180100,19900))
        scheduler.run_once(principal_id='demo_user_a')
        self.assertEqual(scheduler.list_tasks(principal_id='demo_user_a')[0]['state'],'dispatched')
        self.assertEqual(self.send(task['spec']['payload'])['status'],'awaiting_verification')

    def test_concurrent_dispatch_and_cancel_approve_serialized(self):
        task=self.register(); self.now+=timedelta(hours=1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _:MerchantScheduler(self.g).run_once(principal_id='demo_user_a'),range(2)))
        pending=self.send(task['spec']['payload']); n=pending['next_action']
        proof=self.g.issue_demo_proof(n['challenge_id'],principal_id='demo_user_a')
        def cancel():
            try: self.cancel(task)
            except ServiceError: pass
        def approve():
            return self.g.authorize(dict(schema_version='1.1',challenge_id=n['challenge_id'],operation_digest=n['operation_digest'],decision='approve',proof_token=proof),principal_id='demo_user_a')
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(cancel),pool.submit(approve)]
            for f in futures: f.result()
        result=self.send(task['spec']['payload'])
        self.assertIn(result['status'],('succeeded','cancelled'))
        self.assertEqual(self.balance()[0],180100 if result['status']=='succeeded' else 200000)

    def test_transfer_runner_never_dispatches_merchant(self):
        task=self.register(); self.now+=timedelta(hours=1)
        self.assertEqual(Scheduler(self.g).run_once(principal_id='demo_user_a'),[])
        self.assertEqual(MerchantScheduler(self.g).list_tasks(principal_id='demo_user_a')[0]['state'],'scheduled')

    def test_persisted_unknown_cannot_cancel_and_retains_occupied_amount(self):
        rid=self.reserve(); task=self.register(rid); self.due(task); request=task['spec']['payload']
        result=adapt_result(request,dict(status='unknown',error=dict(code='RESULT_UNKNOWN',message='模拟待核实')),source='gateway')
        with self.store.connection(write=True) as c:
            c.execute("UPDATE operations SET result_json=? WHERE action='merchant.order' AND idempotency_key=?",(json.dumps(result),request['idempotency_key']))
        with self.assertRaises(ServiceError): self.cancel(task)
        self.assertEqual(self.approve(self.release(rid))['status'],'succeeded')
        self.assertEqual(self.balance(),(200000,180100,19900))
        self.assertEqual(self.release(rid)['status'],'failed')

    def test_multiple_fixed_orders_share_reservation(self):
        rid=self.reserve(); first=self.register(rid); second=self.register(rid)
        self.due(first)
        for task in (first,second): self.assertEqual(self.approve(self.send(task['spec']['payload']))['status'],'succeeded')
        self.assertEqual(self.approve(self.release(rid))['status'],'succeeded')
        self.assertEqual(self.balance(),(160200,160200,0))

    def test_registration_time_delivery_and_long_advance(self):
        rid=self.reserve(); scheduler=MerchantScheduler(self.g)
        params=self.order(rid,deliver_on='2027-01-01')['params']
        for when in (self.now,self.now-timedelta(seconds=1)):
            with self.assertRaises(ServiceError):
                scheduler.register(params,when.isoformat(),uuid4().hex,principal_id='demo_user_a',confirmed=True)
        task=scheduler.register(params,(self.now+timedelta(days=31)).isoformat(),uuid4().hex,principal_id='demo_user_a',confirmed=True)
        self.assertEqual(task['state'],'scheduled')
        with self.assertRaises(ServiceError):
            scheduler.register(params,'2027-01-01T00:00:00+08:00',uuid4().hex,principal_id='demo_user_a',confirmed=True)
