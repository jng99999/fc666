"""Publish bounded evidence summaries, never private config or full trader payloads."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def export(evidence, source_commit):
    if not re.fullmatch(r'[0-9a-f]{40}', source_commit):
        raise ValueError('Exact reviewed source commit required')
    root = Path(evidence)
    study = json.loads((root / 'directional-history/study.json').read_text())
    observation = json.loads((root / 'traders/observation.json').read_text())
    behavior = json.loads((root / 'traders/behavior-summary.json').read_text())
    test_log = (root / 'committed-source-regression.log').read_text()
    matches = re.findall(r'(\d+) passed, (\d+) warning', test_log)
    if not matches or re.search(r'\d+ failed|ERROR collecting', test_log):
        raise ValueError('Successful committed-source test evidence required')
    passed, warnings = map(int, matches[-1])
    cases = []
    for row in study['results']:
        cases.append({key: row[key] for key in ('year', 'scenario', 'bars', 'long_entries', 'short_entries',
                                               'stop_exits', 'profit_exits', 'return', 'news_validated')})
        cases[-1]['fees'] = row['final']['fees']
        cases[-1]['position_open_at_end'] = row['final']['position'] is not None
    accounts = []
    for account in observation['accounts']:
        identity = account['trader']
        per_coin = behavior[identity]
        if sum(v['fill_count'] for v in per_coin.values()) != len(account['fills']):
            raise ValueError('Behavior features must account for every observed fill')
        accounts.append({'address': identity, 'fills': len(account['fills']),
                         'assets': len(per_coin),
                         'complete_episodes': sum(v['complete_zero_to_zero_episode_count'] for v in per_coin.values()),
                         'unmatched_fills': sum(v['unmatched_or_unknown_fill_count'] for v in per_coin.values()),
                         'fees_by_token': account['summary']['reported_fees_by_token'],
                         'window_complete': account['coverage']['requested_window_complete'],
                         'pagination_truncated': account['coverage']['pagination_truncated']})
    files = ['directional-history/study.json', 'traders/observation.json', 'traders/behavior-summary.json', 'committed-source-regression.log']
    return {'version': 'execution-validation-v1', 'source_commit': source_commit,
            'mode': 'READ_ONLY_RESEARCH', 'trading_enabled': False,
            'tests': {'passed': passed, 'warnings': warnings},
            'source_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files},
            'gold': {'dataset_id': study['dataset_id'], 'assumptions': study['assumptions'], 'results': cases},
            'traders': {'as_of': observation['finished_at_utc'], 'live_stream': False,
                        'requests': len(observation['request_receipts']), 'accounts': accounts,
                        'gold_connected': False}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = export(args.evidence, args.source_commit)
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'cases': len(result['gold']['results']), 'accounts': len(result['traders']['accounts']), 'trading_enabled': False}))
