"""
메모리 검색 에이전트 (Memory Retriever Agent)

Python 직접 파일 검색 방식으로 메모리를 빠르게 취합합니다.
(기존 ClaudeSDKClient 방식 대비 5~15초 절감)
"""

import logging
import os
import re
from typing import Optional


from app.config.settings import get_settings


def _resolve_memories_path(message_data: Optional[dict]) -> Optional[str]:
    """유저별 memories 경로를 결정합니다."""
    settings = get_settings()
    base_dir = settings.FILESYSTEM_BASE_DIR or os.getcwd()

    user_id = message_data.get("user_id") if message_data else None
    user_subdir = user_id if user_id else "shared"
    memories_path = os.path.join(base_dir, "memories", user_subdir)

    if not os.path.exists(memories_path):
        shared_path = os.path.join(base_dir, "memories", "shared")
        if os.path.exists(shared_path):
            memories_path = shared_path
        else:
            # user_subdir 없이 base memories 폴더 직접 확인 (flat 구조 호환)
            flat_path = os.path.join(base_dir, "memories")
            if os.path.exists(flat_path):
                memories_path = flat_path
            else:
                return None

    return memories_path


def _read_file_safe(path: str, max_chars: int = 8000) -> str:
    """파일을 안전하게 읽습니다. 너무 긴 파일은 잘라냅니다."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read(max_chars)
        if len(content) == max_chars:
            content += "\n... (truncated)"
        return content
    except Exception:
        return ""


def _extract_file_paths_from_index(index_content: str, memories_path: str) -> list[str]:
    """index.md에서 참조된 파일 경로들을 추출합니다."""
    paths = []
    # 마크다운 링크 패턴: [text](relative/path.md)
    for match in re.finditer(r'\[.*?\]\(([^)]+\.md)\)', index_content):
        rel_path = match.group(1).replace('/', os.sep)
        abs_path = os.path.join(memories_path, rel_path)
        if os.path.exists(abs_path):
            paths.append(abs_path)
    return paths


def _score_file_relevance(file_path: str, file_content: str, keywords: list[str]) -> int:
    """파일의 관련성 점수를 계산합니다."""
    score = 0
    text = (file_path + " " + file_content).lower()
    for kw in keywords:
        if kw.lower() in text:
            score += text.count(kw.lower())
    return score


def _extract_keywords(search_query: str, slack_data: Optional[dict], message_data: Optional[dict]) -> list[str]:
    """검색 쿼리와 컨텍스트에서 키워드를 추출합니다."""
    keywords = []

    # 쿼리에서 키워드 추출 (불용어 제외)
    stopwords = {
        "의", "를", "을", "에", "에서", "한", "하는", "위한", "메모리를", "취합해",
        "알려주세요", "반드시", "해당", "포함하세요", "채널", "사용자", "요청",
        "수행하기", "정보", "지침", "channel_id", "user_id", "user_name",
    }
    for word in re.split(r'[\s,.\'"(){}]+', search_query):
        word = word.strip()
        if len(word) >= 2 and word not in stopwords:
            keywords.append(word)

    # 컨텍스트에서 추가 키워드
    if message_data:
        channel_id = message_data.get("channel_id", "")
        user_id = message_data.get("user_id", "")
        user_name = message_data.get("user_name", "")
        if channel_id:
            keywords.append(channel_id)
        if user_id:
            keywords.append(user_id)
        if user_name:
            keywords.append(user_name)

    return keywords


async def call_memory_retriever(
    search_query: str,
    slack_data: Optional[dict] = None,
    message_data: Optional[dict] = None
) -> str:
    """
    메모리 검색 — JSON 인덱스 우선, fallback으로 파일 직접 검색.

    1단계: JSON 인덱스에서 토큰 매칭 (빠름, 파일 안 읽음)
    2단계: 관련 파일만 로드하여 내용 취합
    Fallback: JSON 인덱스 없으면 기존 방식 (전체 파일 스캔)

    Returns:
        str: 취합된 메모리 내용 (Operator에게 전달)
    """
    import time as _time
    _t0 = _time.time()

    user_id = message_data.get("user_id") if message_data else None
    channel_id = message_data.get("channel_id") if message_data else None

    # JSON 인덱스 우선 시도
    try:
        from app.cc_utils.memory_index import memory_index
        result = memory_index.search_and_load(
            query=search_query,
            user_id=user_id,
            channel_id=channel_id,
            limit=6,
        )
        elapsed = _time.time() - _t0
        logging.info(f"[MEMORY_RETRIEVER] JSON index search completed in {elapsed:.3f}s")
        if result and result != "관련된 메모리가 없습니다.":
            return result
        # 결과 없으면 fallback
        logging.info(f"[MEMORY_RETRIEVER] JSON index returned no results, falling back to file scan")
    except Exception as e:
        logging.warning(f"[MEMORY_RETRIEVER] JSON index failed, falling back: {e}")

    # Fallback: 기존 파일 직접 검색
    memories_path = _resolve_memories_path(message_data)
    if not memories_path:
        logging.info(f"[MEMORY_RETRIEVER] No memories folder found for user_id={user_id}")
        return "관련된 메모리가 없습니다."

    # 1. index.md 읽기
    index_path = os.path.join(memories_path, "index.md")
    index_content = _read_file_safe(index_path) if os.path.exists(index_path) else ""

    # 2. 파일 경로 수집 (index에서 추출 + 디렉토리 스캔 fallback)
    file_paths = _extract_file_paths_from_index(index_content, memories_path)

    if not file_paths:
        for root, _dirs, files in os.walk(memories_path):
            for fname in files:
                if fname.endswith(".md") and fname != "index.md":
                    file_paths.append(os.path.join(root, fname))

    if not file_paths:
        logging.info(f"[MEMORY_RETRIEVER] No memory files found in {memories_path}")
        return "관련된 메모리가 없습니다."

    # 3. 키워드 추출
    keywords = _extract_keywords(search_query, slack_data, message_data)

    # 4. 파일별 관련성 점수 계산
    scored_files = []
    for fpath in file_paths:
        content = _read_file_safe(fpath)
        if not content:
            continue
        score = _score_file_relevance(fpath, content, keywords)
        scored_files.append((fpath, content, score))

    # 5. 점수순 정렬, 상위 파일 취합 (최대 총 30000자)
    scored_files.sort(key=lambda x: x[2], reverse=True)

    result_parts = []
    total_chars = 0
    max_total_chars = 30000
    files_included = 0

    for fpath, content, score in scored_files:
        if score == 0 and len(scored_files) > 5:
            continue

        rel_path = os.path.relpath(fpath, memories_path)
        section = f"### {rel_path}\n{content}"

        if total_chars + len(section) > max_total_chars:
            break

        result_parts.append(section)
        total_chars += len(section)
        files_included += 1

    if not result_parts:
        for fpath, content, score in scored_files[:3]:
            rel_path = os.path.relpath(fpath, memories_path)
            result_parts.append(f"### {rel_path}\n{content}")

    if not result_parts:
        logging.info(f"[MEMORY_RETRIEVER] No relevant memories found")
        return "관련된 메모리가 없습니다."

    elapsed = _time.time() - _t0
    result = f"## 메모리 취합 결과 ({files_included}개 파일)\n\n" + "\n\n---\n\n".join(result_parts)
    logging.info(f"[MEMORY_RETRIEVER] File scan completed in {elapsed:.3f}s: {result[:100]}...")
    return result
