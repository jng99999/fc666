"""Real SQLite, gzip and CLI checks; no business Store or live account is opened."""
import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from scripts import archive_gold_quotes as archive


@pytest.fixture
def provenance():
    return {'claimed_source': archive.SOURCE, 'deployment_image_id': 'sha256:'+'1'*64,
            'collector_code_sha256': {name: '2'*64 for name in archive.CODE_FILES},
            'archive_tool_sha256': '3'*64, 'evidence_sha256': '4'*64}


def database(path, rows=((1000, '2000.00', '2000.25'), (11000, '2001', '2001.3')), *, strict=True):
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE quotes(ts INTEGER'+(' PRIMARY KEY' if strict else '')+
                           ',bid TEXT NOT NULL,ask TEXT NOT NULL)')
        connection.executemany('INSERT INTO quotes VALUES(?,?,?)', rows)
        connection.execute('CREATE TABLE private_ledger(secret TEXT)')
        connection.execute("INSERT INTO private_ledger VALUES('must not be archived')")
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def records(path):
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]


def write_records(path, values):
    path.write_bytes(gzip.compress(b''.join(archive._canonical(v)+b'\n' for v in values), mtime=0))
    return digest(path)


def rechain(values):
    """Make a structurally rehashed attack to test semantic checks independently."""
    header = values[0]
    header_sha = chain = archive._sha(header)
    rows_sha = hashlib.sha256()
    rows = values[1:-1]
    for i, row in enumerate(rows):
        row.update(index=i, previous_sha256=chain)
        row.pop('sha256', None)
        chain = archive._sha(row)
        row['sha256'] = chain
        rows_sha.update(archive._canonical([row['ts'], row['bid'], row['ask']])+b'\n')
    footer = {'kind': 'footer', 'count': len(rows),
              'first_ts': rows[0]['ts'] if rows else None, 'last_ts': rows[-1]['ts'] if rows else None,
              'rows_sha256': rows_sha.hexdigest(), 'last_record_sha256': chain, 'header_sha256': header_sha}
    return [header, *rows, {**footer, 'sha256': archive._sha(footer)}]


@pytest.fixture
def exported(tmp_path, provenance):
    source = database(tmp_path/'business.sqlite3')
    path = tmp_path/'quotes.jsonl.gz'
    receipt = archive.export_quotes(source, path, provenance)
    return source, path, receipt


def test_read_only_snapshot_restores_only_exact_quotes(exported, tmp_path, monkeypatch):
    source, path, receipt = exported
    before = digest(source)
    real_connect = sqlite3.connect
    reads = []

    def observed_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        if isinstance(args[0], str) and args[0].startswith('file:'):
            assert args[0].endswith('?mode=ro') and kwargs['uri'] is True
            connection.set_authorizer(lambda action, table, column, *rest:
                                      (reads.append((action, table, column)) or sqlite3.SQLITE_OK))
        return connection

    monkeypatch.setattr(archive.sqlite3, 'connect', observed_connect)
    second = tmp_path/'second.gz'
    again = archive.export_quotes(source, second, receipt_to_provenance(path))
    assert again['source_total_changes'] == 0
    assert (sqlite3.SQLITE_PRAGMA, 'query_only', 'ON') in reads
    assert all(table == 'quotes' for action, table, column in reads if action == sqlite3.SQLITE_READ)
    assert {column for action, table, column in reads if action == sqlite3.SQLITE_READ} == {'ts', 'bid', 'ask'}
    assert digest(source) == before
    verified = archive.verify_archive(path, receipt['archive_sha256'])
    assert verified['count'] == 2 and verified['rows_sha256'] == receipt['rows_sha256']
    destination = tmp_path/'restored.sqlite3'
    restored = archive.restore_archive(path, destination, receipt['archive_sha256'])
    assert restored['restored_quotes_only'] is True
    assert destination.stat().st_mode & 0o777 == 0o600
    with real_connect(destination) as connection, real_connect(source) as original:
        assert connection.execute('SELECT ts,bid,ask FROM quotes ORDER BY ts').fetchall() == original.execute(
            'SELECT ts,bid,ask FROM quotes ORDER BY ts').fetchall()
        assert connection.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall() == [('quotes',)]
    assert b'must not be archived' not in gzip.decompress(path.read_bytes())
    assert b'private_ledger' not in gzip.decompress(path.read_bytes())


def receipt_to_provenance(path):
    return records(path)[0]['provenance']


def test_wal_writer_does_not_change_export_snapshot(tmp_path, provenance):
    source = database(tmp_path/'source.sqlite3')
    writer = sqlite3.connect(source)
    writer.execute('PRAGMA journal_mode=WAL')

    class DuringOutput(io.BytesIO):
        changed = False

        def write(self, value):
            if not self.changed:
                self.changed = True
                writer.execute("INSERT INTO quotes VALUES(21000,'2002','2002.25')")
                writer.commit()
            return super().write(value)

    output = DuringOutput()
    try:
        result = archive.export_quotes(source, output, provenance)
        assert output.changed and result['count'] == 2
        assert writer.execute('SELECT count(*) FROM quotes').fetchone()[0] == 3
    finally:
        writer.close()


@pytest.mark.parametrize('target', ['file', 'empty', 'symlink', 'directory'])
def test_existing_destination_is_never_overwritten(exported, tmp_path, provenance, target):
    source, path, receipt = exported
    destination = tmp_path/'existing'
    if target == 'directory':
        destination.mkdir()
    elif target == 'symlink':
        destination.symlink_to(tmp_path/'does-not-exist')
    else:
        destination.write_bytes(b'keep' if target == 'file' else b'')
    with pytest.raises(archive.ArchiveError, match='already exists'):
        archive.export_quotes(source, destination, provenance)
    with pytest.raises(archive.ArchiveError, match='already exists'):
        archive.restore_archive(path, destination, receipt['archive_sha256'])
    if target == 'file':
        assert destination.read_bytes() == b'keep'
    if target == 'symlink':
        assert destination.is_symlink() and not destination.exists()


@pytest.mark.parametrize('race', ['create', 'replace', 'dangling_symlink'])
def test_restore_publication_race_preserves_concurrent_destination(exported, tmp_path, monkeypatch, race):
    _, path, receipt = exported
    destination = tmp_path/'racing.sqlite3'
    actual_link = archive.os.link

    def concurrent_creator(staged, target):
        assert Path(staged).parent.stat().st_mode & 0o777 == 0o700
        if race == 'dangling_symlink':
            destination.symlink_to(tmp_path/'absent-target')
        elif race == 'replace':
            destination.write_bytes(b'first concurrent file')
            replacement = tmp_path/'replacement'
            replacement.write_bytes(b'keep concurrent replacement')
            replacement.replace(destination)
        else:
            destination.write_bytes(b'keep concurrent replacement')
        return actual_link(staged, target)

    monkeypatch.setattr(archive.os, 'link', concurrent_creator)
    with pytest.raises(archive.ArchiveError, match='already exists'):
        archive.restore_archive(path, destination, receipt['archive_sha256'])
    if race == 'dangling_symlink':
        assert destination.is_symlink() and not destination.exists()
    else:
        assert destination.read_bytes() == b'keep concurrent replacement'
    assert not list(tmp_path.glob('.gold-quotes-*'))


def test_unsupported_atomic_restore_publication_has_no_overwrite_fallback(exported, tmp_path, monkeypatch):
    _, path, receipt = exported
    destination = tmp_path/'no-hard-links.sqlite3'

    def unsupported(*args):
        raise OSError('Hard links unavailable')

    monkeypatch.setattr(archive.os, 'link', unsupported)
    with pytest.raises(archive.ArchiveError, match='Atomic hard-link'):
        archive.restore_archive(path, destination, receipt['archive_sha256'])
    assert not destination.exists() and not list(tmp_path.glob('.gold-quotes-*'))


@pytest.mark.parametrize('row', [(-1, '1', '2'), (1.5, '1', '2'), ('bad', '1', '2'),
                                (1000, 'NaN', '2'), (1000, '1', 'Infinity'),
                                (1000, '-1', '2'), (1000, '0', '1'), (1000, '3', '2'),
                                (1000, 'bad', '2'), (1000, '1'*129, '2'),
                                (1000, ' 1 ', '2'), (1000, '1_000', '2000')])
def test_invalid_stored_quotes_fail_and_remove_partial_export(tmp_path, provenance, row):
    source = database(tmp_path/'invalid.sqlite3', rows=(row,), strict=False)
    output = tmp_path/'new.gz'
    before = digest(source)
    with pytest.raises(archive.ArchiveError):
        archive.export_quotes(source, output, provenance)
    assert not output.exists() and digest(source) == before


def test_duplicate_stored_timestamps_are_not_silently_collapsed(tmp_path, provenance):
    source = database(tmp_path/'duplicate.sqlite3', rows=((1000, '1', '2'), (1000, '1', '2')), strict=False)
    with pytest.raises(archive.ArchiveError, match='strictly increasing'):
        archive.export_quotes(source, tmp_path/'duplicate.gz', provenance)


@pytest.mark.parametrize('attack', ['price', 'header', 'footer', 'footer_bool', 'extra_footer',
                                   'extra_row', 'missing_footer', 'duplicate_ts', 'backwards',
                                   'nonfinite', 'noninteger', 'selection', 'reordered'])
def test_archive_tampering_is_rejected_and_restore_rolls_back(exported, tmp_path, attack):
    _, path, receipt = exported
    values = records(path)
    if attack == 'price':
        values[1]['bid'] = '1999'
    elif attack == 'header':
        values[0]['provenance']['claimed_source'] = 'histdata'
    elif attack == 'footer':
        values[-1]['count'] += 1
    elif attack == 'footer_bool':
        values = rechain(values[:-2]+values[-1:])
        values[-1]['count'] = True
    elif attack == 'extra_footer':
        values.append(copy.deepcopy(values[-1]))
    elif attack == 'extra_row':
        values.append(copy.deepcopy(values[1]))
    elif attack == 'missing_footer':
        values.pop()
    elif attack in ('duplicate_ts', 'backwards', 'nonfinite', 'noninteger', 'selection'):
        if attack == 'duplicate_ts':
            values[2]['ts'] = values[1]['ts']
        elif attack == 'backwards':
            values[2]['ts'] = values[1]['ts']-1
        elif attack == 'nonfinite':
            values[1]['ask'] = 'NaN'
        elif attack == 'noninteger':
            values[1]['ts'] = True
        else:
            values[0]['selection']['start_ms'] = 2000
        values = rechain(values)
    elif attack == 'reordered':
        values[1], values[2] = values[2], values[1]
    damaged = tmp_path/'damaged.gz'
    rewritten_sha = write_records(damaged, values)
    # Even with the new compressed hash supplied, semantic/chain errors must fail.
    with pytest.raises(archive.ArchiveError):
        archive.verify_archive(damaged, rewritten_sha)
    destination = tmp_path/'bad-restore.sqlite3'
    with pytest.raises(archive.ArchiveError):
        archive.restore_archive(damaged, destination, rewritten_sha)
    assert not destination.exists()


def test_trusted_outer_hash_rejects_fully_rehashed_replacement(exported, tmp_path):
    _, path, receipt = exported
    values = records(path)
    values[1].update(bid='1999.0', ask='1999.25')
    damaged = tmp_path/'rehashed.gz'
    write_records(damaged, rechain(values))
    with pytest.raises(archive.ArchiveError, match='trusted export receipt'):
        archive.verify_archive(damaged, receipt['archive_sha256'])


@pytest.mark.parametrize('cut', [1, 8, 30])
def test_truncated_gzip_is_rejected(exported, tmp_path, cut):
    _, path, _ = exported
    damaged = tmp_path/'truncated.gz'
    damaged.write_bytes(path.read_bytes()[:-cut])
    with pytest.raises(archive.ArchiveError):
        archive.verify_archive(damaged, digest(damaged))


def test_empty_and_half_open_selections_are_explicit(exported, tmp_path, provenance):
    source, _, _ = exported
    selected = tmp_path/'selection.gz'
    result = archive.export_quotes(source, selected, provenance, start_ms=1000, end_ms=11000)
    assert archive.verify_archive(selected, result['archive_sha256'])['count'] == 1
    empty = tmp_path/'empty.gz'
    result = archive.export_quotes(source, empty, provenance, start_ms=99999)
    checked = archive.verify_archive(empty, result['archive_sha256'])
    assert checked['count'] == 0 and checked['first_ts'] is None and checked['last_ts'] is None
    archive.restore_archive(empty, tmp_path/'empty.sqlite3', result['archive_sha256'])


def test_capacity_and_deadline_fail_closed(exported, tmp_path, provenance, monkeypatch):
    source, path, receipt = exported
    with pytest.raises(archive.ArchiveError, match='max_rows'):
        archive.export_quotes(source, tmp_path/'rows.gz', provenance, limits=archive.Limits(max_rows=1))
    assert not (tmp_path/'rows.gz').exists()
    with pytest.raises(archive.ArchiveError, match='max_rows'):
        archive.verify_archive(path, receipt['archive_sha256'], limits=archive.Limits(max_rows=1))
    with pytest.raises(archive.ArchiveError, match='max_bytes'):
        archive.export_quotes(source, tmp_path/'bytes.gz', provenance, limits=archive.Limits(max_bytes=20))
    with pytest.raises(archive.ArchiveError, match='max_bytes'):
        archive.verify_archive(path, receipt['archive_sha256'], limits=archive.Limits(max_bytes=20))
    ticks = iter([0, 2, 2, 2, 2])
    monkeypatch.setattr(archive.time, 'monotonic', lambda: next(ticks, 2))
    with pytest.raises(archive.ArchiveError, match='deadline'):
        archive.export_quotes(source, tmp_path/'timeout.gz', provenance, limits=archive.Limits(timeout_seconds=1))
    assert not (tmp_path/'timeout.gz').exists()


@pytest.mark.parametrize('limits', [{'max_rows': 0}, {'max_bytes': -1}, {'timeout_seconds': float('inf')},
                                  {'timeout_seconds': 0}, {'max_rows': True}])
def test_invalid_budgets_rejected(limits):
    with pytest.raises(archive.ArchiveError):
        archive.Limits(**limits)


def test_provenance_requires_explicit_evidence(exported, tmp_path, provenance):
    source, _, _ = exported
    provenance.pop('evidence_sha256')
    with pytest.raises(archive.ArchiveError, match='Provenance requires'):
        archive.export_quotes(source, tmp_path/'missing-evidence.gz', provenance)


@pytest.mark.parametrize('inline', [False, True])
def test_cli_stdin_execution_has_binary_stdout_and_receipt_stderr(exported, tmp_path, provenance, inline):
    source, _, _ = exported
    tool = Path(archive.__file__)
    provenance['archive_tool_sha256'] = digest(tool)
    evidence = tmp_path/'provenance.json'
    evidence.write_text(json.dumps(provenance))
    evidence_args = ['--provenance-json', json.dumps(provenance)] if inline else ['--provenance', str(evidence)]
    result = subprocess.run([sys.executable, '-', 'export', '--database', str(source), '--output', '-',
                             *evidence_args], input=tool.read_bytes(), capture_output=True,
                            timeout=10)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stderr)
    assert result.stdout.startswith(b'\x1f\x8b')
    path = tmp_path/'stdin.gz'
    path.write_bytes(result.stdout)
    assert digest(path) == receipt['archive_sha256']
    command = subprocess.run([sys.executable, str(tool), 'verify', '--archive', str(path),
                              '--expected-sha256', receipt['archive_sha256']], capture_output=True,
                             timeout=10)
    assert command.returncode == 0 and json.loads(command.stdout)['count'] == 2
    assert not command.stderr
