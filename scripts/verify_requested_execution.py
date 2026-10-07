"""Verify a bounded explicit-request Paper export; internal consistency only."""
import argparse
import json
from pathlib import Path
from core.paper.requested_execution import MAX_BYTES, verify


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path',type=Path)
    args=parser.parse_args()
    with args.path.open('rb') as file:
        raw=file.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('Export exceeds 32 MiB')
    result=verify(json.loads(raw))
    print('Verified requested Paper execution:', {key:result[key] for key in ['state','filled_quantity','unfilled_quantity','local_source_sealed']})


if __name__=='__main__':main()
