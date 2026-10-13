import json,os,subprocess
import pytest
from scripts.reproduce_backtest import load,reproduce
from tests.test_backtest import liquid,rules,cfg


def report(kind='ordinary'):
    from core.backtest.spot import simulate
    if kind=='holdout':
        from core.backtest.holdout import simulate
        return simulate(liquid([10,12,14,16,30,28,25,27]),rules(),cfg(),strategy_id='ema_long_flat_v1',parameters={'period':2},train_bars=4)
    return simulate(liquid([10,12,14,10,9]),rules(),cfg())


@pytest.mark.parametrize('kind',['ordinary','holdout'])
def test_export_and_actual_cli(tmp_path,kind):
    data=report(kind);path=tmp_path/'report.json';path.write_text(json.dumps(data))
    assert reproduce(load(path))==data['run_id']
    result=subprocess.run(['.venv/bin/python','-m','scripts.reproduce_backtest',str(path)],capture_output=True,text=True)
    assert result.returncode==0 and data['run_id'] in result.stdout


@pytest.mark.parametrize('kind',['ordinary','holdout'])
def test_boolean_integer_substitution(kind):
    data=report(kind);data['trading_enabled']=0
    with pytest.raises(ValueError,match='differs'):reproduce(data)


@pytest.mark.parametrize('raw',['{"x":1,"x":2}','{"x":NaN}','{"x":Infinity}','{"x":-Infinity}'])
def test_strict_json(tmp_path,raw):
    path=tmp_path/'bad';path.write_text(raw)
    with pytest.raises(ValueError):load(path)


def test_nonregular_and_symlink(tmp_path):
    path=tmp_path/'file';path.write_text('{}');link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises(OSError):load(link)
    fifo=tmp_path/'fifo';os.mkfifo(fifo)
    with pytest.raises(ValueError):load(fifo)


def test_bounded_read(tmp_path,monkeypatch):
    import scripts.reproduce_backtest as cli
    monkeypatch.setattr(cli,'MAX_BYTES',8)
    path=tmp_path/'large';path.write_bytes(b' '*9)
    with pytest.raises(ValueError):load(path)


@pytest.mark.parametrize('change',[{'dataset':[]},{'dataset':[{}]*1001},{'manifest':None},{'bad':float('nan')}])
def test_preflight_envelope(change):
    data=report();data.update(change)
    with pytest.raises(ValueError):reproduce(data)


def test_cli_failure_does_not_echo_input(tmp_path):
    path=tmp_path/'bad';path.write_text('{"private":"DO_NOT_ECHO","x":NaN}')
    result=subprocess.run(['.venv/bin/python','-m','scripts.reproduce_backtest',str(path)],capture_output=True,text=True)
    assert result.returncode==1 and 'verification failed' in result.stderr
    assert 'DO_NOT_ECHO' not in result.stdout+result.stderr and 'Traceback' not in result.stderr


@pytest.mark.parametrize('version',['v1','v2'])
def test_legacy_reports_keep_replay_compatibility(version):
    import importlib
    legacy=importlib.import_module('core.backtest.legacy_'+version)
    data=legacy.simulate(liquid([10,12,14,10,9]),rules(),legacy.BacktestConfig.model_validate(cfg().model_dump(mode='json')))
    assert reproduce(data)==data['run_id']
    data['trading_enabled']=0
    with pytest.raises(ValueError):reproduce(data)
