"""
자동 생성된 에이전트 디렉토리.

이 패키지의 모든 서브모듈은 agent_factory 가 생성·관리합니다.
수동 편집 가능하지만, registry.json 의 version 도 함께 갱신하세요.

깨진 에이전트가 있어도 다른 에이전트는 살아남도록 격리됩니다.
loader.load_all_generated_streamers() 가 try/except 로 import.
"""

from app.cc_agents.generated.loader import load_all_generated_streamers

__all__ = ["load_all_generated_streamers"]
