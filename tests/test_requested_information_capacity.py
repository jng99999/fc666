"""Protect terminal slots from repeated information, not settlement facts."""
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from core.paper import requested_journal as journal,requested_execution as execution,requested_capacity as capacity,requested_event_preview as preview,requested_sources as sources,requested_ownership as ownership
from tests.test_integration import database
from tests.test_requested_controls import setup,command
from tests.test_requested_execution import BASE,request,event,fill,cumulative
from tests.test_requested_event_preview_api import body
from tests.test_requested_commands import configured,HEADERS
from apps.api.main import create_app


@pytest.mark.parametrize('dimension',['account','request'])
@pytest.mark.parametrize('side',['BUY','SELL'])
def test_repeated_ack_does_not_displace_remaining_fill_and_seal(database,monkeypatch,dimension,side):
    engine,_,_=database
    monkeypatch.setattr(journal if dimension=='account' else execution,'TOTAL_EVENTS' if dimension=='account' else 'EVENT_LIMIT',5)
    base=BASE if side=='BUY' else {**BASE,'quantity':'4','cost_basis':'200'}
    setup(engine,base=base);req=request(side=side,base=base);journal.prepare(engine,req)
    price='90' if side=='BUY' else '110';fee='.90' if side=='BUY' else '1.10'
    for value in [event(req,0,'SUBMIT'),event(req,1,'ACK'),fill(req,2,price=price,fee=fee)]:journal.accept(engine,'account',req['request_id'],value)
    before=journal.read(engine,'account');assert before['total_events']==3
    repeated=event(req,3,'ACK');proposal=body(engine,req,repeated)
    # Frozen historical preview replay remains valid, current capture/write refuse.
    from core.paper import requested_controls as controls
    financial,controlled=controls.read(engine,'account');frozen=preview.evaluate(financial,controlled,proposal)
    assert preview.verify(frozen)==frozen
    with pytest.raises(ValueError,match='terminal event headroom'):preview.capture(engine,proposal)
    with pytest.raises(ValueError,match='terminal event headroom'):journal.accept(engine,'account',req['request_id'],repeated)
    assert journal.read(engine,'account')==before
    funding=before['requests'][0]['summary']['funding']
    assert Decimal(funding['reserved_cash' if side=='BUY' else 'reserved_quantity'])==Decimal('303' if side=='BUY' else '3')
    command(engine,'stop','STOP',seconds=3)
    journal.accept(engine,'account',req['request_id'],fill(req,3,size='3',price=price,fee='2.70' if side=='BUY' else '3.30',identity='f2'))
    journal.accept(engine,'account',req['request_id'],cumulative(req,4,'4','3.60' if side=='BUY' else '4.40','360' if side=='BUY' else '440',seal=True))
    settled=journal.read(engine,'account');assert settled['active_request_id'] is None and settled['total_events']==5
    assert Decimal(settled['account']['cash'])==Decimal('636.40' if side=='BUY' else '1435.60')
    journal.accept(engine,'account',req['request_id'],event(req,1,'ACK'))
    assert journal.read(engine,'account')==settled


def test_repeated_receipt_keeps_one_slot_for_seal(database,monkeypatch):
    engine,_,_=database;monkeypatch.setattr(journal,'TOTAL_EVENTS',5);setup(engine);req=request();journal.prepare(engine,req)
    for value in [event(req,0,'SUBMIT'),fill(req,1,size='4',fee='3.60'),cumulative(req,2,'4','3.60','360'),cumulative(req,3,'4','3.60','360')]:journal.accept(engine,'account',req['request_id'],value)
    before=journal.read(engine,'account')
    with pytest.raises(ValueError,match='terminal event headroom'):journal.accept(engine,'account',req['request_id'],cumulative(req,4,'4','3.60','360'))
    assert journal.read(engine,'account')==before and before['active_request_id']==req['request_id']
    journal.accept(engine,'account',req['request_id'],cumulative(req,4,'4','3.60','360',seal=True))
    assert journal.read(engine,'account')['active_request_id'] is None


@pytest.mark.parametrize('owned',[False,True])
def test_source_http_refusal_rolls_back_and_old_receipts_retry(database,monkeypatch,owned):
    engine,_,settings=database;monkeypatch.setattr(journal,'TOTAL_EVENTS',4);setup(engine);req=request();journal.prepare(engine,req)
    if owned:ownership.claim(engine,'account',req['request_id'],'worker',60,expected_financial_revision=1,expected_control_revision=2,expected_token=0)
    for value in [event(req,0,'SUBMIT'),event(req,1,'ACK')]:
        if owned:
            seed=body(engine,req,value);forecast=preview.capture(engine,seed)
            ownership.deliver(engine,seed,forecast['sha256'],'worker',1)
        else:journal.accept(engine,'account',req['request_id'],value)
    proposal=body(engine,req,event(req,2,'ACK'))
    from core.paper import requested_controls as controls
    financial,controlled=controls.read(engine,'account');frozen=preview.evaluate(financial,controlled,proposal)
    value={**proposal,'preview_sha256':frozen['sha256']};path='/api/v1/paper-requested/source-commands'
    if owned:value.update(owner='worker',ownership_token=1);path='/api/v1/paper-requested/owned-source-commands'
    before=journal.read(engine,'account')
    with TestClient(create_app(configured(settings,actions=['PREVIEW_EVENT','INGEST_EVENT','DELIVER_OWNED_EVENT']))) as client:
        response=client.post('/api/v1/paper-requested/event-previews',json=proposal,headers=HEADERS)
        assert response.status_code==409 and response.headers['cache-control']=='no-store'
        response=client.post(path,json=value,headers=HEADERS);assert response.status_code==409
        assert journal.read(engine,'account')==before
        # Reproduce a pre-policy receipt; it must never be invalidated retroactively.
        with monkeypatch.context() as old:
            old.setattr(capacity,'require_information',lambda *args:None)
            initial=client.post(path,json=value,headers=HEADERS);assert initial.status_code==200
        saved=journal.read(engine,'account')
        assert client.post(path,json=value,headers=HEADERS).json()==initial.json()
        assert journal.read(engine,'account')==saved
    assert len([r for r in sources.capture(engine,'account')['sources'] if r['receipt'] is not None])==(3 if owned else 1)


@pytest.mark.parametrize('terminal',['cancel','reject'])
def test_safety_facts_can_still_exhaust_capacity_without_releasing_hold(database,monkeypatch,terminal):
    # Deliberately reproduce the remaining limitation; this rule is not overflow support.
    engine,_,_=database;limit=5 if terminal=='cancel' else 3
    monkeypatch.setattr(journal,'TOTAL_EVENTS',limit);setup(engine);req=request();journal.prepare(engine,req)
    values=[event(req,0,'SUBMIT'),event(req,1,'ACK')]
    if terminal=='cancel':values.extend([event(req,2,'ACK'),event(req,3,'CANCEL_REQUEST'),event(req,4,'CANCEL_ACK')])
    else:values.append(event(req,2,'REJECT'))
    for value in values:journal.accept(engine,'account',req['request_id'],value)
    before=journal.read(engine,'account');assert before['total_events']==limit
    assert before['active_request_id']==req['request_id'] and before['account']==BASE
    assert Decimal(before['requests'][0]['summary']['funding']['reserved_cash'])==404
    with pytest.raises(ValueError,match='Event history capacity reached'):
        journal.accept(engine,'account',req['request_id'],cumulative(req,limit,'0','0','0',seal=True))
    assert journal.read(engine,'account')==before
