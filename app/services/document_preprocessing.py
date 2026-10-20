"""Pure corpus preprocessing shared by production indexing and snapshots."""

import re


def preprocess_content(content: str) -> str:
    out, skip = [], False
    markers = ("模拟运维知识库内容", "用于演示", "用于验证 RAG", "用于验证向量检索",
               "用于验证 RAG 检索", "simulated_oncall_knowledge_base")
    for line in content.splitlines():
        if re.match(r"^\s*##\s*\d+\.?\s*检索与演示建议\s*$", line):
            skip = True
            continue
        if skip:
            if re.match(r"^\s*##\s+", line):
                skip = False
            else:
                continue
        if not any(marker in line for marker in markers):
            out.append(line)
    return "\n".join(out).strip()
