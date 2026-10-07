"""Offline local source-receipt verification; no authenticated venue origin."""
import argparse,json
from pathlib import Path
from core.paper.requested_execution import MAX_BYTES
from core.paper.requested_sources import verify
from scripts.verify_requested_journal import unique_object


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('path',type=Path);args=parser.parse_args()
    with args.path.open('rb') as file:raw=file.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('Source export exceeds 32 MiB')
    view=verify(json.loads(raw,object_pairs_hook=unique_object))
    print('Verified local source history:',{'events':len(view['sources']),'labeled':sum(item['receipt'] is not None for item in view['sources'])})


if __name__=='__main__':main()
