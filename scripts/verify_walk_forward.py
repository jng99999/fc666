"""Offline complete causal walk-forward replay."""
import argparse,json
from pathlib import Path
from core.backtest.walk_forward import MAX_BYTES,verify
from core.market_data.quality import unique_object


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('path',type=Path);args=parser.parse_args()
    with args.path.open('rb') as file:raw=file.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('Walk-forward input exceeds32MiB')
    result=verify(json.loads(raw,object_pairs_hook=unique_object));print('Verified walk-forward folds:',len(result['folds']))


if __name__=='__main__':main()
