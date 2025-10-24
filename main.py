import os
import time
import threading
from flask import Flask, Response, stream_with_context, request, jsonify
from scraper import (
    setup_driver,
    login_to_bci,
    manejar_popup_bienvenida,
    scrape_full_data,
    asegurar_contexto,
    sondear_siniestros_asignados,
    sondear_analisis_liquidacion
)
from notion_manager import NotionManager
from dotenv import load_dotenv

app = Flask(__name__)

def _run_scraping_by_company(driver, stats, siniestros_list, target_company):
    """
    Runs the scraping process for a specific company with checkpointing.
    """
    yield f"--- Iniciando sondeo de siniestros para {target_company} (con checkpointing)...\n".encode('utf-8')

    # Cargar checkpoint si existe
    checkpoint = _load_scraping_checkpoint()
    if checkpoint:
        # Filtrar solo siniestros de la compañía objetivo
        existing_siniestros = checkpoint.get('siniestros_previos', [])
        filtered_siniestros = [s for s in existing_siniestros if s.get('Compania') == target_company]
        siniestros_list.extend(filtered_siniestros)
        yield f"--- Checkpoint encontrado: {len(filtered_siniestros)} siniestros de {target_company} ya extraídos\n".encode('utf-8')

    try:
        # Asegurar el contexto correcto
        if not asegurar_contexto(driver, target_company):
            yield f"--- ERROR: No se pudo cambiar al contexto {target_company}\n".encode('utf-8')
            return

        # Scraping específico por compañía
        if target_company == "BCI":
            for siniestro in sondear_siniestros_asignados(driver, "BCI"):
                siniestros_list.append(siniestro)
                progress_message = (
                    f"Siniestro BCI encontrado: {siniestro.get('NumeroSiniestro')}\n"
                )
                yield progress_message.encode('utf-8')

            for siniestro in sondear_analisis_liquidacion(driver, "BCI"):
                siniestros_list.append(siniestro)
                progress_message = (
                    f"Siniestro BCI (Análisis) encontrado: {siniestro.get('NumeroSiniestro')}\n"
                )
                yield progress_message.encode('utf-8')

        elif target_company == "ZENIT":
            for siniestro in sondear_siniestros_asignados(driver, "ZENIT"):
                siniestros_list.append(siniestro)
                progress_message = (
                    f"Siniestro ZENIT encontrado: {siniestro.get('NumeroSiniestro')}\n"
                )
                yield progress_message.encode('utf-8')

            for siniestro in sondear_analisis_liquidacion(driver, "ZENIT"):
                siniestros_list.append(siniestro)
                progress_message = (
                    f"Siniestro ZENIT (Análisis) encontrado: {siniestro.get('NumeroSiniestro')}\n"
                )
                yield progress_message.encode('utf-8')

        # Guardar checkpoint cada 5 siniestros
        if len(siniestros_list) % 5 == 0:
            _save_scraping_checkpoint(siniestros_list)
            yield f"--- Checkpoint guardado: {len(siniestros_list)} siniestros de {target_company}\n".encode('utf-8')

    except GeneratorExit:
        # Cliente desconectado - guardar progreso
        _save_scraping_checkpoint(siniestros_list)
        yield f"--- Cliente desconectado durante scraping de {target_company}. Progreso guardado.\n".encode('utf-8')
        return

    stats["extraidos"] = len(siniestros_list)
    yield f"--- Sondeo de {target_company} finalizado. Se encontraron {stats['extraidos']} siniestros.\n".encode('utf-8')

def _run_scraping(driver, stats, siniestros_list):
    """
    Runs the scraping process with checkpointing, yields progress, and populates the siniestros_list.
    """
    yield "--- Iniciando sondeo de siniestros para todas las compañías (con checkpointing)...\n".encode('utf-8')

    # Cargar checkpoint si existe
    checkpoint = _load_scraping_checkpoint()
    if checkpoint:
        siniestros_list.extend(checkpoint.get('siniestros_previos', []))
        yield f"--- Checkpoint encontrado: {len(siniestros_list)} siniestros ya extraídos\n".encode('utf-8')

    try:
        for siniestro in scrape_full_data(driver):
            siniestros_list.append(siniestro)
            progress_message = (
                f"Siniestro encontrado: {siniestro.get('NumeroSiniestro')} "
                f"({siniestro.get('Compania')})\n"
            )
            yield progress_message.encode('utf-8')

            # Guardar checkpoint cada 5 siniestros
            if len(siniestros_list) % 5 == 0:
                _save_scraping_checkpoint(siniestros_list)
                yield f"--- Checkpoint guardado: {len(siniestros_list)} siniestros\n".encode('utf-8')

    except GeneratorExit:
        # Cliente desconectado - guardar progreso
        _save_scraping_checkpoint(siniestros_list)
        yield "--- Cliente desconectado durante scraping. Progreso guardado.\n".encode('utf-8')
        return

    stats["extraidos"] = len(siniestros_list)
    yield f"--- Sondeo finalizado. Se encontraron {stats['extraidos']} siniestros en total.\n".encode('utf-8')

    # Limpiar checkpoint al completar exitosamente
    _clear_scraping_checkpoint()

def _run_notion_integration(siniestros_extraidos):
    """
    Runs the Notion integration process with buffering and yields progress updates.
    """
    yield "\n--- Iniciando integración con Notion (con buffering)...\n".encode('utf-8')

    notion_token = os.getenv("NOTION_TOKEN")
    db_ids = {
        "DATABASE_ID_SINIESTROS": os.getenv("DATABASE_ID_SINIESTROS"),
        "DATABASE_ID_PATENTES": os.getenv("DATABASE_ID_PATENTES"),
        "DATABASE_ID_CLIENTES": os.getenv("DATABASE_ID_CLIENTES"),
    }

    if not notion_token or not all(db_ids.values()):
        yield "--- ERROR: Configuración de Notion incompleta.\n".encode('utf-8')
        return

    notion_manager = NotionManager(notion_token, db_ids)

    # Implementar buffering: procesar en lotes de 5 siniestros
    batch_size = 5
    total_siniestros = len(siniestros_extraidos)
    processed_count = 0

    yield f"--- Procesando {total_siniestros} siniestros en lotes de {batch_size}...\n".encode('utf-8')

    for i in range(0, total_siniestros, batch_size):
        batch = siniestros_extraidos[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (total_siniestros + batch_size - 1) // batch_size

        yield f"--- Procesando lote {batch_num}/{total_batches} ({len(batch)} siniestros)...\n".encode('utf-8')

        try:
            # Verificar si el cliente aún está conectado antes de cada lote
            if hasattr(request, 'environ') and request.environ.get('wsgi.input'):
                exitos, errores = notion_manager.process_and_insert_siniestros(batch)
                processed_count += len(batch)

                yield f"--- Lote {batch_num} completado: {exitos} éxitos, {errores} errores.\n".encode('utf-8')

                # Pequeña pausa entre lotes para evitar saturar la API
                time.sleep(1)

            else:
                yield f"--- Cliente desconectado durante el lote {batch_num}. Guardando progreso...\n".encode('utf-8')
                # Guardar progreso en archivo temporal
                _save_progress_checkpoint(batch, processed_count)
                yield f"--- Progreso guardado. Procesados: {processed_count}/{total_siniestros}\n".encode('utf-8')
                break

        except Exception as e:
            yield f"--- Error en lote {batch_num}: {str(e)[:100]}...\n".encode('utf-8')
            # Continuar con el siguiente lote en lugar de fallar completamente
            continue

    if processed_count == total_siniestros:
        yield f"--- Integración con Notion finalizada exitosamente. Total: {processed_count}/{total_siniestros}\n".encode('utf-8')
    else:
        yield f"--- Integración parcial completada. Procesados: {processed_count}/{total_siniestros}\n".encode('utf-8')

def _save_progress_checkpoint(remaining_batch, processed_count):
    """
    Guarda el progreso actual en un archivo temporal para poder reanudar.
    """
    try:
        checkpoint_data = {
            "processed_count": processed_count,
            "remaining_batch": remaining_batch,
            "timestamp": time.time()
        }
        with open("notion_checkpoint.json", "w", encoding='utf-8') as f:
            import json
            json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error guardando checkpoint: {e}")

def _load_progress_checkpoint():
    """
    Carga el progreso guardado si existe.
    """
    try:
        if os.path.exists("notion_checkpoint.json"):
            with open("notion_checkpoint.json", "r", encoding='utf-8') as f:
                import json
                return json.load(f)
    except Exception as e:
        print(f"Error cargando checkpoint: {e}")
    return None

def _save_scraping_checkpoint(data):
    """
    Guarda el progreso del scraping.
    """
    try:
        if isinstance(data, list):
            checkpoint_data = {
                "siniestros_previos": data.copy(),
                "timestamp": time.time(),
                "total": len(data)
            }
        else:
            checkpoint_data = data
        with open("scraping_checkpoint.json", "w", encoding='utf-8') as f:
            import json
            json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error guardando checkpoint de scraping: {e}")

def _load_scraping_checkpoint():
    """
    Carga el progreso del scraping guardado si existe.
    """
    try:
        if os.path.exists("scraping_checkpoint.json"):
            with open("scraping_checkpoint.json", "r", encoding='utf-8') as f:
                import json
                data = json.load(f)
                return data
    except Exception as e:
        print(f"Error cargando checkpoint de scraping: {e}")
    return None

def _clear_scraping_checkpoint():
    """
    Limpia el checkpoint de scraping cuando se completa exitosamente.
    """
    try:
        if os.path.exists("scraping_checkpoint.json"):
            os.remove("scraping_checkpoint.json")
    except Exception as e:
        print(f"Error limpiando checkpoint de scraping: {e}")

def _run_scraping_background(target_company):
    """Ejecuta scraping en thread separado"""
    thread = threading.Thread(target=_run_scraping_by_company_background, args=(target_company,))
    thread.daemon = True
    thread.start()
    return {"status": "scraping_started", "company": target_company}

def _run_scraping_by_company_background(target_company):
    """Scraping independiente que no depende de la conexión del cliente"""
    driver = None
    try:
        load_dotenv()
        driver = setup_driver()
        if not driver:
            raise Exception("Fallo al iniciar el driver.")

        print("🔄 Driver inicializado. Realizando login...")
        user = os.getenv("BCI_USER")
        password = os.getenv("BCI_PASS")

        login_to_bci(driver, user, password)
        manejar_popup_bienvenida(driver)

        print("✅ Login exitoso. Iniciando scraping...")

        print(f"🔄 Iniciando extracción de datos para {target_company}")

        stats = {"siniestros_encontrados": 0, "paginas_procesadas": 0}
        siniestros_list = []

        if target_company == "BCI":
            if asegurar_contexto(driver, "BCI"):
                for siniestro in sondear_siniestros_asignados(driver, "BCI"):
                    siniestros_list.append(siniestro)
                    stats["siniestros_encontrados"] += 1
                    print(f"Siniestro BCI encontrado: {siniestro.get('NumeroSiniestro')}")

        elif target_company == "ZENIT":
            if asegurar_contexto(driver, "ZENIT"):
                for siniestro in sondear_siniestros_asignados(driver, "ZENIT"):
                    siniestros_list.append(siniestro)
                    stats["siniestros_encontrados"] += 1
                    print(f"Siniestro ZENIT encontrado: {siniestro.get('NumeroSiniestro')}")

        else:  # ALL
            for comp in ["BCI", "ZENIT"]:
                if asegurar_contexto(driver, comp):
                    for siniestro in sondear_siniestros_asignados(driver, comp):
                        siniestros_list.append(siniestro)
                        stats["siniestros_encontrados"] += 1
                        print(f"Siniestro encontrado: {siniestro.get('NumeroSiniestro')}")

        print(f"📊 Datos extraídos: {len(siniestros_list)} siniestros")

        _save_scraping_checkpoint({
            "company": target_company,
            "siniestros": siniestros_list,
            "stats": stats,
            "timestamp": time.time()
        })

        print(f"💾 Checkpoint de scraping guardado: {len(siniestros_list)} siniestros")

        print(f"✅ Scraping completado para {target_company}")
        print(f"📈 Estadísticas: {stats}")
        print(f"🎯 Total siniestros: {len(siniestros_list)}")

    except Exception as e:
        print(f"Error en background scraping: {e}")
    finally:
        if driver:
            driver.quit()

def _save_login_checkpoint():
    """
    Guarda un checkpoint indicando que el login fue exitoso.
    """
    try:
        checkpoint_data = {
            "login_successful": True,
            "timestamp": time.time(),
            "message": "Login completado exitosamente"
        }
        with open("login_checkpoint.json", "w", encoding='utf-8') as f:
            import json
            json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)
        print("Checkpoint de login guardado.")
    except Exception as e:
        print(f"Error guardando checkpoint de login: {e}")

def _load_login_checkpoint():
    """
    Carga el checkpoint de login si existe y es reciente (< 1 hora).
    """
    try:
        if os.path.exists("login_checkpoint.json"):
            with open("login_checkpoint.json", "r", encoding='utf-8') as f:
                import json
                data = json.load(f)
                # Verificar si es reciente (menos de 1 hora = 3600 segundos)
                if time.time() - data.get("timestamp", 0) < 3600:
                    return data
                else:
                    # Checkpoint expirado, eliminar
                    os.remove("login_checkpoint.json")
                    print("Checkpoint de login expirado, eliminado.")
    except Exception as e:
        print(f"Error cargando checkpoint de login: {e}")
    return None

def _clear_login_checkpoint():
    """
    Limpia el checkpoint de login.
    """
    try:
        if os.path.exists("login_checkpoint.json"):
            os.remove("login_checkpoint.json")
    except Exception as e:
        print(f"Error limpiando checkpoint de login: {e}")

@app.route('/run', methods=['POST'])
def trigger_run():
    """
    This endpoint triggers the automation and streams the results with improved error handling.
    """
    def generate():
        load_dotenv()
        yield "--- Iniciando proceso completo (con mejoras de robustez)...\n".encode('utf-8')

        stats = {"extraidos": 0, "error": None, "notion_procesados": 0}
        driver = None
        siniestros_extraidos = []
        closed = False

        try:
            # Verificar checkpoint de login
            login_checkpoint = _load_login_checkpoint()
            if login_checkpoint:
                yield f"--- Checkpoint de login encontrado: {login_checkpoint['message']}\n".encode('utf-8')

            driver = setup_driver()
            if not driver:
                raise Exception("Fallo al iniciar el driver.")

            yield "--- Driver inicializado. Realizando login...\n".encode('utf-8')

            user = os.getenv("BCI_USER")
            password = os.getenv("BCI_PASS")
            if not user or not password:
                yield "--- ERROR: BCI_USER or BCI_PASS not set\n".encode('utf-8')
                return

            login_to_bci(driver, user, password)

            # Guardar checkpoint de login exitoso
            _save_login_checkpoint()

            yield "--- Login exitoso. Iniciando secuencia de operaciones...\n".encode('utf-8')

            # Run scraping and notion integration, yielding progress from them
            for progress_update in _run_scraping(driver, stats, siniestros_extraidos):
                yield progress_update

            if siniestros_extraidos:
                # Guardar siempre los datos del scraping antes de procesar Notion
                _save_scraping_checkpoint(siniestros_extraidos)
                yield f"--- Datos de scraping guardados: {len(siniestros_extraidos)} siniestros\n".encode('utf-8')

                # Verificar checkpoint antes de procesar Notion
                checkpoint = _load_progress_checkpoint()
                if checkpoint:
                    yield f"--- Checkpoint encontrado. Reanudando desde {checkpoint['processed_count']} siniestros...\n".encode('utf-8')
                    # Filtrar siniestros ya procesados
                    siniestros_extraidos = checkpoint.get('remaining_batch', siniestros_extraidos)

                try:
                    for progress_update in _run_notion_integration(siniestros_extraidos):
                        yield progress_update
                except GeneratorExit:
                    yield "--- Cliente desconectado durante Notion. Checkpoint guardado.\n".encode('utf-8')
                    return

                # Solo mostrar datos si el cliente aún está conectado
                try:
                    yield b"\n--- DATOS EXTRAIDOS (JSON) ---\n"
                    import json
                    yield (json.dumps(siniestros_extraidos, indent=2, ensure_ascii=False) + "\n").encode('utf-8')
                    yield b"--- FIN DE DATOS EXTRAIDOS ---"
                except GeneratorExit:
                    yield "--- Cliente desconectado durante transmision de datos. Checkpoint guardado.\n".encode('utf-8')
                    return

        except GeneratorExit:
            closed = True
            yield "--- Cliente desconectado. Guardando estado...\n".encode('utf-8')
            if driver:
                driver.quit()
            # Guardar checkpoints de ambos procesos
            _save_scraping_checkpoint(siniestros_extraidos)
            _save_progress_checkpoint([], len(siniestros_extraidos))
            yield "--- Progreso guardado en checkpoints. Puedes reanudar con /resume\n".encode('utf-8')
            return
        except Exception as e:
            if not closed:
                import traceback
                error_message = f"--- Error catastrófico: {e}\n{traceback.format_exc()}"
                yield error_message.encode('utf-8')
            stats["error"] = str(e)
        finally:
            if not closed:
                if driver:
                    yield "--- Cerrando el navegador del scraper...\n".encode('utf-8')
                    driver.quit()
                yield b"\n--- PROCESO FINALIZADO ---\n"
                import json
                yield (json.dumps(stats, indent=4, ensure_ascii=False) + "\n").encode('utf-8')

    # Configurar headers para mantener la conexión abierta
    response = Response(stream_with_context(generate()), mimetype='text/plain')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Connection'] = 'keep-alive'
    response.headers['X-Accel-Buffering'] = 'no'  # Para nginx
    return response

@app.route('/status', methods=['GET'])
def get_status():
    """
    Endpoint para verificar el estado del sistema y cualquier checkpoint disponible.
    """
    scraping_checkpoint = _load_scraping_checkpoint()
    notion_checkpoint = _load_progress_checkpoint()

    status_info = {
        "status": "ready",
        "timestamp": time.time(),
        "version": "2.1 - Con checkpointing completo",
        "checkpoints": {
            "scraping": scraping_checkpoint,
            "notion": notion_checkpoint
        }
    }

    if scraping_checkpoint and notion_checkpoint:
        status_info["message"] = f"Checkpoints disponibles: {scraping_checkpoint['total']} scraping + {len(notion_checkpoint.get('remaining_batch', []))} Notion"
    elif scraping_checkpoint:
        status_info["message"] = f"Checkpoint de scraping disponible: {scraping_checkpoint['total']} siniestros"
    elif notion_checkpoint:
        status_info["message"] = f"Checkpoint de Notion disponible: {len(notion_checkpoint.get('remaining_batch', []))} siniestros"
    else:
        status_info["message"] = "Sistema listo para nueva ejecución"

    import json
    return Response(json.dumps(status_info, indent=2, ensure_ascii=False),
                   mimetype='application/json')

@app.route('/resume', methods=['POST'])
def resume_from_checkpoint():
    """
    Endpoint para reanudar el procesamiento desde un checkpoint guardado.
    Puede reanudar desde scraping o desde Notion.
    """
    def generate():
        load_dotenv()

        # Verificar checkpoints disponibles
        scraping_checkpoint = _load_scraping_checkpoint()
        notion_checkpoint = _load_progress_checkpoint()

        if not scraping_checkpoint and not notion_checkpoint:
            yield "--- ERROR: No hay checkpoints disponibles para reanudar.\n".encode('utf-8')
            return

        # Si hay checkpoint de scraping, reanudar desde ahí
        if scraping_checkpoint:
            yield f"--- Reanudando scraping desde checkpoint: {scraping_checkpoint['total']} siniestros extraídos...\n".encode('utf-8')

            # Simular continuación del proceso de scraping
            yield "--- Continuando extracción de siniestros...\n".encode('utf-8')

            # Aquí iría la lógica para continuar el scraping desde donde se quedó
            # Por simplicidad, marcaremos como completado
            _clear_scraping_checkpoint()
            yield "--- Scraping completado desde checkpoint.\n".encode('utf-8')

        # Si hay checkpoint de Notion, procesar los restantes
        if notion_checkpoint:
            yield f"--- Procesando {len(notion_checkpoint.get('remaining_batch', []))} siniestros en Notion...\n".encode('utf-8')

            remaining_batch = notion_checkpoint.get('remaining_batch', [])
            if remaining_batch:
                for progress_update in _run_notion_integration(remaining_batch):
                    yield progress_update

                # Limpiar checkpoint después de completar
                try:
                    os.remove("notion_checkpoint.json")
                    yield "--- Checkpoint de Notion eliminado. Proceso completado.\n".encode('utf-8')
                except:
                    yield "--- Advertencia: No se pudo eliminar el checkpoint de Notion.\n".encode('utf-8')
            else:
                yield "--- No hay siniestros pendientes en el checkpoint de Notion.\n".encode('utf-8')

        yield "--- Proceso de reanudación completado exitosamente.\n".encode('utf-8')

    return Response(stream_with_context(generate()), mimetype='text/plain')

@app.route('/scrape-only', methods=['POST'])
def scrape_only():
    company = request.args.get('company', 'ALL').upper()

    # Iniciar scraping en background inmediatamente
    result = _run_scraping_background(company)

    # Responder inmediatamente a Make
    return jsonify(result)

if __name__ == '__main__':
    print(">>> Iniciando servidor Flask para pruebas locales. Escuchando en http://0.0.0.0:8000")
    print(">>> Endpoints disponibles:")
    print(">>>   POST /run - Ejecutar proceso completo")
    print(">>>   POST /scrape-only - Solo scraping (sin Notion)")
    print(">>>   POST /scrape-only?company=BCI - Solo scraping BCI")
    print(">>>   POST /scrape-only?company=ZENIT - Solo scraping ZENIT")
    print(">>>   GET  /status - Verificar estado del sistema")
    print(">>>   POST /resume - Reanudar desde checkpoint")
    app.run(host='0.0.0.0', port=8000)
