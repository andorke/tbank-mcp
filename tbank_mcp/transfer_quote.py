"""Read-only preparation of exact external-transfer terms; no payment methods."""
from decimal import Decimal
from time import monotonic
from . import client
from .own_transfer import money


def quote(session, amount, source, provider, fields):
    amount = money(amount)
    if not source:
        raise ValueError('Нужен явный счёт списания')
    q = session.payment_commission({'payParameters': {
        'account': source, 'moneyAmount': client.money_amount(amount),
        'currency': 'RUB', 'paymentType': 'Transfer',
        'provider': provider, 'providerFields': fields}})
    # For a credit source the app also returns unfinishedFlag=True with fully
    # priced terms. It is not a refusal (unlike the own-account shortcut, which
    # deliberately requires a completed debit-only quote). Exact money is still
    # mandatory here, and the terms are quoted again after human confirmation.
    if not isinstance(q, dict) or not all(k in q for k in ('value', 'total')):
        raise ValueError('Банк не сообщил точную котировку перевода')
    def value(k):
        v = q.get(k)
        return v.get('value') if isinstance(v, dict) else v
    fee, total = Decimal(str(value('value'))), Decimal(str(value('total')))
    if not fee.is_finite() or fee < 0 or not total.is_finite() or total != amount + fee:
        raise ValueError('Банк не сообщил точные комиссию и итог списания')
    for key, lower in [('minAmount', True), ('maxAmount', False)]:
        if q.get(key) is not None:
            bound = Decimal(str(q[key]))
            if not bound.is_finite() or (amount < bound if lower else amount > bound):
                raise ValueError('Сумма вне лимитов банка')
    return {'amount': format(amount, '.2f'), 'source': source, 'provider': provider,
            'fields': dict(fields), 'fee': format(fee, '.2f'), 'total': format(total, '.2f')}


def p2p(session, amount, phone, source, pointer, bank, description):
    if not pointer:
        raise ValueError('Получатель не выбран: сначала transfer_sbp_resolve')
    matches = [x for x in session.resolve_recipient(phone)
               if str(x.get('pointer_link_id')) == str(pointer)
               and str(x.get('bank_member_id') or '') == str(bank or '')]
    if len(matches) != 1 or not matches[0].get('masked_fio'):
        raise ValueError('Банк не подтвердил выбранного получателя и маршрут')
    recipient = matches[0]
    fields = {'pointerType': client.SBP_PHONE_POINTER_TYPE,
              'pointer': client._normalize_phone(phone),
              'maskedFIO': recipient['masked_fio'], 'pointerLinkId': pointer}
    if bank:
        fields['bankMemberId'] = bank
    if description:
        fields['message'] = description
    pack = quote(session, amount, source, 'p2p-anybank', fields)
    pack['recipient'] = recipient['masked_fio']
    pack['bank'] = recipient.get('bank_name') or bank or 'Т-Банк'
    return pack


def display(pack):
    from .server import _money
    fields = pack['fields']
    recipient = pack.get('recipient') or fields.get('addressee') or 'Получатель'
    routing = fields.get('pointer') or fields.get('bankAcnt') or ''
    bank = pack.get('bank') or fields.get('bankName') or fields.get('bankBik') or ''
    return (f"Перевести {_money(pack['amount'], 'RUB')}\nСо счёта: {pack['source']}\n"
            f"Получатель: {recipient}\nБанк: {bank}\nРеквизиты: {routing}\n"
            f"Комиссия: {pack['fee']} RUB\nВсего: {pack['total']} RUB\n"
            f"Назначение: {fields.get('comment') or fields.get('message') or '—'}\n"
            "Одноразовое подтверждение, срок 120 секунд.")
