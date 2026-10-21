"""销售数据 HTTP 入口。同步函数由 FastAPI 线程池执行，不阻塞 SSE。

所有 owner 都取自登录令牌；即使知道别人的 Person/message ID 也不能读取。
这里只提供导入和证据读取，不提供未授权的群聊采集或消息发送接口。
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.auth import get_current_user
from backend.config import AGENT_TOOL_STRATEGY
from backend.db import neo4j_client
from backend.db.customer_chat_store import ChatImportConflict, get_message
from backend.sales.contracts import ChatImportRequest, ChatImportResult, StoredChatMessage
from backend.sales.importer import SalesPersonNotFound, import_simulated_chat

router = APIRouter(prefix="/api/sales", tags=["sales"])
logger = logging.getLogger(__name__)


@router.get("/capabilities")
def sales_capabilities(current_user=Depends(get_current_user)):
    return {"preparation_available": AGENT_TOOL_STRATEGY == "direct",
            "source_types": ["simulated_chat"]}


@router.get("/customers")
def search_customers(query: Annotated[str, Query(min_length=1, max_length=128)],
                     current_user=Depends(get_current_user)):
    driver = None
    try:
        driver = neo4j_client.get_neo4j_driver()
        people = neo4j_client.find_person_fuzzy(driver, query.strip(), current_user["user_id"], 10)
        # 不自动选择第一个同名结果，不返回人物内部 owner 或整个关系图。
        return {"customers": [{"id": person["id"], "name": person.get("name", "未命名"),
                               "occupation": person.get("occupation", "")}
                              for person in people]}
    except Exception:
        logger.warning("sales.customer_search_failed")
        raise HTTPException(503, "客户查询暂不可用，请稍后重试") from None
    finally:
        if driver is not None:
            driver.close()


@router.post("/chat-imports", response_model=ChatImportResult)
def import_chat(request: ChatImportRequest, current_user=Depends(get_current_user)):
    try:
        return import_simulated_chat(owner_id=current_user["user_id"], request=request)
    except SalesPersonNotFound:
        raise HTTPException(404, "客户不存在") from None
    except ChatImportConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception:
        # 数据库/驱动异常可能含连接信息和原文，不能直接 str(exc) 返回给浏览器。
        logger.warning("sales.chat_import_failed")
        raise HTTPException(503, "聊天导入暂不可用，请稍后重试") from None


@router.get("/messages/{message_id}", response_model=StoredChatMessage)
def read_evidence(message_id: UUID, current_user=Depends(get_current_user)):
    try:
        message = get_message(owner_id=current_user["user_id"], message_id=str(message_id))
    except Exception:
        logger.warning("sales.evidence_read_failed")
        raise HTTPException(503, "证据原文暂不可用") from None
    if message is None:
        raise HTTPException(404, "证据原文不存在")
    return message
