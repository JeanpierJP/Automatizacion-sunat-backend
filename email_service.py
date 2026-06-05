import imaplib
import email
import os
import uuid
import traceback
from pathlib import Path
from dotenv import load_dotenv
from extractor import extract_invoice_data
from database import supabase, BUCKET_NAME

load_dotenv()

# Configuración desde .env
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_IMAP = os.getenv("EMAIL_IMAP")

def download_attachments_and_process():
    print(f"Conectando a {EMAIL_IMAP}...")
    try:
        # 1. Conexión al correo
        mail = imaplib.IMAP4_SSL(EMAIL_IMAP)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        # 2. Buscar correos (puedes filtrar por fecha o remitente aquí)
        # "ALL" busca todos. Para retroactivos podrías usar "SINCE 01-Jan-2024"
        status, messages = mail.search(None, 'ALL')
        if status != 'OK':
            print("No se pudieron buscar correos.")
            return

        mail_ids = messages[0].split()
        print(f"Se encontraron {len(mail_ids)} correos. Empezando procesamiento...")

        # Procesamos de los más nuevos a los más viejos
        for mail_id in reversed(mail_ids):
            status, data = mail.fetch(mail_id, '(RFC822)')
            if status != 'OK': continue

            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            for part in msg.walk():
                if part.get_content_maintype() == 'multipart': continue
                if part.get('Content-Disposition') is None: continue

                filename = part.get_filename()
                if not filename: continue

                # Solo PDFs e Imágenes
                ext = Path(filename).suffix.lower()
                if ext in ['.pdf', '.jpg', '.jpeg', '.png']:
                    print(f"Procesando adjunto: {filename}")
                    
                    # Guardar temporalmente
                    temp_path = f"temp_{uuid.uuid4()}{ext}"
                    with open(temp_path, "wb") as f:
                        f.write(part.get_payload(decode=True))

                    try:
                        # Mandar a la IA
                        data_extracted = extract_invoice_data(temp_path)
                        
                        if "error" not in data_extracted:
                            # Subir a Supabase Storage
                            storage_filename = f"{uuid.uuid4()}{ext}"
                            with open(temp_path, "rb") as f:
                                supabase.storage.from_(BUCKET_NAME).upload(
                                    path=storage_filename,
                                    file=f,
                                    file_options={"content-type": part.get_content_type()},
                                )
                            
                            public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(storage_filename)
                            
                            # Guardar en Tabla Invoices
                            records = data_extracted.get("records", [])
                            for record in records:
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
                                supabase.table("invoices").insert(payload).execute()
                                print(f"Factura guardada: {record.get('operacion')}")
                        else:
                            print(f"Error IA en {filename}: {data_extracted['error']}")

                    except Exception as e:
                        print(f"Error procesando {filename}: {str(e)}")
                    finally:
                        if os.path.exists(temp_path):
                            os.remove(temp_path)

        mail.logout()
        print("Proceso terminado correctamente.")

    except Exception as e:
        traceback.print_exc()
        print(f"Error de conexión: {str(e)}")

if __name__ == "__main__":
    download_attachments_and_process()
