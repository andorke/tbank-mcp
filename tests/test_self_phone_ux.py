"""Own-phone UX: synthetic bank, never live payments."""
import asyncio, os, sys, tempfile
from pathlib import Path
from unittest.mock import patch
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from tbank_mcp import server, transfer_quote
from elicit_fake import FakeCtx

PHONE = '+79990000000'
class Bank:
    fee = 0
    verified = True
    phone = PHONE
    tick = 0
    def ensure_fresh(self): pass
    def list_accounts(self):
        return [dict(id="1000000001", accountType="Current", currency="RUB", moneyAmount={"value":100})]
    def _call_userinfo(self):
        return dict(phone_number=self.phone, phone_number_verified=self.verified)
    def resolve_recipient(self, phone):
        self.tick += 1
        return [dict(pointer_link_id='TEMP-' + str(self.tick), bank_member_id='BANK',
                     masked_fio='Тест Т.', bank_name='Банк', is_default_bank=True, is_tbank=False)]
    def payment_commission(self, body):
        return dict(unfinishedFlag=False, value=self.fee, total=float(body['payParameters']['moneyAmount']) + self.fee)

async def main():
    b = Bank()
    with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, TBANK_OWN_REQUESTS=td), \
         patch.object(server, '_require', return_value=b), \
         patch.object(server, '_do_transfer', return_value='PAID') as pay:
        args = dict(amount=1, to_account=PHONE, from_account='1000000001',
                    bank_member_id='BANK', pointer_link_id='STALE', request_id='telegram:776201:10001')
        # Self, no elicitation capability, rotating bank-issued handle.
        assert await server.transfer(**args) == 'PAID'
        assert pay.call_count == 1
        assert pay.call_args.args[6].startswith('TEMP-')
        assert 'Повтор поручения' in await server.transfer(**args)
        assert pay.call_count == 1
        args['amount'] = 2
        assert 'другими параметрами' in await server.transfer(**args)
        assert pay.call_count == 1
        args.update(amount=1, request_id='telegram:776201:10002')
        b.verified = False
        assert 'НЕ ВЫПОЛНЕН' in await server.transfer(**args)
        assert pay.call_count == 1
        # Without a source-message id, even self cannot silently execute.
        b.verified = True
        args['request_id'] = ''
        assert 'НЕ ВЫПОЛНЕН' in await server.transfer(**args)
        assert pay.call_count == 1
        # Different phone still needs the human button.
        b.phone = '+79991234576'; args['request_id'] = 'telegram:776201:10003'
        assert 'НЕ ВЫПОЛНЕН' in await server.transfer(**args)
        assert pay.call_count == 1
        # External decline and changed fee cannot pay; rotated handle alone can.
        ctx = FakeCtx(action='decline')
        assert 'Отменено' in await server.transfer(**args, ctx=ctx)
        assert pay.call_count == 1
        assert len(ctx.asked[-1][0]) < 240
        assert await server.transfer(**args, ctx=FakeCtx()) == 'PAID'
        b.phone = PHONE; b.fee = 1; args['request_id'] = 'telegram:776201:10004'
        assert 'нулевая комиссия' in await server.transfer(**args)
        assert pay.call_count == 2
        # Ambiguous bank never silently picks a candidate.
        original = b.resolve_recipient
        b.resolve_recipient = lambda phone: original(phone) * 2
        args['request_id'] = 'telegram:776201:10005'
        assert 'Укажи банк' in await server.transfer(**args)
        assert pay.call_count == 2
    print('self-phone UX: verified owner, deduplication, rotating handles, external consent, fee and ambiguity: PASS')
asyncio.run(main())
