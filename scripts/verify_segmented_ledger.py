"""Verify a complete segmented ledger archive offline, without database access."""
import argparse
import json
from pathlib import Path
from core.exchange.private_spot import unique_object
from core.paper import segmented_ledger_archive as archive


def reject_constant(_):
    raise ValueError('Nonfinite JSON')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    args = parser.parse_args()
    try:
        with args.file.open('rb') as stream:
            data = stream.read(archive.MAX_BYTES + 1)
        if len(data) > archive.MAX_BYTES:
            raise ValueError('Oversize input')
        result = archive.verify(json.loads(data, object_pairs_hook=unique_object, parse_constant=reject_constant))
    except Exception:
        print('Invalid segmented ledger evidence')
        return 1
    print(json.dumps({'version': result['version'], 'commits': len(result['commits']),
                      'revision': result['commits'][-1]['report']['revision'], 'verified': True,
                      'operator_worker_binding_supported': False, 'external_submission_allowed': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
