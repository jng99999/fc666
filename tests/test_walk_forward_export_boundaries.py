import json
import os

import pytest
from core.backtest.walk_forward import simulate, verify
from scripts.verify_walk_forward import load
from tests.test_backtest import liquid, rules, cfg


def report():
    return simulate(liquid([10,12,14,16,30,28,25,27]),rules(),cfg(),strategy_id='ema_long_flat_v1',grid=[{'period':2},{'period':3}],train_bars=4,test_bars=4)


def test_offline_replay_and_detached_result(tmp_path):
    original=report(); path=tmp_path/'export.json';path.write_text(json.dumps(original))
    replay=verify(load(path));assert replay==original
    replay['folds'].clear();assert original['folds']


@pytest.mark.parametrize('field,value',[('trading_enabled',0),('trading_enabled',1),('run_id','bad')])
def test_exact_export_types(field,value):
    damaged=report();damaged[field]=value
    with pytest.raises(ValueError):verify(damaged)


@pytest.mark.parametrize('change',[{'cold_test_windows':1},{'trailing_unused_bars':0.0},{'extra':'field'},{'train_bars':True}])
def test_manifest_tampering(change):
    damaged=report();damaged['manifest'].update(change)
    with pytest.raises(ValueError):verify(damaged)


@pytest.mark.parametrize('field,value',[('dataset',[]),('dataset',[{}]*1001),('folds',[]),('folds',[{}]*9),('manifest',None)])
def test_preflight_bounds(field,value):
    damaged=report();damaged[field]=value
    with pytest.raises(ValueError):verify(damaged)


@pytest.mark.parametrize('raw',['{"x":1,"x":2}','{"x":NaN}','{"x":Infinity}','{"x":-Infinity}'])
def test_strict_json(tmp_path,raw):
    path=tmp_path/'bad.json';path.write_text(raw)
    with pytest.raises(ValueError):load(path)


def test_regular_file_only(tmp_path):
    target=tmp_path/'target';target.write_text('{}')
    link=tmp_path/'link';link.symlink_to(target)
    with pytest.raises(OSError):load(link)
    fifo=tmp_path/'fifo';os.mkfifo(fifo)
    with pytest.raises(ValueError):load(fifo)


def test_read_limit(tmp_path,monkeypatch):
    import scripts.verify_walk_forward as cli
    monkeypatch.setattr(cli,'MAX_BYTES',8)
    path=tmp_path/'large';path.write_bytes(b' '*9)
    with pytest.raises(ValueError):load(path)
