import os
import re
import sys
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

from app.config import constant


def _win_to_wsl_path(path: str) -> str:
    """Windows 경로를 WSL 경로로 변환 (WSL 환경에서만 동작)

    예: C:\\Users\\foo\\bar → /mnt/c/Users/foo/bar
    """
    if not path:
        return path
    # WSL이 아닌 환경이면 그대로 반환
    if sys.platform != "linux" or not os.path.exists("/mnt/c"):
        return path
    # 이미 Linux 경로면 그대로
    if path.startswith("/"):
        return path
    # Windows 경로 패턴: C:\... 또는 C:/...
    match = re.match(r'^([A-Za-z]):[\\\/](.*)$', path)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2).replace('\\', '/')
        return f"/mnt/{drive}/{rest}"
    return path


class Settings(BaseSettings):
    # 공통 환경
    APP_ENV: str = ""

    # AI 모델 설정 (Electron 앱에서 선택 가능, Vertex AI 사용 시 자동 변환)
    MODEL_FOR_SIMPLE: str = "haiku"      # 판단용 (봇 호출 감지, 분류 등)
    MODEL_FOR_MODERATE: str = "sonnet"   # 분석용 (메모리 관리, 요약 등)
    MODEL_FOR_COMPLEX: str = "sonnet"    # 작업용 (핵심 작업 수행)

    # SLACK 관련
    SLACK_BOT_TOKEN: str = ""
    SLACK_APP_TOKEN: str = ""
    SLACK_SIGNING_SECRET: str = ""
    SLACK_TEAM_ID: str = ""

    # 봇 정보
    BOT_NAME: str = ""
    BOT_EMAIL: str = ""
    BOT_ORGANIZATION: str = ""
    BOT_TEAM: str = ""
    BOT_AUTHORIZED_USERS_EN: str = ""
    BOT_AUTHORIZED_USERS_KR: str = ""
    BOT_ROLE: str = ""
    FILESYSTEM_BASE_DIR: str = ""

    # Agent Factory (자동 에이전트 생성 시스템)
    # AGENT_APPROVER_SLACK_ID 가 비어있으면 검증 통과 시 자동 publish.
    # 채워두면 Slack DM 으로 승인 요청 후 publish.
    AGENT_APPROVER_SLACK_ID: str = ""
    AGENT_FACTORY_ENABLED: bool = True
    # 사용량 N일 0회 에이전트 자동 archive
    AGENT_AUTO_ARCHIVE_DAYS: int = 30
    # Phase 2 — 메모리 분석으로 후보 도메인 자동 감지 → 사용자 confirm
    AGENT_CANDIDATE_SUGGESTER_ENABLED: bool = True
    # 메모리 스캔 윈도우 (일). 이 기간 내 수정된 파일만 분석
    AGENT_CANDIDATE_DETECT_WINDOW_DAYS: int = 30
    # 일 1회 라이프사이클 작업 시작 시각 (0~23). suggester=HH:00, archive=HH:30
    AGENT_LIFECYCLE_DAILY_HOUR: int = 3

    # MCP - Perplexity
    PERPLEXITY_ENABLED: bool = True
    PERPLEXITY_API_KEY: str = ""

    # MCP - DeepL
    DEEPL_ENABLED: bool = True
    DEEPL_API_KEY: str = ""

    # ElevenLabs
    ELEVENLABS_API_KEY: str = ""

    # MCP - GitHub
    GITHUB_ENABLED: bool = True
    GITHUB_PERSONAL_ACCESS_TOKEN: str = ""

    # MCP - GitLab
    GITLAB_ENABLED: bool = True
    GITLAB_API_URL: str = ""
    GITLAB_PERSONAL_ACCESS_TOKEN: str = ""

    # MCP - Microsoft 365 (Lokka)
    MS365_ENABLED: bool = False
    MS365_CLIENT_ID: str = ""
    MS365_TENANT_ID: str = ""

    # MCP - Atlassian Rovo
    ATLASSIAN_ENABLED: bool = False
    ATLASSIAN_CONFLUENCE_SITE_URL: str = ""
    ATLASSIAN_CONFLUENCE_DEFAULT_PAGE_ID: str = ""
    ATLASSIAN_JIRA_SITE_URL: str = ""

    # MCP - Google Drive
    GOOGLE_DRIVE_ENABLED: bool = True
    GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON: str = ""
    GOOGLE_DRIVE_OAUTH_CREDENTIALS: str = ""

    # MCP - CogSearch (Google Drive 시맨틱 검색)
    COGSEARCH_ENABLED: bool = False
    COGSEARCH_API_KEY: str = ""
    COGSEARCH_BASE_URL: str = "http://localhost:3050/api"
    COGSEARCH_PIPELINE: str = "gdrive_pipeline"

    # MCP - Gmail
    GMAIL_ENABLED: bool = False
    GMAIL_USER_EMAIL: str = ""  # 접근할 사용자 이메일 (Domain-Wide Delegation)

    # MCP - Google Calendar
    GOOGLE_CALENDAR_ENABLED: bool = False
    GOOGLE_CALENDAR_USER_EMAIL: str = ""  # 접근할 사용자 이메일 (Domain-Wide Delegation)

    # CRM - Working Day Google Calendar 양방향 동기화
    # JSON 문자열: {"Harry":"harry@example.com","Chloe":"chloe@example.com"}
    WORKING_DAY_GCAL_SYNC_USERS: str = '{"Harry":"harry@example.com","Chloe":"chloe@example.com"}'
    WORKING_DAY_GCAL_SYNC_ENABLED: bool = True
    WORKING_DAY_GCAL_SYNC_INTERVAL_MIN: int = 30

    # MCP - Tableau
    TABLEAU_ENABLED: bool = False
    TABLEAU_SERVER: str = ""
    TABLEAU_SITE_NAME: str = ""
    TABLEAU_PAT_NAME: str = ""
    TABLEAU_PAT_VALUE: str = ""

    # MCP - X (Twitter)
    X_ENABLED: bool = True
    X_API_KEY: str = ""
    X_API_SECRET: str = ""
    X_ACCESS_TOKEN: str = ""
    X_ACCESS_TOKEN_SECRET: str = ""
    X_OAUTH2_CLIENT_ID: str = ""
    X_OAUTH2_CLIENT_SECRET: str = ""

    # MCP - ClickUp
    CLICKUP_ENABLED: bool = False
    CLICKUP_API_KEY: str = ""
    CLICKUP_API_KEY_DOROTHY: str = ""
    CLICKUP_API_KEY_MATT: str = ""
    CLICKUP_API_KEY_GLORY: str = ""
    CLICKUP_API_KEY_WIDER: str = ""

    # CLAW OPS (AICC)
    CLAWOPS_API_KEY: str = ""
    CLAWOPS_WEBHOOK_SECRET: str = ""
    CLAWOPS_ACCOUNT_ID: str = ""
    GEMINI_API_KEY: str = ""

    # MCP - Clova Speech
    CLOVA_ENABLED: bool = False
    CLOVA_INVOKE_URL: str = ""
    CLOVA_SECRET_KEY: str = ""

    # MCP - Custom Remote MCP Servers (JSON array)
    # Format: [{"name": "server1", "url": "https://...", "instruction": "..."}, ...]
    REMOTE_MCP_SERVERS: str = ""

    # Computer Use
    CHROME_ENABLED: bool = False
    CHROME_ALWAYS_PROFILE_SETUP: bool = False

    # 웹 서버 / 음성 수신 채널
    WEB_INTERFACE_ENABLED: bool = True
    WEB_INTERFACE_AUTH_PROVIDER: str = "microsoft"
    WEB_INTERFACE_URL: str = ""
    CLOUDFLARE_TUNNEL_ENABLED: bool = True
    WEB_SLACK_CLIENT_ID: str = ""
    WEB_SLACK_CLIENT_SECRET: str = ""
    WEB_MS365_CLIENT_ID: str = ""
    WEB_MS365_CLIENT_SECRET: str = ""
    WEB_MS365_TENANT_ID: str = ""
    WEB_GOOGLE_CLIENT_ID: str = ""
    WEB_GOOGLE_CLIENT_SECRET: str = ""

    # 능동 수신 채널 - Outlook
    OUTLOOK_CHECK_ENABLED: bool = False
    OUTLOOK_CHECK_INTERVAL: int = 5

    # 능동 수신 채널 - Confluence
    CONFLUENCE_CHECK_ENABLED: bool = False
    CONFLUENCE_CHECK_INTERVAL: int = 60
    CONFLUENCE_CHECK_HOURS: int = 1

    # 능동 수신 채널 - Jira
    JIRA_CHECK_ENABLED: bool = False
    JIRA_CHECK_INTERVAL: int = 30

    # 선제적 제안 기능
    DYNAMIC_SUGGESTER_ENABLED: bool = False
    DYNAMIC_SUGGESTER_INTERVAL: int = 15

    # Skill Marketplace
    SKILL_MARKETPLACE_ENABLED: bool = False
    SKILL_MARKETPLACE_FOLDER_ID: str = ""  # Google Drive 공유 폴더 ID
    SKILL_AUTO_APPROVE: bool = True  # 스킬 자동 활성화 여부

    # MCP Server (외부 Claude Code/Desktop 노출)
    MCP_ENABLED: bool = False
    MCP_PATH: str = "/mcp"
    MCP_TOKEN_FILE: str = "~/.moco/mcp_tokens.json"

    # 디버그
    DEBUG_SLACK_MESSAGES_ENABLED: bool = False

    def model_post_init(self, __context):
        # 1) ~/.moco/config.env 로드 (Electron 없이 직접 실행 시)
        from app.cc_utils.path_helper import get_moco_file
        moco_config = get_moco_file("config.env")
        if os.path.exists(moco_config):
            load_dotenv(moco_config, override=True)
            # Pydantic이 이미 초기화된 후이므로, 환경변수에서 다시 읽어서 반영
            for field_name in self.model_fields:
                env_val = os.environ.get(field_name, "")
                if env_val:
                    current_val = getattr(self, field_name, "")
                    field_type = type(current_val)
                    try:
                        if field_type is bool:
                            converted = env_val.lower() in ("true", "1", "yes")
                        elif field_type is int:
                            converted = int(env_val)
                        else:
                            converted = env_val
                        if converted != current_val:
                            object.__setattr__(self, field_name, converted)
                    except (ValueError, TypeError):
                        pass

        load_dotenv("app/config/env/dev.env", override=True)

        # WSL 환경: Windows 경로를 자동 변환
        if sys.platform == "linux" and os.path.exists("/mnt/c"):
            for field_name in ("FILESYSTEM_BASE_DIR", "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "GOOGLE_DRIVE_OAUTH_CREDENTIALS"):
                val = getattr(self, field_name, "")
                converted = _win_to_wsl_path(val)
                if converted != val:
                    object.__setattr__(self, field_name, converted)

        # Check for Vertex AI credential
        # 1st priority: User's home directory (~/.moco/) - for internal users
        # 2nd priority: App internal path - for local development
        explicit_credential_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
        google_drive_service_account_path = (
            self.GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON or ""
        ).strip()
        gcloud_adc_path = os.path.join(
            os.path.expanduser("~"),
            ".config",
            "gcloud",
            "application_default_credentials.json",
        )
        from app.cc_utils.path_helper import get_moco_file
        user_credential_path = get_moco_file("credential.json")
        dev_credential_path = os.path.join(
            os.path.dirname(__file__), "env", "credential.json"
        )

        credential_path = ""
        if explicit_credential_path and os.path.exists(explicit_credential_path):
            credential_path = explicit_credential_path
        elif (
            google_drive_service_account_path
            and os.path.exists(google_drive_service_account_path)
        ):
            credential_path = google_drive_service_account_path
        elif os.path.exists(user_credential_path):
            credential_path = user_credential_path
        elif os.path.exists(dev_credential_path):
            credential_path = dev_credential_path

        if credential_path:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credential_path
            # Claude Vertex 경로는 명시적으로 활성화한 경우에만 사용
            # (기본은 기존 Claude 모델 라우팅 유지)
            if os.environ.get("CLAUDE_CODE_USE_VERTEX", "").strip() == "1":
                os.environ.setdefault("ANTHROPIC_VERTEX_PROJECT_ID", "your-gcp-project")
                os.environ.setdefault("ANTHROPIC_VERTEX_REGION", "us-east5")
                # Pydantic v2 BaseSettings는 frozen이므로 object.__setattr__ 사용
                # 사용자 선택 모델을 Vertex AI 모델명으로 변환
                vertex_model_map = {
                    "haiku": "claude-haiku-4-5@20251001",
                    "sonnet": "claude-sonnet-4-5@20250929",
                    "opus": "claude-opus-4-5@20251101",
                }
                object.__setattr__(self, 'MODEL_FOR_SIMPLE', vertex_model_map.get(self.MODEL_FOR_SIMPLE, self.MODEL_FOR_SIMPLE))
                object.__setattr__(self, 'MODEL_FOR_MODERATE', vertex_model_map.get(self.MODEL_FOR_MODERATE, self.MODEL_FOR_MODERATE))
                object.__setattr__(self, 'MODEL_FOR_COMPLEX', vertex_model_map.get(self.MODEL_FOR_COMPLEX, self.MODEL_FOR_COMPLEX))
        elif not os.path.exists(gcloud_adc_path):
            pass  # No GCP credentials found

@lru_cache
def get_settings() -> Settings:
    run_env: str = os.environ.get("RUN_ENV", constant.DEV)

    setting_config = {}
    env_file_path = f"{constant.ENV_DIR_PATH}/{run_env}.env".replace("..", ".")
    if os.path.exists(env_file_path):
        setting_config["_env_file"] = env_file_path

    settings = Settings(**setting_config)

    if settings.FILESYSTEM_BASE_DIR and '/mnt/c/' in settings.FILESYSTEM_BASE_DIR:
        import platform
        if platform.system() == 'Linux' and os.path.exists('/proc/version'):
            wsl_native = '/home/user/MOCO_DATA'
            if os.path.exists(wsl_native):
                settings.FILESYSTEM_BASE_DIR = wsl_native

    return settings
