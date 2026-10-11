"""Protected append-only local evidence snapshots; no financial or worker authority."""
from contextlib import contextmanager
import fcntl
import json
import os
import re
import stat
import time
from uuid import uuid4
from core.paper import requested_segmented_execution as replay
from core.paper.fault_adapter import encoded, sha
from core.exchange.private_spot import unique_object

VERSION = 'paper-segmented-local-store-v1'
NODE_BYTES = replay.MAX_BYTES + 4096
STORAGE_BYTES = 64 * 1024 * 1024
FILE_LIMIT = 512


def _regular(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Protected owner-only regular file required')


def _read(directory, name, limit):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        _regular(stream.fileno())
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Stored evidence exceeds bounds')
    def reject(_):
        raise ValueError('Nonfinite JSON')
    value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject)
    if raw != encoded(value).encode():
        raise ValueError('Stored evidence is not canonical')
    return value


def _write(directory, name, value):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(encoded(value).encode())
        stream.flush(); os.fsync(stream.fileno())


def _budget(directory, additional=0):
    names = os.listdir(directory)
    if len(names) >= FILE_LIMIT:
        raise ValueError('Store file capacity exceeded')
    size = 0
    for name in names:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Unprotected or nonregular store entry')
        size += info.st_size
    if size + additional > STORAGE_BYTES:
        raise ValueError('Store byte capacity exceeded')


@contextmanager
def _locked(path, *, create=False):
    if create:
        os.mkdir(path, 0o700)
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock = None
    try:
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Protected owner-only store directory required')
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_CREAT if create else 0)
        lock = os.open('.lock', flags, 0o600, dir_fd=directory)
        _regular(lock)
        deadline = time.monotonic() + 2
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError('Store lock timeout')
                time.sleep(.01)
        _budget(directory)
        yield directory
    finally:
        if lock is not None:
            os.close(lock)
        os.close(directory)


def _digest(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _load(directory):
    head = _read(directory, 'HEAD', 4096)
    if not isinstance(head, dict) or set(head) != {'version', 'node_sha256', 'request_id', 'revision'} or head['version'] != VERSION or not _digest(head['node_sha256']) or not _digest(head['request_id']) or type(head['revision']) is not int or not 0 <= head['revision'] <= replay.MAX_SEGMENTS:
        raise ValueError('Invalid store index')
    digest = head['node_sha256']; child = None; current = None
    for revision in range(head['revision'], -1, -1):
        node = _read(directory, digest + '.json', NODE_BYTES)
        if not isinstance(node, dict) or set(node) != {'version','revision','parent_sha256','report','sha256'} or node['version'] != VERSION or type(node['revision']) is not int or node['revision'] != revision or node['sha256'] != digest or sha({k:v for k,v in node.items() if k != 'sha256'}) != digest:
            raise ValueError('Invalid immutable store node')
        report = replay.verify(node['report'])
        if report['request']['request_id'] != head['request_id'] or len(report['segments']) != revision:
            raise ValueError('Store request or revision differs')
        if child is not None and encoded(child['segments'][:-1]) != encoded(report['segments']):
            raise ValueError('Stored append rewrote historical evidence')
        if current is None:
            current = node
        child = report
        if revision == 0:
            if node['parent_sha256'] is not None:
                raise ValueError('Invalid store root predecessor')
        else:
            if not _digest(node['parent_sha256']):
                raise ValueError('Missing store predecessor')
            digest = node['parent_sha256']
    return head, current


def _publish(directory, head):
    temporary = '.head-' + uuid4().hex
    _write(directory, temporary, head)
    os.replace(temporary, 'HEAD', src_dir_fd=directory, dst_dir_fd=directory)
    os.fsync(directory)


def _object(directory, name, node):
    # Never expose a partially written object under its final digest name.
    temporary = '.object-' + uuid4().hex
    try:
        _write(directory, temporary, node)
        try:
            os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            if encoded(_read(directory, name, NODE_BYTES)) != encoded(node):
                raise ValueError('Conflicting immutable store object')
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
    os.fsync(directory)


def _commit(directory, report, revision, parent):
    body = dict(version=VERSION, revision=revision, parent_sha256=parent, report=report)
    node = {**body, 'sha256':sha(body)}
    if len(encoded(node)) > NODE_BYTES:
        raise ValueError('Store node exceeds bounds')
    head = dict(version=VERSION, node_sha256=node['sha256'], request_id=report['request']['request_id'], revision=revision)
    _budget(directory, len(encoded(node)) + len(encoded(head)))
    name = node['sha256'] + '.json'
    _object(directory, name, node)
    _publish(directory, head)
    return head


def create(path, request):
    report = replay.replay(request, [])
    with _locked(path, create=True) as directory:
        return _commit(directory, report, 0, None)


def load(path):
    with _locked(path) as directory:
        head, node = _load(directory)
        return dict(index=head, report=node['report'])


def append(path, batch, *, expected_node_sha256):
    if not _digest(expected_node_sha256):
        raise ValueError('Explicit store checkpoint digest required')
    with _locked(path) as directory:
        head, node = _load(directory)
        report = node['report']
        # Lost reply: only an exact final batch tied to its original parent.
        if report['segments'] and encoded(report['segments'][-1]['events']) == encoded(batch):
            if expected_node_sha256 not in [head['node_sha256'], node['parent_sha256']]:
                raise ValueError('Retry checkpoint differs')
            return head
        if expected_node_sha256 != head['node_sha256']:
            raise ValueError('Stale store checkpoint')
        result = replay.append(report, batch)
        return _commit(directory, result, head['revision'] + 1, head['node_sha256'])
