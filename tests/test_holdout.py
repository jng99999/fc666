from copy import deepcopy
import pytest
from core.backtest.holdout import simulate
from core.backtest.spot import simulate as spot
from tests.test_backtest import liquid,rules,cfg
from scripts.reproduce_backtest import reproduce


def run(values,split=4,**options):
    return simulate(liquid(values),rules(),cfg(),strategy_id='ema_long_flat_v1',parameters={'period':2},train_bars=split,**options)


def test_separate_funds_indicators_and_no_crossing_pending_signal():
    values=[10,12,14,16,30,28,25,27,29]
    result=run(values)
    assert result['train']==spot(liquid(values)[:4],rules(),cfg())
    assert result['test']==spot(liquid(values)[4:],rules(),cfg())
    assert result['train']['pending_final_signal'] is not None
    assert result['test']['equity'][0]['cash']=='1000'
    assert result['test']['equity'][0]['quantity']=='0'
    assert result['test']['fills'][0]['decision_at']>=result['manifest']['split_at']
    assert all(fill['execution_at']>=result['manifest']['split_at'] for fill in result['test']['fills'])
    assert len(result['test']['signals'])==len(values)-4-1
    assert reproduce(result)==result['run_id']


def test_future_changes_cannot_change_training_and_past_cannot_change_cold_test():
    original=run([10,12,14,16,30,28,25,27,29])
    future=run([10,12,14,16,30,300,250,270,290])
    past=run([100,120,140,160,30,28,25,27,29])
    assert original['train']==future['train']
    assert original['test']==past['test']
    assert original['run_id']!=future['run_id']!=past['run_id']
    assert original['train']['manifest']['parameters']==original['test']['manifest']['parameters']


@pytest.mark.parametrize('split',[True,2.0,0,2,7,9])
def test_invalid_split(split):
    with pytest.raises(ValueError):run([10,12,14,16,30,28,25,27,29],split)


def test_sma_and_export_tamper_rejection():
    result=simulate(liquid([10,12,14,16,30,28,25,27,29,31]),rules(),cfg(),strategy_id='sma_long_flat_v1',parameters={'fast':2,'slow':3},train_bars=5)
    assert result['train']['manifest']['parameters']==result['test']['manifest']['parameters']=={'fast':2,'slow':3}
    tampered=deepcopy(result);tampered['test']['metrics']['net_pnl']='99'
    with pytest.raises(ValueError,match='differs'):reproduce(tampered)
    tampered=deepcopy(result);tampered['manifest']['split_at']='2020-01-01T00:00:00+00:00'
    with pytest.raises(ValueError,match='differs'):reproduce(tampered)
    changed=deepcopy(result);changed['dataset'][5]['volume']='9999'
    with pytest.raises(ValueError,match='differs'):reproduce(changed)


def test_global_progress_boundary_and_cancellation():
    progress=[]
    result=run([10,12,14,16,30,28,25,27,29],checkpoint=lambda done,total:progress.append((done,total)))
    assert progress[0]==(0,9) and progress[-1]==(9,9)
    assert [done for done,total in progress]==sorted(done for done,total in progress)
    class Cancel(Exception):pass
    def cancel(done,total):
        if done>=4:raise Cancel()
    with pytest.raises(Cancel):run([10,12,14,16,30,28,25,27,29],checkpoint=cancel)
