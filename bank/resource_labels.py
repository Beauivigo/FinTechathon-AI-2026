"""Display names for the existing E sample cards; identifiers remain authoritative."""

CARD_NAMES = {'CARD-E001': '白金卡', 'CARD-E002': '普通卡', 'CARD-E003': '挂失卡'}


def card_name(card_id):
    # Unknown resources stay distinguishable; never invent a bank/card number.
    return CARD_NAMES.get(card_id, '银行卡 ' + card_id)
