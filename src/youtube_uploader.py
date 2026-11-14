"""
YouTube Uploader — headless OAuth token refresh + resumable upload
via YouTube Data API v3. Uploads as 'private' by default
(required for unverified API projects — user manually publishes in Studio).
"""

import json
import os
import sys

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = "yt_token.json"
SECRETS_FILE = "client_secrets.json"


def _load_credentials() -> Credentials:
    if not os.path.exists(TOKEN_FILE):
        raise FileNotFoundError(f"Token file not found: {TOKEN_FILE}")
    if not os.path.exists(SECRETS_FILE):
        raise FileNotFoundError(f"Client secrets not found: {SECRETS_FILE}")

    with open(TOKEN_FILE) as f:
        token_data = json.load(f)
    with open(SECRETS_FILE) as f:
        secrets = json.load(f)

    web = secrets.get("web") or secrets.get("installed", {})
    client_id = web.get("client_id")
    client_secret = web.get("client_secret")

    creds = Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )

    # Refresh if expired
    if creds.expired or not creds.valid:
        print("[YT] Refreshing access token...")
        creds.refresh(Request())
        # Save updated token
        with open(TOKEN_FILE, "w") as f:
            json.dump({
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
            }, f)
        print("[YT] Token refreshed.")

    return creds


def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list,
    category_id: str = "25",       # 25 = News & Politics
    privacy_status: str = "private",  # Always start private for unverified projects
) -> str:
    """
    Upload video to YouTube. Returns the video ID.
    Privacy is 'private' by default — manually set to Public in YouTube Studio,
    OR wait for your Google Cloud project to pass the OAuth verification audit.
    """
    print(f"[YT] Loading credentials...")
    creds = _load_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:500],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024,  # 10 MB chunks
    )

    print(f"[YT] Starting upload: '{title}'")
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                print(f"[YT] Upload progress: {pct}%")
        except HttpError as e:
            if e.resp.status in [500, 502, 503, 504]:
                print(f"[YT] Retryable error {e.resp.status}, retrying...")
                continue
            raise

    video_id = response["id"]
    print(f"[YT] ✅ Upload complete! Video ID: {video_id}")
    print(f"[YT] 🔒 Video is PRIVATE — go to YouTube Studio to publish:")
    print(f"[YT]    https://studio.youtube.com/video/{video_id}/edit")
    return video_id


def upload_thumbnail(video_id: str, thumbnail_path: str) -> bool:
    """
    Upload a custom thumbnail.
    Requires youtube (not just youtube.upload) scope — skipped gracefully
    if the token doesn't have it.  User can set thumbnail manually in Studio.
    The thumbnail file is kept in output/ as a reference.
    """
    if not thumbnail_path or not os.path.exists(thumbnail_path):
        print(f"[YT] No thumbnail file at {thumbnail_path}, skipping")
        return False

    print(f"[YT] Uploading thumbnail for video {video_id}...")
    try:
        creds   = _load_credentials()
        youtube = build("youtube", "v3", credentials=creds)
        media   = MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=media,
        ).execute()
        print(f"[YT] ✅ Thumbnail uploaded!")
        return True
    except HttpError as e:
        err_str = str(e)
        if "insufficientPermissions" in err_str or "invalid_scope" in err_str:
            print(f"[YT] ⚠️  Token scope: thumbnail upload needs youtube scope.")
            print(f"[YT]    Thumbnail saved at: {thumbnail_path}")
            print(f"[YT]    Upload it manually in YouTube Studio → Content → {video_id}")
            return False
        if "thumbnailsNotAllowed" in err_str or "forbidden" in err_str.lower():
            print(f"[YT] ⚠️  Custom thumbnails not yet enabled for this channel.")
            print(f"[YT]    Thumbnail saved at: {thumbnail_path}")
            return False
        print(f"[YT] Thumbnail upload error (non-fatal): {e}")
        return False
    except Exception as e:
        print(f"[YT] Thumbnail upload failed (non-fatal): {e}")
        return False


if __name__ == "__main__":
    vid_id = upload_video(
        video_path="output/final_video.mp4",
        title="Test Upload",
        description="Test description",
        tags=["test"],
    )
    print(f"Done: {vid_id}")
