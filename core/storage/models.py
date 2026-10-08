from datetime import datetime
from decimal import Decimal
from sqlalchemy import Boolean, CheckConstraint, ForeignKeyConstraint, BigInteger, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class InstrumentRecord(Base):
    __tablename__ = "instruments"
    instrument_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    exchange: Mapped[str] = mapped_column(String(32))
    native_symbol: Mapped[str] = mapped_column(String(64))
    base: Mapped[str] = mapped_column(String(32))
    quote: Mapped[str] = mapped_column(String(32))
    market_type: Mapped[str] = mapped_column(String(16))
    contract_type: Mapped[str | None] = mapped_column(String(32))
    settlement_currency: Mapped[str | None] = mapped_column(String(32))
    tick_size: Mapped[Decimal] = mapped_column(Numeric(38,18))
    quantity_step: Mapped[Decimal] = mapped_column(Numeric(38,18))
    min_quantity: Mapped[Decimal] = mapped_column(Numeric(38,18))
    min_notional: Mapped[Decimal] = mapped_column(Numeric(38,18))
    contract_size: Mapped[Decimal] = mapped_column(Numeric(38,18))
    __table_args__ = (
        UniqueConstraint("exchange", "native_symbol", "market_type", name="uq_instrument_native"),
        CheckConstraint("market_type IN ('SPOT','PERPETUAL')", name="ck_market_type"),
        CheckConstraint("tick_size > 0 AND quantity_step > 0 AND min_quantity >= 0 AND min_notional >= 0 AND contract_size > 0", name="ck_instrument_rules"),
        CheckConstraint("market_type <> 'PERPETUAL' OR (settlement_currency IS NOT NULL AND contract_type IS NOT NULL)", name="ck_contract"),
    )

class CandleRecord(Base):
    __tablename__ = "candles"
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.instrument_id"), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(Numeric(38,18))
    high: Mapped[Decimal] = mapped_column(Numeric(38,18))
    low: Mapped[Decimal] = mapped_column(Numeric(38,18))
    close: Mapped[Decimal] = mapped_column(Numeric(38,18))
    volume: Mapped[Decimal] = mapped_column(Numeric(38,18))
    is_closed: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint("low > 0 AND low <= open AND low <= close AND high >= open AND high >= close AND volume >= 0", name="ck_candle_values"),
        CheckConstraint("close_time > open_time", name="ck_candle_time"),
        CheckConstraint("extract(epoch from close_time-open_time) = CASE timeframe WHEN '1m' THEN 60 WHEN '5m' THEN 300 WHEN '15m' THEN 900 WHEN '1h' THEN 3600 WHEN '4h' THEN 14400 WHEN '1d' THEN 86400 END AND mod(extract(epoch from open_time), CASE timeframe WHEN '1m' THEN 60 WHEN '5m' THEN 300 WHEN '15m' THEN 900 WHEN '1h' THEN 3600 WHEN '4h' THEN 14400 WHEN '1d' THEN 86400 END) = 0", name="ck_candle_interval"),
        CheckConstraint("timeframe IN ('1m','5m','15m','1h','4h','1d')", name="ck_timeframe"),
    )

# TimescaleDB creates this descending time index for the hypertable.
Index("candles_open_time_idx", CandleRecord.open_time.desc())

from sqlalchemy import Integer, JSON

class ResearchJobRecord(Base):
    __tablename__='research_jobs'
    job_id: Mapped[str]=mapped_column(String(36),primary_key=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    status: Mapped[str]=mapped_column(String(16))
    progress: Mapped[int]=mapped_column(Integer,default=0)
    cancel_requested: Mapped[bool]=mapped_column(Boolean,default=False)
    attempts: Mapped[int]=mapped_column(Integer,default=0)
    lease_owner: Mapped[str|None]=mapped_column(String(36))
    lease_until: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    request: Mapped[dict]=mapped_column(JSON)
    snapshot: Mapped[dict]=mapped_column(JSON)
    result: Mapped[dict|None]=mapped_column(JSON(none_as_null=True),nullable=True)
    error: Mapped[str|None]=mapped_column(String(160))
    __table_args__=(
        CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')",name='ck_research_status'),
        CheckConstraint('progress BETWEEN 0 AND 100 AND attempts BETWEEN 0 AND 3',name='ck_research_progress'),
        CheckConstraint("(status = 'SUCCEEDED' AND result IS NOT NULL AND progress = 100) OR (status <> 'SUCCEEDED' AND result IS NULL AND progress < 100)",name='ck_research_result'),
        Index('research_jobs_queue_idx','status','created_at'),
    )

class ResearchWorkerRecord(Base):
    __tablename__='research_workers'
    worker_id: Mapped[str]=mapped_column(String(36),primary_key=True)
    heartbeat_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))

class CandleRevisionRecord(Base):
    __tablename__='candle_revisions'
    revision_id: Mapped[str]=mapped_column(String(36),primary_key=True)
    instrument_id: Mapped[str]=mapped_column(String(160))
    timeframe: Mapped[str]=mapped_column(String(4))
    open_time: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    reason: Mapped[str]=mapped_column(String(160))
    previous: Mapped[dict]=mapped_column(JSON)
    revised: Mapped[dict]=mapped_column(JSON)
    __table_args__=(Index('candle_revisions_lookup_idx','instrument_id','timeframe','open_time'),)

class ReplaySessionRecord(Base):
    __tablename__='replay_sessions'
    session_id: Mapped[str]=mapped_column(String(36),primary_key=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    snapshot: Mapped[dict]=mapped_column(JSON)
    cursor: Mapped[int]=mapped_column(Integer)
    revision: Mapped[int]=mapped_column(Integer)
    __table_args__=(
        CheckConstraint("cursor >= 0 AND cursor <= json_array_length(snapshot->'dataset')",name='ck_replay_cursor'),
        CheckConstraint('revision >= 0',name='ck_replay_revision'),
    )

class PaperSessionRecord(Base):
    __tablename__ = 'paper_sessions'
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot: Mapped[dict] = mapped_column(JSON)
    ledger: Mapped[dict] = mapped_column(JSON)
    cursor: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    halt_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (
        CheckConstraint("cursor >= 0 AND cursor <= json_array_length(snapshot->'dataset')", name='ck_paper_cursor'),
        CheckConstraint('revision >= 0', name='ck_paper_revision'),
        CheckConstraint('halt_at IS NULL OR (halt_at >= 0 AND halt_at <= cursor)', name='ck_paper_halt'),
    )

class PaperStreamRecord(Base):
    __tablename__ = 'paper_streams'
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot: Mapped[dict] = mapped_column(JSON)
    observations: Mapped[list] = mapped_column(JSON)
    ledger: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    halt_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feed: Mapped[dict] = mapped_column(JSON)
    intent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    __table_args__ = (
        CheckConstraint("json_array_length(snapshot->'dataset') + json_array_length(observations) <= 1000", name='ck_stream_size'),
        CheckConstraint('revision >= 0', name='ck_stream_revision'),
        CheckConstraint("status IN ('RUNNING','PAUSED','STOPPED','BLOCKED','LIMIT_REACHED')", name='ck_stream_status'),
        CheckConstraint('halt_at IS NULL OR (halt_at >= 0 AND halt_at <= json_array_length(observations))', name='ck_stream_halt'),
        Index('paper_streams_active_idx','status','created_at'),
    )

class PaperWorkerRecord(Base):
    __tablename__ = 'paper_workers'
    worker_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class PaperOrderIntentRecord(Base):
    __tablename__ = 'paper_order_intents'
    intent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('paper_streams.session_id'))
    order_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    origin: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    transitions: Mapped[list] = mapped_column(JSON)
    __table_args__ = (
        UniqueConstraint('session_id','order_id',name='uq_paper_intent_order'),
        CheckConstraint("status IN ('FILLED','PARTIAL_CANCELLED','REJECTED')",name='ck_paper_intent_status'),
        CheckConstraint("origin IN ('BAR_ACCEPTANCE','HISTORICAL_MATERIALIZATION')",name='ck_paper_intent_origin'),
        Index('paper_intents_account_idx','session_id','order_id'),
    )

class PaperPreparationRecord(Base):
    __tablename__ = 'paper_preparations'
    preparation_id: Mapped[str] = mapped_column(String(64),primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('paper_streams.session_id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(32),nullable=True)
    authorization_version: Mapped[str | None] = mapped_column(String(64),nullable=True)
    funding_version: Mapped[str | None] = mapped_column(String(64),nullable=True)
    __table_args__ = (
        CheckConstraint("status IN ('PREPARED','CONSUMED','CANCELLED')",name='ck_preparation_status'),
        CheckConstraint('finished_at IS NULL OR finished_at >= created_at',name='ck_preparation_clock'),
        CheckConstraint("(status='PREPARED' AND finished_at IS NULL AND reason IS NULL) OR (status='CONSUMED' AND finished_at IS NOT NULL AND reason IS NULL) OR (status='CANCELLED' AND finished_at IS NOT NULL AND reason IS NOT NULL)",name='ck_preparation_outcome'),
        Index('paper_preparation_pending_idx','session_id',unique=True,postgresql_where=text("status='PREPARED'")),
        Index('paper_preparation_history_idx','session_id','created_at','preparation_id'),
    )

class PaperAuthorizationRecord(Base):
    __tablename__ = 'paper_authorizations'
    authorization_id: Mapped[str] = mapped_column(String(64),primary_key=True)
    preparation_id: Mapped[str] = mapped_column(ForeignKey('paper_preparations.preparation_id'))
    order_id: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint('preparation_id','order_id',name='uq_paper_authorization_order'),)

class PaperLifecycleCoverageRecord(Base):
    __tablename__ = 'paper_lifecycle_coverage'
    preparation_id: Mapped[str] = mapped_column(ForeignKey('paper_preparations.preparation_id'),primary_key=True)
    version: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    origin: Mapped[str] = mapped_column(String(32))
    __table_args__ = (CheckConstraint("origin IN ('NEW_PREPARATION','LEGACY_PENDING_ENROLLMENT')",name='ck_lifecycle_origin'),)

class PaperLifecycleEventRecord(Base):
    __tablename__ = 'paper_lifecycle_events'
    event_id: Mapped[str] = mapped_column(String(64),primary_key=True)
    authorization_id: Mapped[str] = mapped_column(ForeignKey('paper_authorizations.authorization_id'))
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    fill_id: Mapped[str | None] = mapped_column(String(64),nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint('authorization_id','sequence',name='uq_lifecycle_sequence'),
        UniqueConstraint('authorization_id','fill_id',name='uq_lifecycle_fill'),
        CheckConstraint('sequence >= 0 AND sequence <= 2',name='ck_lifecycle_sequence'),
        CheckConstraint("kind IN ('CREATED','FILL','OUTCOME')",name='ck_lifecycle_kind'),
        CheckConstraint("(kind='FILL' AND fill_id IS NOT NULL) OR (kind<>'FILL' AND fill_id IS NULL)",name='ck_lifecycle_fill'),
    )

class PaperControlRecord(Base):
    __tablename__ = 'paper_controls'
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(8))
    session_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(8))
    count: Mapped[int] = mapped_column(Integer)
    expected_revision: Mapped[int] = mapped_column(Integer)
    before_revision: Mapped[int] = mapped_column(Integer)
    after_revision: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(8))
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (
        CheckConstraint("kind IN ('sessions','streams')", name='ck_control_kind'),
        CheckConstraint("outcome IN ('APPLIED','NOOP','CONFLICT')", name='ck_control_outcome'),
        CheckConstraint("expected_revision >= 0 AND before_revision >= 0 AND after_revision >= 0 AND count BETWEEN 1 AND 10", name='ck_control_values'),
        CheckConstraint("(outcome = 'APPLIED' AND expected_revision = before_revision AND after_revision = before_revision + 1) OR (outcome = 'NOOP' AND expected_revision = before_revision AND after_revision = before_revision) OR (outcome = 'CONFLICT' AND expected_revision <> before_revision AND after_revision = before_revision)", name='ck_control_revision'),
        Index('paper_controls_account_idx', 'kind', 'session_id', 'recorded_at', 'event_id'),
    )

class PortfolioScenarioRecord(Base):
    __tablename__ = 'portfolio_scenarios'
    scenario_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    definition: Mapped[dict] = mapped_column(JSON)
    definition_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (Index('portfolio_scenarios_created_idx','created_at','scenario_id'),)

class PortfolioSnapshotRecord(Base):
    __tablename__ = 'portfolio_snapshots'
    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey('portfolio_scenarios.scenario_id'))
    request_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    report: Mapped[dict] = mapped_column(JSON)
    report_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint('scenario_id','request_id',name='uq_portfolio_snapshot_request'),
        Index('portfolio_snapshots_created_idx','scenario_id','created_at','snapshot_id'),
    )


class PaperFundingReservationRecord(Base):
    __tablename__ = 'paper_funding_reservations'
    preparation_id: Mapped[str] = mapped_column(ForeignKey('paper_preparations.preparation_id'),primary_key=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_sha256: Mapped[str] = mapped_column(String(64))

class PaperFundingOutcomeRecord(Base):
    __tablename__ = 'paper_funding_outcomes'
    preparation_id: Mapped[str] = mapped_column(ForeignKey('paper_funding_reservations.preparation_id'),primary_key=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_sha256: Mapped[str] = mapped_column(String(64))

# Separate explicit-quantity Paper engine; never reused by closed-bar streams.
class RequestedPaperAccountRecord(Base):
    __tablename__='requested_paper_accounts'
    account_id: Mapped[str]=mapped_column(String(128),primary_key=True)
    opening: Mapped[dict]=mapped_column(JSON)
    opening_sha256: Mapped[str]=mapped_column(String(64))
    current: Mapped[dict]=mapped_column(JSON)
    active_request_id: Mapped[str|None]=mapped_column(String(64),nullable=True)
    revision: Mapped[int]=mapped_column(Integer)
    control_version: Mapped[str|None]=mapped_column(String(64),nullable=True)
    health_version: Mapped[str|None]=mapped_column(String(64),nullable=True)

class RequestedPaperRequestRecord(Base):
    __tablename__='requested_paper_requests'
    request_id: Mapped[str]=mapped_column(String(64),primary_key=True)
    account_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_accounts.account_id'))
    client_request_id: Mapped[str]=mapped_column(String(128))
    ordinal: Mapped[int]=mapped_column(Integer)
    payload: Mapped[dict]=mapped_column(JSON)
    payload_sha256: Mapped[str]=mapped_column(String(64))
    __table_args__=(UniqueConstraint('account_id','client_request_id',name='uq_requested_paper_client'),
                   UniqueConstraint('account_id','ordinal',name='uq_requested_paper_ordinal'))

class RequestedPaperEventRecord(Base):
    __tablename__='requested_paper_events'
    dispatch_version: Mapped[str|None]=mapped_column(String(64),nullable=True)
    ownership_accepted_us: Mapped[int|None]=mapped_column(BigInteger,nullable=True)
    ownership_token: Mapped[int|None]=mapped_column(Integer,nullable=True)
    source_version: Mapped[str|None]=mapped_column(String(64),nullable=True)
    request_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_requests.request_id'),primary_key=True)
    sequence: Mapped[int]=mapped_column(Integer,primary_key=True)
    event_id: Mapped[str]=mapped_column(String(128))
    payload: Mapped[dict]=mapped_column(JSON)
    payload_sha256: Mapped[str]=mapped_column(String(64))
    summary: Mapped[dict]=mapped_column(JSON)
    summary_sha256: Mapped[str]=mapped_column(String(64))
    __table_args__=(UniqueConstraint('request_id','event_id',name='uq_requested_paper_event'),)

class RequestedPaperPolicyRecord(Base):
    __tablename__='requested_paper_policies'
    account_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_accounts.account_id'),primary_key=True)
    payload: Mapped[dict]=mapped_column(JSON)
    payload_sha256: Mapped[str]=mapped_column(String(64))

class RequestedPaperControlRecord(Base):
    __tablename__='requested_paper_controls'
    account_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_policies.account_id'),primary_key=True)
    sequence: Mapped[int]=mapped_column(Integer,primary_key=True)
    command_id: Mapped[str]=mapped_column(String(128))
    payload: Mapped[dict]=mapped_column(JSON)
    payload_sha256: Mapped[str]=mapped_column(String(64))
    __table_args__=(UniqueConstraint('account_id','command_id',name='uq_requested_paper_command'),)

class RequestedPaperGateRecord(Base):
    __tablename__='requested_paper_gates'
    request_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_requests.request_id'),primary_key=True)
    phase: Mapped[str]=mapped_column(String(16),primary_key=True)
    payload: Mapped[dict]=mapped_column(JSON)
    payload_sha256: Mapped[str]=mapped_column(String(64))
    __table_args__=(CheckConstraint("phase IN ('PREPARE','SUBMIT')",name='ck_requested_paper_gate_phase'),)

class RequestedPaperVoidRecord(Base):
    __tablename__='requested_paper_voids'
    request_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_requests.request_id'),primary_key=True)
    account_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_accounts.account_id'))
    command_id: Mapped[str]=mapped_column(String(128))
    payload: Mapped[dict]=mapped_column(JSON)
    payload_sha256: Mapped[str]=mapped_column(String(64))
    __table_args__=(UniqueConstraint('account_id','command_id',name='uq_requested_paper_void_command'),)


class RequestedPaperSourceRecord(Base):
    __tablename__='requested_paper_sources'
    request_id: Mapped[str]=mapped_column(String(64),primary_key=True)
    sequence: Mapped[int]=mapped_column(Integer,primary_key=True)
    account_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_accounts.account_id'))
    event_id: Mapped[str]=mapped_column(String(128))
    payload: Mapped[dict]=mapped_column(JSON)
    payload_sha256: Mapped[str]=mapped_column(String(64))
    __table_args__=(ForeignKeyConstraint(['request_id','sequence'],['requested_paper_events.request_id','requested_paper_events.sequence']),)


class RequestedPaperClaimRecord(Base):
    __tablename__='requested_paper_claims'
    request_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_requests.request_id'),primary_key=True)
    token: Mapped[int]=mapped_column(Integer,primary_key=True)
    account_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_accounts.account_id'))
    payload: Mapped[dict]=mapped_column(JSON)
    payload_sha256: Mapped[str]=mapped_column(String(64))
    __table_args__=(CheckConstraint('token>0',name='ck_requested_paper_claim_token'),)


class RequestedPaperDispatchRecord(Base):
    __tablename__='requested_paper_dispatches'
    request_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_requests.request_id'),primary_key=True)
    account_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_accounts.account_id'))
    token: Mapped[int]=mapped_column(Integer)
    client_id: Mapped[str]=mapped_column(String(64))
    payload: Mapped[dict]=mapped_column(JSON)
    payload_sha256: Mapped[str]=mapped_column(String(64))
    __table_args__=(ForeignKeyConstraint(['request_id','token'],['requested_paper_claims.request_id','requested_paper_claims.token']),
                   UniqueConstraint('client_id',name='uq_requested_paper_dispatch_client'))


class RequestedPaperAttemptRecord(Base):
    __tablename__='requested_paper_attempts'
    request_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_dispatches.request_id'),primary_key=True)
    ordinal: Mapped[int]=mapped_column(Integer,primary_key=True)
    account_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_accounts.account_id'))
    attempt_id: Mapped[str]=mapped_column(String(128))
    token: Mapped[int]=mapped_column(Integer)
    payload: Mapped[dict]=mapped_column(JSON)
    payload_sha256: Mapped[str]=mapped_column(String(64))
    __table_args__=(ForeignKeyConstraint(['request_id','token'],['requested_paper_claims.request_id','requested_paper_claims.token']),
                   UniqueConstraint('request_id','attempt_id',name='uq_requested_paper_attempt_id'),
                   CheckConstraint('ordinal>=0 AND ordinal<16',name='ck_requested_paper_attempt_ordinal'))


class PaperCapitalPoolRecord(Base):
    __tablename__='paper_capital_pools'
    pool_id: Mapped[str]=mapped_column(String(128),primary_key=True)
    payload: Mapped[dict]=mapped_column(JSON)
    payload_sha256: Mapped[str]=mapped_column(String(64))


class PaperCapitalMemberRecord(Base):
    __tablename__='paper_capital_members'
    account_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_accounts.account_id'),primary_key=True)
    pool_id: Mapped[str]=mapped_column(ForeignKey('paper_capital_pools.pool_id'))
    ordinal: Mapped[int]=mapped_column(Integer)
    payload: Mapped[dict]=mapped_column(JSON)
    payload_sha256: Mapped[str]=mapped_column(String(64))
    __table_args__=(UniqueConstraint('pool_id','ordinal',name='uq_paper_capital_member_ordinal'),
                   CheckConstraint('ordinal>=0 AND ordinal<100',name='ck_paper_capital_member_ordinal'))


class PaperCapitalAdmissionRecord(Base):
    __tablename__='paper_capital_admissions'
    request_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_requests.request_id'),primary_key=True)
    account_id: Mapped[str]=mapped_column(ForeignKey('paper_capital_members.account_id'))
    pool_id: Mapped[str]=mapped_column(ForeignKey('paper_capital_pools.pool_id'))
    payload: Mapped[dict]=mapped_column(JSON)
    payload_sha256: Mapped[str]=mapped_column(String(64))


class RequestedPaperHealthPolicyRecord(Base):
    __tablename__='requested_paper_health_policies'
    account_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_accounts.account_id'),primary_key=True)
    payload: Mapped[dict]=mapped_column(JSON)
    payload_sha256: Mapped[str]=mapped_column(String(64))


class RequestedPaperHealthGateRecord(Base):
    __tablename__='requested_paper_health_gates'
    request_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_requests.request_id'),primary_key=True)
    phase: Mapped[str]=mapped_column(String(16),primary_key=True)
    account_id: Mapped[str]=mapped_column(ForeignKey('requested_paper_health_policies.account_id'))
    payload: Mapped[dict]=mapped_column(JSON)
    payload_sha256: Mapped[str]=mapped_column(String(64))
    __table_args__=(CheckConstraint("phase IN ('PREPARE','SUBMIT')",name='ck_requested_health_gate_phase'),)
