"""Reproduce an exported immutable history envelope without a database."""
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
from core.portfolio.history import VERSION, snapshot_visible


def record(value):
    result=dict(value)
    for key in ['scenario_id','request_id','snapshot_id']:
        if key in result and str(UUID(result[key]))!=result[key]:raise ValueError('Noncanonical identifier')
    result['created_at']=datetime.fromisoformat(result['created_at'])
    if result['created_at'].tzinfo is None:raise ValueError('Timezone required')
    return SimpleNamespace(**result)


def verify(value):
    if value['version']!=VERSION:raise ValueError('Unknown version')
    scenario=record(value['scenario']);snapshot=record(value)
    if snapshot.created_at<scenario.created_at:raise ValueError('Snapshot predates scenario')
    if datetime.fromisoformat(value['report']['as_of'])>snapshot.created_at:raise ValueError('Future report')
    if snapshot_visible(snapshot,scenario)!=value:raise ValueError('Envelope differs from reconstructed snapshot')
    return {'status':value['report']['status'],'accounts':len(value['report']['rows']),'snapshot_id':value['snapshot_id']}


if __name__=='__main__':
    path=Path(sys.argv[1])
    if path.stat().st_size>32*1024*1024:raise ValueError('Export exceeds 32 MiB')
    print('Verified Paper history:',verify(json.loads(path.read_text())))
