import os
import json
import base64
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from dotenv import load_dotenv

load_dotenv()

DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
SCOPES = ["https://www.googleapis.com/auth/drive"]
SA_FILE = "comprobauto-backend-4f05bba3a32f.json"


def _service():
    if Path(SA_FILE).exists():
        creds = service_account.Credentials.from_service_account_file(SA_FILE, scopes=SCOPES)
    else:
        sa_b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not sa_b64:
            raise ValueError("No se encontró credencial de cuenta de servicio")
        info = json.loads(base64.b64decode(sa_b64).decode())
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
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
