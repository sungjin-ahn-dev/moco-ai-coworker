"""
Google Drive Tools for Claude Code SDK
Claude can manage files, folders, and documents in Google Drive
"""

import asyncio
import json
import logging
import os
import io
from typing import Any, Dict

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


def retry_on_rate_limit(func, max_retries=3, initial_delay=2):
    """Google API rate limit (403) 시 exponential backoff로 재시도하는 래퍼."""
    import time
    delay = initial_delay
    for attempt in range(max_retries + 1):
        try:
            return func()
        except HttpError as e:
            if e.resp.status == 403 and "rateLimitExceeded" in str(e) or "userRateLimitExceeded" in str(e):
                if attempt < max_retries:
                    logger.warning(f"[GOOGLE_DRIVE] Rate limit hit, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise
            else:
                raise

from app.cc_tools.google_drive.auth_helper import get_drive_service
from app.config.settings import get_settings


@tool(
    "google_drive_list_shared_drives",
    "List all shared drives (team drives) that the bot has access to. Use this first when user mentions a shared drive name.",
    {
        "type": "object",
        "properties": {
            "page_size": {
                "type": "integer",
                "description": "Number of results to return (max 100, default 20)",
            },
        },
        "required": [],
    },
)
async def google_drive_list_shared_drives(args: Dict[str, Any]) -> Dict[str, Any]:
    """List all accessible shared drives"""
    try:
        service = get_drive_service()
        page_size = min(args.get("page_size", 20), 100)

        # List shared drives
        results = service.drives().list(
            pageSize=page_size,
            fields="drives(id, name, createdTime, capabilities)"
        ).execute()

        drives = results.get('drives', [])

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "count": len(drives),
                    "shared_drives": drives
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        error_message = f"Drive API 오류 (HTTP {e.resp.status}): {e.error_details}"
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": error_message
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"공유 드라이브 목록 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "google_drive_list_files",
    "List files and folders in Google Drive. Returns up to 100 items. Use slack_user_id to access user's personal My Drive.",
    {
        "type": "object",
        "properties": {
            "folder_id": {
                "type": "string",
                "description": "Parent folder ID to list files from. Use 'root' for the root folder. If omitted, lists all accessible files.",
            },
            "page_size": {
                "type": "integer",
                "description": "Number of results to return (max 100, default 20)",
            },
            "order_by": {
                "type": "string",
                "description": "Sort order: 'modifiedTime desc', 'createdTime desc', 'name', etc.",
            },
            "slack_user_id": {
                "type": "string",
                "description": "Slack user ID (e.g., 'U12345678') to access user's personal My Drive. If omitted, only shared drives are accessible.",
            },
        },
        "required": [],
    },
)
async def google_drive_list_files(args: Dict[str, Any]) -> Dict[str, Any]:
    """List files and folders in Google Drive"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_drive_service(slack_user_id=slack_user_id)

        folder_id = args.get("folder_id")
        page_size = min(args.get("page_size", 20), 100)
        order_by = args.get("order_by", "modifiedTime desc")

        # Build query
        query_parts = ["trashed = false"]
        if folder_id:
            query_parts.append(f"'{folder_id}' in parents")

        query = " and ".join(query_parts)

        # List files
        results = retry_on_rate_limit(lambda: service.files().list(
            q=query,
            pageSize=page_size,
            orderBy=order_by,
            fields="files(id, name, mimeType, createdTime, modifiedTime, size, webViewLink, iconLink, owners)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora='allDrives'
        ).execute())

        files = results.get('files', [])

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "count": len(files),
                    "files": files
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        error_message = f"Drive API 오류 (HTTP {e.resp.status}): {e.error_details}"
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": error_message
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"파일 목록 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "google_drive_search_files",
    "Search for files and folders in Google Drive by name or content. Use slack_user_id to also search user's personal My Drive.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (file name or content keyword)",
            },
            "mime_type": {
                "type": "string",
                "description": "Filter by MIME type (e.g., 'application/vnd.google-apps.document', 'application/pdf')",
            },
            "page_size": {
                "type": "integer",
                "description": "Number of results to return (max 100, default 20)",
            },
            "slack_user_id": {
                "type": "string",
                "description": "Slack user ID (e.g., 'U12345678') to also search user's personal My Drive. If omitted, only shared drives are searched.",
            },
        },
        "required": ["query"],
    },
)
async def google_drive_search_files(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search for files in Google Drive"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_drive_service(slack_user_id=slack_user_id)

        query_text = args["query"]
        mime_type = args.get("mime_type")
        page_size = min(args.get("page_size", 20), 100)

        # Build query
        query_parts = ["trashed = false"]
        query_parts.append(f"name contains '{query_text}' or fullText contains '{query_text}'")

        if mime_type:
            query_parts.append(f"mimeType = '{mime_type}'")

        query = " and ".join(query_parts)

        # Search files
        results = retry_on_rate_limit(lambda: service.files().list(
            q=query,
            pageSize=page_size,
            orderBy="modifiedTime desc",
            fields="files(id, name, mimeType, createdTime, modifiedTime, size, webViewLink, iconLink, owners)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora='allDrives'
        ).execute())

        files = results.get('files', [])

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "query": query_text,
                    "count": len(files),
                    "files": files
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        error_message = f"Drive API 오류 (HTTP {e.resp.status}): {e.error_details}"
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": error_message
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"파일 검색 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "google_drive_get_file_metadata",
    "Get detailed metadata for a specific file or folder.",
    {
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "File or folder ID",
            },
            "slack_user_id": {
                "type": "string",
                "description": "Slack user ID (e.g., 'U12345678') to access user's personal My Drive.",
            },
        },
        "required": ["file_id"],
    },
)
async def google_drive_get_file_metadata(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get file metadata from Google Drive"""
    try:
        service = get_drive_service(slack_user_id=args.get("slack_user_id"))
        file_id = args["file_id"]

        # Get file metadata
        file_metadata = retry_on_rate_limit(lambda: service.files().get(
            fileId=file_id,
            fields="id, name, mimeType, description, createdTime, modifiedTime, size, webViewLink, webContentLink, iconLink, thumbnailLink, owners, permissions, parents, shared, capabilities",
            supportsAllDrives=True
        ).execute())

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "file": file_metadata
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        if e.resp.status == 404:
            error_message = "파일을 찾을 수 없습니다."
        else:
            error_message = f"Drive API 오류 (HTTP {e.resp.status}): {e.error_details}"

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": error_message
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"메타데이터 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "google_drive_download_file",
    "Download a file from Google Drive to local storage.",
    {
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "File ID to download",
            },
            "output_path": {
                "type": "string",
                "description": "Absolute path where the file should be saved (e.g., '/path/to/downloaded_file.pdf')",
            },
            "slack_user_id": {
                "type": "string",
                "description": "Slack user ID (e.g., 'U12345678') to access user's personal My Drive.",
            },
        },
        "required": ["file_id", "output_path"],
    },
)
async def google_drive_download_file(args: Dict[str, Any]) -> Dict[str, Any]:
    """Download a file from Google Drive"""
    try:
        service = get_drive_service(slack_user_id=args.get("slack_user_id"))
        file_id = args["file_id"]
        output_path = args["output_path"]

        # Get file metadata first
        file_metadata = service.files().get(
            fileId=file_id,
            fields="id, name, mimeType, size",
            supportsAllDrives=True
        ).execute()

        # Google 네이티브 포맷은 export() 사용, 일반 파일은 get_media() 사용
        GOOGLE_NATIVE_EXPORT_MAP = {
            'application/vnd.google-apps.spreadsheet': ('text/csv', '.csv'),
            'application/vnd.google-apps.presentation': ('application/vnd.openxmlformats-officedocument.presentationml.presentation', '.pptx'),
            'application/vnd.google-apps.document': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx'),
        }

        mime_type = file_metadata.get('mimeType', '')

        # Create output directory if needed
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        if mime_type in GOOGLE_NATIVE_EXPORT_MAP:
            # Google 네이티브 포맷 → export
            export_mime, ext = GOOGLE_NATIVE_EXPORT_MAP[mime_type]
            # 확장자가 없으면 자동 추가
            if not os.path.splitext(output_path)[1]:
                output_path = output_path + ext
            content = service.files().export(fileId=file_id, mimeType=export_mime).execute()
            with open(output_path, 'wb') as f:
                f.write(content)
        else:
            # 일반 파일 → get_media
            request = service.files().get_media(fileId=file_id)
            with io.FileIO(output_path, 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "file_id": file_id,
                    "file_name": file_metadata.get('name'),
                    "mime_type": file_metadata.get('mimeType'),
                    "size_bytes": file_metadata.get('size'),
                    "output_path": output_path,
                    "message": f"파일 다운로드 완료: {output_path}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        if e.resp.status == 404:
            error_message = "파일을 찾을 수 없습니다."
        elif e.resp.status == 403:
            error_message = "파일 접근 권한이 없습니다."
        else:
            error_message = f"Drive API 오류 (HTTP {e.resp.status}): {e.error_details}"

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": error_message
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"파일 다운로드 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "google_drive_upload_file",
    "Upload a file to Google Drive.",
    {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to upload",
            },
            "file_name": {
                "type": "string",
                "description": "Name for the file in Google Drive (optional, defaults to original filename)",
            },
            "parent_folder_id": {
                "type": "string",
                "description": "Parent folder ID to upload to (optional, defaults to root)",
            },
            "mime_type": {
                "type": "string",
                "description": "MIME type of the file (optional, auto-detected if omitted)",
            },
            "slack_user_id": {
                "type": "string",
                "description": "Slack user ID (e.g., 'U12345678') to access user's personal My Drive.",
            },
        },
        "required": ["file_path"],
    },
)
async def google_drive_upload_file(args: Dict[str, Any]) -> Dict[str, Any]:
    """Upload a file to Google Drive"""
    try:
        service = get_drive_service(slack_user_id=args.get("slack_user_id"))
        file_path = args["file_path"]
        file_name = args.get("file_name", os.path.basename(file_path))
        parent_folder_id = args.get("parent_folder_id")
        mime_type = args.get("mime_type")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")

        # File metadata
        file_metadata = {"name": file_name}
        if parent_folder_id:
            file_metadata["parents"] = [parent_folder_id]

        # DOCX/XLSX/PPTX → Google Docs/Sheets/Slides 자동 변환
        # Google Drive에서 네이티브 형식으로 변환하면 서식 호환성이 향상됨
        convert_map = {
            '.docx': 'application/vnd.google-apps.document',
            '.xlsx': 'application/vnd.google-apps.spreadsheet',
            '.pptx': 'application/vnd.google-apps.presentation',
        }
        ext = os.path.splitext(file_path)[1].lower()
        if ext in convert_map:
            file_metadata["mimeType"] = convert_map[ext]
            # 변환 시 파일명에서 확장자 제거 (Google Docs는 확장자 불필요)
            if file_metadata["name"].lower().endswith(ext):
                file_metadata["name"] = file_metadata["name"][:-len(ext)]

        # Upload file
        media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, mimeType, webViewLink, size",
            supportsAllDrives=True
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "file_id": file.get('id'),
                    "file_name": file.get('name'),
                    "mime_type": file.get('mimeType'),
                    "web_view_link": file.get('webViewLink'),
                    "size_bytes": file.get('size'),
                    "message": f"파일 업로드 완료: {file.get('name')}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        if e.resp.status == 403:
            # 403은 할당량 초과, 권한 부족, 도메인 설정 등 여러 원인 가능
            error_detail = str(e.error_details) if e.error_details else ""
            if "quotaExceeded" in error_detail or "userRateLimitExceeded" in error_detail:
                error_message = f"Google Drive API 할당량을 초과했습니다. 잠시 후 다시 시도해주세요. (상세: {error_detail})"
            else:
                error_message = f"Google Drive 접근 권한 오류 (HTTP 403): {error_detail or '파일 업로드 권한이 없거나 대상 폴더에 접근할 수 없습니다.'}"
        else:
            error_message = f"Drive API 오류 (HTTP {e.resp.status}): {e.error_details}"

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": error_message
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"파일 업로드 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "google_drive_create_folder",
    "Create a new folder in Google Drive.",
    {
        "type": "object",
        "properties": {
            "folder_name": {
                "type": "string",
                "description": "Name of the folder to create",
            },
            "parent_folder_id": {
                "type": "string",
                "description": "Parent folder ID (optional, defaults to root)",
            },
            "slack_user_id": {
                "type": "string",
                "description": "Slack user ID (e.g., 'U12345678') to access user's personal My Drive.",
            },
        },
        "required": ["folder_name"],
    },
)
async def google_drive_create_folder(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a folder in Google Drive"""
    try:
        service = get_drive_service(slack_user_id=args.get("slack_user_id"))
        folder_name = args["folder_name"]
        parent_folder_id = args.get("parent_folder_id")

        # Folder metadata
        file_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder"
        }
        if parent_folder_id:
            file_metadata["parents"] = [parent_folder_id]

        # Create folder
        folder = service.files().create(
            body=file_metadata,
            fields="id, name, webViewLink",
            supportsAllDrives=True
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "folder_id": folder.get('id'),
                    "folder_name": folder.get('name'),
                    "web_view_link": folder.get('webViewLink'),
                    "message": f"폴더 생성 완료: {folder.get('name')}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        error_message = f"Drive API 오류 (HTTP {e.resp.status}): {e.error_details}"
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": error_message
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"폴더 생성 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "google_drive_delete_file",
    "Delete a file or folder from Google Drive (moves to trash).",
    {
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "File or folder ID to delete",
            },
            "slack_user_id": {
                "type": "string",
                "description": "Slack user ID (e.g., 'U12345678') to access user's personal My Drive.",
            },
        },
        "required": ["file_id"],
    },
)
async def google_drive_delete_file(args: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a file from Google Drive"""
    try:
        service = get_drive_service(slack_user_id=args.get("slack_user_id"))
        file_id = args["file_id"]

        # Get file metadata first
        file_metadata = retry_on_rate_limit(lambda: service.files().get(
            fileId=file_id,
            fields="id, name, mimeType",
            supportsAllDrives=True
        ).execute())

        # Delete file
        retry_on_rate_limit(lambda: service.files().delete(
            fileId=file_id,
            supportsAllDrives=True
        ).execute())

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "file_id": file_id,
                    "file_name": file_metadata.get('name'),
                    "message": f"파일 삭제 완료: {file_metadata.get('name')}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        if e.resp.status == 404:
            error_message = "파일을 찾을 수 없습니다."
        elif e.resp.status == 403:
            error_message = "파일 삭제 권한이 없습니다."
        else:
            error_message = f"Drive API 오류 (HTTP {e.resp.status}): {e.error_details}"

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": error_message
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"파일 삭제 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "google_drive_move_file",
    "Move a file or folder to a different folder in Google Drive.",
    {
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "File or folder ID to move",
            },
            "destination_folder_id": {
                "type": "string",
                "description": "Destination folder ID to move the file into",
            },
            "slack_user_id": {
                "type": "string",
                "description": "Slack user ID (e.g., 'U12345678') to access user's personal My Drive.",
            },
        },
        "required": ["file_id", "destination_folder_id"],
    },
)
async def google_drive_move_file(args: Dict[str, Any]) -> Dict[str, Any]:
    """Move a file to a different folder in Google Drive"""
    try:
        service = get_drive_service(slack_user_id=args.get("slack_user_id"))
        file_id = args["file_id"]
        destination_folder_id = args["destination_folder_id"]

        # Get current parents
        file_metadata = retry_on_rate_limit(lambda: service.files().get(
            fileId=file_id,
            fields="id, name, parents",
            supportsAllDrives=True
        ).execute())

        previous_parents = ",".join(file_metadata.get("parents", []))

        # Move file
        retry_on_rate_limit(lambda: service.files().update(
            fileId=file_id,
            addParents=destination_folder_id,
            removeParents=previous_parents,
            fields="id, name, parents, webViewLink",
            supportsAllDrives=True
        ).execute())

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "file_id": file_id,
                    "file_name": file_metadata.get("name"),
                    "destination_folder_id": destination_folder_id,
                    "message": f"파일 이동 완료: {file_metadata.get('name')}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        if e.resp.status == 404:
            error_message = "파일 또는 폴더를 찾을 수 없습니다."
        elif e.resp.status == 403:
            error_message = "파일 이동 권한이 없습니다."
        else:
            error_message = f"Drive API 오류 (HTTP {e.resp.status}): {e.error_details}"

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": error_message
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"파일 이동 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "google_drive_share_file",
    "Share a file or folder with specific users or make it publicly accessible.",
    {
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "File or folder ID to share",
            },
            "email": {
                "type": "string",
                "description": "Email address to share with (optional, for user-specific sharing)",
            },
            "role": {
                "type": "string",
                "description": "Permission role: 'reader', 'writer', 'commenter' (default: 'reader')",
            },
            "type": {
                "type": "string",
                "description": "Permission type: 'user', 'anyone' (for public sharing). Default: 'user'",
            },
            "slack_user_id": {
                "type": "string",
                "description": "Slack user ID (e.g., 'U12345678') to access user's personal My Drive.",
            },
        },
        "required": ["file_id"],
    },
)
async def google_drive_share_file(args: Dict[str, Any]) -> Dict[str, Any]:
    """Share a file in Google Drive"""
    try:
        service = get_drive_service(slack_user_id=args.get("slack_user_id"))
        file_id = args["file_id"]
        email = args.get("email")
        role = args.get("role", "reader")
        perm_type = args.get("type", "user")

        # Get file metadata
        file_metadata = service.files().get(
            fileId=file_id,
            fields="id, name, webViewLink",
            supportsAllDrives=True
        ).execute()

        # Create permission
        permission = {
            "type": perm_type,
            "role": role
        }

        if perm_type == "user" and email:
            permission["emailAddress"] = email

        # Add permission
        permission_result = service.permissions().create(
            fileId=file_id,
            body=permission,
            fields="id",
            supportsAllDrives=True
        ).execute()

        share_info = f"{role} 권한으로 "
        if perm_type == "anyone":
            share_info += "공개 공유"
        elif email:
            share_info += f"{email}에게 공유"
        else:
            share_info += "공유"

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "file_id": file_id,
                    "file_name": file_metadata.get('name'),
                    "permission_id": permission_result.get('id'),
                    "web_view_link": file_metadata.get('webViewLink'),
                    "message": f"{share_info} 완료: {file_metadata.get('name')}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        if e.resp.status == 403:
            error_message = "파일 공유 권한이 없습니다."
        else:
            error_message = f"Drive API 오류 (HTTP {e.resp.status}): {e.error_details}"

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": error_message
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"파일 공유 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "google_drive_create_document",
    "Create a new Google Docs document.",
    {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Title of the document",
            },
            "parent_folder_id": {
                "type": "string",
                "description": "Parent folder ID (optional, defaults to root)",
            },
            "slack_user_id": {
                "type": "string",
                "description": "Slack user ID (e.g., 'U12345678') to access user's personal My Drive.",
            },
        },
        "required": ["title"],
    },
)
async def google_drive_create_document(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Google Docs document"""
    try:
        service = get_drive_service(slack_user_id=args.get("slack_user_id"))
        title = args["title"]
        parent_folder_id = args.get("parent_folder_id")

        # Document metadata
        file_metadata = {
            "name": title,
            "mimeType": "application/vnd.google-apps.document"
        }
        if parent_folder_id:
            file_metadata["parents"] = [parent_folder_id]

        # Create document
        document = service.files().create(
            body=file_metadata,
            fields="id, name, webViewLink, mimeType",
            supportsAllDrives=True
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "document_id": document.get('id'),
                    "title": document.get('name'),
                    "web_view_link": document.get('webViewLink'),
                    "message": f"Google Docs 문서 생성 완료: {document.get('name')}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        error_message = f"Drive API 오류 (HTTP {e.resp.status}): {e.error_details}"
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": error_message
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"문서 생성 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "google_drive_create_spreadsheet",
    "Create a new Google Sheets spreadsheet.",
    {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Title of the spreadsheet",
            },
            "parent_folder_id": {
                "type": "string",
                "description": "Parent folder ID (optional, defaults to root)",
            },
            "slack_user_id": {
                "type": "string",
                "description": "Slack user ID (e.g., 'U12345678') to access user's personal My Drive.",
            },
        },
        "required": ["title"],
    },
)
async def google_drive_create_spreadsheet(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Google Sheets spreadsheet"""
    try:
        service = get_drive_service(slack_user_id=args.get("slack_user_id"))
        title = args["title"]
        parent_folder_id = args.get("parent_folder_id")

        # Spreadsheet metadata
        file_metadata = {
            "name": title,
            "mimeType": "application/vnd.google-apps.spreadsheet"
        }
        if parent_folder_id:
            file_metadata["parents"] = [parent_folder_id]

        # Create spreadsheet
        spreadsheet = service.files().create(
            body=file_metadata,
            fields="id, name, webViewLink, mimeType",
            supportsAllDrives=True
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "spreadsheet_id": spreadsheet.get('id'),
                    "title": spreadsheet.get('name'),
                    "web_view_link": spreadsheet.get('webViewLink'),
                    "message": f"Google Sheets 스프레드시트 생성 완료: {spreadsheet.get('name')}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        error_message = f"Drive API 오류 (HTTP {e.resp.status}): {e.error_details}"
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": error_message
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"스프레드시트 생성 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


def _check_cogsearch_enabled():
    """CogSearch 활성화 여부 확인"""
    settings = get_settings()
    if not settings.COGSEARCH_ENABLED:
        return {
            "content": [{"type": "text", "text": "CogSearch is disabled. Use google_drive_search_files instead."}]
        }
    return None


def _get_cogsearch_headers():
    """CogSearch API 헤더 반환"""
    settings = get_settings()
    return {
        "Authorization": f"Bearer {settings.COGSEARCH_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _parse_cogsearch_documents(content: str) -> list:
    """CogSearch 멀티라인 JSON 응답 파싱"""
    documents = []
    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            doc = json.loads(line)
            if "special_text" in doc:
                continue
            documents.append({
                "file_name": doc.get("file_name", ""),
                "file_path": doc.get("file_path", ""),
                "file_type": doc.get("file_type_ext", ""),
                "file_size": doc.get("file_size", 0),
                "creator": doc.get("creator", ""),
                "modified_at": doc.get("modified_at", ""),
                "created_at": doc.get("created_at", ""),
                "hyperlink": doc.get("hyperlink", ""),
                "text_preview": doc.get("text", "")[:500] if doc.get("text") else "",
            })
        except json.JSONDecodeError:
            continue
    return documents


def _parse_llm_streaming_response(content: str) -> str:
    """CogSearch LLM 스트리밍 청크 응답을 텍스트로 파싱"""
    import re

    # ChatCompletionChunk에서 content 추출
    # Pattern: content='텍스트내용'
    pattern = r"content='([^']*?)'"
    matches = re.findall(pattern, content)

    if matches:
        return "".join(matches)

    # 패턴 매칭 실패 시 원본 반환
    return content


@tool(
    "google_drive_semantic_search",
    "Semantic search for documents in Google Drive using AI-powered content understanding. Use this to find documents by meaning/context rather than exact keywords. Returns relevant documents with content previews. Supports metadata filtering.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query (e.g., '우리 회사 관련 문서', '프로젝트 기획서', '계약서')",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default 5, max 10)",
            },
            "enable_metadata_filter": {
                "type": "boolean",
                "description": "Enable smart metadata filtering (date, author, file type). Default: False",
            },
            "user_email": {
                "type": "string",
                "description": "User email for personalized/access-controlled results (optional)",
            },
        },
        "required": ["query"],
    },
)
async def google_drive_semantic_search(args: Dict[str, Any]) -> Dict[str, Any]:
    """Semantic search for documents using CogSearch API"""
    error_response = _check_cogsearch_enabled()
    if error_response:
        return error_response

    settings = get_settings()

    try:
        query = args["query"]
        max_results = min(args.get("max_results", 5), 10)
        enable_metadata_filter = args.get("enable_metadata_filter", False)
        user_email = args.get("user_email")

        # CogSearch API call
        url = f"{settings.COGSEARCH_BASE_URL}/chat/completions"
        data = {
            "model": settings.COGSEARCH_PIPELINE,
            "messages": [{"role": "user", "content": query}],
            "stream": False,
            "skip_llm": True,
            "return_json": True,
            "enable_smart_search": enable_metadata_filter,
        }

        if user_email:
            data["user2"] = {"email": user_email}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=_get_cogsearch_headers(), json=data)
            response.raise_for_status()
            result = response.json()

        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        documents = _parse_cogsearch_documents(content)[:max_results]

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "query": query,
                    "count": len(documents),
                    "metadata_filter_enabled": enable_metadata_filter,
                    "documents": documents
                }, ensure_ascii=False, indent=2)
            }]
        }

    except httpx.HTTPStatusError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"CogSearch API 오류 (HTTP {e.response.status_code}): {e.response.text[:200]}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except httpx.TimeoutException:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": "CogSearch API 요청 시간 초과. 잠시 후 다시 시도해주세요."
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"시맨틱 검색 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "google_drive_document_qa",
    "Ask questions about documents in Google Drive. Uses CogSearch's built-in LLM to generate answers based on document content. More efficient than semantic_search when you need a direct answer rather than document list. Reduces Claude API token usage.",
    {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Natural language question about documents (e.g., '우리 회사 프로젝트 진행 상황은?', '최근 회의에서 결정된 사항은?')",
            },
            "user_email": {
                "type": "string",
                "description": "User email for personalized/access-controlled results (optional)",
            },
        },
        "required": ["question"],
    },
)
async def google_drive_document_qa(args: Dict[str, Any]) -> Dict[str, Any]:
    """Document Q&A using CogSearch's built-in LLM (skip_llm=False)"""
    error_response = _check_cogsearch_enabled()
    if error_response:
        return error_response

    settings = get_settings()

    try:
        question = args["question"]
        user_email = args.get("user_email")

        # CogSearch API call with LLM enabled
        url = f"{settings.COGSEARCH_BASE_URL}/chat/completions"
        data = {
            "model": settings.COGSEARCH_PIPELINE,
            "messages": [{"role": "user", "content": question}],
            "stream": False,
            "skip_llm": False,  # LLM이 문서 기반 답변 생성
            "return_json": False,
        }

        if user_email:
            data["user2"] = {"email": user_email}

        async with httpx.AsyncClient(timeout=60.0) as client:  # LLM 응답은 더 오래 걸릴 수 있음
            response = await client.post(url, headers=_get_cogsearch_headers(), json=data)
            response.raise_for_status()
            result = response.json()

        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

        # 스트리밍 청크 형태인 경우 파싱
        if "ChatCompletionChunk" in content:
            answer = _parse_llm_streaming_response(content)
        else:
            answer = content

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "question": question,
                    "answer": answer,
                    "source": "CogSearch LLM"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except httpx.HTTPStatusError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"CogSearch API 오류 (HTTP {e.response.status_code}): {e.response.text[:200]}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except httpx.TimeoutException:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": "CogSearch API 요청 시간 초과. 잠시 후 다시 시도해주세요."
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"문서 Q&A 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "google_drive_document_summary",
    "Get a summary of documents matching a topic. Uses CogSearch's built-in LLM to analyze and summarize document content. Useful for getting quick overviews of multiple documents.",
    {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Topic to summarize (e.g., '우리 회사 프로젝트', '2024년 회의록', '계약 관련 문서')",
            },
            "user_email": {
                "type": "string",
                "description": "User email for personalized/access-controlled results (optional)",
            },
        },
        "required": ["topic"],
    },
)
async def google_drive_document_summary(args: Dict[str, Any]) -> Dict[str, Any]:
    """Document summary using CogSearch's built-in LLM"""
    error_response = _check_cogsearch_enabled()
    if error_response:
        return error_response

    settings = get_settings()

    try:
        topic = args["topic"]
        user_email = args.get("user_email")

        # 요약 요청을 위한 프롬프트 구성
        summary_prompt = f"{topic}에 대한 문서들을 요약해줘. 주요 내용과 핵심 포인트를 정리해줘."

        url = f"{settings.COGSEARCH_BASE_URL}/chat/completions"
        data = {
            "model": settings.COGSEARCH_PIPELINE,
            "messages": [{"role": "user", "content": summary_prompt}],
            "stream": False,
            "skip_llm": False,
            "return_json": False,
        }

        if user_email:
            data["user2"] = {"email": user_email}

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=_get_cogsearch_headers(), json=data)
            response.raise_for_status()
            result = response.json()

        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

        if "ChatCompletionChunk" in content:
            summary = _parse_llm_streaming_response(content)
        else:
            summary = content

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "topic": topic,
                    "summary": summary,
                    "source": "CogSearch LLM"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except httpx.HTTPStatusError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"CogSearch API 오류 (HTTP {e.response.status_code}): {e.response.text[:200]}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except httpx.TimeoutException:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": "CogSearch API 요청 시간 초과. 잠시 후 다시 시도해주세요."
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"문서 요약 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


# ──────────────────────────────────────────────
# Google Drive Labels 도구
# ──────────────────────────────────────────────

DRIVE_LABEL_ID = 'kumDe1bT78rdBKvcli9H2X0eY8LphVfbDWVRNNEbbFcb'
DRIVE_LABEL_FIELDS = {
    'Domain': {
        'field_id': '95C63C965A',
        'choices': {
            'Governance': '020AC114CE', 'Support': 'D82BFA2CC8', 'Intelligence': '8ED58B4CF2',
            'Solution': '594E5F83F8', 'Market': '4D69ABA5C5', 'Operations': '79FA80EB55',
            'HR': '20FA61E539', 'Finance': '1A2D182A25', 'RND': '57BEF04DBC',
            'Product': 'C65BA86AA5', 'Engineering': 'AD3DA59898', 'Clinical': 'B7219D7C1E',
            'Sales': 'DA5B1CE008',
        },
    },
    'Nature': {
        'field_id': 'CE778D8929',
        'choices': {
            'Rule': 'D531366944', 'Plan': '8B6C5726C4', 'Do': 'D5859D6A91',
            'Report': '04DFFA7BAD', 'Learn': '661DA6ED26', 'Strategy': '1158D89CCB',
            'Proposal': 'C2946BD367', 'Roadmap': '6225C48795', 'Policy': '5541989FCE',
            'Guide': '51386FD9FB', 'Checklist': 'DD4C9B9FE5', 'Template': '7B7B2D51EE',
            'Record': '296078DA21', 'MeetingNote': '3335359C3B', 'StandupNote': '66C2FFDB51',
            'PRD': 'ACA5CFE8FF', 'Spec': 'A092A4A421', 'SystemDesign': 'F37D41CAD8',
            'UserStory': '65A3AA01B0', 'APIDoc': '3C289CFB81', 'ExperimentNote': 'F1B93DBB6F',
            'ResearchPlan': 'D276BC9709', 'Analysis': '5BDD770E13', 'Benchmark': 'B1F304E3E9',
            'ABTestReport': '60128AF11E', 'Estimate': '96B65D69EC', 'Contract': 'B5AE12E680',
            'Agreement': '5CC257198A', 'Regulatory': '70B71E3D69', 'Patent': 'D6C9D92D0C',
            'Official': 'ECADCEAE60', 'Marketing': '954F4D16F9', 'Sales': '39263EF108',
            'IR': '55CCB65C1E', 'Presentation': '4D6870EFB1', 'Onboarding': '7430187063',
            'Reference': '764CB5A4CC',
        },
    },
    'standard_filename': {
        'field_id': 'BE1F3DCDEF',
    },
}


@tool(
    "google_drive_apply_labels",
    "Google Drive 파일에 Domain, Nature, 표준파일명 라벨을 적용합니다. 문서의 업무 영역(Domain), 문서 목적(Nature), 표준 파일명을 자동 분류하여 적용합니다.",
    {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "Google Drive 파일 ID"},
            "domain": {
                "type": "string",
                "description": "업무 영역: Governance, Support, Intelligence, Solution, Market, Operations, HR, Finance, RND, Product, Engineering, Clinical, Sales",
            },
            "nature": {
                "type": "string",
                "description": "문서 목적: Rule, Plan, Do, Report, Learn, Strategy, Proposal, Roadmap, Policy, Guide, Checklist, Template, Record, MeetingNote, StandupNote, PRD, Spec, SystemDesign, UserStory, APIDoc, ExperimentNote, ResearchPlan, Analysis, Benchmark, ABTestReport, Estimate, Contract, Agreement, Regulatory, Patent, Official, Marketing, Sales, IR, Presentation, Onboarding, Reference",
            },
            "standard_filename": {
                "type": "string",
                "description": "표준파일명 (폴더명_Topic 형식, 예: Lab-AI-MVP_AI-프로토타이핑-도구-비교-분석)",
            },
            "slack_user_id": {"type": "string", "description": "요청한 Slack 사용자 ID"},
        },
        "required": ["file_id"],
    },
)
async def google_drive_apply_labels(args: Dict[str, Any]) -> Dict[str, Any]:
    """Google Drive 파일에 Domain/Nature/표준파일명 라벨 적용"""
    try:
        from app.cc_tools.google_drive.auth_helper import get_drive_service
        service = get_drive_service(slack_user_id=args.get("slack_user_id"))

        field_mods = []

        # Domain
        domain = args.get("domain")
        if domain:
            domain_info = DRIVE_LABEL_FIELDS['Domain']
            choice_id = domain_info['choices'].get(domain)
            if choice_id:
                field_mods.append({
                    'fieldId': domain_info['field_id'],
                    'setSelectionValues': [choice_id],
                })

        # Nature
        nature = args.get("nature")
        if nature:
            nature_info = DRIVE_LABEL_FIELDS['Nature']
            choice_id = nature_info['choices'].get(nature)
            if choice_id:
                field_mods.append({
                    'fieldId': nature_info['field_id'],
                    'setSelectionValues': [choice_id],
                })

        # 표준파일명
        standard_filename = args.get("standard_filename")
        if standard_filename:
            field_mods.append({
                'fieldId': DRIVE_LABEL_FIELDS['standard_filename']['field_id'],
                'setTextValues': [standard_filename],
            })

        if not field_mods:
            return {
                "content": [{"type": "text", "text": json.dumps({
                    "success": False, "message": "적용할 라벨 값이 없습니다. domain, nature, standard_filename 중 하나 이상을 지정하세요."
                }, ensure_ascii=False)}],
                "error": True,
            }

        result = retry_on_rate_limit(lambda: service.files().modifyLabels(
            fileId=args["file_id"],
            body={'labelModifications': [{'labelId': DRIVE_LABEL_ID, 'fieldModifications': field_mods}]},
        ).execute())

        applied = []
        if domain:
            applied.append(f"Domain={domain}")
        if nature:
            applied.append(f"Nature={nature}")
        if standard_filename:
            applied.append(f"표준파일명={standard_filename}")

        return {
            "content": [{"type": "text", "text": json.dumps({
                "success": True,
                "file_id": args["file_id"],
                "applied": applied,
                "message": f"라벨 적용 완료: {', '.join(applied)}",
            }, ensure_ascii=False)}],
        }

    except HttpError as e:
        return {
            "content": [{"type": "text", "text": json.dumps({
                "success": False, "error": True, "message": f"Drive Labels API 오류: {str(e)}"
            }, ensure_ascii=False)}],
            "error": True,
        }
    except Exception as e:
        logger.error(f"[GOOGLE_DRIVE] apply_labels error: {e}")
        return {
            "content": [{"type": "text", "text": json.dumps({
                "success": False, "error": True, "message": f"라벨 적용 실패: {str(e)}"
            }, ensure_ascii=False)}],
            "error": True,
        }


# MCP Server
google_drive_tools = [
    # 기본 Google Drive API 도구
    google_drive_list_shared_drives,
    google_drive_list_files,
    google_drive_search_files,
    google_drive_get_file_metadata,
    google_drive_download_file,
    google_drive_upload_file,
    google_drive_create_folder,
    google_drive_delete_file,
    google_drive_move_file,
    google_drive_share_file,
    google_drive_create_document,
    google_drive_create_spreadsheet,
    google_drive_apply_labels,
]

# CogSearch 활성화 시에만 시맨틱 검색 도구 추가
_settings = get_settings()
if _settings.COGSEARCH_ENABLED:
    google_drive_tools.extend([
        google_drive_semantic_search,      # 문서 목록 검색
        google_drive_document_qa,          # 문서 기반 Q&A (LLM 답변)
        google_drive_document_summary,     # 문서 요약 (LLM 요약)
    ])


def create_google_drive_mcp_server():
    """Claude Code SDK Google Drive MCP server"""
    return create_sdk_mcp_server(
        name="google-drive",
        version="1.0.0",
        tools=google_drive_tools
    )
