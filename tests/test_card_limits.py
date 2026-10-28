from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import unittest
from bank.gateway import BankGateway
from bank.seed import initialize
from bank.store import Store
from shared import adapt_request, ServiceError
from tests.ui_support import AppTest


class CardLimitTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name)/'bank.db'); initialize(self.store)
        self.now = datetime(2026,10,17,tzinfo=timezone.utc)
        self.g = BankGateway(self.store, clock=lambda:self.now)

    def request(self, limit=12345, card='CARD-E001'):
        return adapt_request('card.set_limit',dict(card_id=card,limit_minor=limit),
            request_id='r',trace_id='t',idempotency_key=uuid4().hex)

    def start(self, req):
        return self.g.execute(req, principal_id='demo_user_a')

    def finish(self, r, decision='approve', proof=True):
        n = r['next_action']
        p = dict(schema_version='1.1', challenge_id=n['challenge_id'],operation_digest=n['operation_digest'],decision=decision)
        if proof and decision=='approve':
            p['proof_token']=self.g.issue_demo_proof(n['challenge_id'],principal_id='demo_user_a')
        return self.g.authorize(p,principal_id='demo_user_a')

    def row(self, card='CARD-E001'):
        with self.store.connection() as c:
            return dict(c.execute('SELECT * FROM cards WHERE card_id=?',(card,)).fetchone())

    def test_boundaries_all_states_no_money_and_replay(self):
        for card in ('CARD-E001','CARD-E002','CARD-E003'):
            state=self.row(card)['card_state']
            for limit in (0,1000000):
                req=self.request(limit,card); p=self.start(req)
                self.assertEqual(p['status'],'awaiting_verification')
                r=self.finish(p); self.assertEqual(r['status'],'succeeded')
                initialize(self.store); self.g=BankGateway(self.store,clock=lambda:self.now)
                self.assertEqual(self.start(req),r)
                self.assertEqual(self.row(card)['limit_minor'],limit)
                self.assertEqual(self.row(card)['card_state'],state)
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ledger_entries').fetchone()[0],0)
            self.assertEqual(c.execute("SELECT balance_minor FROM accounts WHERE account_id='demo_account_a'").fetchone()[0],200000)

    def test_invalid_values_and_ownership(self):
        self.assertEqual(self.start(self.request(1000001))['error']['code'],'INVALID_REQUEST')
        for value in (-1,1.1,True,'5'):
            req=self.request(); req['params']['limit_minor']=value
            with self.assertRaises(ServiceError):self.start(req)
        self.assertEqual(self.g.execute(self.request(),principal_id='demo_user_li')['error']['code'],'FORBIDDEN')
        self.assertEqual(self.row()['limit_minor'],50000)

    def test_reject_missing_proof_expiry_conflict(self):
        req=self.request(); p=self.start(req)
        with self.assertRaises(ServiceError):self.finish(p,proof=False)
        altered=req|dict(params=dict(card_id='CARD-E001',limit_minor=2))
        self.assertEqual(self.start(altered)['error']['code'],'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.finish(p,'reject')['status'],'cancelled')
        pending=self.start(self.request()); self.now+=timedelta(minutes=10)
        self.assertEqual(self.finish(pending,proof=False)['error']['code'],'AUTH_EXPIRED')
        self.assertEqual(self.row()['limit_minor'],50000)

    def test_changed_limit_state_and_owner_invalidate(self):
        for sql in ("UPDATE cards SET limit_minor=77 WHERE card_id='CARD-E001'",
                    "UPDATE cards SET card_state='frozen' WHERE card_id='CARD-E001'",
                    "UPDATE cards SET account_id='demo_account_li' WHERE card_id='CARD-E001'"):
            p=self.start(self.request())
            with self.store.connection(write=True) as c:c.execute(sql)
            self.assertIn(self.finish(p)['error']['code'],('AUTH_INVALID','FORBIDDEN'))

    def test_audit_failure_rolls_back(self):
        p=self.start(self.request()); original=self.g._audit
        def fail(*args,**kwargs):
            if args[3]=='authorization_consumed':raise ServiceError('UPSTREAM_ERROR','injected')
            return original(*args,**kwargs)
        with patch.object(self.g,'_audit',side_effect=fail):
            self.assertEqual(self.finish(p)['status'],'failed')
        self.assertEqual(self.row()['limit_minor'],50000)

    def test_page_refresh_recovery(self):
        root=Path(__file__).resolve().parents[1]
        with patch('bank.seed.demo_path',return_value=self.store.path),patch('app.demo.gateway',return_value=self.g):
            page=AppTest.from_file(str(root/'app/main.py')).run()
            page.switch_page('pages/3_卡片管理.py').run()
            page.selectbox(key='card_action').select('card.set_limit').run()
            page.text_input(key='card_limit_CARD-E001').set_value('0').run()
            page.button(key='card_submit').click().run()
            self.assertFalse(page.exception);self.assertEqual(self.row()['limit_minor'],50000)
            page=AppTest.from_file(str(root/'app/main.py')).run()
            page.switch_page('pages/3_卡片管理.py').run()
            page.button(key='action_recover').click().run()
            page.button(key='action_approve').click().run()
            self.assertFalse(page.exception);self.assertEqual(self.row()['limit_minor'],0)
