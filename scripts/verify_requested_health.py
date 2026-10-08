"""Offline complete local health replay; no venue authentication or current approval."""
import argparse,json
from pathlib import Path
from core.market_data.quality import unique_object
from core.paper.requested_health import MAX_STORED_BYTES,verify


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('path',type=Path);args=parser.parse_args()
    with args.path.open('rb') as file:raw=file.read(MAX_STORED_BYTES+1)
    if len(raw)>MAX_STORED_BYTES:raise ValueError('Health export exceeds 32 MiB')
    result=verify(json.loads(raw,object_pairs_hook=unique_object))
    print('Verified complete recorded local health history:',len(result['gates']),'gates')


if __name__=='__main__':main()
