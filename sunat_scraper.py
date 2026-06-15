import os
import pandas as pd
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from dotenv import load_dotenv

load_dotenv()

SUNAT_RUC  = os.getenv("SUNAT_RUC")
SUNAT_USER = os.getenv("SUNAT_USER")
SUNAT_PASS = os.getenv("SUNAT_PASS")
EXCEL_PATH = os.getenv("EXCEL_PATH", "Compras Abril 2026.xlsx")
DOWNLOAD_DIR = "downloads"
IS_PROD = os.getenv("RENDER") == "true" or os.getenv("K_SERVICE") is not None

LOGIN_URL = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm"

# Mapeo tipo código → texto en dropdown SUNAT
TIPO_TEXTO = {
    "01": "Factura",
    "03": "Boleta de Venta",
    "07": "Nota de Crédito",
    "08": "Nota de Débito",
    "09": "Guía de Remisión",
    "12": "Ticket",
}


def leer_excel() -> list[dict]:
    """Lee el Excel y retorna lista de comprobantes con los 4 campos."""
    df = pd.read_excel(EXCEL_PATH, header=0)
    rows = []
    for _, row in df.iterrows():
        try:
            tipo   = str(row.iloc[6]).strip()   # Columna G
            serie  = str(row.iloc[7]).strip()   # Columna H
            numero = str(row.iloc[9]).strip()   # Columna J
            ruc    = str(row.iloc[12]).strip()  # Columna M

            # Saltar filas vacías o encabezados
            if not ruc or ruc in ("nan", "RUC", ""):
                continue
            # Normalizar tipo a 2 dígitos
            tipo = tipo.zfill(2) if tipo.isdigit() else tipo

            rows.append({"tipo": tipo, "serie": serie, "numero": numero, "ruc_emisor": ruc})
        except Exception:
            continue
    return rows

URL = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm?pestana=*&agrupacion=*"

def cerrar_popup(page):
    for i in range(3):
        try:
            page.wait_for_selector("#btnFinalizarValidacionDatos", timeout=40000)  # era 20000
            page.click("#btnFinalizarValidacionDatos")
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            page.wait_for_timeout(3000)  # era 1500
            print(f"Popup {i+1} cerrado")
        except PWTimeout:
            break  # No hay más popups


def login(page):
    for intento in range(3):
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            break
        except Exception as e:
            print(f"  Login intento {intento+1} fallido: {e}")
            if intento == 2:
                raise
            page.wait_for_timeout(5000)
    page.fill("#txtRuc", SUNAT_RUC)
    page.fill("#txtUsuario", SUNAT_USER)
    page.fill("#txtContrasena", SUNAT_PASS)
    page.click("#btnAceptar")

    # Esperar que complete el OAuth y llegue al menú
    page.wait_for_url("**/e-menu.sunat.gob.pe/**", timeout=60000)
    page.wait_for_load_state("domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    cerrar_popup(page)
    print("Login SUNAT OK")


def cerrar_modal_campana(page):
    """Fuerza el cierre de todos los modales de campaña que bloquen el menú."""
    try:
        page.evaluate("""
            () => {
                document.querySelectorAll('[id*="Campana"], [id*="campana"], [id*="Modal"], [id*="modal"]')
                    .forEach(el => el.style.display = 'none');
            }
        """)
        page.wait_for_timeout(500)
        print("Modales campaña cerrados")
    except Exception:
        pass


def ir_a_comprobantes(page):
    # No llamar cerrar_popup aquí, ya se cerró en login
    cerrar_modal_campana(page)

    print(f"  [DEBUG] URL: {page.url}")
    print(f"  [DEBUG] Titulo: {page.title()}")
    print(f"  [DEBUG] body text (primeros 1000): {page.inner_text('body')[:1000]}")
    print(f"  [DEBUG] h4 tags: {page.eval_on_selector_all('h4', 'els => els.map(e => e.innerText)')}")

    # Esperar explícitamente que el menú cargue
    try:
        page.wait_for_selector("h4:has-text('Empresas')", timeout=60000)
    except PWTimeout:
        print("  [DEBUG] No apareció h4 Empresas, reintentando popup...")
        cerrar_popup(page)
        cerrar_modal_campana(page)
        page.wait_for_selector("h4:has-text('Empresas')", timeout=30000)

    print(f"  [DEBUG] h4 Empresas count: {page.locator('h4:has-text(\"Empresas\")').count()}")

    page.click("h4:has-text('Empresas')", timeout=30000)
    page.wait_for_timeout(2000)

    page.click(".spanNivelDescripcion:has-text('Comprobantes de pago')", timeout=20000)
    page.wait_for_timeout(1500)

    page.locator(".spanNivelDescripcion:has-text('Comprobantes de Pago')").nth(1).click(timeout=20000)
    page.wait_for_timeout(1500)

    page.click(".spanNivelDescripcion:has-text('Consulta de Comprobantes de Pago')", timeout=20000)
    page.wait_for_timeout(1500)

    page.click(".spanNivelDescripcion:has-text('Nueva Consulta de comprobantes de pago')", timeout=20000)
    page.wait_for_timeout(5000)
    print("Formulario de consulta cargado")


def get_form_frame(page):
    """Retorna el frame que contiene el formulario (puede estar en iframe Angular)."""
    page.wait_for_timeout(2000)
    for frame in page.frames:
        try:
            if frame.locator("input[name='rucEmisor']").count() > 0:
                return frame
        except Exception:
            continue
    return page  # fallback: form está directo en la página


def seleccionar_tipo_dropdown(frame, tipo_codigo: str):
    """Selecciona el tipo en el dropdown PrimeNG de SUNAT."""
    tipo_texto = TIPO_TEXTO.get(tipo_codigo, "Factura")
    # Abrir dropdown
    frame.click(".p-dropdown", timeout=8000)
    frame.wait_for_timeout(600)
    # Seleccionar opción por texto
    frame.click(f".p-dropdown-item:has-text('{tipo_texto}')", timeout=6000)
    frame.wait_for_timeout(400)
    print(f"  Tipo seleccionado: {tipo_texto}")


def descargar_comprobante(page, comp: dict, download_dir: str) -> dict:
    base = f"{comp['serie']}-{comp['numero']}"
    resultado = {
        "ruc_emisor": comp["ruc_emisor"],
        "tipo": comp["tipo"],
        "serie": comp["serie"],
        "numero": comp["numero"],
    }

    try:
        frame = get_form_frame(page)

        # 0. Seleccionar "Recibido"
        try:
            frame.click("label[for='recibido']", timeout=5000)
            frame.wait_for_timeout(400)
        except PWTimeout:
            pass

        # 1. RUC Emisor
        frame.fill("input[name='rucEmisor']", comp["ruc_emisor"], timeout=8000)

        # 2. Tipo de comprobante (dropdown PrimeNG)
        seleccionar_tipo_dropdown(frame, comp["tipo"])

        # 3. Serie
        frame.fill("input[name='serieComprobante']", comp["serie"], timeout=5000)

        # 4. Número
        frame.fill("input[name='numeroComprobante']", comp["numero"], timeout=5000)

        # 5. Click Consultar
        frame.click("button.boton-primary", timeout=8000)
        frame.wait_for_timeout(4000)
        print(f"  Consulta enviada para {base}")

        # 6. Verificar si hay resultados
        sin_resultados = False
        for texto_vacio in ["No se encontraron", "no existen", "Sin resultados", "0 registro"]:
            try:
                if frame.locator(f"text={texto_vacio}").count() > 0:
                    sin_resultados = True
                    break
            except Exception:
                pass

        if sin_resultados:
            resultado["error"] = "Comprobante no encontrado en SUNAT"
            print(f"  No encontrado: {base}")
        else:
            # 7. Descargar PDF
            try:
                pdf_path = Path(download_dir) / f"{base}.pdf"
                with page.expect_download(timeout=20000) as dl:
                    frame.click(
                        "button[ngbtooltip*='PDF'], button:has(.fas.fa-file-pdf)",
                        timeout=8000
                    )
                dl.value.save_as(str(pdf_path))
                resultado["pdf"] = str(pdf_path)
                print(f"  PDF: {pdf_path.name}")
            except Exception as e:
                resultado["pdf_error"] = str(e)
                print(f"  Sin PDF: {e}")

            # 8. Descargar XML
            try:
                xml_path = Path(download_dir) / f"{base}.xml"
                with page.expect_download(timeout=20000) as dl:
                    frame.click(
                        "button[ngbtooltip='Descargar XML'], button:has(.far.fa-file-code)",
                        timeout=8000
                    )
                dl.value.save_as(str(xml_path))
                resultado["xml"] = str(xml_path)
                print(f"  XML: {xml_path.name}")
            except Exception as e:
                resultado["xml_error"] = str(e)
                print(f"  Sin XML: {e}")

        # 9. Cerrar modal de resultado (×) para volver al formulario
        try:
            frame.click("button.close-without-header", timeout=4000)
            frame.wait_for_timeout(600)
            print("  Modal cerrado")
        except PWTimeout:
            pass

        # 10. Limpiar formulario para la siguiente búsqueda
        try:
            frame.click("button:has-text('Limpiar'), button:has-text('Nueva Consulta')", timeout=4000)
            frame.wait_for_timeout(800)
        except PWTimeout:
            # Fallback: limpiar campos manualmente
            try:
                frame.fill("input[name='rucEmisor']", "")
                frame.fill("input[name='serieComprobante']", "")
                frame.fill("input[name='numeroComprobante']", "")
            except Exception:
                pass

    except PWTimeout as e:
        resultado["error"] = f"Timeout: {e}"
        print(f"  ERROR timeout en {base}")
    except Exception as e:
        resultado["error"] = str(e)
        print(f"  ERROR en {base}: {e}")

    return resultado


def run_sunat_scraper(comprobantes: list[dict], on_result=None) -> list[dict]:
    """
    comprobantes: lista de dicts con keys ruc_emisor, tipo, serie, numero.
    on_result: callback(resultado_dict) llamado inmediatamente después
    de descargar cada comprobante (para subir a Drive y guardar en BD).
    """
    Path(DOWNLOAD_DIR).mkdir(exist_ok=True)
    print(f"Comprobantes a procesar: {len(comprobantes)}")
    resultados = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=IS_PROD,
            slow_mo=0 if IS_PROD else 400,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ] if IS_PROD else [],
        )
        context = browser.new_context(
            accept_downloads=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        login(page)
        ir_a_comprobantes(page)

        for i, comp in enumerate(comprobantes, 1):
            print(f"\n[{i}/{len(comprobantes)}] {comp['serie']}-{comp['numero']} | RUC: {comp['ruc_emisor']}")
            res = descargar_comprobante(page, comp, DOWNLOAD_DIR)
            resultados.append(res)
            if on_result:
                try:
                    on_result(res)
                except Exception as e:
                    print(f"  Error en callback post-descarga: {e}")

        browser.close()

    return resultados


if __name__ == "__main__":
    comprobantes = leer_excel()
    resultados = run_sunat_scraper(comprobantes)
    ok    = [r for r in resultados if "error" not in r]
    error = [r for r in resultados if "error" in r]
    print(f"\nDescargados: {len(ok)} | Errores: {len(error)}")
