"""Offline complete causal walk-forward replay."""
import argparse,json
import os,stat
from pathlib import Path
from core.backtest.walk_forward import MAX_BYTES,verify
from core.market_data.quality import unique_object


def load(path):
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):raise ValueError('Regular export file required')
        raw=file.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('Walk-forward input exceeds32MiB')
    def invalid_constant(value):raise ValueError('Non-finite JSON rejected')
    return json.loads(raw,object_pairs_hook=unique_object,parse_constant=invalid_constant)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('path',type=Path);args=parser.parse_args()
    try:result=verify(load(args.path))
    except (OSError,ValueError,KeyError,TypeError):parser.exit(1,'Walk-forward export verification failed\n')
    print('Verified walk-forward folds:',len(result['folds']))


if __name__=='__main__':main()
