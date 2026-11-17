"""Archive stored sampled quotes without constructing or mutating a business Store.

Standard-library only; also supports ``python - ... < script`` over a read-only
operator transport. Provenance is caller-supplied evidence, not inferred truth.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
import time
from typing import BinaryIO, Callable
import zlib


VERSION = 'gold-stored-sampled-quotes-v1'
DESCRIPTION = ('Stored sampled bid/ask quotes; not a complete tick feed or raw HTTP archive. '
               'The source table has no per-row source or received-at evidence.')
SOURCE = 'swissquote.public:AT:AT:standard:XAU/USD'
CODE_FILES = ('apps/api/gold_service.py', 'core/gold/ledger.py', 'core/gold/market.py')
SHA256 = re.compile(r'^[0-9a-f]{64}$')
MAX_LINE_BYTES = 65536


class ArchiveError(ValueError):
    """Invalid, incomplete, unauthenticated or out-of-budget archive operation."""


@dataclass(frozen=True)
class Limits:
    max_rows: int = 1000000
    max_bytes: int = 268435456
    timeout_seconds: float = 60

    def __post_init__(self):
        if (type(self.max_rows) is not int or self.max_rows < 1
                or type(self.max_bytes) is not int or self.max_bytes < 1
                or type(self.timeout_seconds) not in (int, float)
                or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0):
            raise ArchiveError('Limits must be finite positive numbers; counts must be integers')


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _hash_string(value: object) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _timestamp(value: object) -> bool:
    return type(value) is int and 0 <= value <= 9223372036854775807


def _provenance(value: object) -> dict:
    required = {'claimed_source', 'deployment_image_id', 'collector_code_sha256',
                'archive_tool_sha256', 'evidence_sha256'}
    if not isinstance(value, dict) or set(value) != required:
        raise ArchiveError('Provenance requires exact source, image, collector, tool and evidence fields')
    image = value['deployment_image_id']
    codes = value['collector_code_sha256']
    if (value['claimed_source'] != SOURCE or not isinstance(image, str)
            or not image.startswith('sha256:') or not _hash_string(image[7:])
            or not isinstance(codes, dict) or set(codes) != set(CODE_FILES)
            or not all(_hash_string(v) for v in codes.values())
            or not _hash_string(value['archive_tool_sha256'])
            or not _hash_string(value['evidence_sha256'])):
        raise ArchiveError('Invalid provenance source or SHA256 evidence')
    return value


def _quote(ts: object, bid: object, ask: object, previous_ts: int | None) -> None:
    if not _timestamp(ts) or (previous_ts is not None and ts <= previous_ts):
        raise ArchiveError('Quote timestamps must be strictly increasing integer milliseconds')
    if not all(isinstance(v, str) and 0 < len(v) <= 128 for v in (bid, ask)):
        raise ArchiveError('Quote prices must be bounded decimal strings')
    try:
        left, right = Decimal(bid), Decimal(ask)
        if not (left.is_finite() and right.is_finite() and 0 < left <= right):
            raise ArchiveError('Quote bid/ask must be finite, positive and ordered')
        if str(left) != bid or str(right) != ask:
            raise ArchiveError('Quote strings must match stored Decimal string serialization')
    except InvalidOperation as error:
        raise ArchiveError('Invalid quote decimal string') from error


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ArchiveError('Archive operation exceeded its deadline')


class _HashedIO:
    def __init__(self, stream: BinaryIO, limit: int, deadline: float):
        self.stream, self.limit, self.deadline = stream, limit, deadline
        self.count = 0
        self.hash = hashlib.sha256()

    def _track(self, value: bytes) -> bytes:
        _check_deadline(self.deadline)
        if self.count+len(value) > self.limit:
            raise ArchiveError('Compressed archive exceeds max_bytes')
        self.count += len(value)
        self.hash.update(value)
        return value

    def write(self, value: bytes) -> int:
        self._track(value)
        written = self.stream.write(value)
        if written != len(value):
            raise ArchiveError('Incomplete archive output write')
        return written

    def read(self, size: int = -1) -> bytes:
        _check_deadline(self.deadline)
        if size < 0:
            size = self.limit-self.count+1
        return self._track(self.stream.read(min(size, self.limit-self.count+1)))

    def flush(self) -> None:
        self.stream.flush()


def _new_file(path: Path) -> tuple[BinaryIO, tuple[int, int]]:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ArchiveError('Destination already exists; refusing to overwrite') from error
    record = os.fstat(fd)
    return os.fdopen(fd, 'wb'), (record.st_dev, record.st_ino)


def _remove_owned(path: Path, identity: tuple[int, int]) -> None:
    """Never unlink a pre-existing or concurrently replaced destination."""
    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) == identity:
        path.unlink()


def export_quotes(database: Path, output: Path | BinaryIO, provenance: dict, *,
                  limits: Limits = Limits(), start_ms: int | None = None,
                  end_ms: int | None = None) -> dict:
    """Stream one SQLite read snapshot; never open the source in writable mode."""
    _provenance(provenance)
    if (start_ms is not None and not _timestamp(start_ms)
            or end_ms is not None and not _timestamp(end_ms)
            or start_ms is not None and end_ms is not None and start_ms >= end_ms):
        raise ArchiveError('Invalid half-open timestamp selection')
    source = Path(database).resolve(strict=True)
    if not source.is_file():
        raise ArchiveError('Source database must be a regular file')
    deadline = time.monotonic()+limits.timeout_seconds
    owned = isinstance(output, Path)
    identity = None
    connection = None
    stream = None
    try:
        connection = sqlite3.connect(source.as_uri()+'?mode=ro', uri=True,
                                     isolation_level=None, timeout=min(3, limits.timeout_seconds))
        connection.execute('PRAGMA query_only=ON')
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        connection.execute('BEGIN')
        # Executing the cursor before output pins the snapshot, including in WAL mode.
        conditions, parameters = [], []
        if start_ms is not None:
            conditions.append('ts>=?')
            parameters.append(start_ms)
        if end_ms is not None:
            conditions.append('ts<?')
            parameters.append(end_ms)
        where = ' WHERE '+' AND '.join(conditions) if conditions else ''
        cursor = connection.execute('SELECT ts,bid,ask FROM quotes'+where+' ORDER BY ts LIMIT ?',
                                    (*parameters, limits.max_rows+1))
        _check_deadline(deadline)
        if owned:
            stream, identity = _new_file(output)
        else:
            stream = output
        target = _HashedIO(stream, limits.max_bytes, deadline)
        header = {'kind': 'header', 'version': VERSION, 'description': DESCRIPTION,
                  'exported_at_ms': int(time.time()*1000), 'provenance': provenance,
                  'selection': {'start_ms': start_ms, 'end_ms': end_ms},
                  'source_read_mode': 'sqlite-mode-ro/query-only/read-transaction'}
        header_sha = chain = _sha(header)
        rows_hash = hashlib.sha256()
        count, raw_bytes, first_ts, previous_ts = 0, 0, None, None
        with gzip.GzipFile(filename='', fileobj=target, mode='wb', mtime=0) as archive:
            def emit(value: dict) -> None:
                nonlocal raw_bytes
                _check_deadline(deadline)
                line = _canonical(value)+b'\n'
                if len(line) > MAX_LINE_BYTES or raw_bytes+len(line) > limits.max_bytes:
                    raise ArchiveError('Uncompressed archive exceeds line or max_bytes bound')
                raw_bytes += len(line)
                archive.write(line)

            emit(header)
            for ts, bid, ask in cursor:
                if count >= limits.max_rows:
                    raise ArchiveError('Source selection exceeds max_rows; archive is incomplete')
                _quote(ts, bid, ask, previous_ts)
                value = {'kind': 'quote', 'index': count, 'ts': ts, 'bid': bid, 'ask': ask,
                         'previous_sha256': chain}
                chain = _sha(value)
                emit({**value, 'sha256': chain})
                rows_hash.update(_canonical([ts, bid, ask])+b'\n')
                if first_ts is None:
                    first_ts = ts
                previous_ts, count = ts, count+1
            footer = {'kind': 'footer', 'count': count, 'first_ts': first_ts, 'last_ts': previous_ts,
                      'rows_sha256': rows_hash.hexdigest(), 'last_record_sha256': chain,
                      'header_sha256': header_sha}
            emit({**footer, 'sha256': _sha(footer)})
        target.flush()
        _check_deadline(deadline)
        if owned:
            os.fsync(stream.fileno())
        return {**footer, 'archive_sha256': target.hash.hexdigest(), 'archive_bytes': target.count,
                'uncompressed_bytes': raw_bytes, 'source_total_changes': connection.total_changes}
    except sqlite3.Error as error:
        if time.monotonic() >= deadline:
            raise ArchiveError('Source query exceeded its deadline') from error
        raise ArchiveError('Read-only source query failed: '+str(error)) from error
    finally:
        failed = sys.exc_info()[0] is not None
        if connection is not None:
            connection.set_progress_handler(None, 0)
            connection.rollback()
            connection.close()
        if owned and stream is not None:
            stream.close()
            if failed and identity is not None:
                _remove_owned(output, identity)


def _header(value: dict) -> None:
    keys = {'kind', 'version', 'description', 'exported_at_ms', 'provenance',
            'selection', 'source_read_mode'}
    if (set(value) != keys or value['kind'] != 'header' or value['version'] != VERSION
            or value['description'] != DESCRIPTION or not _timestamp(value['exported_at_ms'])
            or value['source_read_mode'] != 'sqlite-mode-ro/query-only/read-transaction'):
        raise ArchiveError('Invalid archive header')
    _provenance(value['provenance'])
    selection = value['selection']
    if not isinstance(selection, dict) or set(selection) != {'start_ms', 'end_ms'}:
        raise ArchiveError('Invalid header selection')
    start, end = selection['start_ms'], selection['end_ms']
    if (start is not None and not _timestamp(start) or end is not None and not _timestamp(end)
            or start is not None and end is not None and start >= end):
        raise ArchiveError('Invalid header selection')


def _read_archive(path: Path, expected_sha256: str, limits: Limits,
                  consumer: Callable[[tuple[int, str, str]], None] | None = None) -> dict:
    if not _hash_string(expected_sha256):
        raise ArchiveError('A trusted expected compressed archive SHA256 is required')
    deadline = time.monotonic()+limits.timeout_seconds
    raw_bytes, count, first_ts, previous_ts = 0, 0, None, None
    rows_hash = hashlib.sha256()
    try:
        with Path(path).open('rb') as stream:
            record = os.fstat(stream.fileno())
            if not stat.S_ISREG(record.st_mode) or record.st_size > limits.max_bytes:
                raise ArchiveError('Archive must be a regular file within max_bytes')
            source = _HashedIO(stream, limits.max_bytes, deadline)
            with gzip.GzipFile(fileobj=source, mode='rb') as archive:
                def take() -> dict | None:
                    nonlocal raw_bytes
                    _check_deadline(deadline)
                    line = archive.readline(min(MAX_LINE_BYTES, limits.max_bytes-raw_bytes)+1)
                    if not line:
                        return None
                    raw_bytes += len(line)
                    if raw_bytes > limits.max_bytes or len(line) > MAX_LINE_BYTES:
                        raise ArchiveError('Uncompressed archive exceeds line or max_bytes bound')
                    if not line.endswith(b'\n'):
                        raise ArchiveError('Truncated JSONL record')
                    try:
                        value = json.loads(line)
                    except (ValueError, UnicodeError) as error:
                        raise ArchiveError('Invalid JSON record') from error
                    if not isinstance(value, dict) or _canonical(value)+b'\n' != line:
                        raise ArchiveError('Archive records must be canonical JSON objects')
                    return value

                header = take()
                if header is None:
                    raise ArchiveError('Missing header')
                _header(header)
                header_sha = chain = _sha(header)
                while True:
                    value = take()
                    if value is None:
                        raise ArchiveError('Missing footer; archive is incomplete')
                    if value.get('kind') == 'footer':
                        footer = {'kind': 'footer', 'count': count, 'first_ts': first_ts,
                                  'last_ts': previous_ts, 'rows_sha256': rows_hash.hexdigest(),
                                  'last_record_sha256': chain, 'header_sha256': header_sha}
                        if _canonical(value) != _canonical({**footer, 'sha256': _sha(footer)}):
                            raise ArchiveError('Footer does not match the verified quote prefix')
                        if take() is not None:
                            raise ArchiveError('Records after footer are forbidden')
                        break
                    if count >= limits.max_rows:
                        raise ArchiveError('Archive exceeds max_rows')
                    if (set(value) != {'kind', 'index', 'ts', 'bid', 'ask', 'previous_sha256', 'sha256'}
                            or value['kind'] != 'quote' or type(value['index']) is not int
                            or value['index'] != count or value['previous_sha256'] != chain):
                        raise ArchiveError('Invalid quote record or broken ordered chain')
                    ts, bid, ask = value['ts'], value['bid'], value['ask']
                    _quote(ts, bid, ask, previous_ts)
                    start, end = header['selection']['start_ms'], header['selection']['end_ms']
                    if start is not None and ts < start or end is not None and ts >= end:
                        raise ArchiveError('Quote falls outside the header selection')
                    payload = {k: v for k, v in value.items() if k != 'sha256'}
                    if value['sha256'] != _sha(payload):
                        raise ArchiveError('Quote SHA256 mismatch')
                    chain = value['sha256']
                    rows_hash.update(_canonical([ts, bid, ask])+b'\n')
                    if first_ts is None:
                        first_ts = ts
                    previous_ts, count = ts, count+1
                    if consumer is not None:
                        consumer((ts, bid, ask))
            if source.hash.hexdigest() != expected_sha256:
                raise ArchiveError('Archive SHA256 differs from the trusted export receipt')
            _check_deadline(deadline)
            return {**footer, 'archive_sha256': source.hash.hexdigest(),
                    'archive_bytes': source.count, 'uncompressed_bytes': raw_bytes,
                    'provenance': header['provenance'], 'selection': header['selection']}
    except (EOFError, gzip.BadGzipFile, zlib.error, UnicodeError, OverflowError) as error:
        raise ArchiveError('Invalid or truncated gzip/JSON archive') from error


def verify_archive(path: Path, expected_sha256: str, *, limits: Limits = Limits()) -> dict:
    return _read_archive(path, expected_sha256, limits)


def restore_archive(path: Path, destination: Path, expected_sha256: str, *,
                     limits: Limits = Limits()) -> dict:
    """Validate privately, then atomically publish a new quotes-only database."""
    # Resolve only the parent: a dangling or existing destination symlink must fail.
    destination = destination.parent.resolve(strict=True)/destination.name
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ArchiveError('Destination already exists; refusing to overwrite')
    # mkdtemp uses 0700; stage on the same filesystem for an atomic hard-link.
    with tempfile.TemporaryDirectory(prefix='.gold-quotes-', dir=destination.parent) as temporary:
        staged = Path(temporary)/'quotes.sqlite3'
        connection = sqlite3.connect(staged, isolation_level=None, timeout=min(3, limits.timeout_seconds))
        try:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('CREATE TABLE quotes(ts INTEGER PRIMARY KEY,bid TEXT NOT NULL,ask TEXT NOT NULL)')
            receipt = _read_archive(path, expected_sha256, limits,
                                    lambda row: connection.execute('INSERT INTO quotes VALUES(?,?,?)', row))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        staged.chmod(0o600)
        try:
            # Never replace: a concurrent creator wins and its file stays untouched.
            os.link(staged, destination)
        except FileExistsError as error:
            raise ArchiveError('Destination already exists; refusing to overwrite') from error
        except OSError as error:
            raise ArchiveError('Atomic hard-link restore publication failed: '+str(error)) from error
    return {**receipt, 'restored_quotes_only': True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('export', 'verify', 'restore'):
        sub = commands.add_parser(command)
        sub.add_argument('--max-rows', type=int, default=1000000)
        sub.add_argument('--max-bytes', type=int, default=268435456,
                         help='Limit for each compressed file and decompressed JSONL')
        sub.add_argument('--timeout-seconds', type=float, default=60)
        if command == 'export':
            sub.add_argument('--database', required=True, type=Path)
            sub.add_argument('--output', required=True, help='New file path, or - for pure binary stdout')
            provenance = sub.add_mutually_exclusive_group(required=True)
            provenance.add_argument('--provenance', type=Path)
            provenance.add_argument('--provenance-json',
                                    help='Inline non-secret evidence JSON for stdin execution')
            sub.add_argument('--start-ms', type=int)
            sub.add_argument('--end-ms', type=int)
        else:
            sub.add_argument('--archive', required=True, type=Path)
            sub.add_argument('--expected-sha256', required=True,
                             help='Trusted compressed SHA256 from the export receipt')
            if command == 'restore':
                sub.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        limits = Limits(args.max_rows, args.max_bytes, args.timeout_seconds)
        if args.command == 'export':
            if args.provenance is not None:
                with args.provenance.open('rb') as stream:
                    raw = stream.read(MAX_LINE_BYTES+1)
            else:
                raw = args.provenance_json.encode('utf-8')
            if len(raw) > MAX_LINE_BYTES:
                raise ArchiveError('Provenance file exceeds its size bound')
            provenance = json.loads(raw)
            output = sys.stdout.buffer if args.output == '-' else Path(args.output)
            result = export_quotes(args.database, output, provenance, limits=limits,
                                   start_ms=args.start_ms, end_ms=args.end_ms)
        elif args.command == 'verify':
            result = verify_archive(args.archive, args.expected_sha256, limits=limits)
        else:
            result = restore_archive(args.archive, args.destination, args.expected_sha256, limits=limits)
        print(json.dumps(result, sort_keys=True), file=sys.stderr if args.command == 'export' else sys.stdout)
        return 0
    except (ArchiveError, OSError, sqlite3.Error, ValueError) as error:
        print('archive_gold_quotes: '+str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
