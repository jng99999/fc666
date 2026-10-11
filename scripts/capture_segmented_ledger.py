"""Capture full immutable local financial history into a protected new file."""
import argparse
import os
from sqlalchemy import create_engine
from apps.api.settings import Settings
from core.paper import segmented_ledger_archive as archive, segmented_operational_archive as operational
from core.paper.fault_adapter import encoded


def write(path, value):
    (operational if value.get('version') == operational.VERSION else archive).verify(value)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(encoded(value).encode())
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger-id', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--operational', action='store_true', help='Include complete declared worker fencing evidence')
    args = parser.parse_args()
    try:
        engine = create_engine(Settings().database_url.get_secret_value(), hide_parameters=True)
        try:
            write(args.output, (operational if args.operational else archive).capture(engine, args.ledger_id))
        finally:
            engine.dispose()
    except Exception:
        parser.exit(1, 'Segmented archive capture failed; check ledger and protected destination.\n')
    print('Complete local ledger evidence captured; external submission remains disabled.')


if __name__ == '__main__':
    main()
