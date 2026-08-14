"""MOCO MCP Server — 외부 Claude(Claude Code/Desktop)에서 MOCO 봇 기능 사용.

기존 Slack 봇 진입점은 그대로 두고, FastAPI(8000)에 `/mcp` 엔드포인트를 추가 마운트해서
acme 멤버들이 Claude에서 자연어로 MOCO 도구(Slack/Outlook/Atlassian/메모리/스케줄러 등)를
호출할 수 있게 합니다.

활성화: settings.MCP_ENABLED=true (기본 false → 기존 시스템과 100% 동일)
"""
