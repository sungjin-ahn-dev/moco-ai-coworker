"""
X (Twitter) Tools for Claude Code SDK
Claude가 X(트위터)를 사용할 수 있는 도구
"""

import json
import os
import re
from typing import Any, Dict, Optional

import httpx
import tweepy
from claude_agent_sdk import create_sdk_mcp_server, tool

from app.config.settings import get_settings
from app.cc_utils.x_helper import get_valid_access_token


# OAuth 1.0a 클라이언트 (트윗 작성, 미디어 업로드, 타임라인용)
_x_client_v2: Optional[tweepy.Client] = None
_x_client_v1: Optional[tweepy.API] = None


def get_x_client_v2() -> tweepy.Client:
    """X API v2 클라이언트 반환 (OAuth 1.0a)"""
    global _x_client_v2
    if _x_client_v2 is None:
        raise ValueError("X client not initialized. Call initialize_x_client() first.")
    return _x_client_v2


def get_x_client_v1() -> tweepy.API:
    """X API v1.1 클라이언트 반환 (미디어 업로드용)"""
    global _x_client_v1
    if _x_client_v1 is None:
        raise ValueError("X client not initialized. Call initialize_x_client() first.")
    return _x_client_v1


def initialize_x_client():
    """X 클라이언트 초기화 (OAuth 1.0a)"""
    global _x_client_v2, _x_client_v1

    settings = get_settings()

    if not all([
        settings.X_API_KEY,
        settings.X_API_SECRET,
        settings.X_ACCESS_TOKEN,
        settings.X_ACCESS_TOKEN_SECRET,
    ]):
        raise ValueError("X API OAuth 1.0a credentials not set in settings")

    # API v2 클라이언트
    _x_client_v2 = tweepy.Client(
        consumer_key=settings.X_API_KEY,
        consumer_secret=settings.X_API_SECRET,
        access_token=settings.X_ACCESS_TOKEN,
        access_token_secret=settings.X_ACCESS_TOKEN_SECRET,
    )

    # API v1.1 클라이언트 (미디어 업로드용)
    auth = tweepy.OAuth1UserHandler(
        settings.X_API_KEY,
        settings.X_API_SECRET,
        settings.X_ACCESS_TOKEN,
        settings.X_ACCESS_TOKEN_SECRET,
    )
    _x_client_v1 = tweepy.API(auth)

    return _x_client_v2


@tool(
    "post_tweet",
    "트윗을 작성하여 게시합니다. 중요: 280자 제한이 있으므로 긴 내용은 250자 이내로 요약해서 작성하세요.",
    {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "트윗 내용 (최대 280자, 여유있게 250자 이내 권장)"}
        },
        "required": ["text"],
    },
)
async def x_post_tweet(args: Dict[str, Any]) -> Dict[str, Any]:
    """트윗 작성 (OAuth 1.0a)"""
    text = args["text"]

    try:
        import logging
        logging.info(f"[X_TOOL] Attempting to post tweet: {text[:50]}...")

        client = get_x_client_v2()
        response = client.create_tweet(text=text)

        tweet_data = response.data
        tweet_id = tweet_data.get("id")
        tweet_text = tweet_data.get("text")

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": True,
                            "message": "트윗 게시 완료",
                            "tweet_id": tweet_id,
                            "tweet_text": tweet_text,
                            "tweet_url": f"https://twitter.com/i/web/status/{tweet_id}",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ]
        }

    except Exception as e:
        import logging
        logging.error(f"[X_TOOL] Tweet post error: {type(e).__name__}: {e}")

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": False,
                            "error": True,
                            "message": f"트윗 게시 실패: {str(e)}",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ],
            "error": True,
        }


@tool(
    "get_tweet",
    "특정 트윗의 내용을 읽어옵니다. 트윗 URL 또는 트윗 ID를 입력하세요.",
    {
        "type": "object",
        "properties": {
            "tweet_url_or_id": {
                "type": "string",
                "description": "트윗 URL (예: https://x.com/username/status/1234567890) 또는 트윗 ID (예: 1234567890)",
            }
        },
        "required": ["tweet_url_or_id"],
    },
)
async def x_get_tweet(args: Dict[str, Any]) -> Dict[str, Any]:
    """트윗 내용 조회"""
    tweet_url_or_id = args["tweet_url_or_id"]

    try:
        # OAuth 2.0 User Context 토큰 사용
        access_token = await get_valid_access_token()
        if not access_token:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": False,
                                "error": True,
                                "message": "X 인증이 필요합니다. 웹 인터페이스에서 /bot/auth/x/start로 인증을 진행하세요.",
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                ],
                "error": True,
            }

        # URL에서 tweet_id 추출 또는 ID 그대로 사용
        tweet_id = tweet_url_or_id

        # URL 형식인 경우 ID 추출
        url_pattern = (
            r"(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/(?:\w+)/status/(\d+)"
        )
        match = re.search(url_pattern, tweet_url_or_id)
        if match:
            tweet_id = match.group(1)

        # v2 API 직접 호출: GET /2/tweets/:id
        url = f"https://api.twitter.com/2/tweets/{tweet_id}"

        params = {
            "tweet.fields": "created_at,public_metrics,author_id,text",
            "expansions": "author_id",
            "user.fields": "username,name"
        }

        headers = {
            "Authorization": f"Bearer {access_token}"
        }

        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()

        if not data.get("data"):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": False,
                                "error": True,
                                "message": "트윗을 찾을 수 없습니다.",
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                ],
                "error": True,
            }

        tweet = data["data"]

        # 작성자 정보 가져오기
        author_username = "unknown"
        author_name = "Unknown"
        if data.get("includes") and data["includes"].get("users"):
            author = data["includes"]["users"][0]
            author_username = author.get("username", "unknown")
            author_name = author.get("name", "Unknown")

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": True,
                            "message": "트윗 내용을 가져왔습니다.",
                            "tweet": {
                                "id": tweet["id"],
                                "text": tweet.get("text", ""),
                                "author": {
                                    "username": author_username,
                                    "name": author_name,
                                },
                                "created_at": tweet.get("created_at", ""),
                                "metrics": tweet.get("public_metrics", {}),
                                "url": f"https://twitter.com/{author_username}/status/{tweet['id']}",
                            },
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ]
        }

    except httpx.HTTPStatusError as e:
        error_detail = ""
        try:
            error_detail = e.response.json()
        except:
            error_detail = e.response.text

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": False,
                            "error": True,
                            "message": f"트윗 조회 실패 (HTTP {e.response.status_code}): {error_detail}",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ],
            "error": True,
        }
    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": False,
                            "error": True,
                            "message": f"트윗 조회 실패: {str(e)}",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ],
            "error": True,
        }


@tool(
    "get_my_tweets",
    "내가 작성한 최근 트윗 목록을 가져옵니다.",
    {
        "type": "object",
        "properties": {
            "max_results": {
                "type": "integer",
                "description": "가져올 트윗 수 (기본값: 10, 최대: 100)",
            }
        },
    },
)
async def x_get_my_tweets(args: Dict[str, Any]) -> Dict[str, Any]:
    """내가 작성한 트윗 조회"""
    max_results = args.get("max_results", 10)

    # max_results 제한 (5-100)
    max_results = max(5, min(100, max_results))

    try:
        # OAuth 2.0 User Context 토큰 사용
        access_token = await get_valid_access_token()
        if not access_token:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": False,
                                "error": True,
                                "message": "X 인증이 필요합니다. 웹 인터페이스에서 /bot/auth/x/start로 인증을 진행하세요.",
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                ],
                "error": True,
            }

        # OAuth 2.0 헤더 설정
        headers = {
            "Authorization": f"Bearer {access_token}"
        }

        async with httpx.AsyncClient() as http_client:
            # 먼저 내 user_id 가져오기 (OAuth 2.0)
            me_response = await http_client.get(
                "https://api.twitter.com/2/users/me",
                headers=headers
            )
            me_response.raise_for_status()
            my_user_id = me_response.json()["data"]["id"]

            # v2 API 직접 호출: GET /2/users/:id/tweets
            url = f"https://api.twitter.com/2/users/{my_user_id}/tweets"

            params = {
                "max_results": max_results,
                "tweet.fields": "created_at,public_metrics,text"
            }
            response = await http_client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()

        if not data.get("data"):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": True,
                                "message": "작성한 트윗이 없습니다.",
                                "tweets": [],
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                ]
            }

        tweets = []
        for tweet in data["data"]:
            tweets.append(
                {
                    "id": tweet["id"],
                    "text": tweet.get("text", ""),
                    "created_at": tweet.get("created_at", ""),
                    "metrics": tweet.get("public_metrics", {}),
                    "url": f"https://twitter.com/i/web/status/{tweet['id']}",
                }
            )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": True,
                            "message": f"{len(tweets)}개의 트윗을 가져왔습니다.",
                            "count": len(tweets),
                            "tweets": tweets,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ]
        }

    except httpx.HTTPStatusError as e:
        error_detail = ""
        try:
            error_detail = e.response.json()
        except:
            error_detail = e.response.text

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": False,
                            "error": True,
                            "message": f"트윗 조회 실패 (HTTP {e.response.status_code}): {error_detail}",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ],
            "error": True,
        }
    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": False,
                            "error": True,
                            "message": f"트윗 조회 실패: {str(e)}",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ],
            "error": True,
        }


@tool(
    "search_recent_tweets",
    "최근 7일 이내의 트윗을 키워드로 검색합니다.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "검색 키워드 또는 쿼리 (예: 'claude code', 'from:username', '#hashtag')",
            },
            "max_results": {
                "type": "integer",
                "description": "가져올 트윗 수 (기본값: 10, 최대: 100)",
            },
        },
        "required": ["query"],
    },
)
async def x_search_recent_tweets(args: Dict[str, Any]) -> Dict[str, Any]:
    """최근 트윗 검색 (7일 이내)"""
    query = args["query"]
    max_results = args.get("max_results", 10)

    # max_results 제한 (10-100)
    max_results = max(10, min(100, max_results))

    try:
        # OAuth 2.0 User Context 토큰 사용
        access_token = await get_valid_access_token()
        if not access_token:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": False,
                                "error": True,
                                "message": "X 인증이 필요합니다. 웹 인터페이스에서 /bot/auth/x/start로 인증을 진행하세요.",
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                ],
                "error": True,
            }

        # v2 API 직접 호출: GET /2/tweets/search/recent
        url = "https://api.twitter.com/2/tweets/search/recent"

        params = {
            "query": query,
            "max_results": max_results,
            "tweet.fields": "created_at,public_metrics,author_id,text",
            "expansions": "author_id",
            "user.fields": "username,name"
        }

        # OAuth 2.0 User Context 토큰 헤더
        headers = {
            "Authorization": f"Bearer {access_token}"
        }

        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()

        if not data.get("data"):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": True,
                                "message": f"'{query}' 검색 결과가 없습니다.",
                                "tweets": [],
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                ]
            }

        # 작성자 정보 매핑
        authors = {}
        if data.get("includes") and data["includes"].get("users"):
            for user in data["includes"]["users"]:
                authors[user["id"]] = {
                    "username": user.get("username", "unknown"),
                    "name": user.get("name", "Unknown")
                }

        tweets = []
        for tweet in data["data"]:
            author_info = authors.get(
                tweet.get("author_id"), {"username": "unknown", "name": "Unknown"}
            )
            tweets.append(
                {
                    "id": tweet["id"],
                    "text": tweet.get("text", ""),
                    "author": author_info,
                    "created_at": tweet.get("created_at", ""),
                    "metrics": tweet.get("public_metrics", {}),
                    "url": f"https://twitter.com/{author_info['username']}/status/{tweet['id']}",
                }
            )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": True,
                            "message": f"'{query}' 검색 결과 {len(tweets)}개의 트윗을 찾았습니다.",
                            "query": query,
                            "count": len(tweets),
                            "tweets": tweets,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ]
        }

    except httpx.HTTPStatusError as e:
        error_detail = ""
        try:
            error_detail = e.response.json()
        except:
            error_detail = e.response.text

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": False,
                            "error": True,
                            "message": f"트윗 검색 실패 (HTTP {e.response.status_code}): {error_detail}",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ],
            "error": True,
        }
    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": False,
                            "error": True,
                            "message": f"트윗 검색 실패: {str(e)}",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ],
            "error": True,
        }


@tool(
    "post_tweet_with_media",
    "사진과 함께 트윗을 작성하여 게시합니다. 중요: 280자 제한이 있으므로 긴 내용은 250자 이내로 요약하세요.",
    {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "트윗 내용 (최대 280자, 여유있게 250자 이내 권장)"},
            "image_path": {
                "type": "string",
                "description": "업로드할 이미지 파일의 절대 경로 (예: FILESYSTEM_BASE_DIR/files/image.png)",
            },
        },
        "required": ["text", "image_path"],
    },
)
async def x_post_tweet_with_media(args: Dict[str, Any]) -> Dict[str, Any]:
    """사진과 함께 트윗 작성 (OAuth 1.0a)"""
    text = args["text"]
    image_path = args["image_path"]

    try:
        # 파일 존재 확인
        if not os.path.exists(image_path):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": False,
                                "error": True,
                                "message": f"이미지 파일을 찾을 수 없습니다: {image_path}",
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                ],
                "error": True,
            }

        # 미디어 업로드 (API v1.1)
        client_v1 = get_x_client_v1()
        media = client_v1.media_upload(filename=image_path)
        media_id = media.media_id

        # 트윗 작성 (API v2)
        client_v2 = get_x_client_v2()
        response = client_v2.create_tweet(text=text, media_ids=[media_id])

        tweet_data = response.data
        tweet_id = tweet_data.get("id")
        tweet_text = tweet_data.get("text")

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": True,
                            "message": "사진과 함께 트윗 게시 완료",
                            "tweet_id": tweet_id,
                            "tweet_text": tweet_text,
                            "media_id": str(media_id),
                            "tweet_url": f"https://twitter.com/i/web/status/{tweet_id}",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ]
        }

    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": False,
                            "error": True,
                            "message": f"트윗 게시 실패: {str(e)}",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ],
            "error": True,
        }


@tool(
    "get_home_timeline",
    "홈 타임라인(팔로우한 사람들의 최근 트윗 피드)을 가져옵니다.",
    {
        "type": "object",
        "properties": {
            "max_results": {
                "type": "integer",
                "description": "가져올 트윗 수 (기본값: 10, 최대: 100)",
            }
        },
    },
)
async def x_get_home_timeline(args: Dict[str, Any]) -> Dict[str, Any]:
    """홈 타임라인 조회 (OAuth 1.0a)"""
    max_results = args.get("max_results", 10)
    max_results = max(5, min(100, max_results))

    try:
        client = get_x_client_v2()

        # 먼저 내 user_id 가져오기
        me = client.get_me()
        my_user_id = me.data.id

        # API v2: GET /2/users/:id/timelines/reverse_chronological
        url = f"https://api.twitter.com/2/users/{my_user_id}/timelines/reverse_chronological"

        params = {
            "max_results": max_results,
            "tweet.fields": "created_at,public_metrics,author_id,text",
            "expansions": "author_id",
            "user.fields": "username,name",
        }

        # OAuth 1.0a 인증 헤더 생성
        from requests_oauthlib import OAuth1
        import requests

        settings = get_settings()
        auth = OAuth1(
            settings.X_API_KEY,
            settings.X_API_SECRET,
            settings.X_ACCESS_TOKEN,
            settings.X_ACCESS_TOKEN_SECRET,
        )

        async with httpx.AsyncClient() as http_client:
            # requests로 서명 생성
            req = requests.Request("GET", url, params=params, auth=auth)
            prepared = req.prepare()

            # httpx로 요청
            response = await http_client.get(prepared.url, headers=prepared.headers)
            response.raise_for_status()
            data = response.json()

        if not data.get("data"):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": True,
                                "message": "홈 타임라인에 트윗이 없습니다.",
                                "tweets": [],
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                ]
            }

        # 작성자 정보 매핑
        authors = {}
        if data.get("includes") and data["includes"].get("users"):
            for user in data["includes"]["users"]:
                authors[user["id"]] = {
                    "username": user.get("username", "unknown"),
                    "name": user.get("name", "Unknown"),
                }

        tweets = []
        for tweet in data["data"]:
            author_info = authors.get(
                tweet.get("author_id"), {"username": "unknown", "name": "Unknown"}
            )
            tweets.append(
                {
                    "id": tweet["id"],
                    "text": tweet.get("text", ""),
                    "author": author_info,
                    "created_at": tweet.get("created_at", ""),
                    "metrics": tweet.get("public_metrics", {}),
                    "url": f"https://twitter.com/{author_info['username']}/status/{tweet['id']}",
                }
            )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": True,
                            "message": f"홈 타임라인에서 {len(tweets)}개의 트윗을 가져왔습니다.",
                            "count": len(tweets),
                            "tweets": tweets,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ]
        }

    except httpx.HTTPStatusError as e:
        error_detail = ""
        try:
            error_detail = e.response.json()
        except:
            error_detail = e.response.text

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": False,
                            "error": True,
                            "message": f"홈 타임라인 조회 실패 (HTTP {e.response.status_code}): {error_detail}",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ],
            "error": True,
        }
    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": False,
                            "error": True,
                            "message": f"홈 타임라인 조회 실패: {str(e)}",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ],
            "error": True,
        }


x_tools = [
    # OAuth 1.0a
    x_post_tweet,
    x_post_tweet_with_media,
    x_get_home_timeline,
    # OAuth 2.0
    x_get_tweet,
    x_get_my_tweets,
    x_search_recent_tweets,
]

def create_x_mcp_server():
    """Claude Code SDK용 X MCP 서버"""
    return create_sdk_mcp_server(name="x", version="1.0.0", tools=x_tools)
