"""Offline capacity consistency verification; no storage or venue access."""
import argparse
import json
from pathlib import Path
from core.paper.requested_execution import MAX_BYTES
from core.paper.requested_capacity import verify
from scripts.verify_requested_journal import unique_object


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    with args.path.open('rb') as file:
        raw = file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Capacity export exceeds 32 MiB')
    report = verify(json.loads(raw, object_pairs_hook=unique_object))
    print('Verified local capacity:', report['remaining_event_slots'], 'event slots;',
          'future settlement guaranteed:', report['future_settlement_guaranteed'])


if __name__ == '__main__':
    main()
