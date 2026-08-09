"""文档分割服务：按 Markdown 章节保留 parent，再按 BGE token 切 child。"""

from pathlib import Path
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from loguru import logger
from transformers import AutoTokenizer

from app.config import config


class DocumentSplitterService:
    """文档分割服务 - 使用 LangChain 的分割器"""

    def __init__(self):
        """初始化文档分割服务"""
        self.chunk_size = config.chunk_max_tokens
        self.chunk_overlap = config.chunk_overlap_tokens
        self.tokenizer = AutoTokenizer.from_pretrained(
            config.dashscope_embedding_model,
            cache_dir=".hf-cache",
        )

        # H1/H2 是 parent 边界；child 不跨 parent，避免把不同故障章节拼在一起。
        self.markdown_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "h1"),
                ("##", "h2"),
                # 不再按三级标题分割，避免过度碎片化
            ],
            strip_headers=False,  # 保留标题在内容中
        )

        self.text_splitter = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
            self.tokenizer,
            separators=["\n\n", "\n", "。", "；", ". ", " ", ""],
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            is_separator_regex=False,
        )

        logger.info(
            f"文档分割服务初始化完成, child_tokens={self.chunk_size}, "
            f"overlap_tokens={self.chunk_overlap}"
        )

    def split_markdown(self, content: str, file_path: str = "") -> List[Document]:
        """
        分割 Markdown 文档 (两阶段分割 + 合并小片段)

        Args:
            content: Markdown 内容
            file_path: 文件路径 (用于元数据)

        Returns:
            List[Document]: 文档分片列表
        """
        if not content or not content.strip():
            logger.warning(f"Markdown 文档内容为空: {file_path}")
            return []

        try:
            parent_docs = self.markdown_splitter.split_text(content)
            final_docs: list[Document] = []
            document_title = self._document_title(content, Path(file_path).stem)
            for parent_index, parent in enumerate(parent_docs):
                parent_content = parent.page_content.strip()
                header_path = " > ".join(
                    str(parent.metadata[key])
                    for key in ("h1", "h2")
                    if parent.metadata.get(key)
                )
                context_prefix = "\n".join(
                    part for part in (
                        f"文档：{document_title}" if document_title else "",
                        f"章节：{header_path}" if header_path else "",
                    ) if part
                )
                prefix_tokens = len(self.tokenizer.encode(context_prefix, add_special_tokens=False))
                child_splitter = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
                    self.tokenizer,
                    separators=["\n\n", "\n", "。", "；", ". ", " ", ""],
                    chunk_size=max(64, self.chunk_size - prefix_tokens - 2),
                    chunk_overlap=self.chunk_overlap,
                    is_separator_regex=False,
                )
                child_docs = child_splitter.create_documents(
                    [parent_content], [dict(parent.metadata)]
                )
                for child_index, doc in enumerate(child_docs):
                    child_content = doc.page_content.strip()
                    doc.page_content = f"{context_prefix}\n\n{child_content}" if context_prefix else child_content
                    doc.metadata.update(
                        {
                            "_parent_id": f"{Path(file_path).name}:{parent_index}",
                            "_parent_content": parent_content,
                            "_child_content": child_content,
                            "_child_index": child_index,
                            "_token_count": len(self.tokenizer.encode(doc.page_content, add_special_tokens=False)),
                            "_document_title": document_title,
                            "_header_path": header_path,
                        }
                    )
                    final_docs.append(doc)

            for doc in final_docs:
                doc.metadata["_source"] = file_path
                doc.metadata["_extension"] = ".md"
                doc.metadata["_file_name"] = Path(file_path).name
                doc.metadata.update(self._source_metadata(content, file_path))

            logger.info(f"Markdown 分割完成: {file_path} -> {len(final_docs)} 个分片")
            return final_docs

        except Exception as e:
            logger.error(f"Markdown 分割失败: {file_path}, 错误: {e}")
            raise

    def split_text(self, content: str, file_path: str = "") -> List[Document]:
        """
        分割普通文本文档

        Args:
            content: 文本内容
            file_path: 文件路径 (用于元数据)

        Returns:
            List[Document]: 文档分片列表
        """
        if not content or not content.strip():
            logger.warning(f"文本文档内容为空: {file_path}")
            return []

        try:
            # 普通文本也使用同一 tokenizer，parent 为整份文件。
            docs = self.text_splitter.create_documents(
                texts=[content],
                metadatas=[
                    {
                        "_source": file_path,
                        "_extension": Path(file_path).suffix,
                        "_file_name": Path(file_path).name,
                    }
                ],
            )
            for index, doc in enumerate(docs):
                child_content = doc.page_content
                doc.metadata.update(
                    {
                        "_parent_id": f"{Path(file_path).name}:0",
                        "_parent_content": content,
                        "_child_content": child_content,
                        "_child_index": index,
                        "_token_count": len(self.tokenizer.encode(child_content, add_special_tokens=False)),
                        "_document_title": Path(file_path).stem,
                        "_header_path": "",
                        "_source_type": "incident" if "incident" in file_path.lower() else "knowledge",
                        "_trust_level": "internal",
                    }
                )

            logger.info(f"文本分割完成: {file_path} -> {len(docs)} 个分片")
            return docs

        except Exception as e:
            logger.error(f"文本分割失败: {file_path}, 错误: {e}")
            raise

    def split_document(self, content: str, file_path: str = "") -> List[Document]:
        """
        智能分割文档 (根据文件类型选择分割器)

        Args:
            content: 文档内容
            file_path: 文件路径

        Returns:
            List[Document]: 文档分片列表
        """
        if file_path.endswith(".md"):
            return self.split_markdown(content, file_path)
        else:
            return self.split_text(content, file_path)

    @staticmethod
    def _document_title(content: str, fallback: str) -> str:
        for line in content.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return fallback

    @staticmethod
    def _source_metadata(content: str, file_path: str) -> dict[str, str]:
        path_lower = file_path.lower()
        if "incident" in path_lower or "历史事故" in content:
            return {"_source_type": "incident", "_trust_level": "internal"}
        if "case_" in Path(file_path).name.lower():
            return {"_source_type": "case", "_trust_level": "medium"}
        if "matlab_official" in Path(file_path).name.lower():
            return {"_source_type": "official", "_trust_level": "high"}
        return {"_source_type": "knowledge", "_trust_level": "medium"}


# 全局单例
document_splitter_service = DocumentSplitterService()
