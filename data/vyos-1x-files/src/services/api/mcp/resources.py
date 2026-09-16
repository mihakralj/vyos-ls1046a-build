import json
import logging
import re
from urllib.parse import unquote

from pydantic import AnyUrl
from mcp.server import Server
from mcp.types import ResourceTemplate, TextResourceContents

from . import schema as mcp_schema
from ..session import SessionState

LOG = logging.getLogger('http_api.mcp.resources')

_ILLEGAL_PATH_RE = re.compile(r'\.\.|[;&|`$(){}[\]<>\\\'\"]')


def _sanitize_path(path_str):
    if _ILLEGAL_PATH_RE.search(path_str):
        raise ValueError(f'Illegal characters in resource path: {path_str}')
    return path_str


def _split_path(path_str):
    if not path_str:
        return []
    segments = path_str.strip('/').split('/')
    return [unquote(s) for s in segments if s]


def _parse_uri(uri):
    scheme = uri.scheme
    section = uri.host or ''
    path_str = uri.path or ''
    path_str = _sanitize_path(path_str)
    return scheme, section, _split_path(path_str)


def _read_config_running(path_str):
    # Never surface credential material through the config-read resource. Read
    # the full tree before stripping because VyOS' canonical secret map starts at
    # fixed top-level paths, then project the requested subtree. Public material
    # (SSH public keys, certificates and CRLs) intentionally remains visible.
    from vyos.config import Config
    from vyos.configtree import ConfigTree
    from .redaction import strip_mcp_secrets
    state = SessionState()
    session = state.session
    env = session.get_session_env()
    config = Config(session_env=env)
    path = _split_path(path_str)
    try:
        full = config.show_config([])
    except Exception as e:
        LOG.error(f'Failed to read config at {path_str}: {e}')
        raise
    try:
        ct = ConfigTree(full)
        strip_mcp_secrets(ct)
        if path:
            if not ct.exists(path):
                return '{}'
            ct = ct.get_subtree(path, with_node=True)
        return ct.to_json()
    except Exception as e:
        LOG.error(f'Failed to strip/project config at {path_str}: {e}')
        raise


def _read_config_effective(path_str):
    from vyos.config import Config
    state = SessionState()
    session = state.session
    env = session.get_session_env()
    config = Config(session_env=env)
    path = _split_path(path_str)
    try:
        effective = config.exists_effective(path)
        return json.dumps({'effective': effective}, separators=(',', ':'))
    except Exception as e:
        LOG.error(f'Failed to check effective config at {path_str}: {e}')
        raise


def _read_schema_config(path_str, depth=1):
    path = _split_path(path_str)
    schema = mcp_schema.project_config_schema(path, depth=depth)
    return mcp_schema.to_json_schema_string(schema)


def _read_schema_op(path_str, depth=1):
    path = _split_path(path_str)
    schema = mcp_schema.project_op_schema(path, depth=depth)
    return mcp_schema.to_json_schema_string(schema)


def _read_state_operational(path_str):
    from vyos.configsession import ConfigSessionError
    state = SessionState()
    session = state.session
    path = _split_path(path_str)
    if not path:
        raise ValueError('operational state requires a command path')
    try:
        res = session.show(path)
    except ConfigSessionError as e:
        raise ValueError(str(e))
    except Exception as e:
        LOG.error(f'Failed to run operational command {path}: {e}')
        raise
    return res


_RESOURCE_HANDLERS = {
    'vyos-config': {
        'running': _read_config_running,
        'effective': _read_config_effective,
    },
    'vyos-schema': {
        'config': _read_schema_config,
        'op': _read_schema_op,
    },
    'vyos-state': {
        'operational': _read_state_operational,
    },
}

_TEMPLATES = [
    ResourceTemplate(
        uriTemplate='vyos-config://running/{path}',
        name='Config: Running',
        description='Active configuration subtree at the given path',
        mimeType='application/json',
    ),
    ResourceTemplate(
        uriTemplate='vyos-config://effective/{path}',
        name='Config: Effective',
        description='Check if a node is effectively applied in the running config',
        mimeType='application/json',
    ),
    ResourceTemplate(
        uriTemplate='vyos-schema://config/{path}',
        name='Schema: Config',
        description='JSON Schema for valid CLI config nodes at the given path',
        mimeType='application/json',
    ),
    ResourceTemplate(
        uriTemplate='vyos-schema://op/{path}',
        name='Schema: Operational',
        description='Valid op-mode command continuations at the given path',
        mimeType='application/json',
    ),
    ResourceTemplate(
        uriTemplate='vyos-state://operational/{path}',
        name='State: Operational',
        description='Operational state output for the given command path',
        mimeType='application/json',
    ),
]


def _filter_templates(introspection_enabled):
    if introspection_enabled:
        return _TEMPLATES
    return [t for t in _TEMPLATES if not t.uriTemplate.startswith('vyos-schema://')]


async def _handle_read_resource(uri: AnyUrl, introspection_enabled=True):
    from fastapi.concurrency import run_in_threadpool
    scheme, section, path = _parse_uri(uri)

    # Schema introspection is a separate, opt-in capability. When it is off the
    # templates are hidden from listing; also refuse to serve them by direct URI
    # so listing and reading agree.
    if scheme == 'vyos-schema' and not introspection_enabled:
        raise ValueError('Schema introspection is disabled on this session')

    scheme_handlers = _RESOURCE_HANDLERS.get(scheme)
    if scheme_handlers is None:
        raise ValueError(f'Unknown resource scheme: {scheme}')

    handler = scheme_handlers.get(section)
    if handler is None:
        raise ValueError(f'Unknown resource section: {scheme}://{section}')

    path_str = '/'.join(path)

    if scheme in ('vyos-config', 'vyos-state'):
        result = await run_in_threadpool(handler, path_str)
    else:
        result = handler(path_str)

    return [TextResourceContents(
        uri=uri,
        mimeType='application/json',
        text=result,
    )]


def register_resources(server: Server, introspection_enabled: bool = False):
    templates = _filter_templates(introspection_enabled)

    @server.list_resource_templates()
    async def list_resource_templates():
        return templates

    @server.read_resource()
    async def read_resource(uri: AnyUrl):
        return await _handle_read_resource(uri, introspection_enabled=introspection_enabled)
