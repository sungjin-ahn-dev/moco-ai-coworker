"""
Sub-agent 공통 기반 모듈 (Sub-agent Base)

Result Schema, 헬퍼 함수, Sub-agent 레지스트리를 정의합니다.
"""

import json
import logging
import re

# Sub-agent 결과 스키마 정의
RESULT_SCHEMA = {
    "status": "success | partial | failed",
    "summary": "한 줄 요약",
    "data": {},
    "artifacts": [],
    "next_suggestions": [],
    "error": None,
}

# Sub-agent 레지스트리
SUB_AGENT_REGISTRY = {
    "research": {
        "description": "웹 검색, arXiv 논문, 정보 수집 및 분석",
        "keywords": ["검색", "찾아", "조사", "리서치", "논문"],
    },
    "communication": {
        "description": "Slack 메시지, 이메일, 메시지 전달",
        "keywords": ["보내", "전달", "메일", "알려줘", "공지"],
    },
    "code": {
        "description": "코드 리뷰, PR, GitLab/GitHub 작업",
        "keywords": ["코드", "PR", "GitLab", "GitHub", "리뷰"],
    },
    "pm": {
        "description": "Jira, ClickUp 이슈 관리, 스프린트, 리포트",
        "keywords": ["Jira", "ClickUp", "이슈", "스프린트", "태스크"],
    },
    "document": {
        "description": "문서 작성/편집, Google Drive, 번역",
        "keywords": ["문서", "작성", "드라이브", "번역", "요약"],
    },
    "data": {
        "description": "데이터 분석, Tableau, 시각화",
        "keywords": ["데이터", "분석", "Tableau", "차트", "통계"],
    },
    "web": {
        "description": "웹 브라우저 자동화, 사이트 탐색, 스크래핑",
        "keywords": ["사이트", "웹페이지", "크롤링", "브라우저"],
    },
}


def make_result(
    status: str,
    summary: str,
    data: dict = None,
    artifacts: list = None,
    next_suggestions: list = None,
    error: str = None,
) -> dict:
    """RESULT_SCHEMA 형태의 결과 딕셔너리를 만든다. None 인자는 빈 컨테이너로 채운다."""
    return {
        "status": status,
        "summary": summary,
        "data": data if data is not None else {},
        "artifacts": artifacts if artifacts is not None else [],
        "next_suggestions": next_suggestions if next_suggestions is not None else [],
        "error": error,
    }


def parse_result(text: str) -> dict:
    """LLM 응답 텍스트에서 JSON을 추출하여 RESULT_SCHEMA 형태로 파싱합니다.

    마크다운 코드 블록, 중괄호 기반 JSON 등 다양한 형식을 지원합니다.
    파싱에 실패하면 failed 상태의 기본 결과를 반환합니다.

    Args:
        text: LLM 응답 텍스트

    Returns:
        dict: RESULT_SCHEMA 형태의 딕셔너리
    """
    if not text:
        return make_result(
            status="failed",
            summary="응답 없음",
            error="empty_response",
        )

    # 1. 마크다운 코드 블록에서 JSON 추출 시도 (```json ... ```)
    code_block_pattern = re.compile(
        r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE
    )
    match = code_block_pattern.search(text)
    if match:
        json_str = match.group(1)
        try:
            result = json.loads(json_str)
            return _normalize_result(result, text)
        except json.JSONDecodeError:
            pass

    # 2. 텍스트에서 JSON 객체 직접 추출 시도 ({...})
    brace_pattern = re.compile(r"\{.*\}", re.DOTALL)
    match = brace_pattern.search(text)
    if match:
        json_str = match.group(0)
        try:
            result = json.loads(json_str)
            return _normalize_result(result, text)
        except json.JSONDecodeError:
            pass

    # 3. 텍스트 전체를 JSON으로 파싱 시도
    try:
        result = json.loads(text.strip())
        return _normalize_result(result, text)
    except json.JSONDecodeError:
        pass

    # 파싱 실패 시 기본 실패 결과 반환
    logging.warning(f"[SUB_AGENT_BASE] parse_result failed, raw text: {text[:200]}")
    return {
        "status": "failed",
        "summary": text,
        "data": {},
        "artifacts": [],
        "next_suggestions": [],
        "error": "parse_error",
    }


def _normalize_result(result: dict, raw_text: str) -> dict:
    """파싱된 dict를 RESULT_SCHEMA 형태로 채워 정규화. 누락 필드는 기본값."""
    return {
        "status": result.get("status", "failed"),
        "summary": result.get("summary", raw_text[:200]),
        "data": result.get("data", {}),
        "artifacts": result.get("artifacts", []),
        "next_suggestions": result.get("next_suggestions", []),
        "error": result.get("error", None),
    }
