"""Run real pinned upstream factories with explicit fake models, no network."""
from datetime import datetime, timezone
import json
import socket

from fastapi.testclient import TestClient
import pytest

from apps.tradingagents_worker import Budget, StrictRole, create_app, run
from core.paper.directional import Config
from core.paper.directional_market import source_for
from core.research.tradingagents import ROLES, freeze
from core.backtest.spot import digest


def snapshot():
    now = datetime.now(timezone.utc)
    config = Config(mode='RESEARCH_PAPER', instrument='XAU/USD', quote_source=source_for('XAU/USD'),
                    news_sources=('TEST_NEWS',), initial_cash='10000', tick_size='.01', quantity_step='.01',
                    max_quantity='10', max_order_quote='1000', fee_rate='.001', slippage='.05',
                    quote_max_age_seconds=60, decision_ttl_seconds=60, news_max_age_seconds=120,
                    news_pre_seconds=1800, news_post_seconds=1800)
    return freeze({'config': config.model_dump(mode='json'), 'account_id': '12345678-1234-4234-8234-123456789012',
                   'revision': 0, 'status': 'RUNNING', 'entry_halted': False, 'balance': '10000',
                   'position': None, 'pending': None, 'last_quote': {'instrument': 'XAU/USD', 'source': source_for('XAU/USD'),
                   'at': now.isoformat(), 'bid': '100', 'ask': '101'}, 'recent_quotes': []}, now=now, news=None)


class FakeLLM:
    def __init__(self): self.calls = 0;self.prompts = []
    def invoke(self, prompt):
        from langchain_core.messages import AIMessage
        self.calls += 1;self.prompts.append(str(prompt))
        return AIMessage(content='SYNTHETIC MODEL: insufficient evidence, prefer HOLD. No market call was made.')
    def with_structured_output(self, schema):
        parent = self
        class Bound:
            def invoke(self, prompt):
                parent.calls += 1;parent.prompts.append(str(prompt))
                values = {
                    'ResearchPlan': {'recommendation': 'Hold', 'rationale': 'SYNTHETIC MODEL', 'strategic_actions': 'No action'},
                    'TraderProposal': {'action': 'Hold', 'reasoning': 'SYNTHETIC MODEL'},
                    'PortfolioDecision': {'rating': 'Hold', 'executive_summary': 'SYNTHETIC MODEL', 'investment_thesis': 'Insufficient data'},
                    'Candidate': {'target': 'HOLD', 'quantity': '0', 'stop_loss': None, 'take_profit': None, 'reason': 'SYNTHETIC MODEL'},
                }
                return schema(**values[schema.__name__])
        return Bound()


def test_real_upstream_eight_roles_and_strict_adapter_without_network(monkeypatch):
    # Upstream package initialization must not search the user's .env in this test.
    import dotenv
    monkeypatch.setattr(dotenv, 'find_dotenv', lambda *args, **kwargs: '')
    monkeypatch.setattr(socket.socket, 'connect', lambda *args: (_ for _ in ()).throw(AssertionError('Network forbidden')))
    llm = FakeLLM()
    result = run(snapshot(), llm, model='SYNTHETIC_MODEL_NO_API_CALLS')
    assert result.model_calls == llm.calls == 9
    assert tuple(r.role for r in result.reports) == ROLES
    assert result.candidate.target == 'HOLD'
    assert result.instrument == 'XAU/USD' and result.quote_source == source_for('XAU/USD')
    assert all('GC=F' not in p for p in llm.prompts)
    assert all('OBSERVED_QUOTES_NOT_CANDLES' in p or 'No closed candles' in p or 'SYNTHETIC MODEL' in p for p in llm.prompts)


def test_failed_structured_attempt_cannot_fall_back():
    class Broken:
        def with_structured_output(self, schema): return self
        def invoke(self, prompt): raise ValueError('Synthetic parse error')
    role = StrictRole(Broken(), Budget())
    with pytest.raises(ValueError): role.with_structured_output(dict).invoke('test')
    with pytest.raises(ValueError, match='fallback'): role.invoke('test')


def test_global_call_budget():
    budget = Budget()
    for _ in range(9): budget.consume()
    with pytest.raises(ValueError, match='budget'): budget.consume()


@pytest.mark.parametrize('case', ['digest', 'live', 'market'])
def test_invalid_snapshot_never_calls_model(case):
    value = snapshot()
    if case == 'digest': value['sha256'] = 'a'*64
    if case == 'live': value['snapshot']['trading_enabled'] = True;value['sha256'] = digest(value['snapshot'])
    if case == 'market': value['snapshot']['quote']['source'] = 'WRONG';value['sha256'] = digest(value['snapshot'])
    llm = FakeLLM()
    with pytest.raises(ValueError): run(value, llm, model='SYNTHETIC_NO_CALLS')
    assert llm.calls == 0


def test_worker_auth_and_no_model_config(monkeypatch):
    monkeypatch.setenv('FC666_TA_RUNNER_TOKEN', 'T'*40)
    for name in ('FC666_TA_PROVIDER', 'FC666_TA_MODEL', 'FC666_TA_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app()) as client:
        assert not client.get('/health/ready').json()['model_configured']
        assert client.post('/analyze', json={}).status_code == 401
        assert client.post('/analyze', headers={'authorization': 'Bearer '+'T'*40}, json={}).status_code == 503
