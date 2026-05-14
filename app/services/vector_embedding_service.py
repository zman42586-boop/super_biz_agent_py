"""向量嵌入服务模块 - 使用本地 HuggingFace 模型（无需 API Key）"""

from langchain_huggingface import HuggingFaceEmbeddings
from loguru import logger

from app.config import config

logger.info(f"初始化本地 Embedding 模型: {config.dashscope_embedding_model}")

vector_embedding_service = HuggingFaceEmbeddings(
    model_name=config.dashscope_embedding_model,
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)
