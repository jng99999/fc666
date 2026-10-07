"""Offline local Paper controls and admission verification; not identity authentication."""
import argparse
import json
from pathlib import Path
from core.paper.requested_execution import MAX_BYTES
from core.paper.requested_controls import verify
from scripts.verify_requested_journal import unique_object


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('path',type=Path);args=parser.parse_args()
    with args.path.open('rb') as file:raw=file.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('Export exceeds 32 MiB')
    view=verify(json.loads(raw,object_pairs_hook=unique_object))
    print('Verified local Paper controls:',{key:view[key] for key in ['coverage','state','revision']})


if __name__=='__main__':main()
