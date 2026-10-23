from __future__ import annotations

import os
import shutil
import tempfile
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from config.settings import Settings
from utils.logger import setup_logger


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=16000)
    tenant_id: str = Field(default='default', pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')
    knowledge_base_id: str = Field(default='default', pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')


class ChunkConfigRequest(BaseModel):
    chunk_size: int
    chunk_overlap: int


logger = setup_logger(__name__)

router = APIRouter()

svc: Any = None


def event_mode():
    return os.getenv('RAG_INGESTION_MODE', 'events') == 'events'


def require_legacy_write():
    if event_mode():
        raise HTTPException(status_code=409, detail='事件模式请通过知识库发布文档事件；此接口不允许绕过版本管理。')


@lru_cache(maxsize=1)
def get_event_query_service():
    from reliable_ingestion.config import RuntimeConfig
    from reliable_ingestion.query import EventQueryService
    return EventQueryService(RuntimeConfig.from_env())


def event_snapshot(tenant_id, knowledge_base_id):
    from reliable_ingestion.config import RuntimeConfig
    from reliable_ingestion.store import Inbox
    from reliable_ingestion.events import canonical
    config = RuntimeConfig.from_env()
    snapshot_id = Inbox(config.database).snapshot(canonical([tenant_id, knowledge_base_id]))
    if not snapshot_id:
        return {'nodes': [], 'documents': {}}
    return json.loads((config.root / 'snapshots' / snapshot_id / 'snapshot.json').read_text(encoding='utf-8'))


def init_rag_service():
    global svc
    if svc is not None:
        return

    from core.application import RAGApplication
    from core.documentManager import DocumentManager

    class RAGApiService:
        def __init__(self):
            self.app = RAGApplication()
            self.doc_manager = DocumentManager()

        def upload_files(self, files: list[tuple[str, bytes]],
                         chunk_size: Optional[int] = None,
                         chunk_overlap: Optional[int] = None) -> dict[str, Any]:
            tmpdir = tempfile.mkdtemp(prefix="rag_api_upload_")
            paths = []
            filenames = []
            try:
                for name, content in files:
                    p = os.path.join(tmpdir, name)
                    with open(p, "wb") as f:
                        f.write(content)
                    paths.append(p)
                    filenames.append(name)

                logger.info(f"上传文档: {paths}")
                status, status_text = self.app.upload_and_process_files(
                    paths, chunk_size=chunk_size, chunk_overlap=chunk_overlap
                )
                return {
                    "status": status,
                    "message": status_text,
                    "processed_files": filenames,
                }
            except Exception as e:
                logger.error(f"文件上传失败: {e}")
                return {
                    "status": "error",
                    "message": str(e),
                    "processed_files": [],
                }
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

        def list_documents(self) -> list[dict[str, Any]]:
            return self.doc_manager.get_all_document_names()

        def get_document_chunks(self, doc_id: str, page: int = 1, page_size: int = 10) -> dict[str, Any]:
            return self.doc_manager.get_document_chunks(doc_id, page, page_size)

        def delete_document(self, doc_id: str) -> dict[str, Any]:
            # 1. 删除ChromaDB中的向量
            result = self.doc_manager.delete_document(doc_id)
            
            if result.get("status") == "success":
                # 2. 删除Redis中的文档存储和索引存储
                try:
                    self.app.delete_document_from_redis(doc_id)
                except Exception as e:
                    logger.warning(f"删除Redis存储失败: {e}")
                
                # 3. 删除BM25索引（需要重新构建）
                try:
                    self.app.rebuild_bm25_index()
                except Exception as e:
                    logger.warning(f"重建BM25索引失败: {e}")
            
            return result

        def reset_system(self) -> dict[str, str]:
            self.app.reset()
            return {"status": "success", "message": "系统已重置"}

        async def query(self, query: str) -> dict[str, Any]:
            answer, sources = await self.app.query_documents(query=query, knowledge_bool=True)
            return {"answer": answer, "sources": [str(s) for s in (sources or [])]}

    svc = RAGApiService()


@router.post("/upload")
async def upload_docs(
    files: list[UploadFile] = File(...),
    chunk_size: Optional[int] = Form(None),
    chunk_overlap: Optional[int] = Form(None),
):
    require_legacy_write()
    init_rag_service()
    try:
        payload = [(file.filename or "unnamed", await file.read()) for file in files]
        result = svc.upload_files(payload, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        return result
    except Exception as e:
        logger.error(str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/documents")
async def list_docs(tenant_id: str = 'default', knowledge_base_id: str = 'default'):
    if event_mode():
        snapshot = await run_in_threadpool(event_snapshot, tenant_id, knowledge_base_id)
        documents = {}
        for node in snapshot['nodes']:
            metadata = node['metadata']
            documents.setdefault(metadata['document_id'], {
                'document_id': metadata['document_id'], 'file_name': metadata['file_name'],
                'document_version': metadata['document_version'], 'chunk_count': 0})['chunk_count'] += 1
        return {'documents': list(documents.values())}
    init_rag_service()
    try:
        documents = svc.list_documents()
        return {"documents": documents}
    except Exception as e:
        logger.error(str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chunks/{doc_id:path}")
async def get_document_chunks(
    doc_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    tenant_id: str = 'default',
    knowledge_base_id: str = 'default',
):
    if event_mode():
        snapshot = await run_in_threadpool(event_snapshot, tenant_id, knowledge_base_id)
        nodes = [node for node in snapshot['nodes'] if node['metadata']['document_id'] == doc_id]
        return {'chunks': nodes[(page - 1) * page_size:page * page_size], 'total': len(nodes),
                'page': page, 'page_size': page_size}
    init_rag_service()
    try:
        return svc.get_document_chunks(doc_id, page=page, page_size=page_size)
    except Exception as e:
        logger.error(str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/documents/{doc_id:path}")
async def delete_document(doc_id: str):
    require_legacy_write()
    init_rag_service()
    try:
        return svc.delete_document(doc_id)
    except Exception as e:
        logger.error(str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/chunk")
async def get_chunk_config():
    return {
        "chunk_size": Settings.CHUNK_SIZE,
        "chunk_overlap": Settings.CHUNK_OVERLAP,
    }


@router.put("/config/chunk")
async def update_chunk_config(req: ChunkConfigRequest):
    require_legacy_write()
    if req.chunk_size < 50 or req.chunk_size > 4000:
        raise HTTPException(status_code=400, detail="chunk_size 范围: 50-4000")
    if req.chunk_overlap < 0 or req.chunk_overlap >= req.chunk_size:
        raise HTTPException(status_code=400, detail="chunk_overlap 必须 >= 0 且 < chunk_size")
    Settings.CHUNK_SIZE = req.chunk_size
    Settings.CHUNK_OVERLAP = req.chunk_overlap
    return {
        "status": "success",
        "chunk_size": Settings.CHUNK_SIZE,
        "chunk_overlap": Settings.CHUNK_OVERLAP,
    }


@router.post("/reset")
async def reset_system():
    require_legacy_write()
    init_rag_service()
    try:
        return svc.reset_system()
    except Exception as e:
        logger.error(str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query")
async def query_docs(req: QueryRequest):
    if event_mode():
        try:
            service = await run_in_threadpool(get_event_query_service)
            return await service.query(req.query, req.tenant_id, req.knowledge_base_id)
        except Exception as error:
            logger.error('事件索引查询失败: %s', type(error).__name__)
            raise HTTPException(status_code=503, detail='索引查询暂不可用，请检查服务日志和依赖。') from error
    init_rag_service()
    try:
        return await svc.query(
            query=req.query,
        )
    except Exception as e:
        logger.error(str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/ingestion/jobs')
async def ingestion_jobs(limit: int = Query(20, ge=1, le=200)):
    from reliable_ingestion.config import RuntimeConfig
    from reliable_ingestion.store import Inbox
    jobs = await run_in_threadpool(Inbox(RuntimeConfig.from_env().database).jobs, limit)
    return {'jobs': [{key: value for key, value in job.items()
                      if key not in {'payload', 'fingerprint'}} for job in jobs]}
