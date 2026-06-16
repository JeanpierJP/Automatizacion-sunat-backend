"""
Corre este script UNA SOLA VEZ en tu PC para generar token.json.
Abre el navegador — inicia sesión con l66644948@gmail.com.
"""
from google_auth_oauthlib.flow import InstalledAppFlow
import glob, os

SCOPES = ["https://www.googleapis.com/auth/drive"]

# Busca el client_secret automáticamente
archivos = glob.glob("client_secret*.json")
if not archivos:
    raise FileNotFoundError("No se encontró client_secret*.json en esta carpeta")

oauth_file = archivos[0]
print(f"Usando: {oauth_file}")

flow = InstalledAppFlow.from_client_secrets_file(oauth_file, SCOPES)
creds = flow.run_local_server(port=0)

with open("token.json", "w") as f:
    f.write(creds.to_json())

print("\ntoken.json generado. Ahora corre esto en PowerShell:")
print('[Convert]::ToBase64String([IO.File]::ReadAllBytes("token.json"))')
