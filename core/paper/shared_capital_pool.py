"""Immutable exclusive membership and coherent locked local cash forecasts."""
from copy import deepcopy
from decimal import localcontext
from sqlalchemy import select,text,func
from core.storage.models import PaperCapitalPoolRecord as Pool,PaperCapitalMemberRecord as Member
from core.paper import shared_capital as capital,requested_journal as journal,requested_inspection as inspection,requested_ownership as ownership
from core.paper.fault_adapter import sha,encoded
from core.paper.requested_execution import MAX_BYTES

VERSION='paper-capital-pool-capture-v1'
POOL_LIMIT=100


class MissingPool(ValueError):pass


class PoolScopeDenied(ValueError):pass


def authorize(definition,authorized_accounts):
    if authorized_accounts is None:return  # Trusted internal library path; HTTP always supplies grants.
    if (not isinstance(authorized_accounts,list) or not 1<=len(authorized_accounts)<=capital.LIMIT
        or any(type(value) is not str or not 1<=len(value)<=128 for value in authorized_accounts)
        or not {entry['account_id'] for entry in definition['allocations']}.issubset(set(authorized_accounts))):
        raise PoolScopeDenied('Whole pool account scope required')


def membership(definition):
    return [{'pool_id':definition['pool_id'],'ordinal':index,**deepcopy(row)} for index,row in enumerate(definition['allocations'])]


def validate(definition):
    if len(encoded(definition).encode())>MAX_BYTES:raise ValueError('Pool definition exceeds32MiB')
    with localcontext() as context:
        context.prec=260
        capital.validate_pool(definition)


def check(db,row):
    definition=row.payload;validate(definition)
    if row.pool_id!=definition['pool_id'] or row.payload_sha256!=sha(definition):raise ValueError('Pool storage differs')
    saved=list(db.scalars(select(Member).where(Member.pool_id==row.pool_id).order_by(Member.ordinal).limit(capital.LIMIT+1)))
    expected=membership(definition)
    if len(saved)!=len(expected):raise ValueError('Incomplete pool membership')
    for value,entry in zip(saved,expected):
        if value.account_id!=entry['account_id'] or value.pool_id!=entry['pool_id'] or value.ordinal!=entry['ordinal'] or value.payload_sha256!=sha(value.payload) or encoded(value.payload)!=encoded(entry):raise ValueError('Pool membership differs')
    return deepcopy(definition)


def lock(db,pool_id):
    row=db.scalar(select(Pool).where(Pool.pool_id==pool_id).with_for_update())
    if row is None:raise MissingPool('Unknown capital pool')
    return row


def journals(db,definition):
    # Acquire every member lock before auditing any member; canonical order avoids cycles.
    rows=[journal.lock(db,entry['account_id']) for entry in definition['allocations']]
    reports=[];total=0
    for row in rows:
        report=inspection.seal(journal.audit(db,row));total+=len(encoded(report).encode())
        if total>MAX_BYTES:raise ValueError('Pool journal input exceeds32MiB')
        reports.append(report)
    return reports


def create(engine,definition):
    validate(definition)
    with journal.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout','2000ms',true)"))
        # Serialize constructor capacity and overlapping memberships. Capture needs no global lock.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('fc666-local-capital-pool-constructor-v1'))"))
        row=db.scalar(select(Pool).where(Pool.pool_id==definition['pool_id']).with_for_update())
        if row is not None:
            stored=check(db,row)
            if encoded(stored)!=encoded(definition):raise ValueError('Conflicting pool retry')
            capital.evaluate(stored,journals(db,stored))
            return stored
        if db.scalar(select(func.count()).select_from(Pool))>=POOL_LIMIT:raise ValueError('Pool capacity exceeded')
        reports=journals(db,definition)
        if any(report['journal']['revision']!=0 for report in reports):raise ValueError('Pool membership requires untouched financial openings')
        capital.evaluate(definition,reports)
        account_ids=[entry['account_id'] for entry in definition['allocations']]
        if db.scalar(select(Member.account_id).where(Member.account_id.in_(account_ids)).limit(1)) is not None:raise ValueError('Account already belongs to a pool')
        row=Pool(pool_id=definition['pool_id'],payload=deepcopy(definition),payload_sha256=sha(definition));db.add(row);db.flush()
        for entry in membership(definition):db.add(Member(pool_id=entry['pool_id'],account_id=entry['account_id'],ordinal=entry['ordinal'],payload=entry,payload_sha256=sha(entry)))
        db.flush();return check(db,row)


def evaluate(definition,reports,observed_us,candidate=None):
    validate(definition)
    if type(observed_us) is not int or not 0<observed_us<=2**63-1:raise ValueError('Invalid capture clock')
    preview=capital.evaluate(definition,reports,candidate)
    body={'version':VERSION,'definition':deepcopy(definition),'membership':membership(definition),'preview':preview,'observed_us':observed_us,
          'capture_scope':'POOL_AND_ALL_MEMBER_ACCOUNT_LOCKS','coherent_capture_supported':True,'exclusive_pool_membership_supported':True,
          'shared_reservation_committed':False,'submission_allowed':False,'read_only':True}
    result={**body,'sha256':sha(body)}
    if len(encoded(result).encode())>MAX_BYTES:raise ValueError('Pool capture exceeds32MiB')
    return result


def capture(engine,pool_id,candidate=None,*,authorized_accounts=None):
    with journal.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout','2000ms',true)"))
        definition=check(db,lock(db,pool_id));authorize(definition,authorized_accounts)
        reports=journals(db,definition)
        return evaluate(definition,reports,ownership.clock(db),candidate)


def verify(report):
    keys={'version','definition','membership','preview','observed_us','capture_scope','coherent_capture_supported','exclusive_pool_membership_supported','shared_reservation_committed','submission_allowed','read_only','sha256'}
    if not isinstance(report,dict) or set(report)!=keys or report['version']!=VERSION or len(encoded(report).encode())>MAX_BYTES:raise ValueError('Invalid pool capture')
    capital.verify(report['preview'])
    expected=evaluate(report['definition'],report['preview']['journals'],report['observed_us'],report['preview']['candidate'])
    if encoded(expected)!=encoded(report):raise ValueError('Pool capture differs from complete replay')
    return expected
