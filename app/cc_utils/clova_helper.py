"""
Clova Speech Recognition 클라이언트
웹 인터페이스에서 회의 음성을 텍스트로 변환하는 용도
"""

import httpx
import json
from pathlib import Path
from typing import Optional, Dict, Any
import logging

from app.config.settings import get_settings

settings = get_settings()

class ClovaSpeechClient:
    """Clova Speech Recognition API 클라이언트"""

    def __init__(self):
        self.invoke_url = settings.CLOVA_INVOKE_URL
        self.secret_key = settings.CLOVA_SECRET_KEY

    async def recognize_file(
        self,
        file_path: str,
        completion: str = "sync",
        diarization: Optional[Dict[str, Any]] = None,
        word_alignment: bool = True,
        full_text: bool = True
    ) -> Dict[str, Any]:
        """
        음성 파일을 텍스트로 변환

        Args:
            file_path: 음성 파일 경로
            completion: 'sync' (동기) 또는 'async' (비동기)
            diarization: 화자 구분 설정 {"enable": True, "speakerCountMin": 2, "speakerCountMax": 5}
            word_alignment: 단어별 타임스탬프 포함 여부
            full_text: 전체 텍스트 반환 여부

        Returns:
            Clova API 응답 (JSON)
        """
        # 요청 파라미터
        request_body = {
            'language': 'ko-KR',
            'completion': completion,
            'wordAlignment': word_alignment,
            'fullText': full_text,
        }

        # 화자 구분 옵션
        if diarization:
            request_body['diarization'] = diarization

        # 헤더
        headers = {
            'Accept': 'application/json;UTF-8',
            'X-CLOVASPEECH-API-KEY': self.secret_key
        }

        # 파일 읽기
        file = Path(file_path)
        if not file.exists():
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        logging.info(f"[CLOVA] Uploading file: {file.name} ({file.stat().st_size} bytes)")

        # Multipart form-data 준비
        with open(file_path, 'rb') as f:
            files = {
                'media': (file.name, f, 'audio/mpeg'),
                'params': (None, json.dumps(request_body, ensure_ascii=False).encode('UTF-8'), 'application/json')
            }

            # API 호출
            async with httpx.AsyncClient(timeout=180.0) as client:  # 3분 타임아웃
                response = await client.post(
                    url=f"{self.invoke_url}/recognizer/upload",
                    headers=headers,
                    files=files
                )
                response.raise_for_status()

                result = response.json()
                logging.info(f"[CLOVA] Recognition completed")

                return result


async def convert_speech_to_text(
    audio_file: str,
    enable_diarization: bool = False
) -> Dict[str, Any]:
    """
    음성 파일을 텍스트로 변환 (간편 함수)

    Args:
        audio_file: 음성 파일 경로
        enable_diarization: 화자 구분 활성화

    Returns:
        {
            'text': '전체 텍스트',
            'segments': [...],  # diarization 활성화 시
        }
    """
    client = ClovaSpeechClient()

    # 화자 구분 설정
    diarization = None
    if enable_diarization:
        diarization = {
            "enable": True,
            "speakerCountMin": 1,
            "speakerCountMax": 10
        }

    result = await client.recognize_file(
        file_path=audio_file,
        completion='sync',
        diarization=diarization,
        word_alignment=True,
        full_text=True
    )

    # 결과 파싱
    text = result.get('text', '')
    segments = result.get('segments', [])

    return {
        'text': text,
        'segments': segments
    }


async def convert_speech_to_text_with_speakers(audio_file: str) -> str:
    """
    화자 구분이 포함된 회의록 텍스트 생성

    Args:
        audio_file: 음성 파일 경로

    Returns:
        "[00:00] [화자1]: 안녕하세요\n[00:05] [화자2]: 반갑습니다\n..."
    """
    result = await convert_speech_to_text(audio_file, enable_diarization=True)

    if not result.get('segments'):
        # 화자 구분 실패 시 전체 텍스트만 반환
        return result.get('text', '')

    # 화자별로 정리
    transcript = []
    for segment in result['segments']:
        speaker = segment.get('speaker', {}).get('label', 'Unknown')
        text = segment.get('text', '')
        start_time = segment.get('start', 0) // 1000  # ms → s

        # 타임스탬프 포맷 (00:00)
        minutes = start_time // 60
        seconds = start_time % 60
        timestamp = f"[{minutes:02d}:{seconds:02d}]"

        transcript.append(f"{timestamp} [화자{speaker}]: {text}")

    return "\n".join(transcript)
