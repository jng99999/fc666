"""Recompute a complete local Paper journal offline; no origin authentication."""
import argparse
import json
from pathlib import Path
from core.paper.requested_execution import MAX_BYTES
from core.paper.requested_inspection import verify


def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate JSON object key')
        result[key]=value
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path',type=Path)
    args=parser.parse_args()
    with args.path.open('rb') as file:raw=file.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('Export exceeds 32 MiB')
    view=verify(json.loads(raw,object_pairs_hook=unique_object))
    print('Verified complete local Paper journal:',{'requests':len(view['requests']),'events':view['total_events'],
                                                  'revision':view['revision'],'has_active_request':view['active_request_id'] is not None})


if __name__=='__main__':main()
