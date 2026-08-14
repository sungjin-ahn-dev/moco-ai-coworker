"""
MCP 서버 로컬 실행 헬퍼

npx -y 대신 로컬 설치된 패키지를 node로 직접 실행하여
매 요청마다 발생하는 npm 레지스트리 확인 오버헤드를 제거합니다.
"""

import os

# 프로젝트 루트 (package.json / node_modules 위치)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 패키지별 엔트리포인트 매핑
_PACKAGE_BIN_MAP = {
    "@mcpcentral/mcp-time": "dist/index.js",
    "@upstash/context7-mcp": "dist/index.js",
    "@langgpt/arxiv-paper-mcp": "build/index.js",
    "@openbnb/mcp-server-airbnb": "dist/index.js",
    "@limecooler/yt-info-mcp": "dist/index.js",
    "steam-review-mcp": "build/index.js",
    "server-perplexity-ask": "dist/index.js",
    "@zereight/mcp-gitlab": "build/index.js",
    "@batteryho/lokka-cached": "build/main.js",
    "mcp-remote": "dist/proxy.js",
    "@tableau/mcp-server": "build/index.js",
    "@playwright/mcp": "cli.js",
}


def local_mcp(package: str, extra_args: list[str] | None = None, env: dict | None = None, use_cache: bool = False) -> dict:
    """로컬 설치된 MCP 패키지를 node로 직접 실행하는 서버 설정을 반환합니다.

    Args:
        package: npm 패키지 이름 (예: "@mcpcentral/mcp-time")
        extra_args: 추가 CLI 인자 (예: ["--ignore-robots-txt"])
        env: 환경변수 딕셔너리
        use_cache: mcp-cache로 감싸서 실행할지 여부

    Returns:
        dict: Claude SDK MCP 서버 설정
    """
    entry = _PACKAGE_BIN_MAP.get(package)
    if not entry:
        # 매핑이 없으면 npx 폴백
        args = ["-y", package]
        if extra_args:
            args.extend(extra_args)
        result = {"command": "npx", "args": args}
        if env:
            result["env"] = env
        return result

    script_path = os.path.join(_PROJECT_ROOT, "node_modules", package, entry)

    if use_cache:
        cache_bin = os.path.join(_PROJECT_ROOT, "node_modules", ".bin", "mcp-cache")
        args = [cache_bin, "node", script_path]
    else:
        args = [script_path]

    if extra_args:
        args.extend(extra_args)

    result = {"command": "node", "args": args}
    if env:
        result["env"] = env
    return result
