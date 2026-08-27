#!/usr/share/vyos-http-api-tools/bin/python3

import asyncio
import grp
import json
import logging
import os
import sys

logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')
LOG = logging.getLogger('vyos-mcp-stdio')

_SERVICES_DIR = '/usr/libexec/vyos/services'
if _SERVICES_DIR not in sys.path:
    sys.path.insert(0, _SERVICES_DIR)

CFG_GROUP = 'vyattacfg'


def _prepare_config_access():
    try:
        cfg_gid = grp.getgrnam(CFG_GROUP).gr_gid
    except KeyError as e:
        raise RuntimeError(f'missing required group: {CFG_GROUP}') from e

    if os.geteuid() == 0:
        os.setgid(cfg_gid)
    elif cfg_gid != os.getgid() and cfg_gid not in os.getgroups():
        raise PermissionError(f'user must belong to {CFG_GROUP}')
    os.umask(0o002)


def _load_server_config():
    from vyos.defaults import api_config_state

    try:
        with open(api_config_state) as f:
            server_config = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        LOG.error('failed to read %s: %s', api_config_state, e)
        return None

    if 'mcp' not in server_config:
        return None
    return server_config


def _prime_session_state(server_config):
    from api.session import SessionState
    from vyos.configsession import ConfigSession

    state = SessionState()
    keys = []
    for key_id, key_config in server_config.get('keys', {}).get('id', {}).items():
        key = key_config.get('key', '')
        if key:
            keys.append({'id': key_id, 'key': key})
    state.keys = keys

    mcp_config = server_config['mcp']
    state.mcp = True
    state.mcp_mode = mcp_config.get('mode', 'read-only')
    state.mcp_introspection = 'introspection' in mcp_config
    state.session = ConfigSession(os.getpid())
    return state


async def _serve():
    from mcp.server.stdio import stdio_server
    from api.mcp.server import create_mcp_server

    server = create_mcp_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream,
                         server.create_initialization_options())


def main():
    try:
        _prepare_config_access()
    except (RuntimeError, PermissionError) as e:
        LOG.error('%s', e)
        return 1

    server_config = _load_server_config()
    if server_config is None:
        LOG.error('MCP is not enabled; configure service https api mcp')
        return 1

    state = _prime_session_state(server_config)
    LOG.info('starting (mode=%s introspection=%s)',
             state.mcp_mode, state.mcp_introspection)
    try:
        asyncio.run(_serve())
    except (KeyboardInterrupt, EOFError):
        LOG.info('closed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
