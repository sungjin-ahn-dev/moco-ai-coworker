"""
STT (Speech-to-Text) Provider 추상화
Web Speech API와 Deepgram을 쉽게 교체 가능하도록 설계
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any


class STTProvider(ABC):
    """STT Provider 추상 클래스"""

    @abstractmethod
    def get_provider_type(self) -> str:
        """Provider 타입 반환 (webspeech / deepgram)"""
        pass

    @abstractmethod
    def get_client_config(self) -> Optional[Dict[str, Any]]:
        """클라이언트에서 사용할 설정 반환"""
        pass


class WebSpeechProvider(STTProvider):
    """Web Speech API Provider (브라우저 내장)"""

    def get_provider_type(self) -> str:
        return "webspeech"

    def get_client_config(self) -> Optional[Dict[str, Any]]:
        """Web Speech는 클라이언트에서 모두 처리하므로 설정 불필요"""
        return {
            "type": "webspeech",
            "lang": "ko-KR",  # 한국어
            "continuous": False,  # 연속 인식 여부
            "interimResults": True  # 중간 결과 표시
        }


class DeepgramProvider(STTProvider):
    """Deepgram API Provider (향후 구현)"""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def get_provider_type(self) -> str:
        return "deepgram"

    def get_client_config(self) -> Optional[Dict[str, Any]]:
        """Deepgram 설정 반환"""
        return {
            "type": "deepgram",
            "api_key": self.api_key,
            "language": "ko",
            "model": "nova-2",  # 최신 모델
            "smart_format": True  # 문장 부호 자동 추가
        }


# 현재 사용할 Provider 선택
def get_stt_provider() -> STTProvider:
    """현재 사용할 STT Provider 반환"""
    # TODO: 환경변수나 설정으로 변경 가능하도록
    # 현재는 Web Speech API 사용
    return WebSpeechProvider()

    # Deepgram으로 변경하려면:
    # from app.config.settings import get_settings
    # settings = get_settings()
    # return DeepgramProvider(api_key=settings.DEEPGRAM_API_KEY)
