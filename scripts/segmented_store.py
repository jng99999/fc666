"""Local evidence storage and verification; no account-ledger writes."""
import argparse
import json
from core.paper import segmented_store as store
from core.paper.requested_segmented_execution import SEGMENT_BYTES
from core.exchange.private_spot import unique_object


def read(path):
    with open(path,'rb') as stream:
        raw=stream.read(SEGMENT_BYTES+1)
    if len(raw)>SEGMENT_BYTES:
        raise ValueError('Input exceeds bounds')
    def reject(_):
        raise ValueError('Nonfinite JSON')
    return json.loads(raw,object_pairs_hook=unique_object,parse_constant=reject)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    init=commands.add_parser('init');init.add_argument('--request-file',required=True);init.add_argument('--directory',required=True)
    append=commands.add_parser('append');append.add_argument('--batch-file',required=True);append.add_argument('--directory',required=True);append.add_argument('--expected-node-sha256',required=True)
    check=commands.add_parser('verify');check.add_argument('--directory',required=True)
    args=parser.parse_args()
    try:
        if args.command=='init':
            index=store.create(args.directory,read(args.request_file))
        elif args.command=='append':
            index=store.append(args.directory,read(args.batch_file),expected_node_sha256=args.expected_node_sha256)
        else:
            index=store.load(args.directory)['index']
    except Exception:
        parser.exit(1,'Evidence-store operation failed; check protected complete evidence, capacity and checkpoint.\n')
    print(json.dumps(dict(index=index,ledger_application_supported=False,worker_authorization_supported=False,external_submission_allowed=False),sort_keys=True))


if __name__=='__main__':main()
