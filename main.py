import os
import shutil
import uuid
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
from extractor import extract_invoice_data
from pathlib import Path
from dotenv import load_dotenv
from database import supabase, BUCKET_NAME
from email_service import download_attachments_and_process
import traceback
import re
from datetime import datetime

MONTHS_ES = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6,
    "JULIO": 7, "AGOSTO": 8, "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10,
    "NOVIEMBRE": 11, "DICIEMBRE": 12
}

def normalize_date_es(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip().upper()
    m = re.match(r"^(\d{1,2})/([A-ZÁÉÍÓÚÑ]+)/(\d{4})$", s)
    if m:
        d, mon, y = m.groups()
        mon = (mon.replace("Á", "A").replace("É", "E")
               .replace("Í", "I").replace("Ó", "O").replace("Ú", "U"))
        mm = MONTHS_ES.get(mon)
        if not mm:
            return None
        return f"{int(y):04d}-{mm:02d}-{int(d):02d}"
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except Exception:
        return None

def normalize_time(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip().upper().replace(".", "")
    try:
        return datetime.strptime(s, "%I:%M %p").strftime("%H:%M:%S")
    except Exception:
        return None

# Variables de Supabase vienen de database.py
# (Se eliminó la inicialización redundante aquí)

app = FastAPI(title="API Asistencia")

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://proyecto-facturas.vercel.app",
    "https://automatizacion-de-escaneo-doc-front.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/invoices")
def get_invoices():
    try:
        response = (
            supabase.table("invoices")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        return response.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listando facturas: {str(e)}")


class InvoiceUpdate(BaseModel):
    nombre_razon_social: str | None = None
    ruc: str | None = None
    fecha: str | None = None
    monto_impuesto: float | None = None
    operacion: str | None = None
    periodo: str | None = None
    importe: float | None = None

@app.put("/invoices/{record_id}")
def update_invoice(record_id: str, item: InvoiceUpdate):
    try:
        update_data = {k: v for k, v in item.model_dump().items() if v is not None}
        if not update_data:
            raise HTTPException(status_code=400, detail="No se enviaron datos para actualizar")

        result = (
            supabase.table("invoices")
            .update(update_data)
            .eq("id", record_id)
            .execute()
        )
        return {"status": "success", "data": result.data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error actualizando factura: {str(e)}")


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    temp_filename = f"temp_{uuid.uuid4()}{ext}"

    try:
        # Ahora aceptamos PDFs e Imágenes
        allowed_extensions = [".pdf", ".jpg", ".jpeg", ".png"]
        if ext not in allowed_extensions:
            raise HTTPException(status_code=400, detail=f"Solo se aceptan: {', '.join(allowed_extensions)}")

        with open(temp_filename, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        data_extracted = extract_invoice_data(temp_filename)
        
        if "error" in data_extracted:
            raise HTTPException(
                status_code=data_extracted.get("status_code", 422),
                detail=data_extracted["error"],
            )

        storage_filename = f"{uuid.uuid4()}{ext}"
        content_type = file.content_type or "application/octet-stream"
        
        with open(temp_filename, "rb") as f:
            supabase.storage.from_(BUCKET_NAME).upload(
                path=storage_filename,
                file=f,
                file_options={"content-type": content_type},
            )

        public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(storage_filename)
        records = data_extracted.get("records", [])

        inserted = 0
        for record in records:
            # Adaptamos el payload a los campos de factura
            # Nota: Asegúrate de que tu tabla en Supabase tenga estas columnas
            payload = {
                "nombre_razon_social": record.get("nombre_razon_social"),
                "ruc": record.get("ruc"),
                "fecha": record.get("fecha"),
                "monto_impuesto": record.get("monto_impuesto"),
                "operacion": record.get("operacion"),
                "periodo": record.get("periodo"),
                "importe": record.get("importe"),
                "file_url": public_url,
            }

            # Intentamos insertar en la tabla. 
            # Si aún no has cambiado el nombre de la tabla en Supabase, 
            # podrías seguir usando 'attendance' temporalmente o cambiarlo a 'invoices'
            supabase.table("invoices").insert(payload).execute()
            inserted += 1

        return {"status": "success", "records_saved": inserted}

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error en upload: {str(e)}")
    finally:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

@app.post("/sync-email")
async def sync_email():
    try:
        # Ejecutamos la función de email_service
        download_attachments_and_process()
        return {"status": "success", "message": "Sincronización de correo completada"}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error en la sincronización: {str(e)}")

# ---- EJECUTAR SERVIDOR ----
import uvicorn

if __name__ == "__main__":
    uvicorn.run("main:app", reload=True)