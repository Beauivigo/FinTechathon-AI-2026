"""卡片规则沿用共享 v1.1；新库采用 E 模拟资料，旧库保留工程卡。"""
from shared import ContractError, ServiceError
from shared.services import validate_card_change

TARGETS = {'card.freeze': 'frozen', 'card.unfreeze': 'active',
           'card.report_loss': 'lost', 'card.release_loss': 'frozen'}


def checked_card(connection, card_id, principal_id, action):
    row = connection.execute('''SELECT cards.* FROM cards JOIN accounts USING(account_id)
        WHERE card_id=? AND principal_id=?''', (card_id, principal_id)).fetchone()
    if row is None:
        raise ServiceError('FORBIDDEN', '无权操作此卡片。')
    try:
        validate_card_change(action, row['card_state'], TARGETS[action])
    except (ContractError, KeyError) as exc:
        raise ServiceError('INVALID_STATE', '当前卡片状态不允许此操作。') from exc
    return row
