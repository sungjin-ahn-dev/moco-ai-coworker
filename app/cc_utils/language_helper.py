"""
언어 감지 유틸리티

텍스트의 언어를 감지하는 헬퍼 함수들
"""

import re


def detect_language(text: str) -> str:
    """한글 포함 여부로 언어 감지 ("Korean" 또는 "English")."""
    if re.search(r'[가-힣]', text):
        return "Korean"
    return "English"
