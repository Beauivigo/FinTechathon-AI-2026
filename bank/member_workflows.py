"""卡片申请受理、订阅取消和本地续费提醒。没有自动发卡或扣费授权。"""
from datetime import datetime, time, timezone, timedelta
import json
from uuid import uuid4
from shared import ServiceError
from .services import owned_account, require_principal
from .store import utc_timestamp

ACTIONS=frozenset({'card.apply','subscription.cancel','subscription.remind'})
SHANGHAI=timezone(timedelta(hours=8))


def binding(c,request,principal,now):
    p=request['params']
    if request['action']=='card.apply':
        owned_account(c,p['account_id'],principal)
        if c.execute("SELECT 1 FROM cards WHERE account_id=? AND card_state='active'",(p['account_id'],)).fetchone():
            raise ServiceError('INVALID_STATE','账户已有正常卡片。')
        if c.execute("SELECT 1 FROM card_applications WHERE account_id=? AND state='pending'",(p['account_id'],)).fetchone():
            raise ServiceError('INVALID_STATE','已有处理中申请，请勿换键重复申请。')
        return [p['account_id']]
    row=c.execute('''SELECT subscriptions.* FROM subscriptions JOIN accounts USING(account_id)
        WHERE subscription_id=? AND principal_id=?''',(p['subscription_id'],principal)).fetchone()
    if row is None: raise ServiceError('FORBIDDEN','无权处理该订阅。')
    if row['subscription_state']!='active': raise ServiceError('INVALID_STATE','仅已核实的有效订阅可以取消或登记提醒。')
    if request['action']=='subscription.remind':
        when=datetime.fromisoformat(p['remind_at'].replace('Z','+00:00'))
        due=datetime.combine(datetime.fromisoformat(row['next_charge_date']).date(),time.min,SHANGHAI)
        if when<now or when>=due: raise ServiceError('INVALID_REQUEST','提醒必须不早于当前时间且早于扣费日。')
        existing=c.execute('SELECT * FROM subscription_reminders WHERE subscription_id=? AND charge_date=?',(row['subscription_id'],row['next_charge_date'])).fetchone()
        if existing and (existing['remind_at']!=utc_timestamp(when.isoformat()) or existing['state']=='cancelled'):
            raise ServiceError('INVALID_STATE','本期已有不同提醒，不自动覆盖。')
    return [row['subscription_id'],row['account_id'],row['subscription_state'],row['next_charge_date'],row['revision']]


def apply(store,c,request,principal,now):
    store.require_write_transaction(c);bound=binding(c,request,principal,now);p=request['params']
    if request['action']=='card.apply':
        app='application_'+uuid4().hex
        c.execute("INSERT INTO card_applications VALUES (?,?,?,?,'pending')",(app,p['account_id'],principal,request['idempotency_key']))
        return dict(application_id=app)
    if request['action']=='subscription.cancel':
        c.execute("UPDATE subscriptions SET subscription_state='cancelled',revision=revision+1 WHERE subscription_id=?",(p['subscription_id'],))
        c.execute("UPDATE subscription_reminders SET state='cancelled' WHERE subscription_id=? AND state='scheduled'",(p['subscription_id'],))
        return dict(subscription_id=p['subscription_id'],subscription_state='cancelled')
    existing=c.execute('SELECT reminder_id FROM subscription_reminders WHERE subscription_id=? AND charge_date=?',(p['subscription_id'],bound[3])).fetchone()
    rid=existing[0] if existing else 'reminder_'+uuid4().hex
    if existing is None:
        c.execute("INSERT INTO subscription_reminders VALUES (?,?,?,?,?,'scheduled')",(rid,p['subscription_id'],principal,bound[3],utc_timestamp(p['remind_at'])))
    return dict(reminder_id=rid)


def run_reminders(store,principal,now):
    """可信页面手动到期检查；通知只在本地持久保存，不发送外部消息。"""
    with store.connection(write=True) as c:
        require_principal(c,principal)
        rows=c.execute("SELECT * FROM subscription_reminders WHERE principal_id=? AND state='scheduled' AND remind_at<=?",(principal,utc_timestamp(now.isoformat()))).fetchall()
        for row in rows:
            sub=c.execute('SELECT * FROM subscriptions WHERE subscription_id=?',(row['subscription_id'],)).fetchone()
            state='sent' if sub and sub['subscription_state']=='active' and sub['next_charge_date']==row['charge_date'] else 'cancelled'
            c.execute('UPDATE subscription_reminders SET state=? WHERE reminder_id=?',(state,row['reminder_id']))
            c.execute('''INSERT INTO audit_events(principal_id,trace_id,action,outcome,occurred_at,details_json) VALUES (?,?,?,?,?,?)''',
                      (principal,row['reminder_id'],'subscription.remind',state,utc_timestamp(now.isoformat()),json.dumps(dict(reminder_id=row['reminder_id']))))
        return [dict(r) for r in c.execute('''SELECT r.*,s.merchant_name FROM subscription_reminders r JOIN subscriptions s USING(subscription_id)
            JOIN accounts a ON a.account_id=s.account_id WHERE r.principal_id=? AND a.principal_id=? ORDER BY r.remind_at''',(principal,principal))]
