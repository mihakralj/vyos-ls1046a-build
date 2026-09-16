import asyncio
import logging

from starlette.requests import Request as StarletteRequest
from starlette.responses import Response

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from fastapi import FastAPI
from fastapi import HTTPException

from .server import create_mcp_server
from .auth import mcp_auth
from ..session import SessionState

LOG = logging.getLogger('http_api.mcp.routers')

_session_manager = None
_startup_registered = False


async def _mcp_auth_check(scope, receive, send):
    request = StarletteRequest(scope, receive)

    # If the request already carries a valid MCP session ID, trust it —
    # the session was authenticated on the initial GET.
    session_id = request.headers.get('Mcp-Session-Id') or request.headers.get('mcp-session-id')
    if session_id and _session_manager is not None:
        if session_id in getattr(_session_manager, '_server_instances', {}):
            return True

    try:
        await mcp_auth(request)
    except HTTPException as e:
        response = Response(e.detail, status_code=e.status_code)
        await response(scope, receive, send)
        return False
    return True


async def _mcp_entry(scope, receive, send):
    """Handle both /mcp and /mcp/... paths, forwarding to session manager."""
    if scope['type'] != 'http':
        await _session_manager.handle_request(scope, receive, send)
        return

    authorized = await _mcp_auth_check(scope, receive, send)
    if not authorized:
        return

    await _session_manager.handle_request(scope, receive, send)


def _start_session_manager():
    global _session_manager
    if _session_manager is None:
        return
    LOG.debug('Starting MCP session manager background task')
    loop = asyncio.get_event_loop()
    loop.create_task(_run_session_manager())


def _stop_session_manager():
    global _session_manager
    LOG.debug('Stopping MCP session manager')
    _session_manager = None


async def _run_session_manager():
    global _session_manager
    sm = _session_manager
    if sm is None:
        return
    try:
        async with sm.run():
            LOG.debug('MCP session manager running')
            await asyncio.Event().wait()
    except asyncio.CancelledError:
        LOG.debug('MCP session manager cancelled')
    except Exception:
        LOG.exception('MCP session manager error')


def mcp_init(app: FastAPI):
    global _session_manager, _startup_registered

    if any(getattr(r, 'path', '') == '/mcp/' for r in app.router.routes):
        return

    mcp_server = create_mcp_server()
    session = SessionState()
    json_resp = session.mcp_mode == 'read-write'

    _session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        event_store=None,
        json_response=json_resp,
        stateless=False,
    )

    if not _startup_registered:
        app.add_event_handler('startup', _start_session_manager)
        app.add_event_handler('shutdown', _stop_session_manager)
        _startup_registered = True

    app.mount('/mcp/', _mcp_entry)


def mcp_clear(app: FastAPI):
    global _session_manager, _startup_registered

    app.router.routes = [r for r in app.router.routes if not getattr(r, 'path', '').startswith('/mcp')]
    _session_manager = None
