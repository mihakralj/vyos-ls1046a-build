import logging
import subprocess

from mcp.server import Server
from mcp.types import Tool, TextContent
from fastapi.concurrency import run_in_threadpool

from vyos.configsession import ConfigSessionError
from . import schema as mcp_schema
from ..session import SessionState

LOG = logging.getLogger('http_api.mcp.tools')

_READ_ONLY_OP_VERBS = frozenset({
    'show', 'monitor', 'ping', 'traceroute',
})

TOOL_EXECUTE_OP = 'execute_operational_command'
TOOL_MODIFY_CONFIG = 'modify_configuration'
TOOL_MANAGE_IMAGE = 'manage_system_image'
TOOL_REBOOT = 'reboot_system'
TOOL_POWEROFF = 'poweroff_system'
TOOL_SAVE_CONFIG = 'save_configuration'

_READ_ONLY_TOOLS = [
    Tool(
        name=TOOL_EXECUTE_OP,
        description='Run a VyOS operational-mode (show/monitor/...) command.',
        inputSchema={
            'type': 'object',
            'properties': {
                'path': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Command path (e.g. ["show", "interfaces"])'
                }
            },
            'required': ['path']
        }
    ),
]

_WRITE_TOOLS = [
    Tool(
        name=TOOL_MODIFY_CONFIG,
        description='Stage set/delete operations and atomically commit them.',
        inputSchema={
            'type': 'object',
            'properties': {
                'operations': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'op': {'enum': ['set', 'delete']},
                            'path': {'type': 'array', 'items': {'type': 'string'}},
                            'value': {'type': 'string'}
                        },
                        'required': ['op', 'path']
                    }
                },
                'commit_confirm_minutes': {
                    'type': 'integer',
                    'description': 'If set, use commit-confirm with this many minutes.'
                }
            },
            'required': ['operations']
        }
    ),
    Tool(
        name=TOOL_MANAGE_IMAGE,
        description='Manage VyOS system images (add, delete, set-default).',
        inputSchema={
            'type': 'object',
            'properties': {
                'action': {'enum': ['add', 'delete', 'set']},
                'url': {'type': 'string', 'description': 'URL for add action'},
                'name': {'type': 'string', 'description': 'Image name for delete/set'}
            },
            'required': ['action']
        }
    ),
    Tool(
        name=TOOL_REBOOT,
        description='Reboot the VyOS system.',
        inputSchema={
            'type': 'object',
            'properties': {
                'path': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Reboot command path (default: ["now"])'
                }
            }
        }
    ),
    Tool(
        name=TOOL_POWEROFF,
        description='Power off the VyOS system.',
        inputSchema={
            'type': 'object',
            'properties': {
                'path': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Poweroff command path (default: ["now"])'
                }
            }
        }
    ),
    Tool(
        name=TOOL_SAVE_CONFIG,
        description='Save running configuration to file.',
        inputSchema={
            'type': 'object',
            'properties': {
                'file': {
                    'type': 'string',
                    'description': 'Optional file path to save config to (defaults to /config/config.boot)'
                }
            }
        }
    ),
]


def _redacted_show_configuration(remainder):
    # Secret hardening for the op-mode surface. `show configuration
    # [commands|json|all|<path>]` is a read-only verb, so it passes the
    # read-only allow-list -- but the stock op-mode renderer emits credential
    # material (user password hashes, HTTP API keys, PKI private keys, IPsec/
    # RADIUS/WireGuard/... secrets) in the clear. The vyos-config:// resource is
    # already stripped; this closes the equivalent hole in the tool surface so
    # an agent cannot bypass redaction by asking `show configuration` instead.
    #
    # We rebuild the requested rendering from a stripped ConfigTree rather than
    # calling session.show(), reusing the shared MCP redaction policy (secrets
    # masked, public keys/certs kept). Only the presentation verbs are handled
    # here; anything else (e.g. a bare subtree path) is rendered from the
    # stripped tree too.
    from vyos.config import Config
    from vyos.configtree import ConfigTree
    from .redaction import strip_mcp_secrets

    state = SessionState()
    session = state.session
    env = session.get_session_env() if session is not None else None
    config = Config(session_env=env)
    ct = ConfigTree(config.show_config([]))
    strip_mcp_secrets(ct)

    # remainder is the tokens after 'configuration'
    fmt = remainder[0] if remainder else None
    if fmt == 'commands':
        return ct.to_commands()
    if fmt == 'json':
        return ct.to_json()
    if fmt in (None, 'all'):
        return ct.to_string()
    # Treat any other remainder as a config path to render (stripped).
    if ct.exists(remainder):
        return ct.get_subtree(remainder, with_node=True).to_string()
    return ''


def _execute_operational_command(arguments):
    path = arguments.get('path', [])
    if not path:
        raise ValueError('path is required')

    if not mcp_schema.op_path_valid(path):
        raise ValueError(f'Invalid operational command path: {path}')

    state = SessionState()
    session = state.session

    # Intercept `show configuration ...` and serve it from a stripped
    # ConfigTree so secrets never reach the agent via the op-mode tool.
    if len(path) >= 2 and path[0] == 'show' and path[1] == 'configuration':
        return _redacted_show_configuration(path[2:])

    # All VyOS operational commands are driven by vyatta-op-cmd-wrapper
    cmd = ['/opt/vyatta/bin/vyatta-op-cmd-wrapper'] + list(path)
    env = session.get_session_env() if session is not None else None

    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    stdout_data, _ = p.communicate()
    output = stdout_data.decode() if stdout_data else ''
    if p.returncode != 0:
        raise ValueError(output.strip() or f'Operational command failed with exit status {p.returncode}')
    return output


def _modify_configuration(arguments):
    from api.rest.routers import lock

    operations = arguments.get('operations', [])
    if not operations:
        raise ValueError('operations is required')

    commit_confirm_minutes = arguments.get('commit_confirm_minutes')

    state = SessionState()
    session = state.session
    if session is None:
        raise ValueError('Config session is not initialized')

    lock.acquire()
    try:
        for op_spec in operations:
            op = op_spec.get('op')
            path = op_spec.get('path', [])
            value = op_spec.get('value')

            if not mcp_schema.config_path_valid(path):
                session.discard()
                raise ValueError(f'Invalid config path: {path}')

            if op == 'set':
                session.set(path, value=value or '')
            elif op == 'delete':
                session.delete(path, value=value)
            else:
                session.discard()
                raise ValueError(f'Unknown operation: {op}')

        if commit_confirm_minutes:
            out = session.commit_confirm(minutes=commit_confirm_minutes)
        else:
            out = session.commit()

        return out or 'Configuration committed successfully.'
    except ConfigSessionError as e:
        session.discard()
        raise ValueError(str(e))
    except Exception:
        session.discard()
        raise
    finally:
        lock.release()


def _manage_system_image(arguments):
    action = arguments.get('action')
    url = arguments.get('url', '')
    name = arguments.get('name', '')

    state = SessionState()
    session = state.session
    if session is None:
        raise ValueError('Config session is not initialized')

    try:
        if action == 'add':
            if not url:
                raise ValueError('url is required for add action')
            return session.install_image(url)
        elif action == 'delete':
            if not name:
                raise ValueError('name is required for delete action')
            return session.remove_image(name)
        elif action == 'set':
            if not name:
                raise ValueError('name is required for set action')
            return session.set_default_image(name)
        else:
            raise ValueError(f'Unknown image action: {action}')
    except ConfigSessionError as e:
        raise ValueError(str(e))


def _reboot_system(arguments):
    path = arguments.get('path', ['now'])
    state = SessionState()
    session = state.session
    if session is None:
        raise ValueError('Config session is not initialized')
    try:
        return session.reboot(path)
    except ConfigSessionError as e:
        raise ValueError(str(e))


def _poweroff_system(arguments):
    path = arguments.get('path', ['now'])
    state = SessionState()
    session = state.session
    if session is None:
        raise ValueError('Config session is not initialized')
    try:
        return session.poweroff(path)
    except ConfigSessionError as e:
        raise ValueError(str(e))


def _save_configuration(arguments):
    file_path = arguments.get('file') or '/config/config.boot'
    state = SessionState()
    session = state.session
    if session is None:
        raise ValueError('Config session is not initialized')
    try:
        return session.save_config(file_path)
    except ConfigSessionError as e:
        raise ValueError(str(e))


_TOOL_HANDLERS = {
    TOOL_EXECUTE_OP: _execute_operational_command,
    TOOL_MODIFY_CONFIG: _modify_configuration,
    TOOL_MANAGE_IMAGE: _manage_system_image,
    TOOL_REBOOT: _reboot_system,
    TOOL_POWEROFF: _poweroff_system,
    TOOL_SAVE_CONFIG: _save_configuration,
}


def register_tools(server: Server, read_only: bool = True):
    tools = _READ_ONLY_TOOLS[:]
    if not read_only:
        tools.extend(_WRITE_TOOLS)

    @server.list_tools()
    async def list_tools():
        return tools

    exposed = {t.name for t in tools}

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        handler = _TOOL_HANDLERS.get(name)
        if handler is None or name not in exposed:
            raise ValueError(f'Unknown tool: {name}')

        if read_only and name == TOOL_EXECUTE_OP:
            op = (arguments.get('path') or [None])[0]
            if op not in _READ_ONLY_OP_VERBS:
                raise ValueError(f'Operational verb not allowed in read-only mode: {op}')

        result = await run_in_threadpool(handler, arguments)

        return [TextContent(type='text', text=str(result))]
