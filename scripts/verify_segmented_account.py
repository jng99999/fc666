"""Offline verification of complete segmented account consistency, not authority."""
import argparse
import json
from pathlib import Path
from core.paper import requested_segmented_account as contract
from core.exchange.private_spot import unique_object

def reject_constant(_):
    raise ValueError("Nonfinite JSON")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file',type=Path)
    args=parser.parse_args()
    try:
        with args.file.open('rb') as stream:
            data=stream.read(contract.MAX_BYTES+1)
        if len(data)>contract.MAX_BYTES:raise ValueError('Oversize input')
        value=contract.verify(json.loads(data,object_pairs_hook=unique_object,parse_constant=reject_constant))
    except Exception:
        print('Invalid segmented account evidence')
        return 1
    print(json.dumps({'version':value['version'],'requests':len(value['requests']),'total_events':value['total_events'],'revision':value['revision'],'verified':True,'ledger_application_supported':False}))
    return 0


if __name__=='__main__':raise SystemExit(main())
