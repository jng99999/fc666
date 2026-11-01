from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.gold.backtest import Costs
from core.gold.history import HOUR, aggregate
from core.strategy.registry import DEFINITIONS, lookback, resolve


class Variant(BaseModel):
    model_config = ConfigDict(extra='forbid')
    strategy: str = Field(max_length=64)
    parameters: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def valid(self):
        self.parameters = resolve(self.strategy).create(self.parameters)[1].model_dump(mode='json')
        return self


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    dataset_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    start: date
    end: date
    timeframes: list[Literal['1h', '4h']] = Field(min_length=1, max_length=2)
    variants: list[Variant] = Field(min_length=1, max_length=8)
    costs: Costs

    @model_validator(mode='after')
    def bounded(self):
        if not 90 <= (self.end-self.start).days <= 1830:
            raise ValueError('黄金历史窗口须为90日至5年，结束日不包含在内')
        if len(set(self.timeframes)) != len(self.timeframes):
            raise ValueError('重复的研究周期')
        keys = [(v.strategy, str(sorted(v.parameters.items()))) for v in self.variants]
        if len(set(keys)) != len(keys):
            raise ValueError('重复的策略参数')
        return self


def router_for(research, owner):
    router = APIRouter(prefix='/api/v1/gold/research')

    @router.get('/catalogue')
    def catalogue():
        return {'datasets': research.catalogue.list(), 'strategies': [d.describe() for d in DEFINITIONS.values()], 'trading_enabled': False}

    @router.get('/runs')
    def runs(request: Request, response: Response):
        return {'runs': research.list(owner(request, response, create=True)), 'trading_enabled': False}

    @router.post('/runs')
    def enqueue(body: ResearchRequest, request: Request, response: Response):
        identity = owner(request, response, create=True)
        data, hourly = research.catalogue.load(body.dataset_id)
        normalized = body.model_dump(mode='json', exclude={'request_id'})
        for key in ('start', 'end'):
            normalized[key] = int(datetime.combine(getattr(body, key), datetime.min.time(), tzinfo=timezone.utc).timestamp()*1000)
        if normalized['start'] < data['start'] or normalized['end'] > data['end']:
            raise ValueError('研究窗口超出冻结数据范围，不会自动缩短')
        warmup = max(100, *(lookback(v.strategy, v.parameters) for v in body.variants))
        # Reject every insufficient annual member before admitting any work.
        for timeframe in body.timeframes:
            bars = aggregate(hourly, int(timeframe[0]))
            for year in range(body.start.year, (body.end-date.resolution).year+1):
                a = max(normalized['start'], int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp()*1000))
                b = min(normalized['end'], int(datetime(year+1, 1, 1, tzinfo=timezone.utc).timestamp()*1000))
                indices = [i for i, bar in enumerate(bars) if a <= bar.start and bar.start+int(timeframe[0])*HOUR <= b]
                if not indices or max(indices[0], warmup) >= indices[-1]:
                    raise ValueError(f'{year} {timeframe} 完整柱不足，不能建立年度对比')
        return research.enqueue(identity, str(body.request_id), normalized)

    @router.get('/runs/{job_id}')
    def read(job_id: UUID, request: Request, response: Response):
        return research.read(str(job_id), owner(request, response))

    @router.get('/runs/{job_id}/manifest')
    def manifest(job_id: UUID, request: Request, response: Response):
        return research.result(str(job_id), owner(request, response))

    @router.get('/runs/{job_id}/members/{member}')
    def member(job_id: UUID, member: int, request: Request, response: Response):
        return research.result(str(job_id), owner(request, response), member)

    return router
