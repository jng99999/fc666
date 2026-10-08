"""Offline public-data quality verification; does not prove present freshness."""
import argparse
import json
from pathlib import Path
from core.market_data.quality import MAX_BYTES, unique_object, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    with args.path.open('rb') as file:
        raw = file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Quality export exceeds 256 KiB')
    report = verify(json.loads(raw, object_pairs_hook=unique_object))
    print('Verified recorded data quality:', report['status'], report['reasons'])


if __name__ == '__main__':
    main()
