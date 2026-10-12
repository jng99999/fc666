"""Read-only comparison with an externally trusted selected revocation checkpoint."""
from copy import deepcopy
import re
from core.paper import segmented_revocations as revocations
from core.paper.fault_adapter import encoded,sha
from core.paper.requested_journal import transaction
from core.storage.models import SegmentedOperatorRevocationHead as Head

VERSION='paper-operator-revocation-reference-v1'
SCOPE='OPERATOR_REVOCATION_RETAINED_HEAD'
MAX_BYTES=4096
MAX_REVISION=1024


def verify(value):
    keys={'version','scope','revision','root_sha256','head_sha256','consistency_only','external_submission_allowed','sha256'}
    if (not isinstance(value,dict) or set(value)!=keys or value['version']!=VERSION or value['scope']!=SCOPE
        or type(value['revision']) is not int or not 0<=value['revision']<=MAX_REVISION
        or value['consistency_only'] is not True or value['external_submission_allowed'] is not False):
        raise ValueError('Invalid selected revocation checkpoint')
    roots=[value['root_sha256'],value['head_sha256']]
    if value['revision']==0:
        if roots!=[None,None]:raise ValueError('Empty checkpoint requires empty hashes')
    elif any(not isinstance(item,str) or re.fullmatch('[0-9a-f]{64}',item) is None for item in roots):
        raise ValueError('Nonempty checkpoint requires exact hashes')
    if value['revision']==1 and roots[0]!=roots[1]:raise ValueError('Single-record checkpoint endpoints differ')
    if len(encoded(value).encode())>MAX_BYTES or value['sha256']!=sha({key:item for key,item in value.items() if key!='sha256'}):
        raise ValueError('Selected checkpoint integrity mismatch')
    return deepcopy(value)


def _capture(db):
    revocations.audit(db)
    head=db.get(Head,'global')
    body=dict(version=VERSION,scope=SCOPE,revision=head.revision,root_sha256=head.root_sha256,
              head_sha256=head.head_sha256,consistency_only=True,external_submission_allowed=False)
    return verify({**body,'sha256':sha(body)})


def capture(engine):
    with transaction(engine) as db:
        revocations._lock(db)
        return _capture(db)


def check(engine,expected):
    expected=verify(expected)
    with transaction(engine) as db:
        revocations._lock(db)
        actual=_capture(db)
        if encoded(actual)!=encoded(expected):raise ValueError('Restored revocations differ from selected trusted checkpoint')
    return {'version':VERSION,'matches_selected_checkpoint':True,'revision':actual['revision'],
            'selected_checkpoint_sha256':expected['sha256'],
            'currentness_independently_verified':False,'external_submission_allowed':False}
