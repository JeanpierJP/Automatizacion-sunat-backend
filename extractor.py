from google import genai
import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

api_key = os.getenv("GEMINI_API_KEY")
print("GEMINI DEBUG:", (api_key or "VACIO")[:12])  # temporal

if not api_key:
    raise ValueError("No se encontró GEMINI_API_KEY")

client = genai.Client(api_key=api_key)

def extract_invoice_data(file_path):
    prompt = """
Analiza este comprobante de pago peruano y extrae EXACTAMENTE estos 4 campos.

Campos a extraer:
1) ruc: RUC del emisor (11 dígitos). Busca junto al texto "RUC:" o "R.U.C.".
2) tipo_comprobante: Código SUNAT del tipo de comprobante.
   - "01" = Factura
   - "03" = Boleta de Venta
   - "07" = Nota de Crédito
   - "08" = Nota de Débito
   Si no puedes determinarlo, usa "01".
3) serie: La serie del comprobante. Es la parte ANTES del guion (ej: si dice "F001-00001234", la serie es "F001").
4) numero_comprobante: El número correlativo. Es la parte DESPUÉS del guion (ej: si dice "F001-00001234", el número es "00001234").

Reglas:
- Si el documento es una foto, usa OCR avanzado para no perder dígitos.
- Devuelve SOLO un JSON válido, sin texto extra.

Formato de salida:
{
  "records": [
    {
      "ruc": "",
      "tipo_comprobante": "",
      "serie": "",
      "numero_comprobante": ""
    }
  ]
}
"""

    try:
        ext = Path(file_path).suffix.lower()
        if ext == ".txt":
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text_content = f.read()
            contents = [prompt + f"\n\nContenido del archivo TXT:\n{text_content}"]
        else:
            uploaded = client.files.upload(file=file_path)
            contents = [prompt, uploaded]

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config={"response_mime_type": "application/json"},
        )

        raw = (response.text or "").strip()
        if not raw:
            return {"error": "Gemini devolvió respuesta vacía", "status_code": 502}

        return json.loads(raw)

    except Exception as e:
        return {"error": str(e), "status_code": 422}
