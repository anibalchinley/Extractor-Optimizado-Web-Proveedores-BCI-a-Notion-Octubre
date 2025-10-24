import os
import json
import traceback
from flask import Flask, Response, stream_with_context
from scraper import (
    setup_driver,
    login_to_bci,
    manejar_popup_bienvenida,
    scrape_full_data
)
from notion_manager import NotionManager
from dotenv import load_dotenv

app = Flask(__name__)

def _run_scraping(driver, stats, siniestros_list):
    """
    Runs the scraping process, yields progress, and populates the siniestros_list.
    """
    yield "--- Iniciando sondeo de siniestros para todas las compañías...\n".encode('utf-8')
    for siniestro in scrape_full_data(driver):
        siniestros_list.append(siniestro)
        progress_message = (
            f"Siniestro encontrado: {siniestro.get('NumeroSiniestro')} "
            f"({siniestro.get('Compania')})\n"
        )
        yield progress_message.encode('utf-8')
    
    stats["extraidos"] = len(siniestros_list)
    yield f"--- Sondeo finalizado. Se encontraron {stats['extraidos']} siniestros en total.\n".encode('utf-8')

def _run_notion_integration(siniestros_extraidos):
    """
    Runs the Notion integration process and yields progress updates.
    """
    yield "\n--- Iniciando integración con Notion...\n".encode('utf-8')
    notion_token = os.getenv("NOTION_TOKEN")
    db_ids = {
        "DATABASE_ID_SINIESTROS": os.getenv("DATABASE_ID_SINIESTROS"),
        "DATABASE_ID_PATENTES": os.getenv("DATABASE_ID_PATENTES"),
        "DATABASE_ID_CLIENTES": os.getenv("DATABASE_ID_CLIENTES"),
    }
    notion_manager = NotionManager(notion_token, db_ids)
    notion_manager.process_and_insert_siniestros(siniestros_extraidos)
    yield "--- Integración con Notion finalizada.\n".encode('utf-8')

@app.route('/run', methods=['POST'])
def trigger_run():
    """
    This endpoint triggers the automation and streams the results.
    """
    def generate():
        load_dotenv()
        yield "--- Iniciando proceso completo...\n".encode('utf-8')
        
        stats = {"extraidos": 0, "error": None}
        driver = None
        siniestros_extraidos = []
        try:
            driver = setup_driver()
            if not driver:
                raise Exception("Fallo al iniciar el driver.")

            yield "--- Driver inicializado. Realizando login...\n".encode('utf-8')
            api_key_2captcha = os.getenv("API_KEY_2CAPTCHA")
            user = os.getenv("BCI_USER")
            password = os.getenv("BCI_PASS")

            if not login_to_bci(driver, user, password, api_key_2captcha):
                raise Exception("Fallo en el login.")

            yield "--- Login exitoso. Iniciando secuencia de operaciones...\n".encode('utf-8')

            # Run scraping and notion integration, yielding progress from them
            for progress_update in _run_scraping(driver, stats, siniestros_extraidos):
                yield progress_update
            
            if siniestros_extraidos:
                for progress_update in _run_notion_integration(siniestros_extraidos):
                    yield progress_update
                
                # Yield the final extracted data for verification
                yield b"\n--- DATOS EXTRAIDOS (JSON) ---\n"
                yield (json.dumps(siniestros_extraidos, indent=2, ensure_ascii=False) + "\n").encode('utf-8')
                yield b"--- FIN DE DATOS EXTRAIDOS ---"

        except Exception as e:
            error_message = f"--- Error catastrófico: {e}\n{traceback.format_exc()}"
            yield error_message.encode('utf-8')
            stats["error"] = str(e)
        finally:
            if driver:
                yield "--- Cerrando el navegador del scraper...\n".encode('utf-8')
                driver.quit()
            yield b"\n--- PROCESO FINALIZADO ---\n"
            yield (json.dumps(stats, indent=4, ensure_ascii=False) + "\n").encode('utf-8')
        
    return Response(stream_with_context(generate()), mimetype='text/plain')

if __name__ == '__main__':
    print(">>> Iniciando servidor Flask para pruebas locales. Escuchando en http://0.0.0.0:8000")
    app.run(host='0.0.0.0', port=8000)
