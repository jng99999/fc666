"""Offline local assessment export verification; no venue queries."""
import argparse
import json
from pathlib import Path
from core.paper.requested_execution import MAX_BYTES
from core.paper.requested_attempts import verify_export as verify
from scripts.verify_requested_journal import unique_object


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--expected-receipts',type=Path,help='Trusted separately obtained ordered receipt SHA256 JSON array')
    args = parser.parse_args()
    with args.path.open('rb') as file:
        raw = file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Attempt export exceeds 32 MiB')
    reference = None
    if args.expected_receipts is not None:
        with args.expected_receipts.open('rb') as file:
            reference_raw=file.read(4097)
        if len(reference_raw)>4096:raise ValueError('Trusted receipt reference exceeds4KiB')
        reference=json.loads(reference_raw,object_pairs_hook=unique_object)
    report = verify(json.loads(raw, object_pairs_hook=unique_object),expected_receipt_sha256=reference)
    print('Verified local assessments:', {'request_id':report['request_id'], 'attempts':len(report['attempts'])})


if __name__ == '__main__':
    main()
