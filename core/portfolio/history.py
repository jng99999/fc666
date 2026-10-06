"""Immutable scenario definitions and idempotent, fully reproducible valuation history."""
from datetime import datetime, timezone
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, text, func
from sqlalchemy.orm import Session
from core.backtest.spot import digest
from core.paper.accounts import page
from core.portfolio.valuation import Limits, capture, evaluate, selected, MissingAccount
from core.storage.models import PortfolioScenarioRecord as Scenario, PortfolioSnapshotRecord as Snapshot, PaperStreamRecord

VERSION='paper-portfolio-history-v1'
SCENARIO_LIMIT=100
SNAPSHOT_LIMIT=200

class Capacity(Exception):pass
class Conflict(Exception):pass


class Definition(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    name:str=Field(min_length=1,max_length=80,strict=True)
    session_ids:list[UUID]=Field(min_length=1,max_length=8)
    limits:Limits=Field(default_factory=Limits)

    @field_validator('name')
    @classmethod
    def printable(cls,value):
        value=value.strip()
        if not value or any(ord(char)<32 or ord(char)==127 for char in value):raise ValueError('Printable scenario name required')
        return value

    @model_validator(mode='after')
    def distinct(self):
        selected([str(id) for id in self.session_ids]);return self


def definition(row):
    if set(row.definition)!= {'version','name','session_ids','limits'} or row.definition['version']!=VERSION or digest(row.definition)!=row.definition_sha256:
        raise ValueError('Scenario definition integrity failed')
    parsed=Definition.model_validate({key:value for key,value in row.definition.items() if key!='version'})
    if {'version':VERSION,**parsed.model_dump(mode='json')}!=row.definition:raise ValueError('Noncanonical scenario definition')
    return parsed


def scenario_visible(row):
    definition(row)
    return {'scenario_id':row.scenario_id,'request_id':row.request_id,'created_at':row.created_at.isoformat(),
        'definition':row.definition,'definition_sha256':row.definition_sha256,'trading_enabled':False}


def snapshot_visible(row,scenario):
    if row.scenario_id!=scenario.scenario_id:raise ValueError("Scenario mismatch")
    parsed=definition(scenario)
    report=row.report
    if digest(report)!=row.report_sha256 or report!=evaluate(report['inputs']):raise ValueError('Stored valuation integrity failed')
    if report['inputs']['selected_ids']!=[str(id) for id in parsed.session_ids] or report['inputs']['limits']!=parsed.limits.model_dump(mode='json'):
        raise ValueError('Snapshot does not match frozen scenario')
    return {'version':VERSION,'snapshot_id':row.snapshot_id,'scenario_id':row.scenario_id,'request_id':row.request_id,
        'created_at':row.created_at.isoformat(),'scenario':scenario_visible(scenario),'report':report,'report_sha256':row.report_sha256,'trading_enabled':False}


def create(engine,request_id,request):
    request_id=str(UUID(request_id));frozen={'version':VERSION,**request.model_dump(mode='json')}
    with Session(engine) as db,db.begin():
        db.execute(text('SELECT pg_advisory_xact_lock(6660901)'))
        existing=db.scalar(select(Scenario).where(Scenario.request_id==request_id))
        if existing:
            if existing.definition!=frozen:raise Conflict('Request key reused with another definition')
            return scenario_visible(existing)
        if db.scalar(select(func.count()).select_from(Scenario))>=SCENARIO_LIMIT:raise Capacity('Scenario capacity full')
        ids=[str(id) for id in request.session_ids]
        if len(list(db.scalars(select(PaperStreamRecord.session_id).where(PaperStreamRecord.session_id.in_(ids)))))!=len(ids):raise MissingAccount()
        row=Scenario(scenario_id=str(uuid4()),request_id=request_id,created_at=datetime.now(timezone.utc),definition=frozen,definition_sha256=digest(frozen))
        db.add(row);return scenario_visible(row)


def read(engine,id):
    with Session(engine) as db:
        row=db.get(Scenario,id)
        if row is None:raise MissingAccount()
        return scenario_visible(row)


def listing(engine,*,limit=20,cursor=None):
    with Session(engine) as db:
        rows,next_cursor=page(db,Scenario,select(Scenario),limit,cursor,'created_at','scenario_id')
        return {'items':[scenario_visible(row) for row in rows],'next_cursor':next_cursor,'trading_enabled':False}


def save(engine,id,request_id):
    request_id=str(UUID(request_id))
    with Session(engine) as db,db.begin():
        # Serialize capacity and request replay. Capture reads a separate coherent
        # source snapshot; only this transaction publishes the immutable result.
        db.execute(text('SELECT pg_advisory_xact_lock(6660902)'))
        scenario=db.get(Scenario,id)
        if scenario is None:raise MissingAccount()
        existing=db.scalar(select(Snapshot).where(Snapshot.scenario_id==id,Snapshot.request_id==request_id))
        if existing:return snapshot_visible(existing,scenario)
        if db.scalar(select(func.count()).select_from(Snapshot))>=SNAPSHOT_LIMIT:raise Capacity('Valuation snapshot capacity full')
        parsed=definition(scenario)
        report=capture(engine,[str(id) for id in parsed.session_ids],parsed.limits)
        row=Snapshot(snapshot_id=str(uuid4()),scenario_id=id,request_id=request_id,created_at=datetime.now(timezone.utc),report=report,report_sha256=digest(report))
        response=snapshot_visible(row,scenario);db.add(row);return response


def saved(engine,id,snapshot_id):
    with Session(engine) as db:
        scenario=db.get(Scenario,id);row=db.get(Snapshot,snapshot_id)
        if scenario is None or row is None or row.scenario_id!=id:raise MissingAccount()
        return snapshot_visible(row,scenario)


def snapshots(engine,id,*,limit=20,cursor=None,status=None):
    if status not in [None,'COMPLETE','UNAVAILABLE']:raise ValueError('Unsupported valuation status')
    with Session(engine) as db:
        scenario=db.get(Scenario,id)
        if scenario is None:raise MissingAccount()
        definition(scenario)
        query=select(Snapshot).where(Snapshot.scenario_id==id)
        if status:query=query.where(Snapshot.report['status'].as_string()==status)
        rows,next_cursor=page(db,Snapshot,query,limit,cursor,'created_at','snapshot_id')
        items=[]
        for row in rows:
            report=snapshot_visible(row,scenario)['report']
            items.append({'snapshot_id':row.snapshot_id,'request_id':row.request_id,'created_at':row.created_at.isoformat(),
                'as_of':report['as_of'],'price_as_of':report['price_as_of'],'status':report['status'],
                'totals':report['totals'],'alerts':report['alerts'],'report_sha256':row.report_sha256})
        return {'items':items,'next_cursor':next_cursor,'trading_enabled':False}
