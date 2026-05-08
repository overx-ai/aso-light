"""MCP (Model Context Protocol) server for ASO-Light.

Mounted at ``/mcp`` on the FastAPI app. Exposes the project's REST surface as
MCP tools so LLM clients (Claude Desktop, OpenAI MCP, etc.) can drive the
backend programmatically.

Auth uses Personal Access Tokens (see :mod:`app.api.v1.tokens`); each tool
call resolves the PAT to a ``user_id`` and applies the same
``app.credential_id -> credential.user_id`` ownership chain that the REST
routers enforce via ``_get_verified_app``.
"""

from app.mcp.server import mcp, mcp_app

__all__ = ["mcp", "mcp_app"]
