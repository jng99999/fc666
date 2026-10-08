"""Offline immutable local capital pool capture verification."""
import argparse,json
from pathlib import Path
from core.paper.shared_capital_pool import verify
from core.paper.requested_execution import MAX_BYTES
from scripts.verify_requested_journal import unique_object


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path',type=Path)
    args=parser.parse_args()
    with args.path.open('rb') as file:raw=file.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('Shared capital export exceeds32MiB')
    report=verify(json.loads(raw,object_pairs_hook=unique_object))
    print('Verified local quote-capital forecast:',{'accounts':len(report['membership']),'reserved_quote':report['preview']['reserved_quote'],'submission_allowed':False})


if __name__=='__main__':main()
