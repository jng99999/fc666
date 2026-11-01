"""Pinned real provider SDKs with fake keys and exclusively in-memory HTTP.

The lightweight directional image may omit SDKs; those cases explicitly skip.
The pinned TradingAgents venv and research-worker image must run them without skips.
"""
import json
import os
import socket
from unittest.mock import patch

import pytest

from apps.tradingagents_worker import model_client
from core.research.tradingagents import Candidate
from core.security.public_http import PublicTransport
import core.security.public_http as public_http

pytestmark = pytest.mark.filterwarnings('ignore:Model .* is not in the known model list:RuntimeWarning')


@pytest.fixture
def no_network(monkeypatch):
    attempts = []
    def denied(*args, **kwargs):
        attempts.append('network')
        raise AssertionError('External network is forbidden in provider transport tests')
    monkeypatch.setattr(socket, 'getaddrinfo', denied)
    monkeypatch.setattr(socket.socket, 'connect', denied)
    with patch.dict(os.environ, {'LANGSMITH_TRACING': 'false', 'OTEL_SDK_DISABLED': 'true'}, clear=True):
        yield attempts
    assert attempts == []


def sdk(provider):
    pytest.importorskip('tradingagents')
    pytest.importorskip('langchain_openai' if provider == 'openai' else 'langchain_anthropic' if provider == 'anthropic' else 'langchain_google_genai')
    if provider == 'anthropic':
        pytest.importorskip('httpx2')


def install_transport(monkeypatch, *, address='1.1.1.1', redirect=False):
    records, resolutions, transports = [], [], []
    def resolver(host, port, **kwargs):
        resolutions.append((host, port))
        return [(2, 1, 6, '', (address, port))]
    def factory(*, http_module):
        def respond(request):
            payload = json.loads(request.read())
            records.append({'host': request.url.host, 'host_header': request.headers['Host'],
                'sni': request.extensions['sni_hostname'], 'payload': payload,
                'module': http_module.__name__})
            if redirect:
                return http_module.Response(302, headers={'Location': 'http://127.0.0.1/private'})
            candidate = {'target': 'HOLD', 'quantity': '0', 'stop_loss': None,
                         'take_profit': None, 'reason': 'Explicit in-memory SDK fixture only'}
            if request.url.path.endswith('/messages'):
                blocks = ([{'type': 'tool_use', 'id': 'tool_fixture', 'name': 'Candidate', 'input': candidate}]
                          if payload.get('tools') else [{'type': 'text', 'text': 'In-memory response'}])
                body = {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant',
                        'model': 'claude-sonnet-4-5', 'content': blocks,
                        'stop_reason': 'tool_use' if payload.get('tools') else 'end_turn',
                        'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}}
            else:
                message = ({'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'call_fixture', 'type': 'function', 'function': {
                        'name': 'Candidate', 'arguments': json.dumps(candidate)}}]}
                    if payload.get('tools') else {'role': 'assistant', 'content': 'In-memory response'})
                body = {'id': 'chatcmpl_fixture', 'object': 'chat.completion', 'created': 0,
                        'model': 'gpt-4o-mini', 'choices': [{'index': 0,
                            'finish_reason': 'tool_calls' if payload.get('tools') else 'stop', 'message': message}],
                        'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
            return http_module.Response(200, json=body)
        transport = PublicTransport(resolver=resolver,
            transport=http_module.MockTransport(respond), http_module=http_module)
        transports.append(transport)
        return transport
    monkeypatch.setattr(public_http, 'PublicTransport', factory)
    return records, resolutions, transports


@pytest.mark.parametrize(('provider', 'name'), [('openai', 'gpt-4o-mini'), ('anthropic', 'claude-sonnet-4-5')])
def test_actual_sync_and_structured_sdk_calls_use_pinned_module_matched_transport(monkeypatch, no_network, provider, name):
    sdk(provider)
    records, resolutions, transports = install_transport(monkeypatch)
    with model_client(provider, name, 'FAKE_TEST_KEY_NEVER_SENT_EXTERNALLY', 'https://models.fixture.invalid') as llm:
        actual = llm.root_client if provider == 'openai' else llm._client
        assert actual._client._transport is transports[0]
        assert actual.max_retries == 0 and actual.timeout == 15
        assert actual._client.timeout.read == 15
        assert llm.max_tokens == 1200
        assert 'http_client' not in getattr(llm, 'model_kwargs', {})
        assert llm.invoke('fixture only').content == 'In-memory response'
        candidate = llm.with_structured_output(Candidate).invoke('fixture only')
        assert isinstance(candidate, Candidate) and candidate.target == 'HOLD'
    assert resolutions == [('models.fixture.invalid', 443)] * 2
    assert len(records) == 2
    for record in records:
        assert record['host'] == '1.1.1.1'
        assert record['host_header'] == record['sni'] == 'models.fixture.invalid'
        assert record['module'] == ('httpx2' if provider == 'anthropic' else 'httpx')
        assert record['payload'].get('max_tokens', record['payload'].get('max_completion_tokens')) == 1200
        assert 'http_client' not in record['payload']
    assert no_network == []


@pytest.mark.parametrize(('provider', 'name'), [('openai', 'gpt-4o-mini'), ('anthropic', 'claude-sonnet-4-5')])
def test_actual_provider_sdk_cannot_reach_rebound_private_address(monkeypatch, no_network, provider, name):
    sdk(provider)
    records, resolutions, _ = install_transport(monkeypatch, address='169.254.169.254')
    with model_client(provider, name, 'FAKE_TEST_KEY', 'https://models.fixture.invalid') as llm:
        with pytest.raises(Exception):
            llm.invoke('fixture only')
    assert resolutions == [('models.fixture.invalid', 443)]
    assert records == [] and no_network == []


@pytest.mark.parametrize(('provider', 'name'), [('openai', 'gpt-4o-mini'), ('anthropic', 'claude-sonnet-4-5')])
def test_actual_provider_sdk_never_follows_redirects_or_retries_credentials(monkeypatch, no_network, provider, name):
    sdk(provider)
    records, resolutions, _ = install_transport(monkeypatch, redirect=True)
    with model_client(provider, name, 'FAKE_TEST_KEY', 'https://models.fixture.invalid') as llm:
        with pytest.raises(Exception):
            llm.invoke('fixture only')
    assert len(resolutions) == len(records) == 1
    assert records[0]['host_header'] == 'models.fixture.invalid' and no_network == []


def test_actual_google_sdk_uses_official_endpoint_and_bounded_request_config(no_network):
    sdk('google')
    from langchain_core.messages import HumanMessage
    from google.genai._api_client import retry_args
    with model_client('google', 'gemini-2.5-flash', 'FAKE_TEST_KEY') as llm:
        assert str(llm.client._api_client._http_options.base_url) == 'https://generativelanguage.googleapis.com/'
        assert llm.max_output_tokens == 1200 and llm.timeout == 15 and llm.max_retries == 0
        request = llm._prepare_request([HumanMessage(content='Fixture only; not sent')])
        assert request['config'].max_output_tokens == 1200
        assert request['config'].http_options.timeout == 15000
        options = request['config'].http_options.retry_options
        assert options.attempts == 0
        retry_args(options)
        assert options.attempts == 1, 'SDK zero retries means one total attempt'
    assert no_network == []


def test_google_custom_url_is_rejected_before_any_sdk_network(no_network):
    with pytest.raises(ValueError, match='Unsupported bounded model provider'):
        with model_client('google', 'gemini-fixture', 'FAKE_TEST_KEY', 'https://models.fixture.invalid'):
            raise AssertionError('Unsupported endpoint was admitted')
    assert no_network == []
