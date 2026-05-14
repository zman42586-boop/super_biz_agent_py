"""长期记忆模块（Hot + Cold）

  memory/
  ├── MEMORY.md   — Hot：诊断索引（摘要 + 报告链接），供 load_memory_context 加载
  ├── incidents/  — Cold：每次 AIOps 完整诊断报告
  └── artifacts/  — Microcompact 工具原始结果（归档）
"""

from .memory_writer import MemoryWriter, memory_writer
from .memory_reader import load_memory_context

__all__ = ["MemoryWriter", "memory_writer", "load_memory_context"]
