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
Analiza este documento (Factura o Recibo) y extrae la siguiente información técnica. 
Es vital que seas preciso con los montos y el RUC.

Extrae estos campos:
1) nombre_razon_social: El nombre de la empresa o persona que emite la factura.
2) ruc: El número de RUC (11 dígitos para Perú).
3) monto_impuesto: El valor del impuesto (IGV/IVA). Si no hay, pon 0.
4) operacion: El número de la factura o comprobante (ej: F001-000123).
5) fecha: La fecha de emisión en formato YYYY-MM-DD.
6) periodo: El periodo tributario si aparece (ej: 05-2024), si no, infiérelo de la fecha.
7) importe: El monto total a pagar.

Reglas:
- Si el documento es una foto, usa OCR avanzado para no perder dígitos.
- Devuelve SOLO un JSON válido.
- Si hay varios registros en una sola imagen (poco común), devuelve una lista en 'records'.

Formato de salida:
{
  "records": [
    {
      "nombre_razon_social": "",
      "ruc": "",
      "monto_impuesto": 0.0,
      "operacion": "",
      "fecha": "YYYY-MM-DD",
      "periodo": "MM-YYYY",
      "importe": 0.0
    }
  ]
}
"""

    try:
        uploaded = client.files.upload(file=file_path)
        response = client.models.generate_content(
            model="gemini-2.5-flash", # Usando la versión más estable y rápida
            contents=[prompt, uploaded],
            config={"response_mime_type": "application/json"},
        )

        raw = (response.text or "").strip()
        if not raw:
            return {"error": "Gemini devolvió respuesta vacía", "status_code": 502}

        return json.loads(raw)

    except Exception as e:
        return {"error": str(e), "status_code": 422}
