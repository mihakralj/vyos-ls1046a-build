from fastapi import Request, HTTPException, status
from ..session import SessionState

def check_auth(key_list, key):
    for api_key in key_list:
        if api_key.get("key") == key:
            return api_key.get("id")
    return None

async def mcp_auth(request: Request):
    """
    Dependency to authenticate MCP requests via apikey.
    Checks the 'apikey' header or query parameter, or 'Authorization: Bearer' token.
    Throws 401 if unauthorized, otherwise returns the session identity.
    """
    session = SessionState()
    
    # Check for Bearer token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        from ..graphql.libs.token_auth import get_user_context
        # Use existing logic from graphql token_auth to fetch context which includes verifying the JWT
        try:
            # We construct a fake request object with headers that get_user_context expects
            user, error = get_user_context(request)
            if error:
                 raise HTTPException(
                     status_code=status.HTTP_401_UNAUTHORIZED,
                     detail=error
                 )
            if user:
               return user
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(e)
            )

    # Check for API key in headers or query parameters
    api_key = request.headers.get("apikey") or request.query_params.get("apikey")
    
    if api_key:
        api_keys = session.keys
        key_id = check_auth(api_keys, api_key)
        if key_id:
            return key_id
            
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing authentication credentials"
    )