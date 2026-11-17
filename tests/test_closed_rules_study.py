"""Temporary synthetic files validate offline CLI receipts, never public history claims."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from core.backtest.spot import digest
from scripts.study_closed_rules import load_history, study, verify_result
from tests.test_price_action_rules import trend_fixture, bar


def inputs(tmp_path):
    rows=trend_fixture()
    rows.append(bar(len(rows), rows[-1].close))
    values=[row.model_dump(mode='json') for row in rows]
    directory=tmp_path/'synthetic-history';directory.mkdir()
    path=directory/'BTCUSDT-1h.json'
    value={'instrument':'BTCUSDT','source':'bitget.public','timeframe':'1h','bars':values,
           'dataset_sha256':digest(values),'synthetic':False}
    path.write_text(json.dumps(value))  # An explicit test-only envelope, not a published data file.
    protocol=json.loads((Path(__file__).parents[1]/'docs/closed-rules-backtest-protocol.json').read_text())
    protocol.update(products=['BTCUSDT'],timeframes=['1h'],bars_per_scope=len(rows),
        end_exclusive=rows[-1].close_time.isoformat(),windows={'FULL':[0,len(rows)]},rules=['TREND_PULLBACK'])
    return directory,path,value,protocol


def test_modified_history_never_reuses_an_old_digest(tmp_path):
    _,path,value,_=inputs(tmp_path)
    assert len(load_history(path)[1])>64
    value['bars'][-1]['close']=str(Decimal(value['bars'][-1]['close'])+Decimal('.01'))
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='hash/provenance'):
        load_history(path)


def test_actual_study_repeat_preserves_full_fixed_summary_and_strict_zero_entries(tmp_path):
    directory,_,_,protocol=inputs(tmp_path)
    output=tmp_path/'results'
    study(directory,output,protocol)
    original=(output/'study.json').read_bytes()
    data=json.loads(original)
    assert data['strict_gate']=={'cases':2,'fills':0,'verified':True}
    assert data['gold_status']['state']=='UNAVAILABLE'
    assert data['live_eligible'] is False and data['historical_news_verified'] is False
    study(directory,output,protocol)
    assert (output/'study.json').read_bytes()==original


def test_full_saved_receipt_is_actually_replayed_and_detects_tampered_results(tmp_path):
    directory,_,_,protocol=inputs(tmp_path)
    output=tmp_path/'results';study(directory,output,protocol)
    summary=json.loads((output/'study.json').read_text())
    case=next(row for row in summary['results'] if row['news_policy']=='UNCOVERED_RESEARCH')
    path=output/'receipts'/(case['case_id']+'.json')
    verify_result(path)
    value=json.loads(path.read_text());value['metrics']['net_pnl']='999999'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='differs'):
        verify_result(path)
