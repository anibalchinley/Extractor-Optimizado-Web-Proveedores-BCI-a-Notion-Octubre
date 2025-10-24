import os
import json
import requests
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo # Importar ZoneInfo para manejo de zonas horarias
from dotenv import load_dotenv

class NotionManager:
    def __init__(self, notion_token, db_ids):
        self.notion_token = notion_token
        self.db_ids = db_ids
        self.headers = {
            "Authorization": f"Bearer {self.notion_token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        }

    def _get_page_properties(self, page_id):
        url = f"https://api.notion.com/v1/pages/{page_id}"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def _create_page_in_db(self, database_id, properties):
        url = "https://api.notion.com/v1/pages"
        data = {"parent": {"database_id": database_id}, "properties": properties}
        try: # Add try-except block here
            response = requests.post(url, headers=self.headers, json=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"  ERROR: Notion API Page Creation Failed for DB: {database_id}", flush=True)
            print(f"  Properties Payload: {json.dumps(properties, indent=2, ensure_ascii=False)}", flush=True) # Print the payload
            if e.response is not None:
                print(f"  Notion API Response: {e.response.json()}", flush=True) # Print Notion's error response
            raise e # Re-raise the exception

    def _apply_template_to_page(self, page_id, template_id):
        url = f"https://api.notion.com/v1/pages/{page_id}"
        data = {"template_id": template_id}
        try:
            response = requests.patch(url, headers=self.headers, json=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"  ERROR: Notion API Apply Template Failed for Page ID: {page_id}, Template ID: {template_id}", flush=True)
            if e.response is not None:
                print(f"  Notion API Response: {e.response.json()}", flush=True)
            raise e

    def _query_database(self, database_id, filter_property, filter_value, filter_type="text", query_mode="equals"):
        url = f"https://api.notion.com/v1/databases/{database_id}/query"
        
        # Clean and normalize the filter_value
        cleaned_filter_value = unicodedata.normalize('NFKC', str(filter_value)).strip()

        filter_payload = {
            "filter": {
                "property": filter_property,
                filter_type: {
                    query_mode: cleaned_filter_value
                }
            }
        }
        try: # Add try-except block here
            response = requests.post(url, headers=self.headers, json=filter_payload)
            response.raise_for_status()
            results = response.json().get("results", [])
            return results
        except requests.exceptions.RequestException as e:
            print(f"  ERROR: Notion API Query Failed for DB: {database_id}, Prop: {filter_property}, Value: {cleaned_filter_value}, Type: {filter_type}", flush=True)
            if e.response is not None:
                print(f"  Notion API Response: {e.response.json()}", flush=True) # Print Notion's error response
            raise e # Re-raise the exception so the main error handling still catches it

    def process_and_insert_siniestros(self, siniestros_data):
        print("--- Iniciando inserción de datos en Notion ---", flush=True)
        for i, siniestro in enumerate(siniestros_data):
            print(f"Procesando siniestro {i+1}/{len(siniestros_data)}: {siniestro.get('NumeroSiniestro')}", flush=True)
            try:
                # Regla: No sobrescribir. Buscar siniestro por NumeroSiniestro
                siniestro_notion_id = None
                print(f"  DEBUG: Querying Siniestros DB: ID={self.db_ids['DATABASE_ID_SINIESTROS']}, Prop='Siniestro', Value='{siniestro.get('NumeroSiniestro')}', Type='title'", flush=True)
                existing_siniestros = self._query_database(
                    self.db_ids["DATABASE_ID_SINIESTROS"],
                    "Siniestro",
                    siniestro.get('NumeroSiniestro'),
                    filter_type="title",
                    query_mode="contains"  # Usar 'contains' para buscar el siniestro
                )

                if existing_siniestros:
                    print(f"  Siniestro {siniestro.get('NumeroSiniestro')} ya existe en Notion. No se sobrescribe.", flush=True)
                    siniestro_notion_id = existing_siniestros[0]["id"]
                else:
                    # --- Manejar Cliente ---
                    cliente_id = None
                    print(f"  DEBUG: Querying Clientes DB: ID={self.db_ids['DATABASE_ID_CLIENTES']}, Prop='Rut', Value='{siniestro.get('RutAsegurado')}', Type='text'", flush=True)
                    existing_clientes = self._query_database(
                        self.db_ids["DATABASE_ID_CLIENTES"],
                        "Rut", # Propiedad de búsqueda para Cliente
                        siniestro.get('RutAsegurado'),
                        filter_type="rich_text" # Rut es tipo texto
                    )
                    if existing_clientes:
                        cliente_id = existing_clientes[0]["id"]
                        print(f"  Cliente {siniestro.get('NombreAsegurado')} ya existe.", flush=True)
                    else:
                        # Limpiar datos antes de construir el payload
                        email = siniestro.get('CorreoAsegurado') or None
                        telefono = siniestro.get('TelefonoAsegurado') or None

                        cliente_properties = {
                            "Nombre": {"title": [{"text": {"content": siniestro.get('NombreAsegurado', '').title()}}]}, # Title
                            "Rut": {"rich_text": [{"text": {"content": siniestro.get('RutAsegurado')}}]}, # Text
                            "Teléfono (C)": {"phone_number": telefono}, # Phone Number
                            "Correo (C)": {"email": email} # Email
                        }
                        new_cliente = self._create_page_in_db(self.db_ids["DATABASE_ID_CLIENTES"], cliente_properties)
                        cliente_id = new_cliente["id"]
                        print(f"  Cliente {siniestro.get('NombreAsegurado')} creado en Notion.", flush=True)

                    # --- Manejar Patente ---
                    patente_id = None
                    print(f"  DEBUG: Querying Patentes DB: ID={self.db_ids['DATABASE_ID_PATENTES']}, Prop='Patente', Value='{siniestro.get('Patente')}', Type='title'", flush=True)
                    existing_patentes = self._query_database(
                        self.db_ids["DATABASE_ID_PATENTES"],
                        "Patente", # Propiedad de búsqueda para Patente
                        siniestro.get('Patente'),
                        filter_type="title" # Patente es tipo title
                    )
                    if existing_patentes:
                        patente_id = existing_patentes[0]["id"]
                        print(f"  Patente {siniestro.get('Patente')} ya existe.", flush=True)
                    else:
                        patente_properties = {
                            "Patente": {"title": [{"text": {"content": siniestro.get('Patente')}}]}, # Title
                            "Marca (P)": {"select": {"name": siniestro.get('Marca')}}, # Select
                            "Modelo (P)": {"select": {"name": siniestro.get('Modelo')}} # Select
                        }
                        new_patente = self._create_page_in_db(self.db_ids["DATABASE_ID_PATENTES"], patente_properties)
                        patente_id = new_patente["id"]
                        print(f"  Patente {siniestro.get('Patente')} creada en Notion.", flush=True)

                    # --- Crear Siniestro ---
                    # Formatear la fecha de agendamiento a ISO 8601 (YYYY-MM-DDTHH:MM:SS)
                    fecha_agendamiento_str = siniestro.get('FechaEstimadaIngreso', '')
                    formatted_date = None
                    if fecha_agendamiento_str:
                        try:
                            # Asumiendo formato DD/MM/YYYY HH:MM
                            # Parsear la fecha y hora como naive (sin información de zona horaria)
                            parsed_date_naive = datetime.strptime(fecha_agendamiento_str, '%d/%m/%Y %H:%M')

                            # Definir la zona horaria local (Chile/Santiago)
                            # Asegúrate de que 'tzdata' esté instalado para que ZoneInfo funcione correctamente
                            local_timezone = ZoneInfo("America/Santiago")

                            # Localizar la fecha y hora a la zona horaria de Santiago
                            parsed_date_local = parsed_date_naive.replace(tzinfo=local_timezone)

                            # Convertir la fecha y hora localizada a UTC
                            parsed_date_utc = parsed_date_local.astimezone(ZoneInfo("UTC"))

                            # Formatear a ISO 8601 para Notion
                            formatted_date = parsed_date_utc.strftime('%Y-%m-%dT%H:%M:%S')

                        except ValueError:
                            print(f"  ADVERTENCIA: Formato de fecha/hora inválido para Agendamiento: {fecha_agendamiento_str}. Se omitirá la propiedad.", flush=True)
                        except Exception as e:
                            print(f"  ERROR: Fallo al procesar la zona horaria para Agendamiento: {e}. Se omitirá la propiedad.", flush=True)

                    tipo_seccion = siniestro.get('TipoSeccion')
                    if tipo_seccion == 'Liquidacion':
                        status_value = "Análisis de Liquidación"
                    else:
                        estado_contacto = siniestro.get('EstadoContacto')
                        status_value = estado_contacto if estado_contacto else 'Sin Estado'

                    siniestro_properties = {
                        "Siniestro": {"title": [{"text": {"content": f"{siniestro.get('NumeroSiniestro')} 🤖"}}]}, # Title + Emoji
                        "CÍA": {"select": {"name": siniestro.get('Compania')}},
                        "Agend./Status": {"select": {"name": status_value}} # Select
                    }

                    # Añadir la propiedad de Tipo de Daño solo si no está vacía
                    tipo_danio = siniestro.get('TipoDanio', '')
                    if tipo_danio:
                        siniestro_properties["Tipo de Daño"] = {"select": {"name": tipo_danio}}

                    # Añadir la propiedad de fecha solo si es válida
                    if formatted_date:
                        siniestro_properties["📅Agendamiento"] = {"date": {"start": formatted_date}}

                    # Añadir relaciones si existen los IDs
                    if cliente_id:
                        siniestro_properties["Nombre"] = {"relation": [{"id": cliente_id}]}
                    if patente_id:
                        siniestro_properties["Patente"] = {"relation": [{"id": patente_id}]}
                    
                    siniestro_template_id = "27dda5b4e53742a083bf6aa2a66c0697"
                    new_siniestro = self._create_page_in_db(self.db_ids["DATABASE_ID_SINIESTROS"], siniestro_properties)
                    siniestro_notion_id = new_siniestro["id"]
                    print(f"  Siniestro {siniestro.get('NumeroSiniestro')} creado en Notion con ID: {siniestro_notion_id}", flush=True)

            except requests.exceptions.RequestException as e:
                print(f"  ERROR de red o API al procesar siniestro {siniestro.get('NumeroSiniestro')}: {e}", flush=True)
            except Exception as e:
                print(f"  ERROR inesperado al procesar siniestro {siniestro.get('NumeroSiniestro')}: {e}", flush=True)

        print("--- Inserción de datos en Notion finalizada. ---", flush=True)