"""Current owner policy, synthetic sessions only; all bank writes are spies."""
import asyncio, copy, os, sys, tempfile
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
from tbank_mcp import server, transfer_quote
from elicit_fake import FakeCtx
from test_requisites import LegalSession, fixture

class Bank:
    def __init__(self):
        self.fee=1;self.changed=False
    def ensure_fresh(self):pass
    def _source_account(self):return '1000000001'
    def resolve_recipient(self,phone):return [dict(pointer_link_id='LINK',bank_member_id='BANK',masked_fio='Тест Т.',bank_name='Банк',is_default_bank=True,is_tbank=False)]
    def payment_commission(self,body):
        amount=body['payParameters']['moneyAmount']
        return dict(unfinishedFlag=False,value={'value':self.fee},total={'value':float(amount)+self.fee})
    def ruble_source_accounts(self):return [dict(id='1000000001',name='Тест',balance=1000)]

async def main():
    b=Bank()
    with patch.object(server,'_require',return_value=b),patch.object(server,'_do_transfer',return_value='PAID') as pay:
        for action in ['decline','cancel']:
            ctx=FakeCtx(action=action)
            out=await server.transfer(100,'+79990000000',from_account='1000000001',ctx=ctx)
            assert out!='PAID';pay.assert_not_called()
            text=ctx.asked[-1][0]
            for field in ['100.00 RUB','1000000001','Тест Т.','+79990000000','1.00 RUB','101.00 RUB']:
                assert field in text,(field,text)
        for ctx in [None,FakeCtx(capable=False)]:
            assert 'НЕ ВЫПОЛНЕН' in await server.transfer(100,'+79990000000',ctx=ctx)
            pay.assert_not_called()
        assert await server.transfer(100,'+79990000000',ctx=FakeCtx())=='PAID'
        assert pay.call_args.args[6]=='LINK';assert pay.call_args.args[5]=='Тест Т.'
        pay.reset_mock()
        class Changed(FakeCtx):
            async def elicit(self,*a,**kw):
                result=await super().elicit(*a,**kw);b.fee=2;return result
        assert 'изменились' in await server.transfer(100,'+79990000000',ctx=Changed())
        pay.assert_not_called()
        assert 'Отменено' in await server.transfer(100,'+79990000000',force=True,ctx=FakeCtx(action='decline'))
        pay.assert_not_called()
        # Even a permissive global threshold cannot bypass external-transfer consent.
        with patch.dict(os.environ,TBANK_CONFIRM_ABOVE='100000'):
            assert 'НЕ ВЫПОЛНЕН' in await server.transfer(1,'+79990000000',ctx=None)
            pay.assert_not_called()
        # Expiry includes the time spent rechecking terms AFTER the click.
        with patch.object(transfer_quote, 'monotonic', side_effect=[0, 121]):
            out = await server.transfer(100,'+79990000000',ctx=FakeCtx())
            assert 'истекло' in out,out
            pay.assert_not_called()
    with patch.object(server,'_require',side_effect=AssertionError('test must never read bank')):
        assert 'Тест завершён' in await server.banking_confirmation_test(ctx=FakeCtx())
        assert 'Отменено' in await server.banking_confirmation_test(ctx=FakeCtx(action='decline'))
        assert 'НЕ ВЫПОЛНЕН' in await server.banking_confirmation_test(ctx=None)
    # Real legal preflight against capture-derived fixture; zero signed requests.
    fx=fixture();b=LegalSession(fx)
    args=fx['tool_args'];args.update(amount=fx['qr_amount'],from_account='1111111111',comment='Тест')
    with patch.object(server,'_require',return_value=b),patch.object(server,'_do_transfer_requisites',return_value='PAID') as pay:
        ctx=FakeCtx(action='decline');out=await server.transfer_requisites(**args,ctx=ctx)
        assert 'Отменено' in out,out
        pay.assert_not_called();assert b.body is None
        text=ctx.asked[-1][0]
        assert '1111111111' in text and '40702810000000000001' in text and 'Комиссия' in text
        assert await server.transfer_requisites(**args,ctx=FakeCtx())=='PAID'
        assert pay.call_args.args[-1]['fields']['bankAcnt']=='40702810000000000001'
    # Exercise the real legal executor's post-confirmation expiry check; the
    # capture session must have zero signed payment bodies.
    b=LegalSession(fx)
    with patch.object(server,'_require',return_value=b):
        prepared=pay.call_args.args[-1]
        out=server._do_transfer_requisites(**args, _approved=prepared, _expires_at=0)
        assert 'истекло' in out and b.body is None,out
    for p in ['p2p-anybank','transfer-legal','transfer-inner']:
        out=await server.pay_bill(p,'{}',100,ctx=FakeCtx())
        assert 'PAID' not in out
    print('transfer policy: own/external split, exact card, decline/cancel/unavailable, threshold bypass refusal, changed fee, force refusal, legal preparation, safe probe: PASS (no live HTTP)')
asyncio.run(main())
