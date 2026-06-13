import os
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from dotenv import load_dotenv

load_dotenv()

OAUTH_FILE      = os.getenv("GOOGLE_OAUTH_FILE", "client_secret.json")
TOKEN_FILE      = "token.json"
DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _service():
    creds = None

    if Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(OAUTH_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_to_drive(file_path: str) -> str:
    """Sube un archivo a Google Drive y retorna el link público."""
    path = Path(file_path)
    mime_map = {".pdf": "application/pdf", ".xml": "application/xml"}
    mime = mime_map.get(path.suffix.lower(), "application/octet-stream")

    metadata = {"name": path.name, "parents": [DRIVE_FOLDER_ID]}
    media = MediaFileUpload(str(path), mimetype=mime, resumable=True)

    file = _service().files().create(
        body=metadata, media_body=media, fields="id,webViewLink"
    ).execute()

    return file.get("webViewLink", "")
