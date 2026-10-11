"""Verify a complete segmented ledger archive offline, without database access."""
import argparse
import json
from pathlib import Path
from core.exchange.private_spot import unique_object
from core.paper import segmented_ledger_archive as archive, segmented_operational_archive as operational


def reject_constant(_):
    raise ValueError('Nonfinite JSON')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    args = parser.parse_args()
    try:
        with args.file.open('rb') as stream:
            data = stream.read(operational.MAX_BYTES + 1)
        if len(data) > operational.MAX_BYTES:
            raise ValueError('Oversize input')
        value = json.loads(data, object_pairs_hook=unique_object, parse_constant=reject_constant)
        contract = operational if isinstance(value, dict) and value.get('version') == operational.VERSION else archive
        if len(data) > contract.MAX_BYTES:
            raise ValueError('Oversize scope-specific input')
        result = contract.verify(value)
    except Exception:
        print('Invalid segmented ledger evidence')
        return 1
    financial = result['financial'] if contract is operational else result
    print(json.dumps({'version': result['version'], 'commits': len(financial['commits']),
                      'worker_records': len(result['worker_records']) if contract is operational else 0,
                      'revision': financial['commits'][-1]['report']['revision'], 'verified': True,
                      'operator_worker_binding_supported': False, 'external_submission_allowed': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
