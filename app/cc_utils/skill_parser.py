import yaml
import re
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class Skill:
    name: str
    version: str
    description: str
    system_prompt: str
    required_mcps: List[str] = field(default_factory=list)
    optional_mcps: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    trigger_keywords: List[str] = field(default_factory=list)
    model: str = "claude-haiku-4-5-20251001"
    author: str = ""

def parse_skill_md(content: str) -> Skill:
    """
    skill.md 파일을 파싱해 Skill 객체 반환
    YAML frontmatter(--- 사이) + Markdown body 파싱
    """
    # --- 구분자로 분리
    parts = content.split("---", 2)
    # parts[0]: 빈 문자열, parts[1]: YAML, parts[2]: Markdown body
    metadata = yaml.safe_load(parts[1])
    body = parts[2] if len(parts) > 2 else ""

    # System Prompt 섹션 추출
    system_prompt = _extract_section(body, "## System Prompt")

    # 모델명 정규화 (단순 이름 → 전체 ID)
    model = metadata.get("model", "claude-haiku-4-5")
    model = _normalize_model(model)

    return Skill(
        name=metadata["name"],
        version=str(metadata.get("version", "1.0.0")),
        description=metadata.get("description", ""),
        author=metadata.get("author", ""),
        required_mcps=metadata.get("required_mcps", []),
        optional_mcps=metadata.get("optional_mcps", []),
        tags=metadata.get("tags", []),
        trigger_keywords=metadata.get("trigger_keywords", []),
        model=model,
        system_prompt=system_prompt.strip(),
    )

def _extract_section(body: str, header: str) -> str:
    """Markdown에서 특정 헤더 섹션 추출"""
    lines = body.splitlines()
    capturing = False
    result = []
    for line in lines:
        if line.strip() == header:
            capturing = True
            continue
        if capturing and line.startswith("## "):  # 다음 섹션 시작
            break
        if capturing:
            result.append(line)
    return "\n".join(result)

def _normalize_model(model: str) -> str:
    """단축 모델명을 전체 ID로 변환. Opus는 Sonnet으로 강등 (보안)"""
    mapping = {
        "haiku": "claude-haiku-4-5-20251001",
        "sonnet": "claude-sonnet-4-6",
        "opus": "claude-sonnet-4-6",  # 보안상 Opus 허용 안 함
    }
    return mapping.get(model.lower(), model) if model.lower() in mapping else model
