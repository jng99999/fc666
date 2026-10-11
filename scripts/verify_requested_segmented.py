"""Offline segmented replay verification; no database or exchange access."""
import argparse
import json
from core.paper import requested_segmented_execution as segmented
from core.exchange.private_spot import unique_object


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    args = parser.parse_args()
    try:
        with open(args.input, 'rb') as stream:
            raw = stream.read(segmented.MAX_BYTES + 1)
        if len(raw) > segmented.MAX_BYTES:
            raise ValueError('Segmented export exceeds bounds')
        def reject(_):
            raise ValueError('Nonfinite JSON')
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject)
        result = segmented.verify(value)
    except Exception:
        parser.exit(1, 'Segmented replay verification failed; complete bounded evidence required.\n')
    print(f"Verified {result['total_events']} source events; ledger application remains unsupported.")


if __name__ == '__main__':
    main()
