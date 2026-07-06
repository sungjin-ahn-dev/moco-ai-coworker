"""
JSON Memory Index — LLM 없이 토큰 기반 메모리 검색
기존 index.md + frontmatter 기반 .md 파일을 JSON 인덱스로 변환하여 빠르게 검색.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MEMORIES_DIR = Path("/home/user/MOCO_DATA/memories")


class MemoryIndex:
    """JSON 기반 메모리 인덱스 — 토큰 매칭으로 빠른 검색"""

    def __init__(self, memories_dir: Path = MEMORIES_DIR):
        self._memories_dir = memories_dir
        self._indices: dict[str, list] = {}  # user_id → entries
        self._last_built: dict[str, float] = {}  # user_id → timestamp
        self._cache_ttl = 300  # 5분 캐시

    def _get_index_path(self, user_id: str) -> Path:
        return self._memories_dir / user_id / "index.json"

    def build_index(self, user_id: str) -> list:
        """사용자의 메모리 파일들을 스캔하여 JSON 인덱스 생성"""
        user_dir = self._memories_dir / user_id
        if not user_dir.exists():
            return []

        entries = []
        for md_file in user_dir.rglob("*.md"):
            if md_file.name == "index.md":
                continue

            try:
                content = md_file.read_text(encoding="utf-8", errors="ignore")
                entry = self._parse_md_file(md_file, content, user_id)
                if entry:
                    entries.append(entry)
            except Exception as e:
                logger.warning(f"[MEMORY_INDEX] Parse error {md_file}: {e}")

        # JSON 인덱스 저장
        index_path = self._get_index_path(user_id)
        try:
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False, indent=1)
            logger.info(f"[MEMORY_INDEX] Built index for {user_id}: {len(entries)} entries")
        except Exception as e:
            logger.error(f"[MEMORY_INDEX] Save error: {e}")

        self._indices[user_id] = entries
        self._last_built[user_id] = time.time()
        return entries

    def _parse_md_file(self, file_path: Path, content: str, user_id: str) -> Optional[dict]:
        """마크다운 파일에서 frontmatter + 제목 + 본문 추출"""
        # Frontmatter 파싱
        fm = {}
        body = content
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if fm_match:
            fm_text = fm_match.group(1)
            body = content[fm_match.end():]
            for line in fm_text.split("\n"):
                line = line.strip()
                if ":" in line and not line.startswith("-"):
                    key, _, val = line.partition(":")
                    val = val.strip().strip('"').strip("'")
                    if val:
                        fm[key.strip()] = val

            # tags 파싱 (리스트)
            tags = []
            in_tags = False
            for line in fm_text.split("\n"):
                if line.strip().startswith("tags:"):
                    in_tags = True
                    continue
                if in_tags:
                    if line.strip().startswith("- "):
                        tags.append(line.strip()[2:].strip())
                    else:
                        in_tags = False
            if tags:
                fm["tags"] = tags

        # 제목 추출
        title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else file_path.stem

        # 요약 (첫 200자)
        summary_lines = [l.strip() for l in body.split("\n") if l.strip() and not l.startswith("#")]
        summary = " ".join(summary_lines)[:200]

        # 상대 경로
        rel_path = str(file_path.relative_to(self._memories_dir / user_id))

        return {
            "path": rel_path,
            "title": title,
            "summary": summary,
            "type": fm.get("type", "misc"),
            "tags": fm.get("tags", []) if isinstance(fm.get("tags"), list) else [],
            "user_name": fm.get("user_name", ""),
            "channel_id": fm.get("channel_id", ""),
            "date": fm.get("date", fm.get("created", "")),
            "result": fm.get("result", ""),
            "category": fm.get("category", ""),
            "stakeholders": fm.get("stakeholders", ""),
        }

    def get_index(self, user_id: str) -> list:
        """인덱스 가져오기 (캐시 또는 빌드)"""
        now = time.time()
        if user_id in self._indices and (now - self._last_built.get(user_id, 0)) < self._cache_ttl:
            return self._indices[user_id]

        # JSON 파일에서 로드 시도
        index_path = self._get_index_path(user_id)
        if index_path.exists():
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    entries = json.load(f)
                self._indices[user_id] = entries
                self._last_built[user_id] = now
                return entries
            except Exception:
                pass

        # 빌드
        return self.build_index(user_id)

    def search(self, query: str, user_id: str = None,
               channel_id: str = None, limit: int = 8) -> list:
        """토큰 기반 메모리 검색 — LLM 호출 없음"""
        t_start = time.time()

        # 검색 대상 사용자 결정
        if user_id:
            user_ids = [user_id]
        else:
            user_ids = [d.name for d in self._memories_dir.iterdir()
                        if d.is_dir() and not d.name.startswith(".")]

        # 쿼리 토큰화
        tokens = self._tokenize(query)
        if not tokens:
            return []

        scored_results = []

        for uid in user_ids:
            entries = self.get_index(uid)
            for entry in entries:
                score = self._score_entry(entry, tokens, uid, user_id, channel_id)
                if score > 0:
                    scored_results.append((score, uid, entry))

        # 점수순 정렬
        scored_results.sort(key=lambda x: x[0], reverse=True)
        top = scored_results[:limit]

        elapsed = time.time() - t_start
        logger.info(f"[MEMORY_INDEX] Search '{query[:30]}' → {len(top)} results in {elapsed:.3f}s (scanned {sum(len(self.get_index(uid)) for uid in user_ids)} entries)")

        return [{"user_id": uid, "score": score, **entry} for score, uid, entry in top]

    def search_and_load(self, query: str, user_id: str = None,
                        channel_id: str = None, limit: int = 6) -> str:
        """검색 + 파일 내용 로드 → 문자열 반환 (Memory Retriever 대체)"""
        results = self.search(query, user_id, channel_id, limit)
        if not results:
            return "관련된 메모리가 없습니다."

        parts = [f"## 메모리 검색 결과 ({len(results)}개 파일)\n"]
        for r in results:
            file_path = self._memories_dir / r["user_id"] / r["path"]
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                # 너무 길면 자르기
                if len(content) > 1500:
                    content = content[:1500] + "\n... (truncated)"
                parts.append(f"### {r['title']}\n{content}\n")
            except Exception:
                parts.append(f"### {r['title']}\n(파일 읽기 실패: {r['path']})\n")

        return "\n".join(parts)

    def _tokenize(self, text: str) -> list:
        """텍스트를 검색 토큰으로 분리"""
        text = text.lower()
        # 한국어 + 영어 단어 분리
        words = re.findall(r"[가-힣]+|[a-z0-9]+", text)
        # 1글자 제거, 불용어 제거
        stopwords = {"의", "에", "를", "을", "이", "가", "은", "는", "한", "로", "도",
                     "and", "the", "is", "in", "for", "to", "a", "of", "it"}
        return [w for w in words if len(w) > 1 and w not in stopwords]

    def _score_entry(self, entry: dict, tokens: list,
                     entry_user_id: str, query_user_id: str = None,
                     query_channel_id: str = None) -> float:
        """엔트리의 관련도 점수 계산"""
        score = 0.0

        # 검색 대상 텍스트
        title = (entry.get("title", "") or "").lower()
        summary = (entry.get("summary", "") or "").lower()
        tags = " ".join(entry.get("tags", [])).lower()
        combined = f"{title} {summary} {tags}"

        # 토큰 매칭
        for token in tokens:
            if token in title:
                score += 3.0  # 제목 매칭 = 높은 점수
            if token in tags:
                score += 2.5  # 태그 매칭
            if token in summary:
                score += 1.0  # 요약 매칭

        if score == 0:
            return 0

        # 바이어스: 같은 사용자 +2
        if query_user_id and entry_user_id == query_user_id:
            score += 2.0

        # 바이어스: 같은 채널 +1.5
        if query_channel_id and entry.get("channel_id") == query_channel_id:
            score += 1.5

        # 바이어스: 최근 날짜 +1
        date_str = entry.get("date", "")
        if date_str and "2026-04" in date_str:
            score += 1.0
        elif date_str and "2026-03" in date_str:
            score += 0.5

        # 바이어스: 성공 결과 +0.5
        if entry.get("result") == "success":
            score += 0.5

        return score


# 싱글톤
memory_index = MemoryIndex()
