"""向量存储管理器 - 封装 Milvus 混合检索与有界纠错"""

from dataclasses import dataclass
from typing import List

from langchain_core.documents import Document
from langchain_milvus import BM25BuiltInFunction, Milvus
from loguru import logger

from app.config import config
from app.core.milvus_client import milvus_manager
from app.services.retrieval_confidence import (
    ConfidenceResult,
    evaluate_confidence,
    rewrite_query_for_retry,
)
from app.services.retrieval_reranker import expand_query, rerank_with_fallback, score_documents
from app.services.vector_embedding_service import vector_embedding_service


# 统一使用 biz collection
COLLECTION_NAME = "biz"


@dataclass(frozen=True)
class RetrievalOutcome:
    documents: list[Document]
    confidence: ConfidenceResult
    original_query: str
    query_used: str
    attempts: int
    rewritten: bool
    reranker_used: bool
    source_types: tuple[str, ...]


class VectorStoreManager:
    """向量存储管理器"""

    def __init__(self):
        """初始化向量存储管理器"""
        self.vector_store = None
        self.collection_name = COLLECTION_NAME
        self._initialize_vector_store()

    def _initialize_vector_store(self):
        """初始化 Milvus VectorStore"""
        try:
            # 必须在 PyMilvus / langchain_milvus 访问 Collection 之前建立连接，
            # 否则会出现 ConnectionNotExistException: should create connection first.
            # （模块导入时就会执行此处，早于 FastAPI lifespan 中的 milvus_manager.connect）
            _ = milvus_manager.connect()

            connection_args = {
                "host": config.milvus_host,
                "port": config.milvus_port,
            }

            # 创建 LangChain Milvus VectorStore
            # dense embedding 与 Milvus 内置 BM25 共同召回，再由 RRF 融合。
            self.vector_store = Milvus(
                embedding_function=vector_embedding_service,
                collection_name=self.collection_name,
                connection_args=connection_args,
                auto_id=False,  # 使用自定义 id
                drop_old=False,
                text_field="content",
                vector_field=["dense", "sparse"],
                builtin_function=BM25BuiltInFunction(
                    input_field_names="content",
                    output_field_names="sparse",
                    analyzer_params={"type": "chinese"},
                    function_name="content_bm25",
                ),
                index_params=[
                    {"metric_type": "IP", "index_type": "FLAT", "params": {}},
                    {
                        "metric_type": "BM25",
                        "index_type": "SPARSE_INVERTED_INDEX",
                        "params": {"inverted_index_algo": "DAAT_MAXSCORE"},
                    },
                ],
                search_params=[
                    {"metric_type": "IP", "params": {}},
                    {"metric_type": "BM25", "params": {}},
                ],
                primary_field="id",  # 主键字段
                metadata_field="metadata",  # 元数据字段
            )

            logger.info(
                f"VectorStore 初始化成功: {config.milvus_host}:{config.milvus_port}, "
                f"collection: {self.collection_name}"
            )

        except Exception as e:
            logger.error(f"VectorStore 初始化失败: {e}")
            raise

    def add_documents(self, documents: List[Document]) -> List[str]:
        """
        批量添加文档到向量存储（自动批量向量化）

        Args:
            documents: 文档列表

        Returns:
            List[str]: 文档 ID 列表
        """
        try:
            import time
            import uuid
            start_time = time.time()

            # 为每个文档生成唯一 id（因为 auto_id=False）
            ids = [str(uuid.uuid4()) for _ in documents]

            # LangChain Milvus 的 add_documents 会自动调用 embedding_function
            # 并进行批量处理，性能更好
            result_ids = self.vector_store.add_documents(documents, ids=ids)

            elapsed = time.time() - start_time
            logger.info(
                f"批量添加 {len(documents)} 个文档到 VectorStore 完成, "
                f"耗时: {elapsed:.2f}秒, 平均: {elapsed/len(documents):.2f}秒/个"
            )
            return result_ids
        except Exception as e:
            logger.error(f"添加文档失败: {e}")
            raise

    def delete_by_source(self, file_path: str) -> int:
        """
        删除指定文件的所有文档

        Args:
            file_path: 文件路径

        Returns:
            int: 删除的文档数量
        """
        try:
            # 使用 milvus_manager 获取已连接的 collection
            collection = milvus_manager.get_collection()
            
            # metadata 是 JSON 字段，使用 JSON 路径查询语法
            # _source 是文档的来源文件路径
            expr = f'metadata["_source"] == "{file_path}"'
            
            result = collection.delete(expr)
            deleted_count = result.delete_count if hasattr(result, "delete_count") else 0
            
            logger.info(f"删除文件旧数据: {file_path}, 删除数量: {deleted_count}")
            return deleted_count
            
        except Exception as e:
            logger.warning(f"删除旧数据失败 (可能是首次索引): {e}")
            return 0

    def get_vector_store(self) -> Milvus:
        """
        获取 VectorStore 实例

        Returns:
            Milvus: VectorStore 实例
        """
        return self.vector_store

    def similarity_search(self, query: str, k: int = 3) -> List[Document]:
        """
        相似度搜索

        Args:
            query: 查询文本
            k: 返回结果数量

        Returns:
            List[Document]: 相关文档列表
        """
        try:
            docs = self.vector_store.similarity_search(
                query,
                k=k,
                fetch_k=max(config.rag_dense_candidates, config.rag_sparse_candidates),
                ranker_type="rrf",
                ranker_params={"k": config.rag_rrf_k},
            )
            logger.debug(f"相似度搜索完成: query='{query}', 结果数={len(docs)}")
            return docs
        except Exception as e:
            logger.error(f"相似度搜索失败: {e}")
            return []

    def search_relevant_documents(
        self,
        query: str,
        k: int = 3,
        candidate_k: int | None = None,
        source_types: list[str] | None = None,
    ) -> List[Document]:
        """兼容旧调用；详细诊断由 search_with_diagnostics 返回。"""
        return self.search_with_diagnostics(
            query,
            k=k,
            candidate_k=candidate_k,
            source_types=source_types,
        ).documents

    def search_with_diagnostics(
        self,
        query: str,
        k: int = 3,
        candidate_k: int | None = None,
        source_types: list[str] | None = None,
    ) -> RetrievalOutcome:
        """混合召回后评估置信度；低置信度最多改写并重试一次。"""
        allowed_types = self._validate_source_types(source_types)
        expr = self._source_filter_expr(allowed_types)
        try:
            candidate_count = max(candidate_k or config.rag_rerank_candidates, k)
            candidates = self._hybrid_candidates(query, candidate_count, expr)
            confidence = self._confidence(query, candidates)
            query_used = query
            attempts = 1
            rewritten = False

            if confidence.level == "low" and config.rag_retry_on_low_confidence:
                retry_query = rewrite_query_for_retry(query)
                if retry_query != query:
                    retry_candidates = self._hybrid_candidates(retry_query, candidate_count, expr)
                    retry_confidence = self._confidence(query, retry_candidates)
                    attempts = 2
                    rewritten = True
                    if retry_confidence.score >= confidence.score:
                        candidates = retry_candidates
                        confidence = retry_confidence
                        query_used = retry_query

            semantic_scores: list[float] | None = None
            if confidence.level == "low" and candidates:
                try:
                    semantic_scores = score_documents(
                        query, [doc for doc, _ in candidates]
                    )
                    semantic_score = max(semantic_scores)
                    if semantic_score >= config.rag_semantic_evidence_threshold:
                        confidence = ConfidenceResult(
                            score=max(confidence.score, config.rag_confidence_low),
                            level="medium",
                            lexical_coverage=confidence.lexical_coverage,
                            exact_marker_coverage=confidence.exact_marker_coverage,
                            source_dominance=confidence.source_dominance,
                            top_source=confidence.top_source,
                            semantic_score=semantic_score,
                        )
                except Exception as semantic_error:
                    logger.warning(f"语义证据评估失败，保持低置信度: {semantic_error}")

            reranker_used = confidence.level == "medium"
            if reranker_used:
                docs = rerank_with_fallback(
                    query, candidates, k=k, cross_scores=semantic_scores
                )
            else:
                docs = self._unique_rrf_documents(candidates, k)
            docs = [
                self._expand_parent(
                    doc,
                    confidence=confidence,
                    attempts=attempts,
                    rewritten=rewritten,
                    reranker_used=reranker_used,
                )
                for doc in docs
            ]
            logger.debug(
                f"自适应检索完成: confidence={confidence.score:.3f}/{confidence.level}, "
                f"attempts={attempts}, reranker={reranker_used}, 结果数={len(docs)}"
            )
            return RetrievalOutcome(
                documents=docs,
                confidence=confidence,
                original_query=query,
                query_used=query_used,
                attempts=attempts,
                rewritten=rewritten,
                reranker_used=reranker_used,
                source_types=allowed_types,
            )
        except Exception as e:
            logger.error(f"混合检索失败: {e}")
            confidence = ConfidenceResult(0.0, "low", 0.0, 0.0, 0.0, "")
            return RetrievalOutcome([], confidence, query, query, 1, False, False, allowed_types)

    def _hybrid_candidates(
        self, query: str, candidate_count: int, expr: str | None
    ) -> list[tuple[Document, float]]:
        return self.vector_store.similarity_search_with_score(
            expand_query(query),
            k=candidate_count,
            fetch_k=max(config.rag_dense_candidates, config.rag_sparse_candidates),
            ranker_type="rrf",
            ranker_params={"k": config.rag_rrf_k},
            expr=expr,
        )

    @staticmethod
    def _unique_rrf_documents(
        candidates: list[tuple[Document, float]], k: int
    ) -> list[Document]:
        docs: list[Document] = []
        seen: set[str] = set()
        for doc, score in candidates:
            source = str(doc.metadata.get("_file_name") or doc.metadata.get("_source") or "")
            if source and source not in seen:
                seen.add(source)
                metadata = dict(doc.metadata)
                metadata["_initial_retrieval_score"] = float(score)
                metadata["_retrieval_pipeline"] = "dense+bm25->rrf"
                docs.append(Document(page_content=doc.page_content, metadata=metadata))
            if len(docs) == k:
                break
        return docs

    @staticmethod
    def _confidence(query: str, candidates: list[tuple[Document, float]]) -> ConfidenceResult:
        return evaluate_confidence(
            query,
            candidates,
            low_threshold=config.rag_confidence_low,
            high_threshold=config.rag_confidence_high,
        )

    @staticmethod
    def _validate_source_types(source_types: list[str] | None) -> tuple[str, ...]:
        allowed = {"official", "case", "incident", "knowledge"}
        selected = tuple(dict.fromkeys(source_types or sorted(allowed)))
        invalid = set(selected) - allowed
        if invalid:
            raise ValueError(f"不支持的来源类型: {sorted(invalid)}")
        return selected

    @staticmethod
    def _source_filter_expr(source_types: tuple[str, ...]) -> str | None:
        if set(source_types) == {"official", "case", "incident", "knowledge"}:
            return None
        values = ", ".join(f'"{source_type}"' for source_type in source_types)
        return f'metadata["_source_type"] in [{values}]'

    @staticmethod
    def _expand_parent(
        doc: Document,
        *,
        confidence: ConfidenceResult,
        attempts: int,
        rewritten: bool,
        reranker_used: bool,
    ) -> Document:
        """检索 child，生成阶段恢复其所属 H2 parent。"""
        metadata = dict(doc.metadata)
        parent_content = str(metadata.get("_parent_content") or doc.page_content)
        metadata["_matched_child"] = str(metadata.get("_child_content") or doc.page_content)
        metadata["_context_expanded"] = parent_content != doc.page_content
        metadata["_retrieval_confidence"] = confidence.score
        metadata["_retrieval_confidence_level"] = confidence.level
        metadata["_retrieval_attempts"] = attempts
        metadata["_query_rewritten"] = rewritten
        metadata["_reranker_used"] = reranker_used
        metadata["_evidence_sufficient"] = confidence.level != "low"
        return Document(page_content=parent_content, metadata=metadata)


# 全局单例
vector_store_manager = VectorStoreManager()
