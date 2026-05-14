"""从项目根目录 .claude/skills/*.md 加载 Skill（Claude Code 风格 Markdown）"""

from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Any

from loguru import logger

# 项目根目录：app/claude_skills/reader.py -> parent.parent
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SKILLS_DIR = _PROJECT_ROOT / ".claude" / "skills"

# 注入 Planner 时每个 Skill 正文的最大字符数（避免撑爆上下文）
_MAX_BODY_CHARS_PER_SKILL = 3500


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """解析简单 YAML frontmatter（key: value 行），不含嵌套。"""
    text = content.strip()
    if not text.startswith("---"):
        return {}, text
    rest = text[3:].lstrip("\n")
    end_idx = rest.find("\n---")
    if end_idx == -1:
        return {}, text
    fm_block = rest[:end_idx].strip()
    body = rest[end_idx + 4 :].lstrip("\n")
    meta: dict[str, str] = {}
    for line in fm_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta, body


def _load_one_skill(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        logger.warning(f"[SkillReader] 无法读取 {path}: {e}")
        return None
    meta, body = _parse_frontmatter(raw)
    stem = Path(path).stem
    skill_id = meta.get("id") or stem
    name = meta.get("name") or stem
    description = meta.get("description", "")
    if len(body) > _MAX_BODY_CHARS_PER_SKILL:
        body = body[: _MAX_BODY_CHARS_PER_SKILL] + "\n\n...(Skill 正文已截断)"
    return {
        "id": skill_id,
        "name": name,
        "description": description,
        "body": body,
        "path": path,
    }


def load_skills_description() -> str:
    """扫描 .claude/skills/*.md，生成供 Planner 注入的纯文本描述。"""
    d = str(_SKILLS_DIR)
    if not os.path.isdir(d):
        logger.info(f"[SkillReader] 目录不存在，跳过: {d}")
        return "（暂无可用 Skill：未找到 .claude/skills/ 目录）"

    paths = sorted(glob.glob(os.path.join(d, "*.md")))
    if not paths:
        return "（.claude/skills/ 下暂无 *.md Skill 文件）"

    parts: list[str] = []
    for p in paths:
        info = _load_one_skill(p)
        if not info:
            continue
        block = (
            f"### Skill: `{info['id']}`\n"
            f"**名称**: {info['name']}\n"
            f"**说明**: {info['description']}\n\n"
            f"{info['body']}"
        )
        parts.append(block)

    if not parts:
        return "（未能解析任何 Skill 文件）"

    out = "\n\n---\n\n".join(parts)
    logger.info(f"[SkillReader] 已加载 {len(parts)} 个 Skill，总长度 {len(out)}")
    return out


def list_skill_ids() -> list[str]:
    """返回所有 Skill 的 id（用于 Executor 日志等）。"""
    d = str(_SKILLS_DIR)
    if not os.path.isdir(d):
        return []
    ids: list[str] = []
    for p in sorted(glob.glob(os.path.join(d, "*.md"))):
        info = _load_one_skill(p)
        if info:
            ids.append(str(info["id"]))
    return ids
