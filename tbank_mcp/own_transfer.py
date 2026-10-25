"""Own RUB accounts: catalogue-validated, quoted, no implicit payer or FX."""
from decimal import Decimal, InvalidOperation
import re


def money(value):
    try:
        x = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Некорректная сумма') from None
    if not x.is_finite() or x <= 0 or x != x.quantize(Decimal('.01')):
        raise ValueError('Нужна положительная сумма с точностью до копейки')
    return x


def accounts(session, source, target, amount):
    if source == target or not all(re.fullmatch(r'[0-9]{10}', x or '') for x in (source, target)):
        raise ValueError('Нужны два разных явных id собственных счетов')
    found = {}
    for a in session.list_accounts():
        if str(a.get('id')) in (source, target):
            if str(a['id']) in found:
                raise ValueError('Неоднозначный id счёта')
            found[str(a['id'])] = a
    if len(found) != 2:
        raise ValueError('Оба счёта должны присутствовать в свежем списке банка')
    for a in found.values():
        cur = a.get('currency')
        cur = cur.get('name') if isinstance(cur, dict) else cur
        if a.get('accountType') not in ('Current', 'Saving') or cur != 'RUB':
            raise ValueError('Только собственные Current/Saving RUB; без кредитов, внешних счетов, вкладов и конвертации')
        if a.get('status') not in (None, 'NORM', 'ACTIVE', 'Active', 'Normal'):
            raise ValueError('Статус счёта не разрешает перевод')
    balance = (found[source].get('moneyAmount') or {}).get('value')
    try:
        available = Decimal(str(balance))
    except InvalidOperation:
        raise ValueError('Банк не сообщил доступный остаток') from None
    if not available.is_finite() or available < money(amount):
        raise ValueError('Недостаточно средств на выбранном счёте')
    return found


def prepare(session, source, target, amount):
    amount = money(amount)
    found = accounts(session, source, target, amount)
    provider = session.find_provider('transfer-inner')
    if provider.get('id') != 'transfer-inner' or provider.get('paymentType') != 'Transfer':
        raise ValueError('Банк не подтвердил провайдера внутренних переводов')
    vals = {'bankContract': target}
    errors = session.validate_provider_fields(provider, vals)
    if errors:
        raise ValueError('Поля отклонены актуальной схемой банка: ' + '; '.join(errors))
    q = session.payment_commission({'payParameters': {
        'account': source, 'moneyAmount': float(amount), 'currency': 'RUB',
        'paymentType': 'Transfer', 'provider': 'transfer-inner', 'providerFields': vals}})
    if not isinstance(q, dict) or q.get('unfinishedFlag') is not False:
        raise ValueError('Банк не выдал завершённую котировку')
    def val(key):
        x = q.get(key)
        return x.get('value') if isinstance(x, dict) else x
    fee = Decimal(str(val('value')))
    total = Decimal(str(val('total')))
    # Narrow first implementation: no unexpected charges or currency exchange.
    if not fee.is_finite() or fee != 0 or not total.is_finite() or total != amount:
        raise ValueError('Поддерживается только перевод без комиссии с точной итоговой суммой')
    for key, lower in [('minAmount', True), ('maxAmount', False)]:
        if q.get(key) is not None:
            bound = Decimal(str(q[key]))
            if not bound.is_finite() or (amount < bound if lower else amount > bound):
                raise ValueError('Сумма вне лимитов банка')
    return {'src': source, 'vals': vals, 'fee': 0.0, 'total': float(total),
            'prov_name': 'Свой счёт ' + str(found[target].get('name') or target[-4:])}
