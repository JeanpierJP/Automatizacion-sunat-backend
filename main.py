import os
import shutil
import uuid
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client
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
    "https://proyecto-comprobantes.vercel.app",
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


@app.delete("/invoices")
def clear_invoices():
    try:
        supabase.table("invoices").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        return {"status": "success", "message": "Tabla invoices limpiada"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error limpiando invoices: {str(e)}")


class InvoiceUpdate(BaseModel):
    ruc: str | None = None
    tipo_comprobante: str | None = None
    serie: str | None = None
    numero_comprobante: str | None = None

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
    import pandas as pd
    ext = Path(file.filename).suffix.lower()
    temp_filename = f"temp_{uuid.uuid4()}{ext}"

    allowed_extensions = [".pdf", ".jpg", ".jpeg", ".png", ".txt", ".xlsx"]
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Solo se aceptan: {', '.join(allowed_extensions)}")

    try:
        with open(temp_filename, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # ── Excel: lectura directa de columnas, sin Gemini ──
        if ext == ".xlsx":
            df = pd.read_excel(temp_filename, header=0)
            inserted = 0
            for _, row in df.iterrows():
                try:
                    tipo   = str(row.iloc[6]).strip()
                    serie  = str(row.iloc[7]).strip()
                    numero = str(row.iloc[9]).strip()
                    ruc    = str(row.iloc[12]).strip()
                    if not ruc or ruc in ("nan", "RUC", ""):
                        continue
                    tipo = tipo.zfill(2) if tipo.isdigit() else tipo
                    supabase.table("invoices").insert({
                        "ruc":                ruc,
                        "tipo_comprobante":   tipo,
                        "serie":              serie,
                        "numero_comprobante": numero,
                    }).execute()
                    inserted += 1
                except Exception:
                    continue
            return {"status": "success", "records_saved": inserted}

        # ── TXT / PDF / Imagen: extracción con Gemini ──
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
            supabase.table("invoices").insert({
                "ruc":                record.get("ruc"),
                "tipo_comprobante":   record.get("tipo_comprobante"),
                "serie":              record.get("serie"),
                "numero_comprobante": record.get("numero_comprobante"),
                "file_url":           public_url,
            }).execute()
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

@app.get("/sunat-comprobantes")
def get_sunat_comprobantes():
    try:
        response = (
            supabase.table("sunat_comprobantes")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        return response.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listando sunat_comprobantes: {str(e)}")


@app.post("/run-sunat")
async def run_sunat():
    try:
        from sunat_scraper import run_sunat_scraper
        from drive_service import upload_to_drive
        import asyncio

        # Leer comprobantes de la tabla invoices (ya extraídos por OCR)
        rows = supabase.table("invoices").select(
            "ruc, tipo_comprobante, serie, numero_comprobante"
        ).execute()

        comprobantes = [
            {
                "ruc_emisor": r["ruc"],
                "tipo":       r["tipo_comprobante"],
                "serie":      r["serie"],
                "numero":     r["numero_comprobante"],
            }
            for r in (rows.data or [])
            if r.get("ruc") and r.get("serie") and r.get("numero_comprobante")
        ]

        if not comprobantes:
            raise HTTPException(status_code=400, detail="No hay comprobantes en la tabla invoices para procesar")

        guardados = []
        errores   = []

        def procesar_uno(r):
            """Sube PDF y XML a Drive por separado y guarda en sunat_comprobantes."""
            if "error" in r:
                errores.append(r)
                print(f"  [SKIP] {r.get('serie')}-{r.get('numero')}: {r['error']}")
                return

            drive_pdf_url = ""
            drive_xml_url = ""

            try:
                if r.get("pdf"):
                    drive_pdf_url = upload_to_drive(r["pdf"])
                    print(f"  [Drive] PDF: {drive_pdf_url}")
            except Exception as e:
                print(f"  [Drive] Error PDF: {e}")

            try:
                if r.get("xml"):
                    drive_xml_url = upload_to_drive(r["xml"])
                    print(f"  [Drive] XML: {drive_xml_url}")
            except Exception as e:
                print(f"  [Drive] Error XML: {e}")

            payload = {
                "ruc":                r["ruc_emisor"],
                "tipo_comprobante":   r.get("tipo", "01"),
                "serie":              r["serie"],
                "numero_comprobante": r["numero"],
                "drive_pdf_url":      drive_pdf_url,
                "drive_xml_url":      drive_xml_url,
            }
            supabase.table("sunat_comprobantes").insert(payload).execute()
            guardados.append(r)
            print(f"  [BD] Guardado en sunat_comprobantes: {r['serie']}-{r['numero']}")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: run_sunat_scraper(comprobantes, on_result=procesar_uno)
        )

        # Enviar notificación por correo
        from email_service import send_sunat_notification
        from datetime import date
        rows_sunat = supabase.table("sunat_comprobantes").select("drive_pdf_url, drive_xml_url").execute()
        data_sunat = rows_sunat.data or []
        send_sunat_notification(
            total   = len(guardados),
            pdfs    = sum(1 for r in data_sunat if r.get("drive_pdf_url")),
            xmls    = sum(1 for r in data_sunat if r.get("drive_xml_url")),
            errores = len(errores),
            fecha   = date.today().strftime("%d/%m/%Y"),
        )

        return {
            "status":          "success",
            "guardados":       len(guardados),
            "errores":         len(errores),
            "detalle_errores": errores,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error en run-sunat: {str(e)}")


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