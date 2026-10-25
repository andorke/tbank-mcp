"""Synthetic bank and payment executor; never loads a real session or sends HTTP."""
import asyncio, copy, os, sys, tempfile, threading
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from tbank_mcp import own_transfer, server
from elicit_fake import FakeCtx
SRC,TGT='1000000001','1000000002'
class Bank:
    def __init__(self):
        self.rows=[dict(id=x,accountType='Saving',currency={'name':'RUB'},moneyAmount={'value':1000},name=x) for x in (SRC,TGT)]
        self.quote={'unfinishedFlag':False,'value':{'value':0},'total':{'value':100}}
    def ensure_fresh(self):pass
    def list_accounts(self):return self.rows
    def find_provider(self,p):return {'id':p,'paymentType':'Transfer'}
    def validate_provider_fields(self,p,v):return []
    def payment_commission(self,b):
        assert b['payParameters']['paymentType']=='Transfer'
        assert b['payParameters']['providerFields']=={'bankContract':TGT}
        return self.quote
b=Bank()
assert own_transfer.prepare(b,SRC,TGT,100)['src']==SRC
for bad in [0,-1,float('nan'),float('inf'),1.001]:
    try:own_transfer.prepare(b,SRC,TGT,bad)
    except (ValueError,ArithmeticError):pass
    else:raise AssertionError(bad)
for change in ['currency','type','unknown','same','balance','quote','fee']:
    b=Bank();src,tgt=SRC,TGT
    if change=='currency':b.rows[1]['currency']={'name':'USD'}
    if change=='type':b.rows[0]['accountType']='Credit'
    if change=='unknown':tgt='1000000003'
    if change=='same':tgt=src
    if change=='balance':b.rows[0]['moneyAmount']['value']=99
    if change=='quote':b.quote['unfinishedFlag']=True
    if change=='fee':b.quote['value']['value']=1
    try:own_transfer.prepare(b,src,tgt,100)
    except ValueError:pass
    else:raise AssertionError(change)
async def run():
    with tempfile.TemporaryDirectory() as tmp:
        lock=tmp+'/lock'
        with patch.dict(os.environ, TBANK_OWN_REQUESTS=tmp+'/requests'), patch.object(server.os.path,'expanduser',return_value=lock), patch.object(server,'_require',return_value=Bank()), patch.object(server,'_pay_bill_execute',return_value='SYNTHETIC_PAID') as execute:
            for i, ctx in enumerate([None, FakeCtx(action='decline'), FakeCtx(capable=False)]):
                # No elicitation is requested for a validated OWN account command.
                out=await server.transfer_own(100,SRC,TGT,request_id=f'telegram:776201:{i+1}',ctx=ctx)
                assert out=='SYNTHETIC_PAID',out
                if ctx: assert not ctx.asked
            assert execute.call_count==3
            execute.reset_mock()
            out=await server.transfer_own(100,SRC,TGT,request_id='telegram:776201:1')
            assert 'Повтор поручения' in out;execute.assert_not_called()
            out=await server.transfer_own(101,SRC,TGT,request_id='telegram:776201:1')
            assert 'другими параметрами' in out;execute.assert_not_called()
            out=await server.transfer_own(100,SRC,TGT)
            assert 'request_id' in out;execute.assert_not_called()
            for target in ['1000000003',SRC]:
                out=await server.transfer_own(100,SRC,target,request_id='telegram:776201:20')
                assert out!='SYNTHETIC_PAID';execute.assert_not_called()
            assert 'transfer_own' in await server.transfer(100,TGT,provider='transfer-inner',from_account=SRC)
            assert 'transfer_own' in await server.pay_bill('transfer-inner','{"bankContract":"'+TGT+'"}',100,from_account=SRC)
            from tbank_mcp import own_requests
            own_requests.record('telegram:776201:30',own_requests.identity(100,SRC,TGT),'posting')
            out=await server.transfer_own(100,SRC,TGT,request_id='telegram:776201:30')
            assert 'исход требует проверки' in out;execute.assert_not_called()
            # Decimal-equivalent amounts share a fingerprint.
            assert own_requests.identity(100,SRC,TGT)==own_requests.identity(100.0,SRC,TGT)
            # Cancelling the caller while a worker POST is in flight must not
            # release the process lock or allow a second transfer to start.
            started, release = threading.Event(), threading.Event()
            def delayed(*args):
                started.set()
                assert release.wait(3)
                return 'SYNTHETIC_PAID'
            execute.side_effect=delayed
            first=asyncio.create_task(server.transfer_own(100,SRC,TGT,request_id='telegram:776201:40'))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                first.cancel()
                await asyncio.sleep(0)
                other=await server.transfer_own(100,SRC,TGT,request_id='telegram:776201:41')
                assert 'Уже выполняется' in other,other
                assert execute.call_count==1
            finally:
                release.set()
                try:await first
                except asyncio.CancelledError:pass
            again=await server.transfer_own(100,SRC,TGT,request_id='telegram:776201:40')
            assert 'Повтор поручения' in again,again
asyncio.run(run())
print('own transfer: validated accounts, no button, missing/changed/replayed request, crash recovery and aliases passed; no live HTTP')
