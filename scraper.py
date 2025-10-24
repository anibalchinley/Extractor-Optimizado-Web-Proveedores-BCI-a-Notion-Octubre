
import os
import time
import json
import re
import io
import datetime
import pandas as pd
import traceback
import pdfplumber
import threading
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from selenium import webdriver # Reemplazamos UC por el webdriver estándar
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, NoSuchElementException
import base64
from twocaptcha import TwoCaptcha
from selenium_stealth import stealth

def detectar_contexto_actual(driver):
    """
    Detecta el contexto actual (BCI o Zenit) basado en el src del logo en la página.
    Espera hasta 15 segundos para que aparezca el logo, con múltiples estrategias de detección.

    Args:
        driver: Instancia de Selenium WebDriver.

    Returns:
        str: "BCI", "ZENIT", o "DESCONOCIDO" si no se encuentra ninguno.
    """
    try:
        # Primero esperar a que la página esté completamente cargada
        if not esperar_pagina_cargada(driver, timeout=15):
            print("Advertencia: La página no se cargó completamente antes de detectar contexto.")
            return "DESCONOCIDO"

        # Verificar si estamos dentro de un iframe y cambiar al contexto principal si es necesario
        try:
            driver.switch_to.default_content()
        except Exception:
            pass  # Ya estamos en el contexto principal

        # Buscar el logo por src que contenga "logo"
        logo_selectors = [
            "img[src*='logo']",
            "img[alt*='logo']",
            "img[class*='logo']"
        ]

        logo_element = None
        for selector in logo_selectors:
            try:
                print(f"Intentando encontrar logo con: {selector}")
                # Espera hasta 10 segundos por logo
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                logo_element = driver.find_element(By.CSS_SELECTOR, selector)
                if logo_element.is_displayed():
                    print(f"Logo encontrado y visible: {selector}")
                    break
                else:
                    print(f"Logo encontrado pero no visible: {selector}")
            except TimeoutException:
                print(f"Timeout esperando logo: {selector}")
                continue
            except Exception as e:
                print(f"Error con selector {selector}: {e}")
                continue

        if not logo_element:
            print("Error: Ningún logo fue encontrado.")
            # Intentar inferir contexto desde la URL
            current_url = driver.current_url.lower()
            if "bciseguros" in current_url:
                print("Contexto inferido como BCI desde la URL (logo no encontrado)")
                return "BCI"
            elif "zenit" in current_url:
                print("Contexto inferido como ZENIT desde la URL (logo no encontrado)")
                return "ZENIT"
            else:
                print("No se pudo inferir contexto desde la URL")
                return "DESCONOCIDO"

        # Obtener el src del logo
        logo_src = logo_element.get_attribute("src").lower()
        print(f"Src del logo encontrado: '{logo_src}'")

        if "zenit" in logo_src:
            print("Contexto detectado: ZENIT")
            return "ZENIT"
        elif "bciseguros" in logo_src:
            print("Contexto detectado: BCI")
            return "BCI"

        print(f"Contexto desconocido en el src del logo: '{logo_src}'")
        return "DESCONOCIDO"

    except TimeoutException:
        print("Error de Timeout: No se encontró el logo a tiempo.")
        return "DESCONOCIDO"
    except Exception as e:
        print(f"Error inesperado en detectar_contexto_actual: {e}")
        return "DESCONOCIDO"

def verificar_contexto_bci(driver):
    """
    Verifica si el contexto actual es BCI Seguros utilizando la nueva función de detección.

    Args:
        driver: Instancia de Selenium WebDriver

    Returns:
        bool: True si el contexto es BCI Seguros, False en caso contrario
    """
    return detectar_contexto_actual(driver) == "BCI"

def buscar_opcion_contexto(driver, texto_buscar):
    """
    Busca una opción específica en el menú de contexto.

    Args:
        driver: Instancia de Selenium WebDriver
        texto_buscar: Texto a buscar en las opciones del menú

    Returns:
        WebElement: Elemento encontrado o None si no se encuentra
    """
    try:
        # Intentar con XPath que incluya el texto completo
        xpath = f"//*[contains(translate(., 'ÁÉÍÓÚ', 'AEIOU'), '{texto_buscar.upper()}')]"
        elementos = driver.find_elements(By.XPATH, xpath)

        # Filtrar solo elementos visibles y clickeables
        for elemento in elementos:
            try:
                if elemento.is_displayed() and elemento.is_enabled():
                    return elemento
            except:
                continue

        # Si no se encontró, buscar en menús desplegables
        menus = driver.find_elements(By.XPATH, "//*[contains(@class, 'dropdown-menu') or contains(@class, 'menu-list')]")
        for menu in menus:
            if menu.is_displayed():
                opciones = menu.find_elements(By.XPATH, ".//*[contains(translate(., 'ÁÉÍÓÚ', 'AEIOU'), '" + texto_buscar.upper() + "')] ")
                for opcion in opciones:
                    if opcion.is_displayed() and opcion.is_enabled():
                        return opcion

        return None
    except Exception as e:
        print(f"Error en buscar_opcion_contexto: {str(e)}")
        return None

def buscar_primera_opcion_valida(driver):
    """
    Busca la primera opción válida en el menú de contexto.

    Args:
        driver: Instancia de Selenium WebDriver

    Returns:
        WebElement: Primera opción válida encontrada o None
    """
    try:
        # Buscar en menús desplegables visibles
        menus = driver.find_elements(By.XPATH, "//*[contains(@class, 'dropdown-menu') or contains(@class, 'menu-list')]")

        for menu in menus:
            if menu.is_displayed():
                # Buscar cualquier elemento clickeable dentro del menú
                opciones = menu.find_elements(By.XPATH, ".//*[self::a or self::button or self::div[contains(@class, 'item')]]")
                for opcion in opciones:
                    try:
                        if opcion.is_displayed() and opcion.is_enabled() and opcion.text.strip():
                            return opcion
                    except:
                        continue

        return None
    except Exception as e:
        print(f"Error en buscar_primera_opcion_valida: {str(e)}")
        return None

def take_screenshot(driver, filename="screenshot.png"):
        """Toma una captura de pantalla y la guarda en el directorio /tmp/screenshots/."""
        screenshot_dir = "/tmp/screenshots"
        os.makedirs(screenshot_dir, exist_ok=True)
        filepath = os.path.join(screenshot_dir, filename)
        try:
            driver.save_screenshot(filepath)
            print(f"DEBUG: Captura de pantalla guardada en {filepath}", flush=True)
        except Exception as e:
            print(f"DEBUG: Error al tomar captura de pantalla {filename}: {e}", flush=True)

def apply_stealth_with_timeout(driver, timeout_seconds=30):
    """
    Aplica los parches de selenium-stealth con un timeout para evitar que se cuelgue.

    Args:
        driver: Instancia de WebDriver
        timeout_seconds: Tiempo máximo en segundos para aplicar stealth

    Returns:
        bool: True si se aplicó correctamente, False si timeout o error
    """
    result = {'success': False, 'error': None}

    def stealth_worker():
        try:
            print("DEBUG: Iniciando aplicación de stealth patches...", flush=True)
            stealth(driver,
                    languages=["es-ES", "es"],
                    vendor="Google Inc.",
                    platform="Win32",
                    webgl_vendor="Intel Inc.",
                    renderer="Intel Iris OpenGL Engine",
                    fix_hairline=True,
                    )
            print("DEBUG: Stealth patches aplicados exitosamente.", flush=True)
            result['success'] = True
        except Exception as e:
            print(f"DEBUG: Error durante aplicación de stealth: {e}", flush=True)
            result['error'] = str(e)

    print(f"DEBUG: Aplicando stealth con timeout de {timeout_seconds} segundos...", flush=True)
    thread = threading.Thread(target=stealth_worker)
    thread.daemon = True
    thread.start()
    thread.join(timeout_seconds)

    if thread.is_alive():
        print(f"DEBUG: Timeout alcanzado ({timeout_seconds}s) aplicando stealth. Abortando.", flush=True)
        return False

    if not result['success']:
        print(f"DEBUG: Fallo en aplicación de stealth: {result['error']}", flush=True)
        return False

    print("DEBUG: Stealth aplicado correctamente con timeout.", flush=True)
    return True

def setup_driver():
        """Configura e inicializa el WebDriver estándar de Selenium para Render."""
        print("--- Entrando a setup_driver (MODO ESTÁNDAR DE SELENIUM)...", flush=True)

        options = webdriver.ChromeOptions()
        print("1. ChromeOptions inicializado.", flush=True)

        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        download_dir = "/tmp/downloads"
        os.makedirs(download_dir, exist_ok=True)
        print("3. Directorio de descargas configurado en /tmp/downloads.", flush=True)
        options.add_experimental_option("prefs", {
            "download.default_directory": download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "plugins.always_open_pdf_externally": True
        })
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        print("2. Opciones de Chrome (headless, no-sandbox, etc.) añadidas.", flush=True)

        # En el entorno de Render, el chromedriver que instala el Dockerfile está en el PATH del sistema.
        # Selenium lo encuentra automáticamente, por lo que no es necesario un Service object.
        print("3. Inicializando webdriver.Chrome...", flush=True)

        try:
            driver = webdriver.Chrome(options=options)
            print("4. ¡ÉXITO! WebDriver de Selenium (Modo Estándar) inicializado.", flush=True)
        except Exception as e:
            print(f"Error al inicializar webdriver.Chrome: {e}", flush=True)
            print("Esto puede indicar un problema con el chromedriver en el PATH del servidor.", flush=True)
            return None

        print("5. Aplicando parches de sigilo con selenium-stealth (con timeout)...", flush=True)
        if not apply_stealth_with_timeout(driver, timeout_seconds=30):
            print("ERROR: No se pudieron aplicar los parches de stealth. Continuando sin ellos.", flush=True)
            # No retornamos None aquí, ya que el driver puede funcionar sin stealth
        else:
            print("6. Parches de sigilo aplicados exitosamente.", flush=True)

        return driver

def login_to_bci(driver, user, password, api_key_2captcha):
        """Navega a la página de BCI, resuelve el reCAPTCHA y realiza el login."""
        url = "https://webproveedores.bciseguros.cl/login"
        try:
            print(f"Navegando a: {url}", flush=True)
            driver.get(url)
            print(f"DEBUG: URL actual: {driver.current_url}", flush=True)

            user_selector = 'input[formcontrolname="username"]'
            pass_selector = 'input[formcontrolname="password"]'
            button_selector = 'button.bs-btn.bs-btn-primary.btn-mobile-center.w-100'

            print("Esperando a que los campos de usuario y contraseña sean visibles.", flush=True)
            email_input = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.CSS_SELECTOR, user_selector)))
            email_input.send_keys(user)
            print("DEBUG: Usuario ingresado.", flush=True)

            password_input = driver.find_element(By.CSS_SELECTOR, pass_selector)
            password_input.send_keys(password)
            print("DEBUG: Contraseña ingresada. Credenciales completas.", flush=True)

            print("Iniciando lógica para reCAPTCHA v3...", flush=True)
            page_source = driver.page_source

            match = re.search(r'https://www.google.com/recaptcha/api.js\?render=([^&]+)', page_source)
            sitekey = match.group(1) if match else None

            if sitekey:
                print(f"Sitekey de reCAPTCHA v3 encontrado: {sitekey}", flush=True)

                try:
                    solver = TwoCaptcha(api_key_2captcha)
                    print("Enviando reCAPTCHA v3 a 2Captcha... (esto puede tardar)", flush=True)
                    result = solver.recaptcha(
                        sitekey=sitekey,
                        url=url,
                        version='v3',
                        action='login',
                        score=0.7
                    )

                    if result and result.get('code'):
                        token = result['code']
                        print("reCAPTCHA v3 resuelto. Inyectando token.", flush=True)
                        recaptcha_element_selector = '[name="g-recaptcha-response"]'
                        js_inyectar_token = f"document.querySelector('{recaptcha_element_selector}').value = arguments[0];"

                        try:
                            print(f"INTENTO A: Esperando que el elemento '{recaptcha_element_selector}' exista.", flush=True)
                            WebDriverWait(driver, 10).until(
                                EC.presence_of_element_located((By.CSS_SELECTOR, recaptcha_element_selector))
                            )
                            print("INTENTO A: Éxito. El elemento reCAPTCHA fue encontrado en el DOM.", flush=True)
                            driver.execute_script(js_inyectar_token, token)
                            print("Token inyectado en el elemento existente.", flush=True)

                        except TimeoutException:
                            print("INTENTO A: Falló. El elemento reCAPTCHA no se encontró.", flush=True)
                            print("INTENTO B: Creando el elemento dinámicamente.", flush=True)
                            js_crear_e_inyectar = f"""
                            var newTextarea = document.createElement('textarea');
                            newTextarea.name = 'g-recaptcha-response';
                            newTextarea.style.display = 'none';
                            document.body.appendChild(newTextarea);
                            document.querySelector('{recaptcha_element_selector}').value = arguments[0];
                            """
                            driver.execute_script(js_crear_e_inyectar, token)
                            print("INTENTO B: Éxito. Elemento creado y token inyectado.", flush=True)
                    else:
                        print(f"Error: No se pudo obtener una solución de 2Captcha. Respuesta: {result}", flush=True)
                        return False

                except Exception as e:
                    print(f"Error durante el proceso de resolución de CAPTCHA: {e}", flush=True)
                    return False
            else:
                print("No hay reCAPTCHA presente. Continuando con el login.", flush=True)

            print("Haciendo clic en el botón de login...", flush=True)
            login_button = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, button_selector)))
            login_button.click()
            print("DEBUG: Clic en botón de login realizado.", flush=True)

            print("Esperando redirección a 'busqueda-avanzada'...", flush=True)
            WebDriverWait(driver, 30).until(EC.url_contains('busqueda-avanzada'))
            print(f"Login exitoso. Nueva URL: {driver.current_url}", flush=True)

            # Manejar popup post-login
            print("Esperando y cerrando popup post-login...", flush=True)
            try:
                # Esperar a que el page loader desaparezca antes de intentar interactuar con popups
                WebDriverWait(driver, 30).until(
                    EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.bs-page-loader"))
                )
                print("Page loader desaparecido, procediendo con popup.", flush=True)

                popup_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, ".bs-dynamic-dialog-footer button.bs-btn.bs-btn-primary"))
                )
                # Usar JavaScript click para evitar ElementClickInterceptedException
                driver.execute_script("arguments[0].click();", popup_button)
                print("Popup post-login cerrado.", flush=True)
            except TimeoutException:
                print("No se encontró popup post-login o timeout esperando loader.", flush=True)

            # Verificar que la sesión esté realmente activa
            try:
                if check_login_status(driver):
                    print("Sesión verificada correctamente.", flush=True)
                    # Añadido: Pequeña pausa y manejo de popups post-login para robustez
                    print("Pausa post-login y manejo de popups inicial.", flush=True)
                    time.sleep(2)
                    manejar_posibles_popups(driver)
                    return True
                else:
                    print("No se pudo verificar la sesión.", flush=True)
                    return False
            except Exception as e:
                print(f"Error al verificar el estado de login: {e}", flush=True)
                return False

        except Exception as e:
            print(f"Error durante el proceso de login: {e}", flush=True)
            print(f"Traceback completo del login:\n{traceback.format_exc()}", flush=True)
            take_screenshot(driver, "09_login_exception.png")
            return False


def check_login_status(driver):
    """
    Verifica si el driver sigue logueado buscando un elemento clave en la página.

    Args:
        driver: Instancia de Selenium WebDriver

    Returns:
        bool: True si la sesión está activa, False en caso contrario
    """
    print("\n--- Verificando estado de login ---", flush=True)
    max_retries = 2
    for attempt in range(1, max_retries + 1):
        try:
            # Log current URL and page title for diagnostics
            current_url = driver.current_url
            page_title = driver.title
            print(f"DEBUG: Current URL: {current_url}", flush=True)
            print(f"DEBUG: Page title: {page_title}", flush=True)

            # Ensure page is fully loaded
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script('return document.readyState') == 'complete'
            )
            print(f"DEBUG: Document readyState: complete", flush=True)

            # Dismiss any overlays or popups that might hide elements
            overlays = driver.find_elements(By.CSS_SELECTOR, ".cdk-overlay-backdrop, .modal-backdrop, .mat-dialog-backdrop, .bs-overlay-backdrop")
            for overlay in overlays:
                if overlay.is_displayed():
                    try:
                        driver.execute_script("arguments[0].click();", overlay)
                        print("DEBUG: Overlay dismissed.", flush=True)
                        time.sleep(1)
                    except:
                        pass

            # Check if URL is the expected post-login page and page is loaded
            expected_url = "https://webproveedores.bciseguros.cl/busqueda-avanzada"
            if driver.current_url == expected_url and driver.execute_script('return document.readyState') == 'complete':
                print("URL correcta y página cargada. Sesión activa.", flush=True)
                return True
            else:
                print(f"DEBUG: URL actual: {driver.current_url}, expected: {expected_url}", flush=True)
                print(f"DEBUG: Document readyState: {driver.execute_script('return document.readyState')}", flush=True)
                return False
        except TimeoutException:
            print(f"Elemento 'Calendario' no encontrado en intento {attempt}. Intentando elementos alternativos.", flush=True)
            # Try alternative element checks
            try:
                WebDriverWait(driver, 10).until(
                    EC.visibility_of_element_located((By.XPATH, "//a[contains(., 'Siniestros')]" ))
                )
                print("Elemento alternativo 'Siniestros' encontrado y visible. Sesión activa.", flush=True)
                return True
            except TimeoutException:
                print("Elementos alternativos tampoco encontrados.", flush=True)
            if attempt < max_retries:
                print("Refrescando página y reintentando...", flush=True)
                driver.refresh()
                time.sleep(3)
            else:
                # Additional diagnostic: check if we're on the expected page
                if "busqueda-avanzada" not in current_url:
                    print("DEBUG: Not on expected post-login page (busqueda-avanzada not in URL)", flush=True)
                return False
        except Exception as e:
            print(f"Error al verificar el estado de login en intento {attempt}: {e}", flush=True)
            if attempt < max_retries:
                print("Refrescando página y reintentando...", flush=True)
                driver.refresh()
                time.sleep(3)
            else:
                take_screenshot(driver, "error_check_login_status.png")
                return False
    return False

def esperar_pagina_cargada(driver, timeout=30):
    """
    Espera a que la página se cargue completamente y que los loaders desaparezcan.
    """
    print("--- Esperando carga completa de la página y desaparición de loaders ---", flush=True)
    try:
        # 1. Esperar a que el estado del documento sea 'complete'
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script('return document.readyState') == 'complete'
        )
        print("Documento cargado.", flush=True)

        # 2. Esperar a que cualquier loader desaparezca
        loader_selector = "div.loader-container, .loader, [role='progressbar'], div.bs-page-loader"
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located((By.CSS_SELECTOR, loader_selector))
        )
        print("Loaders desaparecidos. La página está lista.", flush=True)
        return True
    except TimeoutException:
        print("Timeout esperando la carga de la página o la desaparición de los loaders.", flush=True)
        take_screenshot(driver, "error_carga_pagina.png")
        return False

def manejar_popup_bienvenida(driver, timeout=30):
    """
    Busca y cierra la ventana emergente de bienvenida y espera a que su fondo desaparezca.

    Args:
        driver: Instancia de Selenium WebDriver
        timeout: Tiempo máximo de espera en segundos

    Returns:
        bool: True si se manejó correctamente, False en caso contrario
    """
    print("\n--- Buscando pop-up de bienvenida ---", flush=True)

    try:
        # 1. Esperar a que la página y los loaders estén listos
        if not esperar_pagina_cargada(driver, timeout):
            return False # Si la página no carga, no podemos continuar

        # 2. Intentar diferentes selectores para el botón de aceptar
        button_selectors = [
            "//button[contains(., 'Aceptar') or contains(., 'Acepto') or contains(., 'Entendido')]",
            "//button[contains(@class, 'mat-button') and contains(., 'Aceptar')]",
            "//button[contains(@class, 'bs-btn') and contains(@class, 'bs-btn-primary') and contains(., 'Aceptar')]",
            "//div[contains(@class, 'bs-dynamic-dialog-footer')]//button[contains(@class, 'bs-btn') and contains(@class, 'bs-btn-primary')]"
        ]

        button_found = False
        for selector in button_selectors:
            try:
                print(f"Intentando con selector: {selector}", flush=True)
                accept_button = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                driver.execute_script("arguments[0].click();", accept_button)
                print("Botón de aceptar clickeado con éxito.", flush=True)
                button_found = True
                break
            except Exception:
                print(f"No se pudo interactuar con el botón usando {selector}", flush=True)

        if not button_found:
            print("No se encontró ningún botón de aceptar visible y clickeable.", flush=True)
            return False

        # 3. Esperar a que desaparezcan los backdrops
        backdrop_selectors = [
            "div.cdk-overlay-backdrop",
            ".modal-backdrop",
            ".mat-dialog-backdrop"
        ]

        for selector in backdrop_selectors:
            try:
                WebDriverWait(driver, 10).until(
                    EC.invisibility_of_element_located((By.CSS_SELECTOR, selector))
                )
                print(f"Backdrop '{selector}' desaparecido.", flush=True)
            except:
                print(f"No se encontró el backdrop '{selector}' o ya desapareció.", flush=True)

        print("Toda la interfaz está lista para interactuar.", flush=True)
        return True

    except Exception as e:
        error_msg = f"Error inesperado en manejar_popup_bienvenida: {str(e)[:200]}"
        print(error_msg, flush=True)
        take_screenshot(driver, "error_popup_bienvenida.png")
        raise Exception(f"Fallo al manejar el pop-up de bienvenida: {str(e)[:200]}")


def manejar_posibles_popups(driver):
    """
    Maneja posibles popups que puedan aparecer durante la navegación.
    Incluye manejo de popups de bienvenida, notificaciones y otros diálogos emergentes.
    Mejora la verificación para confirmar que los popups se cierren correctamente.
    """
    try:
        # Esperar a que el page loader desaparezca antes de manejar popups
        try:
            WebDriverWait(driver, 30).until(
                EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.bs-page-loader"))
            )
            print("Page loader desaparecido antes de manejar popups.", flush=True)
        except TimeoutException:
            print("Timeout esperando que el page loader desaparezca.", flush=True)

        # Primero intentar manejar el popup de bienvenida estándar
        try:
            manejar_popup_bienvenida(driver)
        except Exception as e:
            print(f"No se pudo manejar el popup de bienvenida: {str(e)[:200]}", flush=True)

        # Esperar un momento para que cualquier popup se cargue completamente
        time.sleep(2)

        # Intentar cerrar cualquier notificación o diálogo emergente
        try:
            # Buscar botones de cierre en diálogos modales
            botones_cierre = driver.find_elements(By.XPATH,
                "//button[contains(@class, 'close') or contains(@class, 'mat-dialog-close') or @aria-label='Cerrar' or @title='Cerrar']"
            )

            for boton in botones_cierre:
                try:
                    if boton.is_displayed() and boton.is_enabled():
                        # Esperar a que no haya page loader antes de clickear
                        WebDriverWait(driver, 10).until(
                            EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.bs-page-loader"))
                        )
                        driver.execute_script("arguments[0].click();", boton)
                        print("Botón de cierre de diálogo encontrado y clickeado.", flush=True)
                        time.sleep(1)  # Esperar a que se cierre la animación
                        # Verificar que el botón ya no esté visible
                        if not boton.is_displayed():
                            print("Verificación: Botón de cierre ya no visible.", flush=True)
                        else:
                            print("Advertencia: Botón de cierre aún visible después del clic.", flush=True)
                except:
                    continue

        except Exception as e:
            print(f"Error al intentar cerrar diálogos: {str(e)[:200]}", flush=True)

        # Verificar si hay algún overlay o backdrop que bloquee la interacción
        try:
            backdrops = driver.find_elements(By.CSS_SELECTOR, ".cdk-overlay-backdrop, .modal-backdrop, .mat-dialog-backdrop, .bs-overlay-backdrop")
            for backdrop in backdrops:
                try:
                    if backdrop.is_displayed():
                        # Esperar a que no haya page loader antes de clickear backdrop
                        WebDriverWait(driver, 10).until(
                            EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.bs-page-loader"))
                        )
                        # Intentar hacer clic en una esquina del backdrop para cerrarlo
                        driver.execute_script("arguments[0].click();", backdrop)
                        print("Backdrop encontrado y clickeado.", flush=True)
                        time.sleep(1)
                        # Verificar que el backdrop ya no esté visible
                        if not backdrop.is_displayed():
                            print("Verificación: Backdrop ya no visible.", flush=True)
                        else:
                            print("Advertencia: Backdrop aún visible después del clic.", flush=True)
                except:
                    continue
        except Exception as e:
            print(f"Error al manejar backdrops: {str(e)[:200]}", flush=True)

        # Verificación final: Asegurarse de que no queden elementos de popup visibles
        try:
            remaining_popups = driver.find_elements(By.CSS_SELECTOR, ".cdk-overlay-container .cdk-overlay-pane, .modal.show, .mat-dialog-container")
            if remaining_popups:
                print(f"Advertencia: Aún hay {len(remaining_popups)} elementos de popup visibles.", flush=True)
                for popup in remaining_popups:
                    try:
                        # Intentar cerrar con Escape
                        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                        time.sleep(1)
                        if not popup.is_displayed():
                            print("Verificación: Popup cerrado con Escape.", flush=True)
                            break
                    except:
                        continue
            else:
                print("Verificación: No se detectan popups visibles.", flush=True)
        except Exception as e:
            print(f"Error en verificación final de popups: {str(e)[:200]}", flush=True)

    except Exception as e:
        print(f"Error inesperado en manejar_posibles_popups: {str(e)[:200]}", flush=True)
        take_screenshot(driver, "error_manejo_popups.png")

    return True


def asegurar_contexto(driver, compania_objetivo, max_retries=2):
    """
    Asegura que el bot esté operando en el contexto deseado (BCI o ZENIT).
    Versión 9.8: Usa logo para detectar contexto y dropdown arrow para cambiar.

    Args:
        driver: Instancia de Selenium WebDriver.
        compania_objetivo: "BCI" o "ZENIT".
        max_retries: Número máximo de reintentos.

    Returns:
        bool: True si el contexto es o se cambió al objetivo, False en caso contrario.
    """
    print(f"\n--- Asegurando contexto {compania_objetivo.upper()} (v9.8) ---", flush=True)

    opciones_menu = {
        "BCI": "BCI Seguros",
        "ZENIT": "Zenit Seguros"
    }
    texto_opcion_menu = opciones_menu.get(compania_objetivo.upper())
    if not texto_opcion_menu:
        print(f"Error: Compañía objetivo '{compania_objetivo}' no es válida.", flush=True)
        return False

    for attempt in range(1, max_retries + 1):
        print(f"Intento {attempt}/{max_retries}...", flush=True)

        contexto_actual = detectar_contexto_actual(driver)

        if contexto_actual == compania_objetivo.upper():
            print(f"Éxito: El contexto actual ya es {compania_objetivo.upper()}.")
            return True

        if contexto_actual == "DESCONOCIDO":
            print("Advertencia: No se pudo determinar el contexto actual. Asumiendo BCI por defecto.", flush=True)
            contexto_actual = "BCI"

        print(f"Contexto actual es {contexto_actual}. Intentando cambiar a {compania_objetivo.upper()}...")

        try:
            # Paso 1: Encontrar el dropdown arrow trigger
            dropdown_arrow = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "img[src*='icon-ui-nav-flecha-abajo.svg']"))
            )
            print("Dropdown arrow encontrado.")

            # Paso 2: Hacer clic en el dropdown arrow para abrir el menú de contexto
            driver.execute_script("arguments[0].click();", dropdown_arrow)
            print("Clic en dropdown arrow realizado.")

            # Paso 3: Esperar a que aparezcan las opciones del menú
            WebDriverWait(driver, 10).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, "a.bs-selector.grande, a.bs-selector.grande.visited")) > 0
            )
            print("Opciones del menú de contexto cargadas.")

            # Paso 4: Encontrar y seleccionar la opción correcta
            option_found = False
            # Buscar opciones con las clases especificadas
            options = driver.find_elements(By.CSS_SELECTOR, "a.bs-selector.grande, a.bs-selector.grande.visited")
            for option in options:
                if option.is_displayed() and option.is_enabled():
                    option_text = option.text.strip()
                    if texto_opcion_menu.lower() in option_text.lower():
                        print(f"Opción encontrada: '{option_text}'. Seleccionando.")
                        driver.execute_script("arguments[0].click();", option)
                        option_found = True
                        break

            if not option_found:
                print(f"Error: No se encontró la opción '{texto_opcion_menu}' en el menú.")
                raise TimeoutException(f"La opción '{texto_opcion_menu}' no fue encontrada en el menú.")

            # Paso 5: Esperar y verificar el cambio
            print("Cambio de contexto solicitado. Esperando carga de página...")
            esperar_pagina_cargada(driver)
            manejar_popup_bienvenida(driver)

            print(f"Esperando la confirmación del cambio a {compania_objetivo.upper()}...")
            WebDriverWait(driver, 20).until(
                lambda d: detectar_contexto_actual(d) == compania_objetivo.upper()
            )

            # Extra wait specifically for BCI context change due to slower loader disappearance
            if compania_objetivo.upper() == "BCI":
                print("Extra wait for BCI context change to ensure page loader fully disappears...")
                max_retries = 5
                for attempt in range(1, max_retries + 1):
                    try:
                        WebDriverWait(driver, 10 + attempt * 5).until(
                            EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.bs-page-loader"))
                        )
                        print(f"Page loader fully disappeared for BCI after {attempt} attempts.")
                        break
                    except TimeoutException:
                        if attempt == max_retries:
                            print("Warning: Page loader still visible after extra waits for BCI.")
                        else:
                            time.sleep(2)

            print(f"Éxito: El contexto se cambió a {compania_objetivo.upper()} correctamente.")
            return True

        except TimeoutException as e:
            print(f"Error de Timeout en el intento {attempt}: {e}")
            take_screenshot(driver, f"contexto_timeout_attempt_{attempt}.png")
            if attempt == max_retries:
                print("Se agotaron los reintentos para cambiar de contexto.")
                traceback.print_exc()
                return False
            time.sleep(3)

        except Exception as e:
            print(f"Error inesperado en el intento {attempt}: {e}")
            take_screenshot(driver, f"contexto_error_inesperado_attempt_{attempt}.png")
            if attempt == max_retries:
                print("Se agotaron los reintentos debido a errores inesperados.")
                traceback.print_exc()
                return False
            time.sleep(3)

    return False


def extraer_datos_pdf(driver):
    """
    Encuentra el enlace 'VER DENUNCIO', abre el PDF en una nueva pestaña,
    extrae el 'Relato', 'VIN' y 'Número de Asegurado', y luego cierra la pestaña.
    """
    print("--- Iniciando extracción de PDF ---", flush=True)
    pdf_data = {"Relato": None, "VIN": None, "NumeroAsegurado": None}
    original_window = driver.current_window_handle

    try:
        ver_denuncio_link = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//a[contains(., 'VER DENUNCIO')]" ))
        )
        print("Enlace 'VER DENUNCIO' encontrado y clickeado.", flush=True)
        ver_denuncio_link.click()

        WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))
        for window_handle in driver.window_handles:
            if window_handle != original_window:
                driver.switch_to.window(window_handle)
                break
        print(f"Cambiado a la nueva pestaña del PDF: {driver.current_url}", flush=True)

        js_script = """
            var url = window.location.href;
            var response = await fetch(url);
            var blob = await response.blob();
            var reader = new FileReader();
            var promise = new Promise((resolve, reject) => {
                reader.onloadend = () => resolve(reader.result);
                reader.onerror = reject;
            });
            reader.readAsDataURL(blob);
            return promise;
        """
        data_url = driver.execute_script(js_script)
        header, encoded = data_url.split(",", 1)
        pdf_bytes = base64.b64decode(encoded)
        print("Contenido del PDF descargado y decodificado.", flush=True)

        full_text = ""
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    full_text += page_text + "\n"

        # --- Expresiones Regulares v4.1 ---
        # Extracción del Relato
        relato_match = re.search(r"RELATO\n([\s\S]*?)(?=\nDATOS VEHÍCULO)", full_text, re.IGNORECASE)
        if relato_match:
            pdf_data["Relato"] = relato_match.group(1).strip()

        # Extracción del VIN
        vin_match = re.search(r"VIN Marca/Modelo/Año Patente\n([A-Z0-9]{17})", full_text)
        if vin_match:
            pdf_data["VIN"] = vin_match.group(1).strip()

        # Extracción del Número de Póliza
        asegurado_match = re.search(r"Póliza Ítem del Vehículo en Póliza Deducible Póliza\n(.*?)\s", full_text)
        if asegurado_match:
            pdf_data["NumeroAsegurado"] = asegurado_match.group(1).strip()

        print(f"Datos extraídos del PDF: {pdf_data}", flush=True)

    except TimeoutException:
        print("WARN: No se encontró el enlace 'VER DENUNCIO' o la pestaña del PDF no apareció.", flush=True)
        take_screenshot(driver, "pdf_link_no_encontrado.png")
    except Exception as e:
        print(f"ERROR: Fallo inesperado durante la extracción del PDF: {e}", flush=True)
        traceback.print_exc()
        take_screenshot(driver, "pdf_extraccion_error.png")
    finally:
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(original_window)
            print("Pestaña del PDF cerrada. Volviendo a la pestaña original.", flush=True)
    return pdf_data

def sondear_siniestros_asignados(driver, compania):
    """
    Orquesta el proceso de scraping en la pestaña 'Asignados'.
    v4.5: Añade el parámetro compania para etiquetar los datos.
    """
    print(f"\n--- Iniciando sondeo de Siniestros Asignados para {compania.upper()} ---", flush=True)

    try:
        # Las pestañas están directamente accesibles, no es necesario navegar a "Siniestros" y "Gestión de siniestros"
        print("Navegando a la pestaña 'Asignados'", flush=True)

        # Enhanced page loader waiting with retry loop and increasing delays
        print("Waiting for page loader to fully disappear before navigating to 'Asignados'...")
        max_loader_retries = 5
        for loader_attempt in range(1, max_loader_retries + 1):
            try:
                WebDriverWait(driver, 10 + loader_attempt * 5).until(
                    EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.bs-page-loader"))
                )
                print(f"Page loader fully disappeared after {loader_attempt} attempts.")
                break
            except TimeoutException:
                if loader_attempt == max_loader_retries:
                    print("Warning: Page loader still visible after all retries. Proceeding anyway.")
                else:
                    time.sleep(2)

        # Intentar clickear con reintentos y manejo de excepciones
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                asignados_tab = WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.XPATH, "//span[contains(@class, 'font-bold') and contains(@class, 'white-space-nowrap') and contains(@class, 'm-0') and contains(@class, 'ng-star-inserted') and contains(text(), 'Asignados')]" )))
                driver.execute_script("arguments[0].click();", asignados_tab)
                print(f"Click en pestaña 'Asignados' realizado exitosamente en intento {attempt}.", flush=True)
                break
            except (ElementClickInterceptedException, StaleElementReferenceException) as e:
                print(f"Error en intento {attempt} al clickear 'Asignados': {e}", flush=True)
                if attempt == max_retries:
                    raise e
                time.sleep(2)
                # Re-encontrar el elemento después de esperar
                continue

        esperar_pagina_cargada(driver)

        page_num = 1
        while True:
            print(f"\nRecolectando datos de tabla en página {page_num}...", flush=True)
            row_selector = "//tr[contains(@class, 'ng-star-inserted')]"
            try:
                WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.XPATH, row_selector)))
                rows = driver.find_elements(By.XPATH, row_selector)
                print(f"Encontradas {len(rows)} filas en la página {page_num}.", flush=True)

                # Extraer todos los datos de cada fila usando índices
                for row in rows:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) >= 18:  # Asegurarse de que hay suficientes celdas
                        row_data = {
                            'Compania': compania,
                            'FechaAsignacion': cells[0].text,
                            'NumeroSiniestro': cells[1].text,
                            'EstadoContacto': cells[2].text,
                            'Patente': cells[4].text,
                            'NombreAsegurado': cells[9].text,
                            'RutAsegurado': cells[10].text,
                            'TelefonoAsegurado': cells[11].text,
                            'CorreoAsegurado': cells[12].text,
                            'Marca': cells[13].text,
                            'Modelo': cells[14].text,
                            'TipoDanio': cells[16].text,
                            'FechaEstimadaIngreso': cells[17].text
                        }
                        yield row_data

                print(f"Datos de {len(rows)} filas guardados.", flush=True)

            except TimeoutException:
                print("No se encontraron más filas de 'Asignados' en esta página. Finalizando recolección.", flush=True)
                break

            # Paginación
            try:
                # Store the first row's unique identifier before attempting to paginate
                first_row_id_before_pagination = None
                if rows: # Check if there are rows on the current page
                    try:
                        cells = rows[0].find_elements(By.TAG_NAME, "td")
                        if len(cells) > 1:
                            first_row_id_before_pagination = cells[1].text  # NumeroSiniestro is at index 1
                    except NoSuchElementException:
                        print("WARN: Could not get first row ID for pagination check.", flush=True)

                next_button_selector = "button.p-paginator-next.p-paginator-element.p-link:not([disabled])"
                next_button = driver.find_element(By.CSS_SELECTOR, next_button_selector)
                driver.execute_script("arguments[0].scrollIntoView(true);", next_button)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", next_button)
                esperar_pagina_cargada(driver)
                page_num += 1

                # After clicking next, re-evaluate rows on the new page
                WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.XPATH, row_selector)))
                rows_after_pagination = driver.find_elements(By.XPATH, row_selector)

                # Check if the content has changed (i.e., we moved to a new page) and if the number of rows is 0
                if first_row_id_before_pagination and rows_after_pagination:
                    cells_after = rows_after_pagination[0].find_elements(By.TAG_NAME, "td")
                    if len(cells_after) > 1:
                        first_row_id_after_pagination = cells_after[1].text
                        if first_row_id_before_pagination == first_row_id_after_pagination:
                            print("Detectado bucle de paginación: La primera fila no cambió. Fin de la recolección.", flush=True)
                            break # Break if we are stuck on the same page content
                elif not rows_after_pagination: # If no rows are found on the new page, it's the end
                    print("No se encontraron filas en la nueva página. Fin de la recolección.", flush=True)
                    break

            except (NoSuchElementException, TimeoutException):
                print("No hay más páginas o el botón de siguiente está deshabilitado. Fin de la recolección.", flush=True)
                break

    except Exception as e:
        print(f"Error crítico durante la recolección de la tabla: {e}", flush=True)
        traceback.print_exc()
        take_screenshot(driver, "error_critico_recoleccion_tabla.png")

def sondear_siniestros_liquidacion(driver, compania):
    """
    Orquesta el proceso de descarga y procesamiento de Excel para Análisis de Liquidación.
    """
    print(f"\n--- Iniciando sondeo de Siniestros Liquidación para {compania.upper()} ---", flush=True)

    # Verificar estado actual antes de navegación
    current_url = driver.current_url
    try:

        # Verificar si el submenu está visible
        submenu_visible = False
        submenu_container = None
        try:
            submenu_container = driver.find_element(By.CSS_SELECTOR, "div#item-1.show")
            if submenu_container.is_displayed():
                submenu_visible = True
                # print("DEBUG: Submenu 'div#item-1.show' ya está visible.", flush=True)
        except NoSuchElementException:
            pass
            # print("DEBUG: Submenu 'div#item-1.show' no encontrado.", flush=True)

        # DEBUG: Inspeccionar pestañas disponibles en el submenu
        if submenu_container and submenu_visible:
            try:
                tabs = submenu_container.find_elements(By.TAG_NAME, "a")
                # print("DEBUG: Pestañas disponibles en el submenu:", flush=True)
                for tab in tabs:
                    pass
                    # print(f"  - Texto: '{tab.text}' | Visible: {tab.is_displayed()} | Enabled: {tab.is_enabled()}", flush=True)
            except Exception as e:
                pass
                # print(f"DEBUG: Error al inspeccionar pestañas: {e}", flush=True)

        # DEBUG: Inspeccionar todas las pestañas con data-toggle="tab" en toda la página
        try:
            all_tabs = driver.find_elements(By.XPATH, "//a[@data-toggle='tab']")
            # print("DEBUG: Pestañas con data-toggle='tab' en toda la página:", flush=True)
            for tab in all_tabs:
                text = tab.text.strip()
                visible = tab.is_displayed()
                enabled = tab.is_enabled()
                data_toggle = tab.get_attribute("data-toggle")
                # print(f"  - Texto: '{text}' | data-toggle: '{data_toggle}' | Visible: {visible} | Enabled: {enabled}", flush=True)
        except Exception as e:
            pass
            # print(f"DEBUG: Error al inspeccionar pestañas data-toggle: {e}", flush=True)

        # DEBUG: Verificar elementos con data-toggle u otros atributos relacionados con descarga
        # print("DEBUG: Inspeccionar elementos con data-toggle o atributos de descarga...", flush=True)
        try:
            elements_with_data_toggle = driver.find_elements(By.XPATH, "//*[@data-toggle]")
            # print(f"DEBUG: Encontrados {len(elements_with_data_toggle)} elementos con data-toggle:", flush=True)
            for i, elem in enumerate(elements_with_data_toggle):
                tag = elem.tag_name
                data_toggle = elem.get_attribute("data-toggle")
                text = elem.text.strip()
                visible = elem.is_displayed()
                enabled = elem.is_enabled() if tag in ['button', 'input', 'a'] else 'N/A'
                # print(f"  Elemento {i+1}: tag='{tag}', data-toggle='{data_toggle}', text='{text}', visible={visible}, enabled={enabled}", flush=True)

            # Otros atributos relacionados con descarga
            download_related = driver.find_elements(By.XPATH, "//*[@download or @href[contains(., 'excel') or @href[contains(., 'download')]]")
            # print(f"DEBUG: Encontrados {len(download_related)} elementos con atributos de descarga:", flush=True)
            for i, elem in enumerate(download_related):
                tag = elem.tag_name
                href = elem.get_attribute("href") or ""
                download = elem.get_attribute("download") or ""
                text = elem.text.strip()
                # print(f"  Elemento {i+1}: tag='{tag}', href='{href}', download='{download}', text='{text}'", flush=True)
        except Exception as e:
            pass
            # print(f"DEBUG: Error al inspeccionar elementos con data-toggle: {e}", flush=True)

        page_num = 1
        while True:
            print(f"\nRecolectando datos de tabla en página {page_num}...", flush=True)
            row_selector = "//tr[contains(@class, 'ng-star-inserted')]"
            try:
                WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.XPATH, row_selector)))
                rows = driver.find_elements(By.XPATH, row_selector)
                print(f"Encontradas {len(rows)} filas en la página {page_num}.", flush=True)

                # Extraer todos los datos de cada fila usando índices
                for row in rows:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) >= 7:  # Asegurarse de que hay suficientes celdas
                        NumeroSiniestro = cells[1].text.strip()
                        if NumeroSiniestro:
                            row_data = {
                                'Compania': compania,
                                'FechaIngreso': cells[0].text,
                                'NumeroSiniestro': NumeroSiniestro,
                                'Patente': cells[2].text,
                                'RutAsegurado': cells[3].text,
                                'Marca': cells[4].text,
                                'Modelo': cells[5].text,
                                'TipoDanio': cells[6].text,
                                'Status': 'ANALISIS LIQUIDACION',
                            }
                            yield row_data

                print(f"Datos de {len(rows)} filas guardados.", flush=True)

            except TimeoutException:
                print("No se encontraron más filas de 'Liquidación' en esta página. Finalizando recolección.", flush=True)
                break

            # Paginación
            try:
                # Store the first row's unique identifier before attempting to paginate
                first_row_id_before_pagination = None
                if rows: # Check if there are rows on the current page
                    try:
                        cells = rows[0].find_elements(By.TAG_NAME, "td")
                        if len(cells) > 1:
                            first_row_id_before_pagination = cells[1].text  # NumeroSiniestro is at index 1
                    except NoSuchElementException:
                        print("WARN: Could not get first row ID for pagination check.", flush=True)

                next_button_selector = "button.p-paginator-next.p-paginator-element.p-link:not([disabled])"
                next_button = driver.find_element(By.CSS_SELECTOR, next_button_selector)
                driver.execute_script("arguments[0].scrollIntoView(true);", next_button)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", next_button)
                esperar_pagina_cargada(driver)
                page_num += 1

                # After clicking next, re-evaluate rows on the new page
                WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.XPATH, row_selector)))
                rows_after_pagination = driver.find_elements(By.XPATH, row_selector)

                # Check if the content has changed (i.e., we moved to a new page) and if the number of rows is 0
                if first_row_id_before_pagination and rows_after_pagination:
                    cells_after = rows_after_pagination[0].find_elements(By.TAG_NAME, "td")
                    if len(cells_after) > 1:
                        first_row_id_after_pagination = cells_after[1].text
                        if first_row_id_before_pagination == first_row_id_after_pagination:
                            print("Detectado bucle de paginación: La primera fila no cambió. Fin de la recolección.", flush=True)
                            break # Break if we are stuck on the same page content
                elif not rows_after_pagination: # If no rows are found on the new page, it's the end
                    print("No se encontraron filas en la nueva página. Fin de la recolección.", flush=True)
                    break

            except (NoSuchElementException, TimeoutException):
                print("No hay más páginas o el botón de siguiente está deshabilitado. Fin de la recolección.", flush=True)
                break

    except Exception as e:
        print(f"Error en sondear_siniestros_liquidacion: {e}")
        traceback.print_exc()
        take_screenshot(driver, "error_liquidacion.png")

    print(f"\n--- Proceso de sondeo de liquidación completado. ---", flush=True)
    print(f"\n--- Proceso de sondeo completado. ---", flush=True)

def scrape_full_data(driver):
    """
    Orquesta el proceso completo de scraping para todas las compañías definidas.
    """
    print("--- Iniciando proceso de scraping completo ---", flush=True)

    companias = ["BCI", "ZENIT"]
    all_data = []

    for compania in companias:
        print(f"\n--- Procesando compañía: {compania.upper()} ---", flush=True)
        if asegurar_contexto(driver, compania):
            data = list(sondear_siniestros_asignados(driver, compania))
            all_data.extend(data)
            data = list(sondear_siniestros_liquidacion(driver, compania))
            all_data.extend(data)
        else:
            print(f"ADVERTENCIA: No se pudo asegurar el contexto para {compania.upper()}. Saltando esta compañía.", flush=True)
            take_screenshot(driver, f"error_contexto_{compania.lower()}.png")

    all_data = list({item['NumeroSiniestro']: item for item in all_data}.values())

    for item in all_data:
        yield item
