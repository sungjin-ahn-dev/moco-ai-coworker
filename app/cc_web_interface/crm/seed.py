# -*- coding: utf-8 -*-
"""데모 시드 데이터는 이 공개 저장소에 포함하지 않는다.

원본에는 실제 병원·거래처·처방 데이터를 담은 seed_demo_data()가 있었으나,
데이터 거버넌스 상 제거했다. CRM 스키마·라우트·서비스 로직은 그대로 두고,
시드는 no-op 으로 대체해 초기화가 데이터 없이도 정상 동작하게 한다.
"""
from __future__ import annotations


async def seed_demo_data(session) -> None:
    # 공개용: 데모 데이터 시드 없음. 필요하면 자체 데이터로 채워 사용.
    return None
