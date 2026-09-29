"""向量存储管理器 - 封装 Milvus 混合检索与有界纠错"""

from dataclasses import dataclass, replace

from langchain_core.documents import Document
from langchain_milvus import BM25BuiltInFunction, Milvus
from loguru import logger

from app.config import config
from app.core.milvus_client import milvus_manager
from app.services.retrieval_confidence import (
    ConfidenceResult,
    evaluate_rank_confidence,
    is_dual_rank_high_confidence,
    parent_key,
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

    def add_documents(self, documents: list[Document]) -> list[str]:
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

    def similarity_search(self, query: str, k: int = 3) -> list[Document]:
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
    ) -> list[Document]:
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
        """双路召回并按排名一致性路由；无候选时最多改写重试一次。"""
        allowed_types = self._validate_source_types(source_types)
        expr = self._source_filter_expr(allowed_types)
        try:
            candidate_count = max(candidate_k or config.rag_rerank_candidates, k)
            candidates = self._hybrid_candidates(query, candidate_count, expr)
            confidence = self._confidence(query, candidates, final_k=k)
            query_used = query
            attempts = 1
            rewritten = False

            if confidence.level == "low" and config.rag_retry_on_low_confidence:
                retry_query = rewrite_query_for_retry(query)
                if retry_query != query:
                    retry_candidates = self._hybrid_candidates(retry_query, candidate_count, expr)
                    retry_confidence = self._confidence(query, retry_candidates, final_k=k)
                    attempts = 2
                    rewritten = True
                    if retry_confidence.score >= confidence.score:
                        candidates = retry_candidates
                        confidence = retry_confidence
                        query_used = retry_query

            reranker_used = confidence.level == "medium"
            if reranker_used:
                cross_scores: list[float] | None = None
                try:
                    cross_scores = score_documents(query, [doc for doc, _ in candidates])
                    semantic_score = max(cross_scores, default=0.0)
                    if semantic_score < config.rag_semantic_evidence_threshold:
                        confidence = replace(
                            confidence,
                            level="low",
                            semantic_score=semantic_score,
                        )
                    else:
                        confidence = replace(confidence, semantic_score=semantic_score)
                except Exception as semantic_error:
                    logger.warning(f"Cross-Encoder 证据校验失败，回退 RRF: {semantic_error}")
                docs = rerank_with_fallback(
                    query,
                    candidates,
                    k=k,
                    cross_scores=cross_scores,
                )
            elif confidence.level == "high":
                docs = self._unique_rrf_documents(candidates, k, high_confidence_only=True)
            else:
                docs = []
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
            confidence = ConfidenceResult(0.0, "low")
            return RetrievalOutcome([], confidence, query, query, 1, False, False, allowed_types)

    def _hybrid_candidates(
        self, query: str, candidate_count: int, expr: str | None
    ) -> list[tuple[Document, float]]:
        """分别执行 Dense/BM25 召回，在应用层 RRF 融合并保留两路名次。"""
        expanded_query = expand_query(query)
        dense_results = self._single_field_search(
            expanded_query,
            field="dense",
            limit=max(config.rag_dense_candidates, candidate_count),
            expr=expr,
        )
        bm25_results = self._single_field_search(
            expanded_query,
            field="sparse",
            limit=max(config.rag_sparse_candidates, candidate_count),
            expr=expr,
        )
        return self._rrf_fuse(dense_results, bm25_results, candidate_count=candidate_count)

    def _single_field_search(
        self,
        query: str,
        *,
        field: str,
        limit: int,
        expr: str | None,
    ) -> list[tuple[Document, float]]:
        """查询单个向量字段；Dense 传向量，BM25 内置函数传原始文本。"""
        if field == "dense":
            search_data = vector_embedding_service.embed_query(query)
            search_params = {"metric_type": "IP", "params": {}}
        elif field == "sparse":
            search_data = query
            search_params = {"metric_type": "BM25", "params": {}}
        else:
            raise ValueError(f"不支持的检索字段: {field}")

        raw_results = self.vector_store.client.search(
            self.collection_name,
            data=[search_data],
            anns_field=field,
            search_params=search_params,
            limit=limit,
            filter=expr,
            output_fields=["id", "content", "metadata"],
        )
        if not raw_results:
            return []

        parsed: list[tuple[Document, float]] = []
        for result in raw_results[0]:
            entity = dict(result.get("entity") or {})
            metadata = dict(entity.get("metadata") or {})
            milvus_id = entity.get("id", result.get("id"))
            if milvus_id is not None:
                metadata["_milvus_id"] = str(milvus_id)
            parsed.append(
                (
                    Document(page_content=str(entity.get("content") or ""), metadata=metadata),
                    float(result.get("distance", 0.0)),
                )
            )
        return parsed

    @staticmethod
    def _candidate_key(doc: Document) -> str:
        """标识同一个 Child，供双路去重和排名对齐。"""
        metadata = doc.metadata
        milvus_id = metadata.get("_milvus_id")
        if milvus_id:
            return f"id:{milvus_id}"
        return "|".join(
            [
                str(metadata.get("_parent_id") or metadata.get("_source") or ""),
                str(metadata.get("_child_index", "")),
                str(metadata.get("_child_content") or doc.page_content),
            ]
        )

    @classmethod
    def _rrf_fuse(
        cls,
        dense_results: list[tuple[Document, float]],
        bm25_results: list[tuple[Document, float]],
        *,
        candidate_count: int,
    ) -> list[tuple[Document, float]]:
        """按 Child ID 合并两路结果，计算 RRF，并把原始名次写入元数据。"""
        fused: dict[str, dict] = {}
        for route, results in (("dense", dense_results), ("bm25", bm25_results)):
            for rank, (doc, raw_score) in enumerate(results, 1):
                key = cls._candidate_key(doc)
                entry = fused.setdefault(
                    key,
                    {
                        "doc": doc,
                        "rrf_score": 0.0,
                        "dense_rank": None,
                        "bm25_rank": None,
                        "dense_score": None,
                        "bm25_score": None,
                    },
                )
                entry["rrf_score"] += 1.0 / (config.rag_rrf_k + rank)
                entry[f"{route}_rank"] = rank
                entry[f"{route}_score"] = float(raw_score)

        ranked = sorted(
            fused.values(),
            key=lambda item: (
                -item["rrf_score"],
                item["dense_rank"] or 10**9,
                item["bm25_rank"] or 10**9,
            ),
        )[:candidate_count]

        candidates: list[tuple[Document, float]] = []
        for item in ranked:
            doc = item["doc"]
            metadata = dict(doc.metadata)
            metadata.update(
                {
                    "_dense_rank": item["dense_rank"],
                    "_bm25_rank": item["bm25_rank"],
                    "_dense_score": item["dense_score"],
                    "_bm25_score": item["bm25_score"],
                    "_rrf_score": item["rrf_score"],
                    "_retrieval_pipeline": "dense+bm25->rrf",
                }
            )
            metadata["_dual_rank_high_confidence"] = is_dual_rank_high_confidence(
                Document(page_content=doc.page_content, metadata=metadata),
                rank_window=config.rag_confidence_rank_window,
            )
            candidates.append(
                (Document(page_content=doc.page_content, metadata=metadata), item["rrf_score"])
            )
        return candidates

    @staticmethod
    def _unique_rrf_documents(
        candidates: list[tuple[Document, float]],
        k: int,
        *,
        high_confidence_only: bool = False,
    ) -> list[Document]:
        docs: list[Document] = []
        seen: set[str] = set()
        for doc, score in candidates:
            if high_confidence_only and not is_dual_rank_high_confidence(
                doc, rank_window=config.rag_confidence_rank_window
            ):
                continue
            dedupe_key = parent_key(doc)
            if dedupe_key and dedupe_key not in seen:
                seen.add(dedupe_key)
                metadata = dict(doc.metadata)
                metadata["_initial_retrieval_score"] = float(score)
                metadata["_retrieval_pipeline"] = "dense+bm25->rrf"
                docs.append(Document(page_content=doc.page_content, metadata=metadata))
            if len(docs) == k:
                break
        return docs

    @staticmethod
    def _confidence(
        query: str,
        candidates: list[tuple[Document, float]],
        *,
        final_k: int,
    ) -> ConfidenceResult:
        del query  # 排名置信度只依赖两路结果，不再重复分析 Query 文本。
        return evaluate_rank_confidence(
            candidates,
            final_k=final_k,
            rank_window=config.rag_confidence_rank_window,
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
        metadata["_high_confidence_parent_count"] = confidence.high_confidence_parent_count
        metadata["_required_parent_count"] = confidence.required_parent_count
        metadata["_confidence_rank_window"] = confidence.rank_window
        metadata["_retrieval_attempts"] = attempts
        metadata["_query_rewritten"] = rewritten
        metadata["_reranker_used"] = reranker_used
        metadata["_evidence_sufficient"] = confidence.level != "low"
        return Document(page_content=parent_content, metadata=metadata)


# 全局单例
vector_store_manager = VectorStoreManager()
