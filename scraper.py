import os
import time
import json
import re
import io
import datetime
import pandas as pd
import traceback
import pdfplumber
import logging
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    NoSuchElementException,
    ElementClickInterceptedException,
    WebDriverException
)
import base64
from selenium_stealth import stealth

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/tmp/scraper.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)

# Constants
LOGIN_URL = "https://webproveedores.bciseguros.cl/login"
BUSQUEDA_AVANZADA_URL = "https://webproveedores.bciseguros.cl/busqueda-avanzada"
USER_SELECTOR = 'input[formcontrolname="username"]'
PASS_SELECTOR = 'input[formcontrolname="password"]'
BUTTON_SELECTOR = 'button.bs-btn.bs-btn-primary.btn-mobile-center.w-100'
PAGE_LOADER_SELECTOR = "div.loader-container, .loader, [role='progressbar'], div.bs-page-loader"

def check_captcha_presence(driver):
    """Check if CAPTCHA is present on the page."""
    try:
        # Check for reCAPTCHA iframe
        captcha_iframe = driver.find_elements(By.XPATH, "//iframe[contains(@src, 'recaptcha')]")
        if captcha_iframe:
            logger.warning("CAPTCHA detected on the page.")
            return True

        # Check for reCAPTCHA div
        captcha_div = driver.find_elements(By.CLASS_NAME, "g-recaptcha")
        if captcha_div:
            logger.warning("CAPTCHA div detected on the page.")
            return True

        return False
    except Exception as e:
        logger.error(f"Error checking for CAPTCHA: {e}")
        return False

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
            logger.warning("La página no se cargó completamente antes de detectar contexto.")
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
                logger.debug(f"Intentando encontrar logo con: {selector}")
                # Espera hasta 10 segundos por logo
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                logo_element = driver.find_element(By.CSS_SELECTOR, selector)
                if logo_element.is_displayed():
                    logger.debug(f"Logo encontrado y visible: {selector}")
                    break
                else:
                    logger.debug(f"Logo encontrado pero no visible: {selector}")
            except TimeoutException:
                logger.debug(f"Timeout esperando logo: {selector}")
                continue
            except Exception as e:
                logger.debug(f"Error con selector {selector}: {e}")
                continue

        if not logo_element:
            logger.error("Ningún logo fue encontrado.")
            # Intentar inferir contexto desde la URL
            current_url = driver.current_url.lower()
            if "bciseguros" in current_url:
                logger.info("Contexto inferido como BCI desde la URL (logo no encontrado)")
                return "BCI"
            elif "zenit" in current_url:
                logger.info("Contexto inferido como ZENIT desde la URL (logo no encontrado)")
                return "ZENIT"
            else:
                logger.warning("No se pudo inferir contexto desde la URL")
                return "DESCONOCIDO"

        # Obtener el src del logo
        logo_src = logo_element.get_attribute("src").lower()
        logger.debug(f"Src del logo encontrado: '{logo_src}'")

        if "zenit" in logo_src:
            logger.info("Contexto detectado: ZENIT")
            return "ZENIT"
        elif "bciseguros" in logo_src:
            logger.info("Contexto detectado: BCI")
            return "BCI"

        logger.warning(f"Contexto desconocido en el src del logo: '{logo_src}'")
        return "DESCONOCIDO"

    except TimeoutException:
        logger.error("Timeout: No se encontró el logo a tiempo.")
        return "DESCONOCIDO"
    except Exception as e:
        logger.error(f"Error inesperado en detectar_contexto_actual: {e}")
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
        logger.error(f"Error en buscar_opcion_contexto: {str(e)}")
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
        logger.error(f"Error en buscar_primera_opcion_valida: {str(e)}")
        return None

def take_screenshot(driver, filename="screenshot.png"):
        """Toma una captura de pantalla y la guarda en el directorio /tmp/screenshots/."""
        screenshot_dir = "/tmp/screenshots"
        os.makedirs(screenshot_dir, exist_ok=True)
        filepath = os.path.join(screenshot_dir, filename)
        try:
            driver.save_screenshot(filepath)
            logger.debug(f"Captura de pantalla guardada en {filepath}")
        except Exception as e:
            logger.debug(f"Error al tomar captura de pantalla {filename}: {e}")


def setup_driver():
    """Configura e inicializa el WebDriver estándar de Selenium para Render."""
    logger.info("Entrando a setup_driver (MODO ESTÁNDAR DE SELENIUM)")

    options = webdriver.ChromeOptions()
    logger.debug("ChromeOptions inicializado.")

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    download_dir = "/tmp/downloads"
    os.makedirs(download_dir, exist_ok=True)
    logger.debug("Directorio de descargas configurado en /tmp/downloads.")
    options.add_experimental_option("prefs", {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True
    })
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    # Optimized for Render: faster startup
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--blink-settings=imagesEnabled=false")
    logger.debug("Opciones de Chrome (headless, no-sandbox, etc.) añadidas.")

    # En el entorno de Render, el chromedriver que instala el Dockerfile está en el PATH del sistema.
    # Selenium lo encuentra automáticamente, por lo que no es necesario un Service object.
    logger.debug("Inicializando webdriver.Chrome...")

    try:
        driver = webdriver.Chrome(options=options)
        logger.info("¡ÉXITO! WebDriver de Selenium (Modo Estándar) inicializado.")
    except WebDriverException as e:
        logger.error(f"Error de WebDriver al inicializar webdriver.Chrome: {e}")
        logger.error("Esto puede indicar un problema con el chromedriver en el PATH del servidor.")
        return None
    except Exception as e:
        logger.error(f"Error inesperado al inicializar webdriver.Chrome: {e}")
        return None

    logger.debug("Aplicando parches de sigilo con selenium-stealth...")
    stealth(driver,
            languages=["es-ES", "es"],
            vendor="Google Inc.",
            platform="Win32",
            webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL Engine",
            fix_hairline=True,
            )
    logger.debug("Parches de sigilo aplicados.")

    return driver

def login_to_bci(driver, user, password):
    """Navega a la página de BCI y realiza el login."""
    try:
        logger.info(f"Navegando a: {LOGIN_URL}")
        driver.get(LOGIN_URL)

        # Check for CAPTCHA immediately
        if check_captcha_presence(driver):
            logger.error("CAPTCHA detected. Cannot proceed with automated login.")
            return False

        logger.debug("Esperando a que los campos de usuario y contraseña sean visibles.")
        # Shorter timeout for Render
        email_input = WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.CSS_SELECTOR, USER_SELECTOR)))
        email_input.send_keys(user)
        logger.debug("Usuario ingresado.")

        password_input = driver.find_element(By.CSS_SELECTOR, PASS_SELECTOR)
        password_input.send_keys(password)
        logger.debug("Contraseña ingresada. Credenciales completas.")

        logger.info("Haciendo clic en el botón de login...")
        login_button = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, BUTTON_SELECTOR)))
        login_button.click()
        logger.debug("Clic en botón de login realizado.")

        logger.info("Esperando redirección a 'busqueda-avanzada'...")
        # Shorter timeout for Render (within 30s limit)
        WebDriverWait(driver, 10).until(EC.url_contains('busqueda-avanzada'))
        logger.info(f"Login exitoso. Nueva URL: {driver.current_url}")

        # Quick verification that we're logged in
        try:
            WebDriverWait(driver, 5).until(
                lambda d: d.current_url == BUSQUEDA_AVANZADA_URL and
                          d.execute_script('return document.readyState') == 'complete'
            )
            logger.info("Sesión verificada correctamente.")
            return True
        except TimeoutException:
            logger.error("No se pudo verificar la sesión.")
            return False

    except TimeoutException as e:
        logger.error(f"Timeout durante el proceso de login: {e}")
        # Check if CAPTCHA appeared after timeout
        if check_captcha_presence(driver):
            logger.error("CAPTCHA appeared during login attempt.")
        return False
    except WebDriverException as e:
        logger.error(f"Error de WebDriver durante el proceso de login: {e}")
        return False
    except Exception as e:
        logger.error(f"Error inesperado durante el proceso de login: {e}")
        return False


def check_login_status(driver):
    """
    Verifica si el driver sigue logueado buscando un elemento clave en la página.

    Args:
        driver: Instancia de Selenium WebDriver

    Returns:
        bool: True si la sesión está activa, False en caso contrario
    """
    logger.info("Verificando estado de login")
    max_retries = 2
    for attempt in range(1, max_retries + 1):
        try:
            # Log current URL and page title for diagnostics
            current_url = driver.current_url
            page_title = driver.title
            logger.debug(f"Current URL: {current_url}")
            logger.debug(f"Page title: {page_title}")

            # Ensure page is fully loaded
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script('return document.readyState') == 'complete'
            )
            logger.debug("Document readyState: complete")

            # Dismiss any overlays or popups that might hide elements
            overlays = driver.find_elements(By.CSS_SELECTOR, ".cdk-overlay-backdrop, .modal-backdrop, .mat-dialog-backdrop, .bs-overlay-backdrop")
            for overlay in overlays:
                if overlay.is_displayed():
                    try:
                        driver.execute_script("arguments[0].click();", overlay)
                        logger.debug("Overlay dismissed.")
                        time.sleep(1)
                    except:
                        pass

            # Check if URL is the expected post-login page and page is loaded
            expected_url = "https://webproveedores.bciseguros.cl/busqueda-avanzada"
            if driver.current_url == expected_url and driver.execute_script('return document.readyState') == 'complete':
                logger.info("URL correcta y página cargada. Sesión activa.")
                return True
            else:
                logger.debug(f"URL actual: {driver.current_url}, expected: {expected_url}")
                logger.debug(f"Document readyState: {driver.execute_script('return document.readyState')}")
                return False
        except TimeoutException:
            logger.warning(f"Elemento 'Calendario' no encontrado en intento {attempt}. Intentando elementos alternativos.")
            # Try alternative element checks
            try:
                WebDriverWait(driver, 10).until(
                    EC.visibility_of_element_located((By.XPATH, "//a[contains(., 'Siniestros')]" ))
                )
                logger.info("Elemento alternativo 'Siniestros' encontrado y visible. Sesión activa.")
                return True
            except TimeoutException:
                logger.warning("Elementos alternativos tampoco encontrados.")
            if attempt < max_retries:
                logger.info("Refrescando página y reintentando...")
                driver.refresh()
                time.sleep(3)
            else:
                # Additional diagnostic: check if we're on the expected page
                if "busqueda-avanzada" not in current_url:
                    logger.debug("Not on expected post-login page (busqueda-avanzada not in URL)")
                return False
        except Exception as e:
            logger.error(f"Error al verificar el estado de login en intento {attempt}: {e}")
            if attempt < max_retries:
                logger.info("Refrescando página y reintentando...")
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
    logger.info("Esperando carga completa de la página y desaparición de loaders")
    try:
        # 1. Esperar a que el estado del documento sea 'complete'
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script('return document.readyState') == 'complete'
        )
        logger.debug("Documento cargado.")

        # 2. Esperar a que cualquier loader desaparezca
        loader_selector = "div.loader-container, .loader, [role='progressbar'], div.bs-page-loader"
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located((By.CSS_SELECTOR, loader_selector))
        )
        logger.debug("Loaders desaparecidos. La página está lista.")
        return True
    except TimeoutException:
        logger.warning("Timeout esperando la carga de la página o la desaparición de los loaders.")
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
    logger.info("Buscando pop-up de bienvenida")

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
                logger.debug(f"Intentando con selector: {selector}")
                accept_button = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                driver.execute_script("arguments[0].click();", accept_button)
                logger.info("Botón de aceptar clickeado con éxito.")
                button_found = True
                break
            except Exception:
                logger.debug(f"No se pudo interactuar con el botón usando {selector}")

        if not button_found:
            logger.warning("No se encontró ningún botón de aceptar visible y clickeable.")
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
                logger.debug(f"Backdrop '{selector}' desaparecido.")
            except:
                logger.debug(f"No se encontró el backdrop '{selector}' o ya desapareció.")

        logger.info("Toda la interfaz está lista para interactuar.")
        return True

    except Exception as e:
        error_msg = f"Error inesperado en manejar_popup_bienvenida: {str(e)[:200]}"
        logger.error(error_msg)
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
            logger.debug("Page loader desaparecido antes de manejar popups.")
        except TimeoutException:
            logger.warning("Timeout esperando que el page loader desaparezca.")

        # Primero intentar manejar el popup de bienvenida estándar
        try:
            manejar_popup_bienvenida(driver)
        except Exception as e:
            logger.warning(f"No se pudo manejar el popup de bienvenida: {str(e)[:200]}")

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
                        logger.info("Botón de cierre de diálogo encontrado y clickeado.")
                        time.sleep(1)  # Esperar a que se cierre la animación
                        # Verificar que el botón ya no esté visible
                        if not boton.is_displayed():
                            logger.debug("Verificación: Botón de cierre ya no visible.")
                        else:
                            logger.warning("Advertencia: Botón de cierre aún visible después del clic.")
                except:
                    continue

        except Exception as e:
            logger.warning(f"Error al intentar cerrar diálogos: {str(e)[:200]}")

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
                        logger.info("Backdrop encontrado y clickeado.")
                        time.sleep(1)
                        # Verificar que el backdrop ya no esté visible
                        if not backdrop.is_displayed():
                            logger.debug("Verificación: Backdrop ya no visible.")
                        else:
                            logger.warning("Advertencia: Backdrop aún visible después del clic.")
                except:
                    continue
        except Exception as e:
            logger.warning(f"Error al manejar backdrops: {str(e)[:200]}")

        # Verificación final: Asegurarse de que no queden elementos de popup visibles
        try:
            remaining_popups = driver.find_elements(By.CSS_SELECTOR, ".cdk-overlay-container .cdk-overlay-pane, .modal.show, .mat-dialog-container")
            if remaining_popups:
                logger.warning(f"Aún hay {len(remaining_popups)} elementos de popup visibles.")
                for popup in remaining_popups:
                    try:
                        # Intentar cerrar con Escape
                        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                        time.sleep(1)
                        if not popup.is_displayed():
                            logger.debug("Verificación: Popup cerrado con Escape.")
                            break
                    except:
                        continue
            else:
                logger.debug("Verificación: No se detectan popups visibles.")
        except Exception as e:
            logger.warning(f"Error en verificación final de popups: {str(e)[:200]}")

    except Exception as e:
        logger.error(f"Error inesperado en manejar_posibles_popups: {str(e)[:200]}")
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
    logger.info(f"Asegurando contexto {compania_objetivo.upper()} (v9.8)")

    opciones_menu = {
        "BCI": "BCI Seguros",
        "ZENIT": "Zenit Seguros"
    }
    texto_opcion_menu = opciones_menu.get(compania_objetivo.upper())
    if not texto_opcion_menu:
        logger.error(f"Compañía objetivo '{compania_objetivo}' no es válida.")
        return False

    for attempt in range(1, max_retries + 1):
        logger.debug(f"Intento {attempt}/{max_retries}...")

        contexto_actual = detectar_contexto_actual(driver)

        if contexto_actual == compania_objetivo.upper():
            logger.info(f"Éxito: El contexto actual ya es {compania_objetivo.upper()}.")
            return True

        if contexto_actual == "DESCONOCIDO":
            logger.warning("No se pudo determinar el contexto actual. Asumiendo BCI por defecto.")
            contexto_actual = "BCI"

        logger.info(f"Contexto actual es {contexto_actual}. Intentando cambiar a {compania_objetivo.upper()}...")

        try:
            # Paso 1: Encontrar el dropdown arrow trigger - Multiple strategies
            dropdown_arrow = None
            for attempt in range(1, 4):
                try:
                    # Strategy 1: Original selector
                    try:
                        dropdown_arrow = WebDriverWait(driver, 10).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, "img[src*='icon-ui-nav-flecha-abajo.svg']"))
                        )
                        logger.debug("Dropdown arrow found with strategy 1")
                        break
                    except TimeoutException:
                        # Strategy 2: More flexible selector
                        try:
                            dropdown_arrow = WebDriverWait(driver, 10).until(
                                EC.element_to_be_clickable((By.CSS_SELECTOR, "img[src*='flecha-abajo']"))
                            )
                            logger.debug("Dropdown arrow found with strategy 2")
                            break
                        except TimeoutException:
                            # Strategy 3: Look for any dropdown trigger
                            try:
                                dropdown_arrow = WebDriverWait(driver, 10).until(
                                    EC.element_to_be_clickable((By.XPATH, "//img[contains(@src, 'flecha') or contains(@alt, 'menu') or contains(@class, 'dropdown')]"))
                                )
                                logger.debug("Dropdown arrow found with strategy 3")
                                break
                            except TimeoutException:
                                logger.warning(f"Dropdown arrow not found in attempt {attempt}")
                                if attempt < 3:
                                    time.sleep(2)
                                    continue
                                else:
                                    raise TimeoutException("No se pudo encontrar el dropdown arrow después de múltiples estrategias")
                except Exception as e:
                    logger.error(f"Error finding dropdown arrow in attempt {attempt}: {e}")
                    if attempt == 3:
                        raise e
    
            # Paso 2: Hacer clic en el dropdown arrow para abrir el menú de contexto
            driver.execute_script("arguments[0].click();", dropdown_arrow)
            logger.debug("Clic en dropdown arrow realizado.")
    
            # Paso 3: Esperar a que aparezcan las opciones del menú - Enhanced waiting
            menu_appeared = False
            for wait_attempt in range(1, 4):
                try:
                    WebDriverWait(driver, 15).until(
                        lambda d: len(d.find_elements(By.CSS_SELECTOR, "a.bs-selector.grande, a.bs-selector.grande.visited")) > 0 or
                                  len(d.find_elements(By.XPATH, "//a[contains(@class, 'selector') and contains(@class, 'grande')]")) > 0 or
                                  len(d.find_elements(By.XPATH, "//div[contains(@class, 'dropdown-menu')]//a")) > 0
                    )
                    menu_appeared = True
                    logger.debug("Opciones del menú de contexto cargadas.")
                    break
                except TimeoutException:
                    logger.warning(f"Menu options not loaded in wait attempt {wait_attempt}")
                    if wait_attempt < 3:
                        time.sleep(2)
                        # Try clicking dropdown again
                        try:
                            driver.execute_script("arguments[0].click();", dropdown_arrow)
                        except:
                            pass
                    else:
                        raise TimeoutException("Las opciones del menú de contexto no aparecieron después de múltiples intentos")
    
            # Paso 4: Encontrar y seleccionar la opción correcta - Enhanced search
            option_found = False
            for search_attempt in range(1, 4):
                try:
                    # Buscar opciones con las clases especificadas
                    options = driver.find_elements(By.CSS_SELECTOR, "a.bs-selector.grande, a.bs-selector.grande.visited")
                    if not options:
                        # Fallback: buscar en cualquier menú desplegable
                        options = driver.find_elements(By.XPATH, "//div[contains(@class, 'dropdown-menu')]//a")
    
                    for option in options:
                        if option.is_displayed() and option.is_enabled():
                            option_text = option.text.strip()
                            if texto_opcion_menu.lower() in option_text.lower():
                                logger.info(f"Opción encontrada: '{option_text}'. Seleccionando.")
                                driver.execute_script("arguments[0].click();", option)
                                option_found = True
                                break
    
                    if option_found:
                        break
                    else:
                        logger.warning(f"Opción '{texto_opcion_menu}' no encontrada en búsqueda {search_attempt}")
                        if search_attempt < 3:
                            time.sleep(2)
                            continue
                except Exception as e:
                    logger.error(f"Error en búsqueda de opción {search_attempt}: {e}")
                    if search_attempt < 3:
                        time.sleep(2)
                        continue
                    else:
                        raise e
    
            if not option_found:
                logger.error(f"No se encontró la opción '{texto_opcion_menu}' en el menú después de múltiples búsquedas.")
                raise TimeoutException(f"La opción '{texto_opcion_menu}' no fue encontrada en el menú.")

            # Paso 5: Esperar y verificar el cambio - Enhanced verification
            logger.info("Cambio de contexto solicitado. Esperando carga de página...")
            esperar_pagina_cargada(driver)
            manejar_popup_bienvenida(driver)

            logger.info(f"Esperando la confirmación del cambio a {compania_objetivo.upper()}...")
            context_changed = False
            for verify_attempt in range(1, 4):
                try:
                    WebDriverWait(driver, 20).until(
                        lambda d: detectar_contexto_actual(d) == compania_objetivo.upper()
                    )
                    context_changed = True
                    logger.info(f"Contexto verificado exitosamente en intento {verify_attempt}")
                    break
                except TimeoutException:
                    logger.warning(f"Verificación de contexto fallida en intento {verify_attempt}")
                    if verify_attempt < 3:
                        # Try refreshing page state
                        time.sleep(3)
                        driver.refresh()
                        esperar_pagina_cargada(driver)
                        manejar_popup_bienvenida(driver)
                    else:
                        # Final fallback: check URL as secondary verification
                        current_url = driver.current_url.lower()
                        if compania_objetivo.upper() == "ZENIT" and "zenit" in current_url:
                            logger.info("Contexto ZENIT verificado por URL como fallback")
                            context_changed = True
                        elif compania_objetivo.upper() == "BCI" and ("bciseguros" in current_url or "busqueda-avanzada" in current_url):
                            logger.info("Contexto BCI verificado por URL como fallback")
                            context_changed = True
                        else:
                            raise TimeoutException(f"No se pudo verificar el cambio a {compania_objetivo.upper()} después de múltiples intentos")

            # Extra wait specifically for BCI context change due to slower loader disappearance
            if compania_objetivo.upper() == "BCI":
                logger.info("Extra wait for BCI context change to ensure page loader fully disappears...")
                max_retries = 5
                for attempt in range(1, max_retries + 1):
                    try:
                        WebDriverWait(driver, 10 + attempt * 5).until(
                            EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.bs-page-loader"))
                        )
                        logger.debug(f"Page loader fully disappeared for BCI after {attempt} attempts.")
                        break
                    except TimeoutException:
                        if attempt == max_retries:
                            logger.warning("Warning: Page loader still visible after extra waits for BCI.")
                        else:
                            time.sleep(2)

            logger.info(f"Éxito: El contexto se cambió a {compania_objetivo.upper()} correctamente.")
            return True

        except TimeoutException as e:
            logger.error(f"Error de Timeout en el intento {attempt}: {e}")
            take_screenshot(driver, f"contexto_timeout_attempt_{attempt}.png")
            if attempt == max_retries:
                logger.error("Se agotaron los reintentos para cambiar de contexto.")
                traceback.print_exc()
                return False
            time.sleep(3)

        except Exception as e:
            logger.error(f"Error inesperado en el intento {attempt}: {e}")
            take_screenshot(driver, f"contexto_error_inesperado_attempt_{attempt}.png")
            if attempt == max_retries:
                logger.error("Se agotaron los reintentos debido a errores inesperados.")
                traceback.print_exc()
                return False
            time.sleep(3)

    return False


def extraer_datos_pdf(driver):
    """
    Encuentra el enlace 'VER DENUNCIO', abre el PDF en una nueva pestaña,
    extrae el 'Relato', 'VIN' y 'Número de Asegurado', y luego cierra la pestaña.
    """
    logger.info("Iniciando extracción de PDF")
    pdf_data = {"Relato": None, "VIN": None, "NumeroAsegurado": None}
    original_window = driver.current_window_handle

    try:
        ver_denuncio_link = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//a[contains(., 'VER DENUNCIO')]" ))
        )
        logger.info("Enlace 'VER DENUNCIO' encontrado y clickeado.")
        ver_denuncio_link.click()

        WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))
        for window_handle in driver.window_handles:
            if window_handle != original_window:
                driver.switch_to.window(window_handle)
                break
        logger.debug(f"Cambiado a la nueva pestaña del PDF: {driver.current_url}")

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
        logger.debug("Contenido del PDF descargado y decodificado.")

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

        logger.debug(f"Datos extraídos del PDF: {pdf_data}")

    except TimeoutException:
        logger.warning("No se encontró el enlace 'VER DENUNCIO' o la pestaña del PDF no apareció.")
        take_screenshot(driver, "pdf_link_no_encontrado.png")
    except Exception as e:
        logger.error(f"Fallo inesperado durante la extracción del PDF: {e}")
        traceback.print_exc()
        take_screenshot(driver, "pdf_extraccion_error.png")
    finally:
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(original_window)
            logger.debug("Pestaña del PDF cerrada. Volviendo a la pestaña original.")
    return pdf_data

def sondear_siniestros_asignados(driver, compania):
    """
    Orquesta el proceso de scraping en la pestaña 'Asignados'.
    v4.5: Añade el parámetro compania para etiquetar los datos.
    """
    logger.info(f"Iniciando sondeo de Siniestros Asignados para {compania.upper()}")

    try:
        # Las pestañas están directamente accesibles, no es necesario navegar a "Siniestros" y "Gestión de siniestros"
        logger.info("Navegando a la pestaña 'Asignados'")

        # Enhanced page loader waiting with retry loop and increasing delays
        logger.info("Waiting for page loader to fully disappear before navigating to 'Asignados'...")
        max_loader_retries = 5
        for loader_attempt in range(1, max_loader_retries + 1):
            try:
                # Multiple loader selectors for robustness
                loader_selectors = [
                    "div.bs-page-loader",
                    "div.loader-container",
                    ".loader",
                    "[role='progressbar']",
                    "div.spinner"
                ]
                loader_found = False
                for selector in loader_selectors:
                    try:
                        WebDriverWait(driver, 5).until(
                            EC.invisibility_of_element_located((By.CSS_SELECTOR, selector))
                        )
                        logger.debug(f"Loader '{selector}' disappeared.")
                        loader_found = True
                        break
                    except TimeoutException:
                        continue

                if loader_found:
                    logger.debug(f"Page loader fully disappeared after {loader_attempt} attempts.")
                    break
                else:
                    # If no loaders found, assume page is ready
                    logger.debug("No loaders detected, assuming page is ready.")
                    break

            except TimeoutException:
                if loader_attempt == max_loader_retries:
                    logger.warning("Warning: Page loader still visible after all retries. Proceeding anyway.")
                else:
                    time.sleep(2)

        # Intentar clickear con reintentos y manejo de excepciones - Multiple selector strategies
        max_retries = 5
        asignados_tab = None
        for attempt in range(1, max_retries + 1):
            try:
                # Strategy 1: Original XPath
                try:
                    asignados_tab = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//span[contains(@class, 'font-bold') and contains(@class, 'white-space-nowrap') and contains(@class, 'm-0') and contains(@class, 'ng-star-inserted') and contains(text(), 'Asignados')]" )))
                    logger.debug("Found 'Asignados' tab with strategy 1")
                except TimeoutException:
                    # Strategy 2: More flexible XPath
                    try:
                        asignados_tab = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Asignados')]" )))
                        logger.debug("Found 'Asignados' tab with strategy 2")
                    except TimeoutException:
                        # Strategy 3: Look for any element containing 'Asignados'
                        try:
                            asignados_tab = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), 'Asignados')]" )))
                            logger.debug("Found 'Asignados' tab with strategy 3")
                        except TimeoutException:
                            # Strategy 4: Check for tab links
                            try:
                                asignados_tab = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//a[contains(@href, 'asignados') or contains(text(), 'Asignados')]" )))
                                logger.debug("Found 'Asignados' tab with strategy 4")
                            except TimeoutException:
                                logger.warning(f"No 'Asignados' tab found in attempt {attempt}")
                                continue

                if asignados_tab:
                    driver.execute_script("arguments[0].click();", asignados_tab)
                    logger.info(f"Click en pestaña 'Asignados' realizado exitosamente en intento {attempt}.")
                    break
            except (ElementClickInterceptedException, StaleElementReferenceException) as e:
                logger.warning(f"Error en intento {attempt} al clickear 'Asignados': {e}")
                if attempt == max_retries:
                    raise e
                time.sleep(3)
                # Re-encontrar el elemento después de esperar
                continue

        esperar_pagina_cargada(driver)

        page_num = 1
        while True:
            logger.info(f"Recolectando datos de tabla en página {page_num}...")
            row_selector = "//tr[contains(@class, 'ng-star-inserted')]"
            try:
                WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.XPATH, row_selector)))
                rows = driver.find_elements(By.XPATH, row_selector)
                logger.debug(f"Encontradas {len(rows)} filas en la página {page_num}.")

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

                logger.debug(f"Datos de {len(rows)} filas guardados.")

            except TimeoutException:
                logger.warning("Timeout esperando filas de 'Asignados'. Verificando estado de página...")
                # Check if page is still responsive and we're on the right tab
                try:
                    current_url = driver.current_url
                    if "busqueda-avanzada" not in current_url:
                        logger.error("Página cambió inesperadamente. Deteniendo recolección.")
                        break
                    # Try to re-click the Asignados tab
                    try:
                        asignados_tab_retry = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Asignados')]" )))
                        driver.execute_script("arguments[0].click();", asignados_tab_retry)
                        logger.info("Re-click en pestaña 'Asignados' realizado.")
                        esperar_pagina_cargada(driver)
                        continue
                    except TimeoutException:
                        logger.error("No se pudo re-acceder a la pestaña 'Asignados'. Finalizando recolección.")
                        break
                except Exception as e:
                    logger.error(f"Error verificando estado de página: {e}")
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
                        logger.warning("Could not get first row ID for pagination check.")

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
                            logger.info("Detectado bucle de paginación: La primera fila no cambió. Fin de la recolección.")
                            break # Break if we are stuck on the same page content
                elif not rows_after_pagination: # If no rows are found on the new page, it's the end
                    logger.info("No se encontraron filas en la nueva página. Fin de la recolección.")
                    break

            except (NoSuchElementException, TimeoutException):
                logger.info("No hay más páginas o el botón de siguiente está deshabilitado. Fin de la recolección.")
                break

    except Exception as e:
        logger.error(f"Error crítico durante la recolección de la tabla: {e}")
        traceback.print_exc()
        take_screenshot(driver, "error_critico_recoleccion_tabla.png")

def sondear_siniestros_liquidacion(driver, compania):
    """
    Orquesta el proceso de descarga y procesamiento de Excel para Análisis de Liquidación.
    """
    logger.info(f"Iniciando sondeo de Siniestros Liquidación para {compania.upper()}")

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
        except NoSuchElementException:
            pass

        # DEBUG: Inspeccionar pestañas disponibles en el submenu
        if submenu_container and submenu_visible:
            try:
                tabs = submenu_container.find_elements(By.TAG_NAME, "a")
                for tab in tabs:
                    pass
            except Exception as e:
                pass

        # DEBUG: Inspeccionar todas las pestañas con data-toggle="tab" en toda la página
        try:
            all_tabs = driver.find_elements(By.XPATH, "//a[@data-toggle='tab']")
            for tab in all_tabs:
                text = tab.text.strip()
                visible = tab.is_displayed()
                enabled = tab.is_enabled()
                data_toggle = tab.get_attribute("data-toggle")
        except Exception as e:
            pass

        # DEBUG: Verificar elementos con data-toggle u otros atributos relacionados con descarga
        try:
            elements_with_data_toggle = driver.find_elements(By.XPATH, "//*[@data-toggle]")
            for i, elem in enumerate(elements_with_data_toggle):
                tag = elem.tag_name
                data_toggle = elem.get_attribute("data-toggle")
                text = elem.text.strip()
                visible = elem.is_displayed()
                enabled = elem.is_enabled() if tag in ['button', 'input', 'a'] else 'N/A'

            # Otros atributos relacionados con descarga
            download_related = driver.find_elements(By.XPATH, "//*[@download or @href[contains(., 'excel') or @href[contains(., 'download')]]")
            for i, elem in enumerate(download_related):
                tag = elem.tag_name
                href = elem.get_attribute("href") or ""
                download = elem.get_attribute("download") or ""
                text = elem.text.strip()
        except Exception as e:
            pass

        page_num = 1
        while True:
            logger.info(f"Recolectando datos de tabla en página {page_num}...")
            row_selector = "//tr[contains(@class, 'ng-star-inserted')]"
            try:
                WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.XPATH, row_selector)))
                rows = driver.find_elements(By.XPATH, row_selector)
                logger.debug(f"Encontradas {len(rows)} filas en la página {page_num}.")

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

                logger.debug(f"Datos de {len(rows)} filas guardados.")

            except TimeoutException:
                logger.info("No se encontraron más filas de 'Liquidación' en esta página. Finalizando recolección.")
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
                        logger.warning("Could not get first row ID for pagination check.")

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
                            logger.info("Detectado bucle de paginación: La primera fila no cambió. Fin de la recolección.")
                            break # Break if we are stuck on the same page content
                elif not rows_after_pagination: # If no rows are found on the new page, it's the end
                    logger.info("No se encontraron filas en la nueva página. Fin de la recolección.")
                    break

            except (NoSuchElementException, TimeoutException):
                logger.info("No hay más páginas o el botón de siguiente está deshabilitado. Fin de la recolección.")
                break

    except Exception as e:
        logger.error(f"Error en sondear_siniestros_liquidacion: {e}")
        traceback.print_exc()
        take_screenshot(driver, "error_liquidacion.png")

    logger.info("Proceso de sondeo de liquidación completado.")
    logger.info("Proceso de sondeo completado.")

def scrape_full_data(driver):
    """
    Orquesta el proceso completo de scraping para todas las compañías definidas.
    """
    logger.info("Iniciando proceso de scraping completo")

    companias = ["BCI", "ZENIT"]
    all_data = []

    for compania in companias:
        logger.info(f"Procesando compañía: {compania.upper()}")
        if asegurar_contexto(driver, compania):
            data = list(sondear_siniestros_asignados(driver, compania))
            all_data.extend(data)
            data = list(sondear_siniestros_liquidacion(driver, compania))
            all_data.extend(data)
        else:
            logger.warning(f"No se pudo asegurar el contexto para {compania.upper()}. Saltando esta compañía.")
            take_screenshot(driver, f"error_contexto_{compania.lower()}.png")

    all_data = list({item['NumeroSiniestro']: item for item in all_data}.values())

    for item in all_data:
        yield item
