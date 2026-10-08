"""Bind independently valid historical dispatch evidence to one current lineage."""
from core.paper.fault_adapter import encoded


def equal(left,right):
    if encoded(left)!=encoded(right):raise ValueError('Historical evidence differs from current lineage')


def prefix(old,new):
    if len(old)>len(new):raise ValueError('Historical evidence is from the future')
    equal(old,new[:len(old)])


def bind(historical,current):
    """Inputs must already pass requested_dispatch.verify; no origin authentication."""
    old=historical['ownership_evidence'];new=current['ownership_evidence']
    before=old['source_evidence'];after=new['source_evidence']
    financial=before['journal']['journal'];latest=after['journal']['journal']
    equal(financial['opening'],latest['opening'])
    if financial['revision']>latest['revision'] or len(financial['requests'])>len(latest['requests']) or old['observed_us']>new['observed_us']:
        raise ValueError('Historical checkpoint exceeds current evidence')
    for entry,now in zip(financial['requests'],latest['requests']):
        equal(entry['request'],now['request']);prefix(entry['events'],now['events'])
        if entry.get('void') is not None:equal(entry['void'],now.get('void'))
    controls=before['controls'];current_controls=after['controls']
    equal(controls['coverage'],current_controls['coverage']);equal(controls['policy'],current_controls['policy'])
    prefix(controls['records'],current_controls['records'])
    gates={(gate['request_id'],gate['phase']):gate for gate in current_controls['gates']}
    for gate in controls['gates']:equal(gate,gates.get((gate['request_id'],gate['phase'])))
    prefix(before['sources'],after['sources']);prefix(old['event_tokens'],new['event_tokens'])
    claims={(claim['request_id'],claim['token']):claim for claim in new['claims']}
    for claim in old['claims']:equal(claim,claims.get((claim['request_id'],claim['token'])))
    original_claims={(claim['request_id'],claim['token']) for claim in old['claims']}
    known_requests={entry['request']['request_id'] for entry in financial['requests']}
    for identity,claim in claims.items():
        if identity[0] in known_requests and claim['acquired_us']<old['observed_us'] and identity not in original_claims:
            raise ValueError('Historical evidence omitted an already acquired claim')
    # A later SUBMIT may add a dispatch where the older prefix had none.
    current_slots={slot['request_id']:slot for slot in current['dispatches']}
    for slot in historical['dispatches']:
        if slot['dispatch'] is not None:equal(slot,current_slots.get(slot['request_id']))
