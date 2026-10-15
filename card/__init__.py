"""卡片规则沿用共享 v1.1；数据均为工程模拟卡，不代表 E 的正式交付。"""
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
