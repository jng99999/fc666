"""Authenticated mobile operator API. It neither calls models nor sends orders."""
import json
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from core.security.operator import COOKIE, OperatorError, OperatorStore


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Setup(Input):
    setup_token: str = Field(min_length=43, max_length=43)
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=12, max_length=128)


class Login(Input):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(max_length=128)


class Password(Input):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class Model(Input):
    provider: Literal['', 'openai', 'anthropic', 'google']
    model: str = Field(max_length=200)
    base_url: str = Field(max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False


class DataSource(Input):
    source: str = Field(max_length=128)
    url: str = Field(max_length=2048)
    token: str | None = Field(default=None, max_length=4096)
    clear_token: bool = False


class Configuration(Input):
    expected_revision: int = Field(ge=0)
    model: Model
    news: DataSource
    gold: DataSource
    authorized_accounts: list[str] = Field(max_length=20)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key')
        value[key] = item
    return value


async def _body(request: Request, schema):
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise OperatorError(415, '请使用 JSON 请求')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 16384:
            raise OperatorError(413, '配置内容过大')
    try:
        value = json.loads(raw, object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        return schema.model_validate(value)
    except (ValueError, UnicodeError, ValidationError, RecursionError, TypeError):
        # Pydantic error input fields can contain passwords and API keys.
        raise OperatorError(422, '请求字段格式不正确，请检查后重试') from None


def router_for(store: OperatorStore, allowed_origins, *, on_change=None,
               validate_accounts=None, accounts=None):
    router = APIRouter(prefix='/api/v1/directional/admin')
    origins = frozenset(allowed_origins)

    def protect(request: Request):
        if request.method != 'GET':
            if request.headers.get('origin') not in origins or request.headers.get('sec-fetch-site') == 'cross-site':
                raise OperatorError(403, '仅允许受信任站点发起此操作')

    def no_store(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'

    def session(response: Response, token: str):
        response.set_cookie(COOKIE, token, max_age=store.session_seconds,
                            secure=store.secure, httponly=True, samesite='strict', path='/')

    def failure(error):
        if isinstance(error, OperatorError):
            return HTTPException(error.status, error.message, headers={'Cache-Control': 'no-store'})
        return HTTPException(503, '管理员配置服务暂不可用，请刷新核对状态', headers={'Cache-Control': 'no-store'})

    @router.get('/status')
    def status(request: Request, response: Response):
        no_store(response)
        try:
            return store.status(request.cookies.get(COOKIE))
        except (OperatorError, sqlite3.Error, OSError):
            raise failure(None) from None

    @router.post('/setup')
    async def setup(request: Request, response: Response):
        no_store(response)
        try:
            protect(request)
            body = await _body(request, Setup)
            token = await run_in_threadpool(store.setup, body.setup_token, body.username, body.password)
            session(response, token)
            return store.status(token)
        except (OperatorError, sqlite3.Error, OSError) as error:
            raise failure(error) from None

    @router.post('/login')
    async def login(request: Request, response: Response):
        no_store(response)
        try:
            protect(request)
            body = await _body(request, Login)
            token = await run_in_threadpool(store.login, body.username, body.password)
            session(response, token)
            return store.status(token)
        except (OperatorError, sqlite3.Error, OSError) as error:
            raise failure(error) from None

    @router.post('/logout')
    async def logout(request: Request, response: Response):
        no_store(response)
        try:
            protect(request)
            store.require_admin(request, csrf=True)
            await _body(request, Input)
            store.logout(request.cookies.get(COOKIE, ''))
            response.delete_cookie(COOKIE, path='/', secure=store.secure, httponly=True, samesite='strict')
            return store.status()
        except (OperatorError, sqlite3.Error, OSError) as error:
            raise failure(error) from None

    @router.post('/password')
    async def password(request: Request, response: Response):
        no_store(response)
        try:
            protect(request)
            store.require_admin(request, csrf=True)
            body = await _body(request, Password)
            token = await run_in_threadpool(store.change_password, request.cookies.get(COOKIE, ''), body.current_password, body.new_password)
            session(response, token)
            return store.status(token)
        except (OperatorError, sqlite3.Error, OSError) as error:
            raise failure(error) from None

    @router.get('/configuration')
    def configuration(request: Request, response: Response):
        no_store(response)
        try:
            store.require_admin(request)
            return store.vault.public()
        except (OperatorError, sqlite3.Error, OSError) as error:
            raise failure(error) from None

    @router.get('/accounts')
    def account_list(request: Request, response: Response):
        no_store(response)
        try:
            store.require_admin(request)
            if accounts is None:
                return {'accounts': [], 'trading_enabled': False}
            return accounts(request)
        except (OperatorError, sqlite3.Error, OSError) as error:
            raise failure(error) from None

    @router.post('/configuration')
    async def save_configuration(request: Request, response: Response):
        no_store(response)
        try:
            protect(request)
            store.require_admin(request, csrf=True)
            body = await _body(request, Configuration)
            data = body.model_dump(exclude_none=True)
            expected = data.pop('expected_revision')
            verified_identity = {}
            # Validation and pausing run while the vault owns its CAS write lock.
            def before_commit():
                request.state.operator_configuration_identity = verified_identity
                try:
                    if validate_accounts is not None:
                        validate_accounts(request, data['authorized_accounts'])
                    if on_change is not None:
                        on_change()
                finally:
                    del request.state.operator_configuration_identity
            return await run_in_threadpool(store.save_configuration,
                request.cookies.get(COOKIE), request.headers.get('x-csrf-token', ''),
                expected, data, before_commit=before_commit, on_authenticated=verified_identity.update)
        except (OperatorError, sqlite3.Error, OSError) as error:
            raise failure(error) from None

    return router
