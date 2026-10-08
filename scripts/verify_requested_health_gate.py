"""Offline local health predicate replay; not authenticated control or current freshness."""
import argparse,json
from pathlib import Path
from core.market_data.quality import MAX_BYTES,unique_object
from core.paper.requested_health import verify_gate


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('path',type=Path);args=parser.parse_args()
    with args.path.open('rb') as file:raw=file.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('Health gate exceeds 256 KiB')
    result=verify_gate(json.loads(raw,object_pairs_hook=unique_object))
    print('Verified recorded local health gate:',result['phase'],result['decision'])


if __name__=='__main__':main()
