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
            'unfinished': q.get('unfinishedFlag'), 'fields': dict(fields), 'fee': format(fee, '.2f'), 'total': format(total, '.2f')}


def p2p(session, amount, phone, source, pointer, bank, description):
    if not pointer:
        raise ValueError('Получатель не выбран: сначала transfer_sbp_resolve')
    matches = [x for x in session.resolve_recipient(phone)
               if str(x.get('bank_member_id') or '') == str(bank or '')]
    if (len(matches) != 1 or not matches[0].get('masked_fio')
            or not matches[0].get('pointer_link_id')):
        raise ValueError('Банк не подтвердил выбранного получателя и маршрут')
    recipient = matches[0]
    fields = {'pointerType': client.SBP_PHONE_POINTER_TYPE,
              'pointer': client._normalize_phone(phone),
              'maskedFIO': recipient['masked_fio'], 'pointerLinkId': recipient['pointer_link_id']}
    if bank:
        fields['bankMemberId'] = bank
    if description:
        fields['message'] = description
    pack = quote(session, amount, source, 'p2p-anybank', fields)
    pack['recipient'] = recipient['masked_fio']
    pack['bank'] = recipient.get('bank_name') or bank or 'Т-Банк'
    return pack


def same_terms(a, b):
    # pointerLinkId is a short-lived resolution handle, not the destination.
    # Phone + bank + bank-returned name and every monetary term stay bound.
    def stable(p):
        p = dict(p); p['fields'] = dict(p['fields'])
        p['fields'].pop('pointerLinkId', None)
        return p
    return stable(a) == stable(b)


def verified_own_phone(session, phone):
    try:
        profile = session._call_userinfo()
        return (profile.get('phone_number_verified') is True
                and client._normalize_phone(profile.get('phone_number', ''))
                == client._normalize_phone(phone))
    except Exception:
        return False


def own_source(session, source, amount):
    from decimal import Decimal
    rows = [a for a in session.list_accounts() if str(a.get('id')) == source]
    if len(rows) != 1:
        raise ValueError('Счёт списания не подтверждён банком')
    a = rows[0]
    currency = a.get('currency')
    if isinstance(currency, dict):
        currency = currency.get('name')
    if (a.get('accountType') not in ('Current', 'Saving') or currency != 'RUB'
            or a.get('status') not in (None, 'NORM', 'ACTIVE', 'Active', 'Normal')):
        raise ValueError('Без кнопки — только собственный рублёвый счёт, без кредита')
    balance = Decimal(str((a.get('moneyAmount') or {}).get('value')))
    if not balance.is_finite() or balance < money(amount):
        raise ValueError('Недостаточно средств на выбранном счёте')


def display(pack):
    from .server import _money
    f = pack['fields']
    recipient = pack.get('recipient') or f.get('addressee') or 'Получатель'
    route = f.get('pointer') or f.get('bankAcnt') or ''
    bank = pack.get('bank') or f.get('bankName') or f.get('bankBik') or ''
    text = (f"Перевести {_money(pack['amount'], 'RUB')} → {recipient} · {bank}"
            f"\n{route} · Со счёта {pack['source']}"
            f"\nКомиссия {pack['fee']} RUB · Итого {pack['total']} RUB")
    purpose = f.get('comment') or f.get('message')
    if purpose:
        text += f"\n{purpose}"
    return text
