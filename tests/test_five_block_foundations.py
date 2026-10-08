"""Independent causal/risk/credential/ingestion checks, fixture data only."""
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
import json,hashlib,hmac,subprocess,sys
import httpx,pytest
from pydantic import SecretStr
from sqlalchemy import text,event as sql_event
from sqlalchemy.exc import DBAPIError,SQLAlchemyError
from fastapi.testclient import TestClient
from apps.api.main import create_app
from apps.api.settings import Settings
from core.paper import requested_inbox as inbox,requested_journal as journal,requested_ownership as ownership,requested_health as health
from core.portfolio import requested_exposure as exposure
from core.backtest import walk_forward
from core.exchange.private_spot import Credentials,ReadOnlySpot,PrivateQueryError
from tests.test_integration import database
from tests.test_requested_health import ready
from tests.test_requested_commands import configured,HEADERS
from tests.test_requested_execution import request,event,fill,STAMP
from tests.test_backtest import liquid,rules,cfg


def staged(engine,sequence=1000):
    req=request();journal.prepare(engine,req)
    ownership.claim(engine,'account',req['request_id'],'worker',60,expected_financial_revision=1,expected_control_revision=2,expected_token=0)
    return {'account_id':'account','request_id':req['request_id'],'expected_financial_revision':1,'owner':'worker','ownership_token':1,
            'source':{'kind':'LOCAL_PAPER_OPERATOR_INPUT','source_id':'fixture'},'event':fill(req,sequence)}


def test_inbox_commits_only_staging_retries_after_expiry_and_conflicts(database,monkeypatch):
    engine,_,_=database;ready(engine,enroll=False);value=staged(engine);before=journal.read(engine,'account')
    one=inbox.stage(engine,value);assert inbox.verify(one)==one
    assert one['state']=='STAGED_UNAPPLIED' and one['staging_committed'] and not one['event_committed']
    assert journal.read(engine,'account')==before and inbox.stage(engine,value)==one
    with monkeypatch.context() as expired:
        expired.setattr(ownership,'clock',lambda db:one['received_us']+100_000_000)
        assert inbox.stage(engine,value)==one
        with pytest.raises(ValueError):inbox.stage(engine,{**value,'event':{**value['event'],'sequence':1001,'event_id':'new'}})
    for change in [{'event':{**value['event'],'payload':{**value['event']['payload'],'quantity':'2'}}},
                   {'event':{**value['event'],'event_id':'conflicting-sequence'}},{'owner':'other'}]:
        with pytest.raises(ValueError):inbox.stage(engine,{**value,**change})
    assert journal.read(engine,'account')==before and len(inbox.capture(engine,'account')['records'])==1


def test_inbox_database_immutability_capacity_and_fault_rollback(database,monkeypatch):
    engine,_,_=database;ready(engine,enroll=False);value=staged(engine)
    def fail(conn,cursor,statement,*args):
        if statement.startswith('INSERT INTO requested_paper_inbox'):raise SQLAlchemyError('Fixture post-insert fault')
    sql_event.listen(engine,'after_cursor_execute',fail)
    try:
        with pytest.raises(SQLAlchemyError):inbox.stage(engine,value)
    finally:sql_event.remove(engine,'after_cursor_execute',fail)
    assert not inbox.capture(engine,'account')['records']
    one=inbox.stage(engine,value)
    for statement in ["UPDATE requested_paper_inbox SET event_id='changed'","DELETE FROM requested_paper_inbox"]:
        with pytest.raises(DBAPIError):
            with engine.begin() as db:db.execute(text(statement))
    monkeypatch.setattr(inbox,'LIMIT',1)
    assert inbox.stage(engine,value)==one
    with pytest.raises(ValueError):inbox.stage(engine,{**value,'event':{**value['event'],'sequence':1001,'event_id':'next'}})
    assert journal.read(engine,'account')['revision']==1


def test_scoped_inbox_and_exposure_grants_precede_storage(monkeypatch):
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    req=request();value={'account_id':'account','request_id':req['request_id'],'expected_financial_revision':1,'owner':'worker','ownership_token':1,
                        'source':{'kind':'LOCAL_PAPER_OPERATOR_INPUT','source_id':'fixture'},'event':fill(req,1000)}
    def forbidden(*args,**kwargs):raise AssertionError('Denied call reached storage')
    monkeypatch.setattr(inbox,'stage',forbidden);monkeypatch.setattr(inbox,'capture',forbidden);monkeypatch.setattr(exposure,'capture',forbidden)
    paths=[('/api/v1/paper-requested/inbox-commands',value,'STAGE_LOCAL_INPUT'),('/api/v1/paper-requested/inbox-captures',{'account_id':'account'},'READ_LOCAL_INBOX'),('/api/v1/paper-requested/exposure-captures',{'account_ids':['account']},'READ_REQUESTED_EXPOSURE')]
    with TestClient(create_app(settings)) as client:
        for path,body,_ in paths:assert client.post(path,json=body,headers=HEADERS).status_code==503
    for action in ['STAGE_LOCAL_INPUT','READ_LOCAL_INBOX','READ_REQUESTED_EXPOSURE']:
        with TestClient(create_app(configured(settings,actions=[action]))) as client:
            for path,body,required in paths:
                assert client.post(path,json=body).status_code==401
                if action!=required:assert client.post(path,json=body,headers=HEADERS).status_code==403
                other={**body,'account_id':'other'} if 'account_id' in body else {'account_ids':['account','other']}
                assert client.post(path,json=other,headers=HEADERS).status_code==403
            assert client.post(paths[0][0],json={**value,'event':{**value['event'],'sequence':True}},headers=HEADERS).status_code==422


def test_actual_inbox_http_read_independent_of_stage(database):
    engine,_,settings=database;ready(engine,enroll=False);value=staged(engine)
    with TestClient(create_app(configured(settings,actions=['STAGE_LOCAL_INPUT']))) as client:
        result=client.post('/api/v1/paper-requested/inbox-commands',json=value,headers=HEADERS)
        assert result.status_code==200 and result.headers['cache-control']=='no-store'
        assert client.post('/api/v1/paper-requested/inbox-captures',json={'account_id':'account'},headers=HEADERS).status_code==403
    with TestClient(create_app(configured(settings,actions=['READ_LOCAL_INBOX']))) as client:
        result=client.post('/api/v1/paper-requested/inbox-captures',json={'account_id':'account'},headers=HEADERS)
        assert result.status_code==200 and len(result.json()['records'])==1


def test_requested_exposure_counts_cash_once_and_pending_buys_conservatively(database):
    engine,_,settings=database;ready(engine,'a',enroll=False);ready(engine,'b',enroll=False)
    req=request(account_id='a');journal.prepare(engine,req)
    report=exposure.capture(engine,['b','a']);assert exposure.verify(report)==report
    price=Decimal(report['accounts'][0]['mark_price'])
    assert Decimal(report['cash'])==2000 and Decimal(report['equity'])==2000
    assert Decimal(report['reserved_cash'])==404 and Decimal(report['available_cash'])==1596
    assert Decimal(report['assets']['BTC']['quantity'])==0 and Decimal(report['assets']['BTC']['pending_buy_quantity'])==4
    assert Decimal(report['assets']['BTC']['worst_market_value'])==4*price
    assert not report['submission_allowed'] and not report['unallocated_pool_cash_included']
    with TestClient(create_app(configured(settings,accounts=['a','b'],actions=['READ_REQUESTED_EXPOSURE']))) as client:
        result=client.post('/api/v1/paper-requested/exposure-captures',json={'account_ids':['b','a']},headers=HEADERS)
        assert result.status_code==200 and result.headers['cache-control']=='no-store'
    broken=deepcopy(report);broken['equity']='999';broken['sha256']=health.sha({k:v for k,v in broken.items() if k!='sha256'})
    with pytest.raises(ValueError):exposure.verify(broken)
    with pytest.raises(ValueError):exposure.capture(engine,['a','a'])


def test_requested_exposure_stale_or_missing_quote_refuses(database):
    engine,_,_=database;ready(engine,enroll=False)
    with engine.begin() as db:db.execute(text("DELETE FROM candles WHERE timeframe='1m'"))
    with pytest.raises(ValueError):exposure.capture(engine,['account'])


def walk(values=None):
    return walk_forward.simulate(liquid(values or [10,11,12,13,9,8,10,11,12,9,8,10]),rules(),cfg(),strategy_id='ema_long_flat_v1',grid=[{'period':2},{'period':3}],train_bars=4,test_bars=4)


def test_walk_forward_selection_is_training_only_and_tests_cold_start(tmp_path):
    original=walk();changed=walk([10,11,12,13,90,80,100,110,120,90,80,100])
    assert len(original['folds'])==2 and walk_forward.verify(original)==original
    assert original['folds'][0]['candidates']==changed['folds'][0]['candidates']
    assert original['folds'][0]['selected_index']==changed['folds'][0]['selected_index']
    for fold in original['folds']:
        assert fold['train_end']==fold['test_start']
        assert fold['test']['equity'][0]['cash']=='1000' and fold['test']['equity'][0]['quantity']=='0'
        assert all(fill['decision_at']>=fold['test_start'] for fill in fold['test']['fills'])
    broken=deepcopy(original);broken['folds'][0]['selected_index']=1-broken['folds'][0]['selected_index']
    with pytest.raises(ValueError):walk_forward.verify(broken)
    path=tmp_path/'walk.json';path.write_text(json.dumps(original))
    assert subprocess.run([sys.executable,'-m','scripts.verify_walk_forward',str(path)],capture_output=True,timeout=10).returncode==0


@pytest.mark.parametrize('grid,train,test',[([{'period':2},{'period':2}],4,4),([{'period':4}],4,4),([{'period':2}],True,4),([],4,4)])
def test_walk_forward_invalid_candidates_or_windows(grid,train,test):
    with pytest.raises(ValueError):walk_forward.simulate(liquid([10]*12),rules(),cfg(),strategy_id='ema_long_flat_v1',grid=grid,train_bars=train,test_bars=test)


def credentials():return Credentials(SecretStr('fixtureAPIKEY0123456789012345'),SecretStr('fixtureSECRET0123456789012345'))


def test_private_signing_fixed_read_only_destination_and_account_validation():
    observed=[];creds=credentials()
    def serve(request):
        observed.append(request);assert request.method=='GET' and request.url.host=='api.binance.com'
        assert request.headers['X-MBX-APIKEY']==creds.api_key.get_secret_value()
        query=str(request.url.query.decode());prefix,signature=query.rsplit('&signature=',1)
        assert signature==hmac.new(creds.api_secret.get_secret_value().encode(),prefix.encode(),hashlib.sha256).hexdigest()
        return httpx.Response(200,json={'accountType':'SPOT','balances':[{'asset':'BTC','free':'1','locked':'.5'}]})
    result=ReadOnlySpot(creds,transport=httpx.MockTransport(serve),clock_ms=lambda:123456).account()
    assert result['balances'][0]['locked']=='.5' and not result['submission_allowed'] and len(observed)==1
    assert creds.api_key.get_secret_value() not in repr(creds) and creds.api_secret.get_secret_value() not in repr(creds)


@pytest.mark.parametrize('response',[httpx.Response(302,headers={'Location':'https://other.invalid'}),httpx.Response(401,text='fixtureAPIKEY0123456789012345'),httpx.Response(200,text='{"accountType":"SPOT","accountType":"SPOT","balances":[]}'),httpx.Response(200,json={'accountType':'SPOT','balances':[{'asset':'BTC','free':'-1','locked':'0'}]})])
def test_private_errors_are_bounded_redacted_and_no_redirect(response):
    seen=[]
    def serve(request):seen.append(request);return response
    with pytest.raises(PrivateQueryError) as error:ReadOnlySpot(credentials(),transport=httpx.MockTransport(serve),clock_ms=lambda:123456).account()
    assert len(seen)==1 and 'fixtureAPIKEY' not in str(error.value) and 'signature=' not in str(error.value)


def test_private_response_byte_limit_and_trade_cursor(monkeypatch):
    import core.exchange.private_spot as private
    monkeypatch.setattr(private,'MAX_BYTES',128)
    with pytest.raises(PrivateQueryError):ReadOnlySpot(credentials(),transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b' '*129)),clock_ms=lambda:1).account()
    client=ReadOnlySpot(credentials(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=[])),clock_ms=lambda:1)
    assert client.trades('BTCUSDT')['pagination_complete'] is False
    for cursor in [-1,True,'0']:
        with pytest.raises(ValueError):client.trades('BTCUSDT',from_id=cursor)


def test_inbox_observes_later_application_without_rewriting_staging_receipt(database):
    from core.paper import requested_event_preview as preview
    engine,_,_=database;ready(engine,enroll=False);value=staged(engine,sequence=0)
    value['event']=event(request(),0,'SUBMIT')
    receipt=inbox.stage(engine,value)
    assert inbox.capture(engine,'account')['application_observations'][0]['journal_state']=='NOT_PRESENT_IN_JOURNAL'
    proposal={key:value[key] for key in ['account_id','request_id','expected_financial_revision','source','event']};proposal['expected_control_revision']=2
    forecast=preview.capture(engine,proposal);ownership.deliver(engine,proposal,forecast['sha256'],'worker',1)
    report=inbox.capture(engine,'account')
    assert report['records']==[receipt] and report['application_observations'][0]['journal_state']=='PRESENT_IN_JOURNAL'
    assert receipt['state']=='STAGED_UNAPPLIED' and journal.read(engine,'account')['revision']==2


@pytest.mark.parametrize('same',[False,True])
def test_concurrent_inbox_delivery_commits_once_or_conflicts(database,same):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    engine,_,_=database;ready(engine,enroll=False);value=staged(engine);barrier=Barrier(2)
    other=deepcopy(value)
    if not same:other['event']['payload']['quantity']='2'
    def attempt(proposal):
        barrier.wait(timeout=5)
        try:inbox.stage(engine,proposal);return 'STAGED'
        except ValueError:return 'CONFLICT'
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,[value,other]))
    assert sorted(results)==(['STAGED','STAGED'] if same else ['CONFLICT','STAGED'])
    assert len(inbox.capture(engine,'account')['records'])==1 and journal.read(engine,'account')['revision']==1


@pytest.mark.parametrize('committed',[False,True])
def test_inbox_sigkill_reopen_and_lost_reply_keep_funds_unchanged(database,committed):
    import os,signal
    from sqlalchemy import create_engine
    engine,_,_=database;ready(engine,enroll=False);value=staged(engine)
    code='''import os,json,signal,sys
from sqlalchemy import create_engine,event
from core.paper import requested_inbox as inbox
engine=create_engine(os.environ['FC666_STAGE_TEST_DB'],hide_parameters=True)
if os.environ['FC666_STAGE_COMMITTED']=='false':
 def die(conn,cursor,statement,*args):
  if statement.startswith('INSERT INTO requested_paper_inbox'):os.kill(os.getpid(),signal.SIGKILL)
 event.listen(engine,'after_cursor_execute',die)
inbox.stage(engine,json.load(sys.stdin))
os.kill(os.getpid(),signal.SIGKILL)
'''
    child=subprocess.run([sys.executable,'-c',code],input=json.dumps(value),text=True,capture_output=True,timeout=20,
                         env={**os.environ,'FC666_STAGE_TEST_DB':engine.url.render_as_string(hide_password=False),'FC666_STAGE_COMMITTED':str(committed).lower()})
    assert child.returncode==-signal.SIGKILL
    reopened=create_engine(engine.url,hide_parameters=True)
    try:
        assert len(inbox.capture(reopened,'account')['records'])==int(committed)
        inbox.stage(reopened,value);assert len(inbox.capture(reopened,'account')['records'])==1
        assert journal.read(reopened,'account')['revision']==1
    finally:reopened.dispose()


def test_populated_inbox_downgrade_refuses_and_empty_roundtrip(database):
    from alembic import command as migration
    engine,config,_=database
    migration.downgrade(config,'0024');migration.upgrade(config,'head')
    ready(engine,enroll=False);value=staged(engine);inbox.stage(engine,value)
    with pytest.raises(ValueError,match='Cannot discard'):migration.downgrade(config,'0024')
    assert len(inbox.capture(engine,'account')['records'])==1


def test_inbox_byte_budget_and_stale_new_fence_leave_no_record(database,monkeypatch):
    engine,_,_=database;ready(engine,enroll=False);value=staged(engine);before=journal.read(engine,'account')
    with monkeypatch.context() as small:
        small.setattr(inbox,'MAX_BYTES',64)
        with pytest.raises(ValueError,match='byte capacity'):inbox.stage(engine,value)
    with pytest.raises(ValueError):inbox.stage(engine,{**value,'ownership_token':2})
    assert not inbox.capture(engine,'account')['records'] and journal.read(engine,'account')==before


def test_actual_walk_forward_api_and_strict_bounds(database):
    from core.market_data.storage import save_instruments,save_candles
    engine,_,settings=database;save_instruments(engine,[rules()]);save_candles(engine,liquid([10,11,12,13,9,8,10,11,12,9,8,10]))
    value={'symbol':'BTCUSDT','timeframe':'1m','limit':12,'config':cfg().model_dump(mode='json'),
           'strategy':'ema_long_flat_v1','grid':[{'period':2},{'period':3}],'train_bars':4,'test_bars':4}
    with TestClient(create_app(settings)) as client:
        result=client.post('/api/v1/research/walk-forward',json=value)
        assert result.status_code==200,result.text
        assert result.headers['cache-control']=='no-store' and walk_forward.verify(result.json())==result.json()
        assert client.post('/api/v1/research/walk-forward',json={**value,'train_bars':True}).status_code==422
        assert client.post('/api/v1/research/walk-forward',json={**value,'grid':[{'period':4}]}).status_code==409


def test_private_order_and_fill_normalization_is_bounded_not_reconciliation():
    order={'symbol':'BTCUSDT','orderId':1,'clientOrderId':'fixture','side':'BUY','status':'PARTIALLY_FILLED','origQty':'2','executedQty':'1','price':'100'}
    trade={'symbol':'BTCUSDT','id':10,'orderId':1,'qty':'1','price':'100','commission':'.1','commissionAsset':'USDT','time':123456,'isBuyer':True}
    def serve(req):return httpx.Response(200,json=[order] if req.url.path.endswith('openOrders') else [trade])
    client=ReadOnlySpot(credentials(),transport=httpx.MockTransport(serve),clock_ms=lambda:123456)
    assert client.open_orders('BTCUSDT')['records'][0]['executedQty']=='1'
    assert client.trades('BTCUSDT')['records'][0]['commissionAsset']=='USDT'
    assert client.trades('BTCUSDT')['reconciled'] is False
    invalid=ReadOnlySpot(credentials(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=[trade,trade])),clock_ms=lambda:123456)
    with pytest.raises(PrivateQueryError):invalid.trades('BTCUSDT')
    with pytest.raises(ValueError):client.open_orders('OTHERUSDT')


def test_populated_financial_and_inbox_snapshot_restores_exact_rows(database,tmp_path):
    from scripts.backup_drill import drill
    from core.paper import requested_event_preview as preview
    engine,_,_=database;ready(engine,enroll=False);value=staged(engine,sequence=0);req=request();value['event']=event(req,0,'SUBMIT')
    inbox.stage(engine,value)
    for source in [event(req,0,'SUBMIT'),fill(req,1)]:
        financial=journal.read(engine,'account')
        proposal={'account_id':'account','request_id':req['request_id'],'expected_financial_revision':financial['revision'],
                  'expected_control_revision':2,'source':value['source'],'event':source}
        forecast=preview.capture(engine,proposal);ownership.deliver(engine,proposal,forecast['sha256'],'worker',1)
    before=journal.read(engine,'account');report=drill(tmp_path/'protected-backups')
    assert report['isolated_restore_verified'] and report['row_content_verified'] and not report['main_database_modified']
    assert report['verified_table_counts']['requested_paper_events']==2
    assert report['verified_table_counts']['requested_paper_inbox']==1
    assert report['verified_table_counts']['requested_paper_accounts']==1
    assert journal.read(engine,'account')==before and Decimal(before['requests'][0]['summary']['funding']['reserved_cash'])==303
    assert (tmp_path/'protected-backups'/report['archive']).stat().st_mode & 0o777==0o600
