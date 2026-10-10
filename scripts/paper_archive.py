"""Read-only financial archive capture and bounded offline replay."""
import argparse
import json
import os
from sqlalchemy import create_engine
from apps.api.settings import Settings
from core.paper import requested_archive as archive
from core.paper.fault_adapter import encoded
from core.exchange.private_spot import unique_object


def read(path):
    with open(path, 'rb') as stream:
        raw = stream.read(archive.MAX_BYTES + 1)
    if len(raw) > archive.MAX_BYTES:
        raise ValueError('Archive exceeds bounds')
    def reject(_):
        raise ValueError('Nonfinite JSON')
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject)


def write(path, value):
    archive.verify(value)
    raw = encoded(value).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    cap = commands.add_parser('capture')
    cap.add_argument('--account-id', required=True)
    cap.add_argument('--output', required=True)
    cap.add_argument('--operational', action='store_true')
    cap.add_argument('--receipts', action='store_true', help='Include attempts and staged inbox; requires --operational')
    check = commands.add_parser('verify')
    check.add_argument('--input', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'capture':
            if args.receipts and not args.operational:
                raise ValueError('Receipts require operational scope')
            engine = create_engine(Settings().database_url.get_secret_value(), hide_parameters=True)
            try:
                if args.operational:
                    from core.paper import requested_operational_archive as operational
                    value = archive.pack(operational.capture(engine, args.account_id, include_receipts=args.receipts))
                else:
                    value = archive.capture(engine, args.account_id)
                write(args.output, value)
            finally:
                engine.dispose()
        else:
            archive.verify(read(args.input))
    except Exception:
        parser.exit(1, 'Archive operation failed; check complete input, account and protected destination.\n')
    print('Bounded Paper archive verified; inspect version/scope for coverage. Retention and external execution are not authorized.')


if __name__ == '__main__':
    main()
