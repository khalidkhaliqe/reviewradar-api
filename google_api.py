"""
Google Business Profile API integration.

To use this module, you need:
1. A Google Cloud project with the Business Profile API enabled
2. OAuth 2.0 credentials (Client ID + Client Secret)
3. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables

Flow:
- User clicks "Connect Google" in dashboard
- Redirect to Google OAuth consent screen
- Google redirects back with auth code
- We exchange code for access_token + refresh_token
- We fetch reviews using the Business Profile API
"""
import os
import httpx
from typing import Optional
from datetime import datetime

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "YOUR_GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "YOUR_GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/google/callback")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_BUSINESS_API = "https://mybusinessbusinessinformation.googleapis.com/v1"
GOOGLE_REVIEWS_API = "https://mybusiness.googleapis.com/v4"

SCOPES = [
    "https://www.googleapis.com/auth/business.manage",
]


def get_google_auth_url(state: str) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{GOOGLE_AUTH_URL}?{query}"


async def exchange_code_for_tokens(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": GOOGLE_REDIRECT_URI,
        })
        resp.raise_for_status()
        return resp.json()


async def refresh_access_token(refresh_token: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        return resp.json()["access_token"]


async def get_accounts(access_token: str) -> list:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GOOGLE_BUSINESS_API}/accounts",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        resp.raise_for_status()
        return resp.json().get("accounts", [])


async def get_locations(access_token: str, account_id: str) -> list:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GOOGLE_BUSINESS_API}/{account_id}/locations",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        resp.raise_for_status()
        return resp.json().get("locations", [])


async def get_reviews(access_token: str, account_id: str, location_id: str) -> list:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GOOGLE_REVIEWS_API}/{account_id}/{location_id}/reviews",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("reviews", [])


async def reply_to_review(access_token: str, review_name: str, reply_text: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{GOOGLE_REVIEWS_API}/{review_name}/reply",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"comment": reply_text}
        )
        resp.raise_for_status()
        return resp.json()


def parse_google_review(review: dict, user_id: int) -> dict:
    """Parse a Google review API response into our Review model fields."""
    rating_map = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
    return {
        "user_id": user_id,
        "platform": "google",
        "external_id": review.get("reviewId") or review.get("name"),
        "author_name": review.get("reviewer", {}).get("displayName", "Anoniem"),
        "rating": rating_map.get(review.get("starRating"), None),
        "text": review.get("comment", ""),
        "review_date": review.get("createTime"),
        "reply": review.get("reviewReply", {}).get("comment") if review.get("reviewReply") else None,
    }
