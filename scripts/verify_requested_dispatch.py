"""Offline local dispatch evidence verification; no venue queries."""
import argparse
import json
from pathlib import Path
from core.paper.requested_execution import MAX_BYTES
from core.paper.requested_dispatch import verify
from scripts.verify_requested_journal import unique_object


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    with args.path.open('rb') as file:
        raw = file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Dispatch export exceeds 32 MiB')
    report = verify(json.loads(raw, object_pairs_hook=unique_object))
    print('Verified local dispatch:', {'requests':len(report['dispatches']), 'declared':sum(value['dispatch'] is not None for value in report['dispatches'])})


if __name__ == '__main__':
    main()
