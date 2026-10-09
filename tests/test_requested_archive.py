from copy import deepcopy
import os
import subprocess
import sys
import pytest
from core.paper import requested_archive as archive, requested_inspection as inspection
from core.paper.fault_adapter import sha, encoded
from tests.test_requested_inspection import fixture_view
from scripts.paper_archive import read, write
from tests.test_integration import database


def test_archive_roundtrip_preserves_pending_holds_and_financial_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, 'SEGMENT_BYTES', 256)
    report = inspection.seal(fixture_view(True))
    value = archive.pack(report)
    assert len(value['segments']) > 2
    path = tmp_path / 'archive.json'
    write(path, value)
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert archive.verify(read(path)) == report
    with pytest.raises(FileExistsError):
        write(path, value)


@pytest.mark.parametrize('mutation', [
    lambda v: v['segments'].pop(),
    lambda v: v['segments'].reverse(),
    lambda v: v['segments'].insert(1, deepcopy(v['segments'][0])),
    lambda v: v['segments'][0].update(ordinal=False),
    lambda v: v['segments'][1].update(previous_sha256='0' * 64),
    lambda v: v['segments'][-1].update(data=v['segments'][-1]['data'][:-1]),
    lambda v: v.update(byte_length=True),
])
def test_rehashed_manifest_cannot_hide_incomplete_or_reordered_segments(monkeypatch, mutation):
    monkeypatch.setattr(archive, 'SEGMENT_BYTES', 256)
    value = archive.pack(inspection.seal(fixture_view()))
    mutation(value)
    value['sha256'] = sha({k: v for k, v in value.items() if k != 'sha256'})
    with pytest.raises(ValueError):
        archive.verify(value)


def test_resealed_economic_corruption_still_requires_full_replay():
    report = inspection.seal(fixture_view())
    report['journal']['account']['cash'] = '999'
    report['sha256'] = sha({k: v for k, v in report.items() if k != 'sha256'})
    with pytest.raises(ValueError):
        archive.pack(report)


def test_all_resealed_segments_cannot_hide_wrong_financial_balances():
    report = inspection.seal(fixture_view())
    value = archive.pack(report)
    report['journal']['account']['cash'] = '999'
    report['sha256'] = sha({k: v for k, v in report.items() if k != 'sha256'})
    raw = encoded(report)
    value.update(export_sha256=report['sha256'], byte_length=len(raw), segments=[])
    previous = report['sha256']
    for offset in range(0, len(raw), archive.SEGMENT_BYTES):
        body = dict(ordinal=len(value['segments']), offset=offset, previous_sha256=previous,
                    data=raw[offset:offset + archive.SEGMENT_BYTES])
        segment = {**body, 'sha256': sha(body)}
        value['segments'].append(segment)
        previous = segment['sha256']
    value['sha256'] = sha({k: v for k, v in value.items() if k != 'sha256'})
    with pytest.raises(ValueError):
        archive.verify(value)


def test_offline_cli_verifies_export_and_rejects_corruption(tmp_path):
    path = tmp_path / 'archive.json'
    value = archive.pack(inspection.seal(fixture_view()))
    write(path, value)
    args = [sys.executable, '-m', 'scripts.paper_archive', 'verify', '--input', str(path)]
    assert subprocess.run(args, capture_output=True).returncode == 0
    value['segments'][0]['data'] = 'corrupt'
    path.write_text(encoded(value))
    result = subprocess.run(args, capture_output=True)
    assert result.returncode == 1
    assert str(path).encode() not in result.stderr


def test_capture_reads_isolated_persistent_account(database):
    from tests.test_requested_health import ready
    engine, _, _ = database
    ready(engine)
    report = archive.verify(archive.capture(engine, 'account'))
    assert report['journal']['revision'] == 0
    assert report['journal']['external_submission_allowed'] is False
