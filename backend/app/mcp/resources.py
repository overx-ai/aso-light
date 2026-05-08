"""MCP resources for ASO-Light.

Resources are read-only handles the LLM can attach to a conversation. The
heavy data lifting lives in the tool modules; resources are intentionally
thin and currently empty — the same data is reachable via tools, and tools
let the LLM describe what it's doing rather than silently attaching state.
Add resources here when there is a concrete need for context-attached data.
"""

# Tools deliberately replace resources for now. This module is the
# registration point if/when we add static resources later.
