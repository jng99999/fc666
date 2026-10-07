"""Offline local event preview verification; no authenticated venue origin."""
import argparse,json
from pathlib import Path
from core.paper.requested_execution import MAX_BYTES
from core.paper.requested_event_preview import verify
from scripts.verify_requested_journal import unique_object


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('path',type=Path);args=parser.parse_args()
    with args.path.open('rb') as file:raw=file.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('Preview exceeds 32 MiB')
    result=verify(json.loads(raw,object_pairs_hook=unique_object))
    print('Verified local event preview:',{'classification':result['classification'],'event_persisted':False})


if __name__=='__main__':main()
