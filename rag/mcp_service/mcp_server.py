from __future__ import annotations

from typing import Any

import httpx
from fastmcp import FastMCP

from config.settings import Settings

mcp = FastMCP("llamaindex-rag")
RAG_API_URL = f"http://{Settings.RAG_API_HOST}:{Settings.RAG_API_PORT}/api/docs"


@mcp.tool()
def query_rag(
    query: str,
    knowledge_base_id: str = 'default',
) -> dict[str, Any]:
    payload = {
        "query": query,
        "tenant_id": Settings.RAG_TENANT_ID,
        "knowledge_base_id": knowledge_base_id,
    }
    headers = {'Authorization': 'Bearer ' + Settings.RAG_SERVICE_TOKEN} if Settings.RAG_SERVICE_TOKEN else {}
    response = httpx.post(f"{RAG_API_URL}/query", json=payload, headers=headers, timeout=120.0)
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    mcp.run()
