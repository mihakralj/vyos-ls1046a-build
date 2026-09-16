from mcp.server import Server
from ..session import SessionState


def create_mcp_server() -> Server:
    server = Server("VyOS")
    state = SessionState()
    introspection = bool(state.mcp_introspection)
    read_only = state.mcp_mode != 'read-write'

    from .resources import register_resources
    register_resources(server, introspection_enabled=introspection)

    from .tools import register_tools
    register_tools(server, read_only=read_only)

    from .prompts import register_prompts
    register_prompts(server, read_only=read_only, introspection_enabled=introspection)

    return server
