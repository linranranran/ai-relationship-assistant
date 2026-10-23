from typing import List, Optional, Tuple
from pathlib import Path
from datetime import datetime
import os

from llama_index.core import (
    VectorStoreIndex,
    StorageContext,
    Settings,
    SimpleDirectoryReader,
    load_index_from_storage
)
from llama_index.core.node_parser import SentenceSplitter, MarkdownNodeParser, MarkdownElementNodeParser
from llama_index.core.extractors import TitleExtractor
from llama_index.core.ingestion import IngestionPipeline, IngestionCache, DocstoreStrategy
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.storage.docstore.redis import RedisDocumentStore
from llama_index.storage.index_store.redis import RedisIndexStore
from llama_index.storage.kvstore.redis import RedisKVStore as RedisCache
import chromadb
from llama_index.embeddings.openai_like import OpenAILikeEmbedding
from llama_index.llms.openai_like import OpenAILike
from config.settings import Settings as AppSettings
from utils.logger import setup_logger
# from core.pdfProcessor import MultimodalPDFProcessor
from core.pdf_parser import MultimodalPDFProcessor

logger = setup_logger(__name__)


class DocumentIngestionPipeline:
    """文档摄取管道"""

    def __init__(self):
        self._setup_models()
        self.index: Optional[VectorStoreIndex] = None
        self.chroma_vector_store: Optional[ChromaVectorStore] = None
        self.text_pipeline: Optional[IngestionPipeline] = None
        self.markdown_pipeline: Optional[IngestionPipeline] = None

        # 1. 初始化存储组件 (Redis & Chroma)
        self._initialize_storage_components()

        # 2. 创建不同类型的 Pipeline
        self._create_pipelines()

        # 3. 初始化 PDF 处理器
        self.pdf_processor = MultimodalPDFProcessor()

        # 创建 StorageContext
        self.storage_context = StorageContext.from_defaults(
            vector_store=self.chroma_vector_store,
            docstore=self.redis_document_store,
            index_store=self.redis_index_store
        )

    def _setup_models(self):
        """设置LLM和嵌入模型"""
        Settings.llm = OpenAILike(
            api_key=AppSettings.API_KEY,
            api_base=AppSettings.API_BASE_URL,
            model=AppSettings.MODEL,
            temperature=AppSettings.TEMPERATURE,
            max_tokens=AppSettings.LLM_MAX_TOKENS,
            context_window=AppSettings.LLM_CONTEXT_WINDOW,
            is_chat_model=True,
            is_function_calling_model=False,
            additional_kwargs={"extra_body": {"enable_thinking": False}},
        )
        Settings.embed_model = OpenAILikeEmbedding(
            model_name=AppSettings.EMBEDDING_MODEL,
            api_base=AppSettings.API_BASE_URL,
            api_key=AppSettings.API_KEY,
            dimensions=AppSettings.EMBEDDING_DIMENSIONS,
            embed_batch_size=AppSettings.EMBEDDING_BATCH_SIZE,
            max_retries=2,
        )

    def _initialize_storage_components(self):
        """初始化存储组件"""
        # 文档、索引和摄取缓存共用带认证的 Redis 连接配置。
        self.redis_kvstore = RedisCache(
            redis_uri=AppSettings.REDIS_URL,
            socket_connect_timeout=5,
            socket_timeout=30,
            **AppSettings.REDIS_TLS_KWARGS,
        )
        self.redis_index_store = RedisIndexStore(
            redis_kvstore=self.redis_kvstore,
            namespace=AppSettings.REDIS_INDEX_NAMESPACE,
        )
        self.redis_document_store = RedisDocumentStore(
            redis_kvstore=self.redis_kvstore,
            namespace=AppSettings.REDIS_DOCSTORE_NAMESPACE,
        )
        # 初始化向量存储
        self._create_chroma_db()

    def _create_chroma_db(self):
        """创建Chroma向量存储"""
        chroma_client = chromadb.PersistentClient(AppSettings.CHROMA_PERSIST_DIR)
        chroma_collection = chroma_client.get_or_create_collection("quickstart")
        self.chroma_vector_store = ChromaVectorStore(chroma_collection=chroma_collection)

    def _create_pipelines(self):
        """创建不同类型的摄取管道"""
        # 通用配置
        common_config = {
            "vector_store": self.chroma_vector_store,
            "docstore": self.redis_document_store,
            "cache": IngestionCache(
                cache=self.redis_kvstore,
                collection=AppSettings.INGESTION_CACHE_COLLECTION,
            ),
            "docstore_strategy": DocstoreStrategy.UPSERTS
        }

        # 1. 普通文本文件的管道 (使用 SentenceSplitter)
        self.text_pipeline = IngestionPipeline(
            transformations=[
                SentenceSplitter(
                    chunk_size=AppSettings.CHUNK_SIZE,
                    chunk_overlap=AppSettings.CHUNK_OVERLAP
                ),
                TitleExtractor(nodes=AppSettings.TITLE_EXTRACTOR_NODES),
                Settings.embed_model,
            ],
            **common_config
        )

        # 2. Markdown 文件的管道 (使用 MarkdownNodeParser)
        # 对于没有目录层级的文档，优化MarkdownNodeParser配置
        self.markdown_pipeline = IngestionPipeline(
            transformations=[
                MarkdownNodeParser(
                    include_metadata=True,  # 保留文档元数据
                    include_prev_next_rel=True,  # 保留节点之间的关系，有助于上下文理解
                ),
                # 使用更适合Markdown的分块策略，避免在图片标签中间切分
                SentenceSplitter(
                    chunk_size=AppSettings.CHUNK_SIZE,
                    chunk_overlap=AppSettings.CHUNK_OVERLAP,
                    # 使用更安全的分隔符，避免在图片标签中间切分
                    separator="\n\n",
                    # 禁用默认的句子切分，使用段落级别切分
                    paragraph_separator="\n\n"
                ),
                TitleExtractor(nodes=AppSettings.TITLE_EXTRACTOR_NODES),
                Settings.embed_model,
            ],
            **common_config
        )

    def _create_custom_pipelines(self, chunk_size: int, chunk_overlap: int):
        """使用自定义分块参数创建临时管道"""
        common_config = {
            "vector_store": self.chroma_vector_store,
            "docstore": self.redis_document_store,
            "cache": IngestionCache(
                cache=self.redis_kvstore,
                collection=AppSettings.INGESTION_CACHE_COLLECTION,
            ),
            "docstore_strategy": DocstoreStrategy.UPSERTS
        }

        text_pipeline = IngestionPipeline(
            transformations=[
                SentenceSplitter(
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap
                ),
                TitleExtractor(nodes=AppSettings.TITLE_EXTRACTOR_NODES),
                Settings.embed_model,
            ],
            **common_config
        )

        markdown_pipeline = IngestionPipeline(
            transformations=[
                MarkdownNodeParser(
                    include_metadata=True,
                    include_prev_next_rel=True,
                ),
                SentenceSplitter(
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    separator="\n\n",
                    paragraph_separator="\n\n"
                ),
                TitleExtractor(nodes=AppSettings.TITLE_EXTRACTOR_NODES),
                Settings.embed_model,
            ],
            **common_config
        )

        return text_pipeline, markdown_pipeline

    def ingest_documents(self, file_paths: List[str],
                         chunk_size: Optional[int] = None,
                         chunk_overlap: Optional[int] = None) -> Tuple:
        """摄取文档并创建索引，支持自定义分块参数"""
        try:
            documents = []
            upload_time = datetime.now().isoformat()

            # 对上传的多个文件进行解析
            for file_path in file_paths:
                if not Path(file_path).exists():
                    logger.warning(f"文件不存在: {file_path}")
                    continue

                file_name = os.path.basename(file_path)
                file_ext = Path(file_path).suffix.lower()
                file_size = os.path.getsize(file_path)

                # 判断是否是PDF文件
                if file_ext == ".pdf":
                    # 保留你的 PDF 处理逻辑
                    document = self.pdf_processor.convert_pdf_to_markdown_doc(file_path)
                    document.metadata["upload_time"] = upload_time
                    document.metadata["file_type"] = file_ext
                    document.metadata["file_size"] = file_size
                    documents.append(document)
                else:
                    # 1. 获取物理路径 (用于读取文件内容)
                    physical_path = file_path

                    # 3. 构造一个固定的"逻辑 ID"
                    stable_id = f"knowledge_base/{file_name}"

                    # 4. 读取数据
                    reader = SimpleDirectoryReader(input_files=[physical_path])
                    docs = reader.load_data()

                    for doc in docs:
                        doc.id_ = stable_id

                        doc.metadata.pop("file_path", None)
                        doc.metadata.pop("last_modified_date", None)

                        doc.metadata["file_name"] = file_name
                        doc.metadata["doc_id"] = stable_id
                        doc.metadata["upload_time"] = upload_time
                        doc.metadata["file_type"] = file_ext
                        doc.metadata["file_size"] = file_size

                    documents.extend(docs)
                    logger.info(f"已处理文档: {file_name} (ID: {stable_id})")
                logger.info(f"已读取文档: {file_path}")

            if not documents:
                return "error", "没有找到有效的文档"

            # 如果指定了自定义分块参数，临时重建管道
            cs = chunk_size or AppSettings.CHUNK_SIZE
            co = chunk_overlap or AppSettings.CHUNK_OVERLAP
            use_custom = chunk_size is not None or chunk_overlap is not None

            if use_custom:
                text_pipeline, markdown_pipeline = self._create_custom_pipelines(cs, co)
            else:
                text_pipeline = self.text_pipeline
                markdown_pipeline = self.markdown_pipeline

            # 根据文档类型分别处理
            logger.info("开始处理文档...")
            markdown_docs = []
            text_docs = []

            for doc in documents:
                if doc.metadata.get("content_type") == "markdown":
                    markdown_docs.append(doc)
                else:
                    text_docs.append(doc)

            pipeline_nodes = []

            # 处理 Markdown 文档
            if markdown_docs:
                logger.info(f"处理 {len(markdown_docs)} 个 Markdown 文档...")
                md_nodes = markdown_pipeline.run(documents=markdown_docs, show_progress=True)
                pipeline_nodes.extend(md_nodes)
                logger.info(f"Markdown 文档处理完成，生成了 {len(md_nodes)} 个节点")

            # 处理普通文本文档
            if text_docs:
                logger.info(f"处理 {len(text_docs)} 个普通文本文档...")
                txt_nodes = text_pipeline.run(documents=text_docs, show_progress=True)
                pipeline_nodes.extend(txt_nodes)
                logger.info(f"普通文本文档处理完成，生成了 {len(txt_nodes)} 个节点")

            logger.info(f"所有文档处理完成，共生成了 {len(pipeline_nodes)} 个节点")

            # 检查是否有新节点生成（如果文档重复，pipeline_nodes将为空）
            if not pipeline_nodes:
                logger.info("所有文档都是重复的，无需更新索引")
                result = "所有文档都是重复的，无需处理"
                return "success", result, pipeline_nodes

            # 把切分好的"小切片"也存进 docstore
            # 注意：虽然方法叫 add_documents，但它其实是用来存 nodes 的
            self.storage_context.docstore.add_documents(pipeline_nodes)

            # 创建向量索引
            logger.info("创建文档对应索引对象")
            if not self.index:
                logger.info("创建文档对应索引对象")
                self.index = VectorStoreIndex(pipeline_nodes, storage_context=self.storage_context,
                                              embed_model=Settings.embed_model)
            else:
                logger.info("获取当前索引，进行添加节点")
                self.index.insert_nodes(pipeline_nodes)

            # 调试信息：创建索引后
            if self.index:
                print(f" 索引后 docstore 文档数: {len(self.index.docstore.docs)}")
                print(f" 索引 ID: {self.index.index_id}")

            result = f"成功摄取了 {len(file_paths)} 个文档，生成了 {len(pipeline_nodes)} 个节点"
            logger.info(result)
            return "success", result, pipeline_nodes

        except Exception as e:
            error_msg = f"文档摄取失败: {str(e)}"
            logger.error(error_msg)
            return "error", error_msg, ""

    def get_documents(self):
        """从 Redis 加载索引，并保留配置的 Redis 和 Chroma 存储上下文。"""
        logger.info("读取已有向量数据库中的文档和索引...")
        try:
            index_structs = self.redis_index_store.index_structs()
            if index_structs:
                self.index = load_index_from_storage(
                    self.storage_context,
                    index_id=index_structs[0].index_id,
                    embed_model=Settings.embed_model,
                )
                logger.info(f"成功加载索引: {self.index.index_id}")
                return self.index

            if self.chroma_vector_store.client.count() == 0:
                logger.info("没有已入库文档，等待首次上传")
                return None

            # 在原存储上下文中恢复结构，确保索引仍保存在 Redis 中。
            self.index = VectorStoreIndex(
                nodes=[],
                storage_context=self.storage_context,
                embed_model=Settings.embed_model,
            )
            logger.info(f"已为现有向量恢复 Redis 索引: {self.index.index_id}")
            return self.index
        except Exception as e:
            logger.error(f"加载索引失败: {e}")
            return None
