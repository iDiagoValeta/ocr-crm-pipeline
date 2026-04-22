import base64
import csv
import io
import json
import logging
import os
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from PIL import Image
from rapidfuzz import fuzz, process, utils

import azure.functions as func
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import BlobServiceClient
from openai import AzureOpenAI

# ————————————————————————————————————————————————————————————————————————————
# CONFIGURACIÓN Y CONSTANTES
# ————————————————————————————————————————————————————————————————————————————

PROVINCE_MATCH_THRESHOLD = 60
COURSE_MATCH_THRESHOLD = 60
TITULACION_MATCH_THRESHOLD = 70

GPT_MAX_OUTPUT_TOKENS = 32000
GPT_REASONING_EFFORT = "high"
GPT_MODEL = "gpt-5.2"

OCR_MODEL = "prebuilt-layout"
IMAGE_MEDIA_TYPE = "image/jpeg"
SELECTION_MARK_CONFIDENCE_THRESHOLD = 0.80
WORD_CONFIDENCE_THRESHOLD = 0.80
WORD_CONFIDENCE_HIGH_CUTOFF = 0.40
CENTER_REVIEW_SCORE_THRESHOLD = 80

DI_ADDON_BARCODES        = False  # QR reverso contiene solo info promocional del CEU
DI_ADDON_KEY_VALUE_PAIRS = True   # pares etiqueta→valor anverso — sin coste adicional
DI_ADDON_HIGH_RESOLUTION = False  # add-on alta resolución (documentos grandes/densos)
DI_ADDON_QUERY_FIELDS    = False  # add-on preguntas directas sobre campos del anverso

DI_QUERY_FIELDS_FRONT: List[str] = [
    "DNI o NIF del alumno",
    "Nombre del alumno",
    "Primer apellido del alumno",
    "Segundo apellido del alumno",
    "Teléfono de contacto",
    "Email o correo electrónico",
    "Nombre del centro educativo",
    "Localidad del centro",
    "Provincia del centro",
]

SELECTION_MARK_CONFIDENCE_THRESHOLD_HIGH_RES = 0.85
QF_CONFIDENCE_FLAG_THRESHOLD = 0.60
QF_CONFIDENCE_SAFE_THRESHOLD = 0.85

DEFAULT_SESSION_ID = 100000036
DEFAULT_CAMPAIGN_ID = "852c6f0a-a94d-f011-877a-7c1e52fbd0c9"
DEFAULT_REQUEST_TYPE = 100000000
DEFAULT_OWNER_ID = "57c8523f-a1de-e411-80f1-c4346bad129c"

OCR_LETTER_TO_DIGIT = {
    'O': '0', 'Q': '0', 'D': '0',    # Formas circulares/ovaladas
    'I': '1', 'L': '1',              # Líneas verticales
    'Z': '2',                        # Forma angular similar
    'E': '3',                        # Forma similar invertida
    'A': '4',                        # Trazo angular en manuscrito
    'S': '5', 'J': '5',              # Curvas similares
    'G': '6',                        # Forma circular con cola
    'T': '7',                        # Trazo horizontal superior
    'B': '8',                        # Doble curva
    'P': '9',                        # Forma circular superior
}

OCR_DIGIT_TO_LETTER = {
    '0': 'O',
    '1': 'I',
    '2': 'Z',
    '3': 'E',
    '4': 'A',
    '5': 'S',
    '6': 'G',
    '7': 'T',
    '8': 'B',
    '9': 'P',
}

EXTRACTION_JSON_FORMAT = {
    "type": "json_object"
}

AZURE_STORAGE_CONN_STR = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
BLOB_CONTAINER_NAME = os.environ.get("AZURE_BLOB_CONTAINER", "fichas-si-escaneadas")

_POLYGON_COORD_RE = re.compile(r'\[([0-9.]+),\s*([0-9.]+)\]')

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
BASE_DIR = os.path.join(PROJECT_ROOT, "centros")
BASE_DIR_TITULACION = os.path.join(PROJECT_ROOT, "titulacion")
BASE_DIR_CURSO = os.path.join(PROJECT_ROOT, "curso")
BASE_DIR_LOCALIDADES = os.path.join(PROJECT_ROOT, "localidades")
DEBUG_DIR = os.environ.get("DEBUG_DIR", os.path.join(PROJECT_ROOT, "debug"))
SAVE_DEBUG_SNAPSHOT = os.environ.get("SAVE_DEBUG_SNAPSHOT", "").lower() in {"1", "true", "yes", "on"}
DOC_INTEL_ENDPOINT = os.environ.get("DOCUMENT_INTELLIGENCE_ENDPOINT", "")
DOC_INTEL_KEY = os.environ.get("DOCUMENT_INTELLIGENCE_KEY", "")

OPENAI_ENDPOINT = os.environ.get("OPENAI_ENDPOINT", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_DEPLOYMENT_NAME = os.environ.get("OPENAI_DEPLOYMENT_NAME", GPT_MODEL)
OPENAI_API_VERSION = os.environ.get("OPENAI_API_VERSION", "2025-03-01-preview")

# ————————————————————————————————————————————————————————————————————————————
# LOGGING Y CLIENTES
# ————————————————————————————————————————————————————————————————————————————

_client_openai: Optional[AzureOpenAI] = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def get_openai_client() -> AzureOpenAI:
    """Singleton para cliente de Azure OpenAI (Responses API)."""
    global _client_openai
    if _client_openai is None:
        _client_openai = AzureOpenAI(
            api_key=OPENAI_API_KEY,
            azure_endpoint=OPENAI_ENDPOINT,
            api_version=OPENAI_API_VERSION
        )
    return _client_openai


# ————————————————————————————————————————————————————————————————————————————
# GESTOR DE DATOS MAESTROS
# ————————————————————————————————————————————————————————————————————————————

class GlobalDataManager:
    """
    Gestor singleton para cargar y cachear datos maestros.
    
    Gestiona tres tipos de datos maestros:
    - Titulaciones: Mapeo de nombre de titulación a ID de CRM
    - Cursos: Mapeo de nombre/año de curso a ID de CRM
    - Centros: Catálogo de centros educativos organizados por provincia
    
    Los datos se cargan una sola vez al inicio y se reutilizan en todas las peticiones,
    optimizando el rendimiento y reduciendo operaciones de I/O.
    """
    titulaciones: Dict[str, str] = {}
    titulaciones_names: List[str] = []
    titulaciones_normalized_to_original: Dict[str, str] = {}
    titulaciones_normalized_names: List[str] = []
    titulacion_groups: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    cursos: Dict[str, str] = {}
    centros_by_provincia: Dict[str, Dict[str, Dict[str, Any]]] = {}
    all_centros_flat: Dict[str, Dict[str, Any]] = {}
    all_centros_names: List[str] = []
    localidades_by_provincia: Dict[str, List[str]] = {}
    all_localidades_normalized: Set[str] = set()
    province_aliases: Dict[str, str] = {}
    localidades_loaded: bool = False
    loaded: bool = False

    @classmethod
    def _normalize_titulacion_name(cls, name: str) -> str:
        """Normaliza el nombre de titulación eliminando prefijos comunes."""
        normalized = name.upper()
        prefixes = [
            "DOBLE GRADO EN ",
            "GRADO EN ",
            "TITULO DE ESPECIALISTA EN ",
            "TITULO DE EXPERTO EN ",
            "DIPLOMA UNIVERSITARIO DE ESPECIALIZACION EN ",
            "DIPLOMA UNIVERSITARIO DE EXPERTO EN ",
        ]
        for prefix in prefixes:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
                break
        return normalized.strip()

    @classmethod
    def _extract_province_from_name(cls, name: str) -> Tuple[str, str]:
        """
        Extrae la provincia del nombre de titulación.
        Returns: (nombre_base, provincia)
        Ej: "ENFERMERÍA (CASTELLÓN)" -> ("ENFERMERÍA", "CASTELLÓN")
        """
        match = re.search(r'\((ELCHE|CASTELLÓN|VALENCIA)\)\s*$', name)
        if match:
            provincia = match.group(1)
            nombre_base = name[:match.start()].strip()
            return nombre_base, provincia
        return name, "VALENCIA"

    @classmethod
    def _load_titulaciones(cls) -> None:
        tit_path = os.path.join(BASE_DIR_TITULACION, "titulaciones.txt")
        if not os.path.exists(tit_path):
            return

        current_checkbox_group = []

        with open(tit_path, encoding="utf-8") as f:
            lines = f.readlines()

            for i, line in enumerate(lines):
                line_stripped = line.strip()

                if not line_stripped:
                    if current_checkbox_group:
                        cls._process_checkbox_group(current_checkbox_group)
                        current_checkbox_group = []
                    continue

                parts = line_stripped.split(",", 1)
                if len(parts) >= 2:
                    name = parts[0].strip()
                    id_val = parts[1].split(",")[0].strip()

                    cls.titulaciones[name] = id_val

                    normalized = cls._normalize_titulacion_name(name)
                    cls.titulaciones_normalized_to_original[normalized] = name

                    nombre_base, provincia = cls._extract_province_from_name(name)

                    current_checkbox_group.append({
                        "name": name,
                        "id": id_val,
                        "base_name": nombre_base,
                        "provincia": provincia,
                        "normalized": normalized
                    })

            if current_checkbox_group:
                cls._process_checkbox_group(current_checkbox_group)

        cls.titulaciones_names = list(cls.titulaciones.keys())
        cls.titulaciones_normalized_names = list(cls.titulaciones_normalized_to_original.keys())

    @classmethod
    def _process_checkbox_group(cls, group: List[Dict[str, str]]) -> None:
        """
        Procesa un grupo de titulaciones que pertenecen al mismo checkbox.
        Crea un mapeo para facilitar la búsqueda por provincia.
        """
        if not group:
            return

        base_name = group[0]["base_name"]
        cls.titulacion_groups[base_name].extend(group)

    @classmethod
    def _load_cursos(cls) -> None:
        cur_path = os.path.join(BASE_DIR_CURSO, "cursos.txt")
        if not os.path.exists(cur_path):
            return

        with open(cur_path, encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split(",", 1)
                if len(parts) >= 2:
                    id_val = parts[0].strip()
                    name = parts[1].strip()
                    cls.cursos[name] = id_val

    @classmethod
    def _parse_center_line(cls, line: str) -> List[str]:
        try:
            parts = next(csv.reader([line], skipinitialspace=True))
        except csv.Error:
            parts = [p.strip() for p in line.split(",")]
        parts = [p.strip() for p in parts]
        if len(parts) > 5:
            return [",".join(parts[:-4]).strip(), *parts[-4:]]
        return parts

    @classmethod
    def _load_centros(cls) -> None:
        if not os.path.exists(BASE_DIR):
            return

        valid_province_keys = {
            os.path.splitext(filename)[0].upper()
            for filename in os.listdir(BASE_DIR_LOCALIDADES)
            if filename.endswith(".json") and filename != "provinceIds.json"
        } if os.path.exists(BASE_DIR_LOCALIDADES) else set()

        for filename in os.listdir(BASE_DIR):
            if not filename.endswith(".txt"):
                continue

            prov_path = os.path.join(BASE_DIR, filename)
            prov_key = filename.replace(".txt", "").upper()
            if valid_province_keys and prov_key not in valid_province_keys:
                continue

            cls.centros_by_provincia[prov_key] = {}

            with open(prov_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or "," not in line:
                        continue

                    parts = cls._parse_center_line(line)
                    if len(parts) < 2:
                        continue

                    center_name = parts[0]
                    center_data = {
                        "Id": parts[1] if len(parts) > 1 else "",
                        "Name": center_name,
                        "IdProvince": parts[2] if len(parts) > 2 else "",
                        "IdCity": parts[3] if len(parts) > 3 else "",
                        "IdCountry": parts[4] if len(parts) > 4 else ""
                    }

                    cls.centros_by_provincia[prov_key][center_name] = center_data
                    cls.all_centros_flat[center_name] = center_data

        cls.all_centros_names = list(cls.all_centros_flat.keys())

    @classmethod
    def _load_localidades(cls) -> None:
        """Carga localidades por provincia desde JSON."""
        if cls.localidades_loaded:
            return

        province_files = [
            filename for filename in os.listdir(BASE_DIR_LOCALIDADES)
            if filename.endswith(".json") and filename != "provinceIds.json"
        ] if os.path.exists(BASE_DIR_LOCALIDADES) else []

        for filename in province_files:
            prov_key = filename.replace(".json", "").upper()
            path = os.path.join(BASE_DIR_LOCALIDADES, filename)
            try:
                with open(path, encoding="utf-8") as f:
                    cities = json.load(f)
                seen = set()
                names = []
                for c in cities:
                    name = (c.get("Name") or "").strip()
                    name_cat = (c.get("Name_cat") or "").strip()
                    if name and name not in seen:
                        names.append(name)
                        seen.add(name)
                    if name_cat and name_cat not in seen:
                        names.append(name_cat)
                        seen.add(name_cat)
                cls.localidades_by_provincia[prov_key] = names
                for n in names:
                    norm = _normalize_center_text(n)
                    if norm:
                        cls.all_localidades_normalized.add(norm)
            except FileNotFoundError:
                logging.info(f"[LOCALIDADES] Archivo no encontrado para {prov_key}, omitiendo")
            except json.JSONDecodeError as e:
                logging.error(f"[LOCALIDADES] JSON malformado en {prov_key}: {e}")
            except OSError as e:
                logging.warning(f"[LOCALIDADES] Error de I/O en {prov_key}: {e}")

        cls._load_province_aliases()

        for pk in cls.centros_by_provincia:
            cls.all_localidades_normalized.add(_normalize_center_text(pk))
        for centers in cls.centros_by_provincia.values():
            for center_name in centers:
                loc = _extract_localidad_from_center_name(center_name)
                if loc:
                    norm_loc = _normalize_center_text(loc)
                    if norm_loc:
                        cls.all_localidades_normalized.add(norm_loc)
        cls.localidades_loaded = True

    @classmethod
    def _load_province_aliases(cls) -> None:
        province_ids_path = os.path.join(BASE_DIR_LOCALIDADES, "provinceIds.json")
        if not os.path.exists(province_ids_path):
            return

        available_keys = set(cls.centros_by_provincia.keys()) | set(cls.localidades_by_provincia.keys())
        if not available_keys:
            return

        try:
            with open(province_ids_path, encoding="utf-8") as f:
                provinces = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"[PROVINCE] No se pudieron cargar alias de provincias: {e}")
            return

        normalized_keys = {_normalize_center_text(key): key for key in available_keys}
        for province in provinces:
            names = [
                (province.get("Name") or "").strip(),
                (province.get("Name_cat") or "").strip(),
            ]
            target_key = None
            for name in names:
                target_key = normalized_keys.get(_normalize_center_text(name))
                if target_key:
                    break
            if not target_key:
                continue
            for name in names + [target_key]:
                norm_name = _normalize_center_text(name)
                if not norm_name:
                    continue
                cls.province_aliases[norm_name] = target_key
                cls.province_aliases[f"PROV {norm_name}"] = target_key
                cls.province_aliases[f"PROVINCIA {norm_name}"] = target_key
                cls.province_aliases[f"PROVINCIA DE {norm_name}"] = target_key

    @classmethod
    def load(cls) -> None:
        if cls.loaded:
            return

        cls._load_titulaciones()
        cls._load_cursos()
        cls._load_centros()
        cls._load_localidades()
        cls.loaded = True

# ————————————————————————————————————————————————————————————————————————————
# NORMALIZACIÓN DE TEXTO Y DATOS
# ————————————————————————————————————————————————————————————————————————————

def clean_text(text: Optional[str]) -> str:
    """Limpia y normaliza texto eliminando espacios extra."""
    if not text:
        return ""
    return str(text).strip()


def _normalize_email(email: Optional[str]) -> str:
    """
    Normaliza una dirección de email:
    - Elimina espacios internos
    - Convierte a ASCII eliminando acentos/diacríticos (los emails no deben llevar tildes)
    """
    if not email:
        return ""
    email_original = str(email).strip()
    email_clean = email_original.replace(" ", "")
    email_ascii = unicodedata.normalize('NFKD', email_clean).encode('ascii', 'ignore').decode('ascii')
    if email_original != email_ascii:
        logging.info(f"[EMAIL_NORMALIZE] '{email_original}' -> '{email_ascii}'")
    else:
        logging.debug(f"[EMAIL_NORMALIZE] '{email_ascii}' (sin cambios)")
    return email_ascii


def _email_has_valid_structure(email: str) -> bool:
    """
    Comprueba estructura bÃ¡sica de email tras normalizaciÃ³n.

    Exige usuario@dominio.tld, sin espacios ni caracteres fuera del alfabeto
    habitual de direcciones de correo. No valida que el dominio exista.
    """
    if not email:
        return False
    return bool(re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
        r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
        r"[A-Za-z]{2,63}",
        email,
    ))


def _normalize_dni_nie(dni: Optional[str]) -> str:
    """
    Normaliza y corrige un DNI/NIE detectado por OCR.
    
    Estructura válida:
    - DNI español: 8 dígitos numéricos + 1 letra de control (ej: 12345678A)
    - NIE extranjero: 1 letra inicial (X/Y/Z) + 7 dígitos + 1 letra de control (ej: X1234567L)
    
    El OCR puede confundir números manuscritos con letras. Esta función:
    1. Elimina espacios, guiones y puntos
    2. Detecta si es DNI o NIE
    3. Corrige letras en posiciones numéricas según similitud visual
    4. Preserva la letra inicial (NIE) y la letra final de control
    """
    if not dni:
        return ""

    dni_original = str(dni).strip()
    dni_clean = re.sub(r"[\s\-.]", "", dni_original.upper())

    if not dni_clean:
        return ""

    chars = list(dni_clean)
    is_nie = chars[0] in ("X", "Y", "Z")
    corrections: List[str] = []

    if is_nie:
        body_start = 1
        body_end = len(chars) - 1
        expected_body_len = 7
    else:
        body_start = 0
        body_end = len(chars) - 1
        expected_body_len = 8

    for i in range(body_start, max(body_start, min(body_end, len(chars) - 1))):
        if i >= len(chars):
            break
        c = chars[i]
        if not c.isdigit():
            if c in OCR_LETTER_TO_DIGIT:
                old_char = c
                chars[i] = OCR_LETTER_TO_DIGIT[c]
                corrections.append(f"pos {i}: '{old_char}'->'{chars[i]}'")

    body_digits = "".join([c for c in chars[body_start:body_end] if c.isdigit()])

    if len(body_digits) != expected_body_len:
        if len(body_digits) == expected_body_len + 1:
            heuristic_body = body_digits[-expected_body_len:]
            logging.info(
                f"[DNI_NORMALIZE] Heurística: body con {len(body_digits)} dígitos, usando últimos {expected_body_len}: {heuristic_body}"
            )
            body_digits = heuristic_body
        else:
            result_fallback = "".join(chars)
            logging.warning(
                f"[DNI_NORMALIZE] No se pudo normalizar correctamente '{dni_original}' -> '{result_fallback}' (digits in body: {len(body_digits)}, expected: {expected_body_len})"
            )
            return result_fallback

    if is_nie:
        prefix_map = {"X": "0", "Y": "1", "Z": "2"}
        prefix_digit = prefix_map.get(chars[0], "0")
        number_for_calc = prefix_digit + body_digits
    else:
        number_for_calc = body_digits

    try:
        num = int(number_for_calc)
    except (ValueError, IndexError) as e:
        logging.warning(f"[DNI_NORMALIZE] Error en cálculo de control: {e}")
        result_fallback = "".join(chars)
        logging.warning(f"[DNI_NORMALIZE] Número inválido para cálculo: '{number_for_calc}' -> retornando '{result_fallback}'")
        return result_fallback

    LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
    expected_index = num % 23
    expected_letter = LETTERS[expected_index]

    current_last = chars[-1] if len(chars) >= 1 else ""

    corrected = None
    if not current_last.isalpha():
        # No hay letra: posición ocupada por dígito u otro carácter → reemplazar con la calculada
        if is_nie:
            corrected = chars[0] + body_digits + expected_letter
        else:
            corrected = body_digits + expected_letter
        logging.info(
            f"[DNI_NORMALIZE] Posición de letra no es alfabética ('{current_last}') "
            f"→ calculada '{expected_letter}' (num: {number_for_calc})"
        )
    elif current_last.upper() != expected_letter:
        # Azure DI leyó una letra real que no coincide con el algoritmo → conservarla
        # El post-procesado la flaggeará para revisión humana
        if is_nie:
            corrected = chars[0] + body_digits + current_last.upper()
        else:
            corrected = body_digits + current_last.upper()
        logging.info(
            f"[DNI_NORMALIZE] Letra leída='{current_last.upper()}' ≠ calculada='{expected_letter}' "
            f"→ conservando la de Azure DI (num: {number_for_calc})"
        )
    else:
        if is_nie:
            corrected = chars[0] + body_digits + current_last.upper()
        else:
            corrected = body_digits + current_last.upper()

    if corrections:
        logging.info(f"[DNI_NORMALIZE] '{dni_original}' -> '{corrected}' (correcciones OCR: {', '.join(corrections)})")
    else:
        logging.debug(f"[DNI_NORMALIZE] '{dni_original}' -> '{corrected}' (sin correcciones OCR)")

    return corrected


def _normalize_center_text(text: str) -> str:
    """
    Normaliza texto de centros para comparación robusta:
    - Quita acentos/diacríticos
    - Convierte a mayúsculas
    - Sustituye puntuación por espacios
    - Colapsa espacios
    """
    if not text:
        return ""
    text = str(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.upper()
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    chars = list(text)
    for i, char in enumerate(chars):
        if char not in OCR_DIGIT_TO_LETTER:
            continue
        prev_is_alpha = i > 0 and chars[i - 1].isalpha()
        next_is_alpha = i + 1 < len(chars) and chars[i + 1].isalpha()
        if prev_is_alpha or next_is_alpha:
            chars[i] = OCR_DIGIT_TO_LETTER[char]
    text = "".join(chars)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_phone(telefono: str) -> str:
    """
    Normaliza un número de teléfono corrigiendo errores de OCR.
    """
    if not telefono:
        return ""
    
    telefono_original = str(telefono).strip()
    telefono_clean = telefono_original.upper().replace(" ", "").replace("-", "").replace(".", "").replace("(", "").replace(")", "")
    
    if not telefono_clean:
        return ""
    
    has_plus_prefix = telefono_clean.startswith("+")
    if has_plus_prefix:
        telefono_clean = telefono_clean[1:]
    
    chars = list(telefono_clean)
    result_chars = []
    corrections = []
    
    for i, char in enumerate(chars):
        if char.isdigit():
            result_chars.append(char)
        elif char in OCR_LETTER_TO_DIGIT:
            result_chars.append(OCR_LETTER_TO_DIGIT[char])
            corrections.append(f"pos {i}: '{char}'->'{OCR_LETTER_TO_DIGIT[char]}'")
    
    result = "".join(result_chars)

    if has_plus_prefix and result:
        result = "+" + result
    
    digits_only = result.lstrip("+")
    
    prefijos_pais = {
        "34": 9,
        "33": 9,
        "39": 10,
        "49": 10,
        "44": 10,
        "1": 10,
        "351": 9,
    }
    
    digitos_nacionales = digits_only
    min_digitos = 9
    
    if has_plus_prefix:
        for prefijo, min_nacional in prefijos_pais.items():
            if digits_only.startswith(prefijo):
                digitos_nacionales = digits_only[len(prefijo):]
                min_digitos = min_nacional
                break
    
    if len(digitos_nacionales) < min_digitos:
        logging.warning(
            f"[PHONE_NORMALIZE] Teléfono inválido: '{telefono_original}' -> '{result}' "
            f"(dígitos nacionales: {len(digitos_nacionales)}, mínimo: {min_digitos}) -> DESCARTADO"
        )
        return ""
    
    if corrections:
        logging.info(f"[PHONE_NORMALIZE] '{telefono_original}' -> '{result}' (correcciones OCR: {', '.join(corrections)})")
    else:
        logging.debug(f"[PHONE_NORMALIZE] '{telefono_original}' -> '{result}' (sin correcciones)")
    
    return result


def _split_apellidos(apellidos: str) -> Tuple[str, str]:
    """Divide apellidos en primer apellido (middlename) y segundo apellido (lastname)."""
    apellidos_completos = clean_text(apellidos)
    apellidos_lista = apellidos_completos.split() if apellidos_completos else []

    if not apellidos_lista:
        return "", ""

    middlename = apellidos_lista[0]
    lastname = " ".join(apellidos_lista[1:]) if len(apellidos_lista) > 1 else ""

    return middlename, lastname

# ————————————————————————————————————————————————————————————————————————————
# UTILIDADES DE IMAGEN
# ————————————————————————————————————————————————————————————————————————————

def encode_image_to_base64(image_bytes: bytes) -> str:
    """Codifica imagen en bytes a string base64."""
    return base64.b64encode(image_bytes).decode('utf-8')


def _log_di_extract_summary(
    result_dict: Dict[str, Any],
    image_index: int,
) -> None:
    """
    Emite un resumen legible de todo lo que Azure DI extrajo de una imagen.

    Muestra texto, manuscrito, selection marks aceptadas/rechazadas, key-value
    pairs y palabras de baja confianza. Facilita la verificación en producción
    de que el pipeline extrae correctamente cada sección del formulario.
    """
    SEP = "─" * 58
    tag = f"[DI_EXTRACT_SUMMARY img={image_index + 1}]"

    logging.info(f"{tag} {SEP}")

    text = result_dict.get("text", "")
    has_hw = result_dict.get("has_handwritten", False)
    hw_content = result_dict.get("handwritten_content", "")
    logging.info(f"{tag} Texto OCR: {len(text)} chars | Manuscrito detectado: {has_hw}")
    if has_hw and hw_content:
        preview = hw_content[:180].replace("\n", " ¶ ")
        logging.info(f"{tag} Manuscrito: «{preview}{'…' if len(hw_content) > 180 else ''}»")

    pages = result_dict.get("pages", [])
    all_words = [w for p in pages for w in p.get("words", [])]
    if all_words:
        confs = [w["confidence"] for w in all_words]
        mean_c = sum(confs) / len(confs)
        min_c  = min(confs)
        low_c  = sum(1 for c in confs if c < WORD_CONFIDENCE_THRESHOLD)
        logging.info(
            f"{tag} Palabras: {len(all_words)} total | "
            f"confianza media={mean_c:.2f} mín={min_c:.2f} | "
            f"{low_c} por debajo de {WORD_CONFIDENCE_THRESHOLD}"
        )

    unfiltered = result_dict.get("all_selection_marks_unfiltered", [])
    accepted   = result_dict.get("selection_marks", [])
    rejected   = len(unfiltered) - len(accepted)
    sel_accepted = sum(1 for m in accepted if str(m.get("state", "")) == "selected")
    logging.info(
        f"{tag} Marks: {len(unfiltered)} total | "
        f"{len(accepted)} aceptadas (umbral≥{SELECTION_MARK_CONFIDENCE_THRESHOLD}) | "
        f"{rejected} rechazadas | {sel_accepted} ':selected:' válidas"
    )

    kvp = result_dict.get("key_value_pairs", [])
    if kvp:
        logging.info(f"{tag} Key-Value Pairs ({len(kvp)}):")
        for kv in kvp:
            conf_str = f" [{kv['confidence']:.0%}]" if kv.get("confidence") is not None else ""
            logging.info(f"{tag}   {kv.get('key', '?')!r:<30} → {kv.get('value', '')!r}{conf_str}")
    else:
        logging.info(f"{tag} Key-Value Pairs: ninguno")

    logging.info(f"{tag} {SEP}")


def rotate_image_if_needed(image_bytes: bytes, rotation_degrees: int = 90) -> bytes:
    """
    Rota una imagen el número especificado de grados en sentido horario.
    
    Args:
        image_bytes: Imagen en formato bytes
        rotation_degrees: Grados a rotar (90, 180, 270). Por defecto 90 (horario)
        
    Returns:
        Imagen rotada en formato bytes JPEG
    """
    try:
        image = Image.open(io.BytesIO(image_bytes))
        rotated_image = image.rotate(-rotation_degrees, expand=True)
        output_buffer = io.BytesIO()
        rotated_image.save(output_buffer, format='JPEG', quality=95)
        return output_buffer.getvalue()
    except Exception as e:
        logging.warning(f"[IMAGE_ROTATE] Error rotando imagen, devolviendo original: {e}")
        return image_bytes

# ————————————————————————————————————————————————————————————————————————————
# UTILIDADES AUXILIARES
# ————————————————————————————————————————————————————————————————————————————

def _format_polygon(polygon: Optional[List[Any]]) -> str:
    """
    Formatea un polígono para logging.

    SDK v3.x devuelve lista de objetos con atributos .x/.y.
    SDK v4.0 devuelve lista plana de floats [x1, y1, x2, y2, ...].
    Ambos formatos se manejan correctamente.
    """
    if not polygon:
        return "N/A"
    if polygon and isinstance(polygon[0], float):
        pairs = [(polygon[i], polygon[i + 1]) for i in range(0, len(polygon) - 1, 2)]
        return ", ".join([f"[{x:.2f}, {y:.2f}]" for x, y in pairs])
    return ", ".join([f"[{p.x:.2f}, {p.y:.2f}]" for p in polygon])


# ————————————————————————————————————————————————————————————————————————————
# OCR CON AZURE DOCUMENT INTELLIGENCE
# ————————————————————————————————————————————————————————————————————————————

def perform_ocr_structured(
    image_bytes: bytes,
    high_resolution: bool = False,
    query_fields: Optional[List[str]] = None,
    image_index: int = 0,
) -> Dict[str, Any]:
    """
    Extrae información estructurada de una imagen usando Azure Document Intelligence v4.0.

    Utiliza el modelo prebuilt-layout con add-ons configurados por constantes globales.
    Con DI_ADDON_BARCODES activo extrae QR/barcodes (útil en el reverso).
    Con DI_ADDON_KEY_VALUE_PAIRS activo extrae pares etiqueta→valor (útil en el anverso).
    Con high_resolution=True activa ocrHighResolution (checkboxes densos del reverso).
    Con DI_ADDON_QUERY_FIELDS y query_fields activo extrae campos por pregunta directa.

    Returns:
        Diccionario con text, selection_marks, all_selection_marks_unfiltered, tables,
        has_handwritten, handwritten_content, pages, barcodes, key_value_pairs,
        raw_document_intelligence.
    """
    empty_result = {
        "text": "",
        "selection_marks": [],
        "all_selection_marks_unfiltered": [],
        "tables": [],
        "has_handwritten": False,
        "handwritten_content": "",
        "pages": [],
        "barcodes": [],
        "key_value_pairs": [],
        "raw_document_intelligence": None,
    }

    if not DOC_INTEL_ENDPOINT or not DOC_INTEL_KEY:
        logging.warning("[OCR_STRUCTURED] Credenciales de OCR no configuradas")
        return empty_result

    try:
        features: List[str] = []
        if DI_ADDON_BARCODES:
            features.append("barcodes")
        if DI_ADDON_KEY_VALUE_PAIRS:
            features.append("keyValuePairs")
        if high_resolution:
            features.append("ocrHighResolution")
        if DI_ADDON_QUERY_FIELDS and query_fields:
            features.append("queryFields")

        credential = AzureKeyCredential(DOC_INTEL_KEY)
        client = DocumentIntelligenceClient(endpoint=DOC_INTEL_ENDPOINT, credential=credential)
        logging.info(f"[OCR_STRUCTURED] Enviando imagen a Azure Document Intelligence (features={features})...")
        poller = client.begin_analyze_document(
            OCR_MODEL,
            io.BytesIO(image_bytes),
            content_type="application/octet-stream",
            features=features if features else None,
            query_fields=query_fields if (DI_ADDON_QUERY_FIELDS and query_fields) else None,
        )
        result = poller.result()
        raw_document_intelligence: Optional[Dict[str, Any]] = None
        if hasattr(result, "as_dict"):
            try:
                raw_document_intelligence = result.as_dict()
            except Exception as raw_exc:
                logging.warning(f"[OCR_STRUCTURED] No se pudo serializar raw de DI: {raw_exc}")

        full_text = result.content
        logging.info(f"[OCR_STRUCTURED] Texto OCR recibido: {len(full_text)} caracteres")
        logging.debug(f"[OCR_STRUCTURED] Texto completo:\n{full_text}")

        has_handwritten = False
        handwritten_spans = []
        if result.styles:
            for style in result.styles:
                if hasattr(style, 'is_handwritten') and style.is_handwritten:
                    has_handwritten = True
                    if hasattr(style, 'spans'):
                        for span in style.spans:
                            handwritten_text = full_text[span.offset:span.offset + span.length]
                            handwritten_spans.append(handwritten_text)

        handwritten_content = " | ".join(handwritten_spans) if handwritten_spans else ""

        all_selection_marks = []
        all_marks_unfiltered = []
        pages_info = []

        for page in result.pages:
            page_info = {
                "page_number": page.page_number,
                "width": page.width,
                "height": page.height,
                "unit": page.unit,
                "lines": [],
                "selection_marks": []
            }

            if page.lines:
                for line in page.lines:
                    page_info["lines"].append({
                        "content": line.content,
                        "polygon": _format_polygon(line.polygon) if hasattr(line, 'polygon') else "N/A"
                    })

            words_info = []
            if page.words:
                for word in page.words:
                    words_info.append({
                        "content": word.content,
                        "confidence": word.confidence,
                    })
            page_info["words"] = words_info

            if page.selection_marks:
                filtered_out_low_confidence = 0
                filtered_out_selected_low_conf = 0
                all_marks_debug = []

                for mark in page.selection_marks:
                    mark_info = {
                        "state": mark.state,
                        "confidence": mark.confidence,
                        "polygon": _format_polygon(mark.polygon) if hasattr(mark, 'polygon') else "N/A",
                        "page": page.page_number
                    }
                    all_marks_debug.append(mark_info)
                    all_marks_unfiltered.append(mark_info)

                    if mark.confidence >= SELECTION_MARK_CONFIDENCE_THRESHOLD:
                        page_info["selection_marks"].append(mark_info)
                        all_selection_marks.append(mark_info)
                    else:
                        filtered_out_low_confidence += 1
                        if mark.state == "selected":
                            filtered_out_selected_low_conf += 1

                kept_count = len(page_info["selection_marks"])
                logging.info(
                    f"[OCR_STRUCTURED] Pág {page.page_number}: {len(all_marks_debug)} marcas ADI "
                    f"({kept_count} aceptadas, {filtered_out_low_confidence} descartadas, "
                    f"umbral={SELECTION_MARK_CONFIDENCE_THRESHOLD})"
                )
                logging.debug(f"[OCR_STRUCTURED] Detalle marcas pág {page.page_number}: " +
                    ", ".join(f"{m['state']}:{m['confidence']:.2f}" for m in sorted(all_marks_debug, key=lambda x: x["confidence"], reverse=True)))

            pages_info.append(page_info)

        selected_count = sum(1 for m in all_selection_marks if m["state"] == "selected")
        unselected_count = sum(1 for m in all_selection_marks if m["state"] == "unselected")

        tables_info = []
        if result.tables:
            for table_idx, table in enumerate(result.tables):
                table_info = {
                    "index": table_idx,
                    "row_count": table.row_count,
                    "column_count": table.column_count,
                    "cells": []
                }

                for cell in table.cells:
                    cell_info = {
                        "row": cell.row_index,
                        "column": cell.column_index,
                        "content": cell.content,
                        "kind": cell.kind if hasattr(cell, 'kind') else "content"
                    }
                    table_info["cells"].append(cell_info)

                tables_info.append(table_info)

        logging.info(
            f"[OCR_STRUCTURED] Selection marks: {selected_count} selected, {unselected_count} unselected "
            f"(umbral confianza: {SELECTION_MARK_CONFIDENCE_THRESHOLD})"
        )
        total_words = sum(len(p.get("words", [])) for p in pages_info)
        if total_words > 0:
            all_confs = [w["confidence"] for p in pages_info for w in p.get("words", [])]
            mean_conf = sum(all_confs) / len(all_confs)
            min_conf = min(all_confs)
            low_conf_count = sum(1 for c in all_confs if c < WORD_CONFIDENCE_THRESHOLD)
            logging.info(
                f"[OCR_STRUCTURED] Palabras: {total_words} total, "
                f"confianza media={mean_conf:.2f}, mín={min_conf:.2f}, "
                f"{low_conf_count} por debajo de {WORD_CONFIDENCE_THRESHOLD}"
            )
        else:
            logging.info("[OCR_STRUCTURED] Sin palabras detectadas")

        logging.info(f"[OCR_STRUCTURED] Manuscrito detectado: {has_handwritten}")
        if has_handwritten and handwritten_content:
            logging.info(f"[OCR_STRUCTURED] Contenido manuscrito: {handwritten_content[:200]}{'...' if len(handwritten_content) > 200 else ''}")
        if tables_info:
            logging.info(f"[OCR_STRUCTURED] Tablas detectadas: {len(tables_info)}")

        barcodes: List[Dict[str, Any]] = []
        if DI_ADDON_BARCODES:
            for page in result.pages:
                for bc in getattr(page, "barcodes", []) or []:
                    barcodes.append({
                        "kind":       str(bc.kind) if bc.kind else "",
                        "value":      bc.value or "",
                        "page":       page.page_number,
                        "confidence": getattr(bc, "confidence", None),
                    })
            if barcodes:
                logging.info(f"[OCR_STRUCTURED] Barcodes/QR detectados: {barcodes}")

        key_value_pairs: List[Dict[str, Any]] = []
        if DI_ADDON_KEY_VALUE_PAIRS:
            for kv in getattr(result, "key_value_pairs", []) or []:
                key_text  = (kv.key.content   or "").strip() if kv.key   else ""
                val_text  = (kv.value.content or "").strip() if kv.value else ""
                if key_text:
                    key_value_pairs.append({
                        "key":        key_text,
                        "value":      val_text,
                        "confidence": getattr(kv, "confidence", None),
                    })
            logging.info(f"[OCR_STRUCTURED] KeyValuePairs extraídos: {len(key_value_pairs)}")

        result_out = {
            "text": full_text,
            "selection_marks": all_selection_marks,
            "all_selection_marks_unfiltered": all_marks_unfiltered,
            "tables": tables_info,
            "has_handwritten": has_handwritten,
            "handwritten_content": handwritten_content,
            "pages": pages_info,
            "barcodes": barcodes,
            "key_value_pairs": key_value_pairs,
            "raw_document_intelligence": raw_document_intelligence,
        }
        _log_di_extract_summary(result_out, image_index=image_index)
        return result_out

    except Exception as e:
        logging.error(f"[OCR_STRUCTURED] Error durante el análisis: {e}", exc_info=True)
        return empty_result


def _extract_selected_from_ocr(ocr_text: str, selection_marks: List[Dict] = None) -> List[str]:
    """
    Extrae titulaciones marcadas del flujo OCR de prebuilt-layout.

    Regla fundamental: el checkbox de una titulación siempre precede a su nombre en el
    flujo OCR. Una titulación está marcada si ':selected:' es el token inmediatamente
    anterior a su nombre (misma línea o línea anterior). Solo se incluye si la marca
    Azure DI tiene confianza >= SELECTION_MARK_CONFIDENCE_THRESHOLD.

    El índice de cada ':selected:' en el texto OCR corresponde 1:1 con el índice
    de la lista selection_marks filtrada por state=='selected'. La confianza de la
    i-ésima marca filtra el i-ésimo ':selected:' del texto.

    Cuando el alumno escribe 'X' o un tick, Azure DI puede capturarlo como texto entre
    el marcador y el nombre: ':selected: X Titulación' o ':selected: ✓\nTitulación'.
    En ambos casos se elimina el prefijo X/tick y se extrae el nombre correcto.

    Precondición: ocr_text proviene de prebuilt-layout de Azure Document Intelligence.
    selection_marks es la lista de todas las marcas del documento (sin filtrar por confianza).
    """
    _x_only = re.compile(r'^[Xx✓✗✘×]\s*$', re.UNICODE)
    _x_prefix = re.compile(r'^[Xx✓✗✘×]\s+', re.UNICODE)

    selected_confs = []
    if selection_marks:
        selected_confs = [m.get("confidence", 1.0) for m in selection_marks if m.get("state") == "selected"]

    _marker_re = re.compile(r'(:(?:un)?selected:)')
    tokens = _marker_re.split(ocr_text)

    selected = []
    last_marker = None
    current_conf = 1.0
    sel_idx = 0

    for token in tokens:
        stripped = token.strip()
        if not stripped:
            continue
        if stripped == ':selected:':
            current_conf = selected_confs[sel_idx] if sel_idx < len(selected_confs) else 1.0
            sel_idx += 1
            last_marker = ':selected:'
        elif stripped == ':unselected:':
            last_marker = ':unselected:'
        else:
            if last_marker == ':selected:' and current_conf >= SELECTION_MARK_CONFIDENCE_THRESHOLD:
                lines = [p.strip() for p in stripped.split('\n') if p.strip()]
                first_line = lines[0] if lines else ''
                if _x_only.match(first_line):
                    label = lines[1] if len(lines) > 1 else ''
                else:
                    label = _x_prefix.sub('', first_line).strip()
                if label:
                    selected.append(label)
            last_marker = None

    if selected:
        logging.info(f"[CHECKBOX_PRIMARY] {len(selected)} titulaciones con confianza >= {SELECTION_MARK_CONFIDENCE_THRESHOLD}: {selected}")
    return selected


def extract_course_from_text(ocr_text: str) -> str:
    """
    Intenta extraer el curso del texto OCR usando patrones regex y fuzzy matching.

    Primero busca patrones explícitos (año académico, bachillerato, CFGS, ESO).
    Si no hay match regex, hace fuzzy contra el catálogo de cursos del CRM.
    """
    course_patterns = [
        r"curso[:\s]*(\d{4}-\d{4})",
        r"curso[:\s]*(\d{4})",
        r"(\d{4}-\d{4})",
        r"(\d+[oº°]?\s?bachillerato)",
        r"(segundo\s+de\s+bachillerato)",
        r"(primero\s+de\s+bachillerato)",
        r"(\d+[oº°]?\s+cfgs)",
        r"(\d+[oº°]?\s+cf\s?gs)",
        r"(ciclo\s+formativo[^\n,]{0,30})",
        r"(\d+[oº°]?\s+eso)",
        r"(cuarto\s+de\s+(?:la\s+)?eso)",
    ]

    for pattern in course_patterns:
        match = re.search(pattern, ocr_text, re.IGNORECASE)
        if match:
            return match.group(1)

    GlobalDataManager.load()
    catalog_courses = list(GlobalDataManager.cursos.keys())
    if not catalog_courses:
        return ""

    match = process.extractOne(ocr_text, catalog_courses, scorer=fuzz.token_set_ratio)
    if match and match[1] > COURSE_MATCH_THRESHOLD:
        return match[0]

    return ""

def _process_barcodes(barcodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Interpreta QR/barcodes extraídos del reverso.

    Loguea el contenido crudo de cada código. Si algún valor coincide con el
    patrón de DNI (8 dígitos + letra) o NIE (X/Y/Z + 7 dígitos + letra),
    lo devuelve como 'dni_qr' para validación cruzada con el DNI extraído por OCR/GPT.
    """
    result: Dict[str, Any] = {}
    for bc in barcodes:
        value = bc.get("value", "")
        logging.info(f"[QR_BARCODE] kind={bc.get('kind')} pág={bc.get('page')} val='{value}'")
        cleaned = re.sub(r'[\s\-]', '', value).upper()
        if re.match(r'^[0-9]{8}[A-Z]$', cleaned) or re.match(r'^[XYZ][0-9]{7}[A-Z]$', cleaned):
            result["dni_qr"] = cleaned
            logging.info(f"[QR_BARCODE] Posible DNI/NIE en QR: '{cleaned}'")
    return result


def _extract_from_key_value_pairs(kvp_list: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Mapea key-value pairs de Azure DI a nombres de campo canónicos.

    Usa fuzzy matching (token_set_ratio >= 80) entre la etiqueta normalizada
    y un dict de aliases. Solo incluye el campo con mejor score; descarta valores
    vacíos. Devuelve el primer match por campo canónico.
    """
    _KVP_FIELD_ALIASES = {
        "DNI": "dni",
        "DNI O NIE": "dni",
        "NIE": "dni",
        "NIF": "dni",
        "LETRA": "letra_dni",
        "NOMBRE": "nombre",
        "NOMBRE DEL ALUMNO": "nombre",
        "PRIMER APELLIDO": "primer_apellido",
        "PRIMER APELLIDO DEL ALUMNO": "primer_apellido",
        "SEGUNDO APELLIDO": "segundo_apellido",
        "SEGUNDO APELLIDO DEL ALUMNO": "segundo_apellido",
        "APELLIDO": "apellido",
        "APELLIDOS": "apellido",
        "TELEFONO": "telefono",
        "TELEFONO DE CONTACTO": "telefono",
        "MOVIL": "telefono",
        "EMAIL": "email",
        "E MAIL": "email",
        "E MAIL EN MAYUSCULAS": "email",
        "CORREO": "email",
        "CORREO ELECTRONICO": "email",
        "LOCALIDAD": "localidad",
        "CENTRO": "centro",
        "PROVINCIA": "provincia",
        "CURSO": "curso",
    }
    extracted: Dict[str, str] = {}
    for kv in kvp_list:
        key_raw = kv.get("key", "")
        value   = kv.get("value", "")
        if not key_raw or not value:
            continue
        key_norm = _normalize_center_text(key_raw)
        best_field: Optional[str] = None
        best_score = 0
        for alias, field in _KVP_FIELD_ALIASES.items():
            score = fuzz.token_set_ratio(key_norm, alias)
            if score > best_score:
                best_score = score
                best_field = field
        if best_score >= 80 and best_field and best_field not in extracted:
            extracted[best_field] = value
            logging.info(f"[KEY_VALUE_PAIRS] '{key_raw}' → campo='{best_field}', valor='{value}', score={best_score}")
    return extracted


def _extract_kvp_confidence(kvp_list: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Devuelve la confianza KVP de Azure DI para los campos 'nombre' y 'apellido'.
    Usa el mismo fuzzy matching que _extract_from_key_value_pairs.
    """
    _TARGETS = {"NOMBRE": "nombre", "APELLIDOS": "apellido"}
    result: Dict[str, float] = {}
    for kv in kvp_list:
        key_raw = kv.get("key", "")
        confidence = kv.get("confidence")
        if not key_raw or confidence is None:
            continue
        key_norm = _normalize_center_text(key_raw)
        for alias, field in _TARGETS.items():
            if field not in result and fuzz.token_set_ratio(key_norm, alias) >= 80:
                result[field] = float(confidence)
    return result


# ————————————————————————————————————————————————————————————————————————————
# MAPEO DE TITULACIONES Y CURSOS (FUZZY MATCHING)
# ————————————————————————————————————————————————————————————————————————————

def _normalize_titulacion_input(titulacion: str) -> str:
    """Normaliza el texto de entrada de titulación para mejorar el matching."""
    titulacion = re.sub(r'[*]', '', titulacion)
    titulacion = re.sub(r'\s+', ' ', titulacion).strip()
    return titulacion.upper()


def _map_province_for_titulacion(provincia_usuario: Optional[str]) -> Optional[str]:
    """
    Las titulaciones del CRM solo tienen variantes VALENCIA, CASTELLON y ELCHE.
    Para Murcia se usa ELCHE/Alicante; para el resto de provincias nuevas,
    VALENCIA.
    """
    if not provincia_usuario:
        return None

    provincia_norm = _normalize_center_text(provincia_usuario)
    provincia_map = {
        "CASTELLON": "CASTELLÃ“N",
        "CASTELLO": "CASTELLÃ“N",
        "CASTELLON DE LA PLANA": "CASTELLÃ“N",
        "CASTELLO DE LA PLANA": "CASTELLÃ“N",
        "ALICANTE": "ELCHE",
        "ALACANT": "ELCHE",
        "ELCHE": "ELCHE",
        "ELX": "ELCHE",
        "MURCIA": "ELCHE",
        "REGION DE MURCIA": "ELCHE",
        "VALENCIA": "VALENCIA",
    }
    castellon_variant = "CASTELL\u00d3N"
    for castellon_key in (
        "CASTELLON",
        "CASTELLO",
        "CASTELLON DE LA PLANA",
        "CASTELLO DE LA PLANA",
    ):
        provincia_map[castellon_key] = castellon_variant
    return provincia_map.get(provincia_norm, "VALENCIA")


def _is_non_degree_checkbox_label(titulo: str) -> bool:
    """
    Detecta si un texto de checkbox es una etiqueta de sede/campus/ruido
    y no una titulación real.

    Comprueba dinámicamente contra TODAS las provincias y localidades
    cargadas del CRM, sin valores hardcodeados.
    """
    if not titulo or not titulo.strip():
        return False
    normalized = _normalize_center_text(titulo)
    if not normalized:
        return False
    if re.fullmatch(r"N\s*/\s*A\.?", normalized) or normalized in ("NA",):
        return True
    GlobalDataManager.load()
    if normalized in GlobalDataManager.all_localidades_normalized:
        return True
    return False


def _select_best_variant_by_province(
    matched_name: str,
    provincia_usuario: Optional[str] = None
) -> Tuple[str, str]:
    """
    Selecciona la mejor variante de una titulación según la provincia del usuario.
    
    Lógica de priorización:
    1. Titulación de la provincia del usuario
    2. Valencia (sin paréntesis o con (VALENCIA))
    3. Castellón
    4. Elche
    """
    GlobalDataManager.load()

    base_name, _ = GlobalDataManager._extract_province_from_name(matched_name)
    group = GlobalDataManager.titulacion_groups.get(base_name, [])

    if not group:
        return matched_name, GlobalDataManager.titulaciones.get(matched_name, "")

    provincia_usuario_upper = _map_province_for_titulacion(provincia_usuario)

    prioridad = {"VALENCIA": 2, "CASTELLÓN": 3, "ELCHE": 4}

    if provincia_usuario_upper:
        prioridad[provincia_usuario_upper] = 1

    best_variant = None
    best_priority = 999

    for variant in group:
        variant_provincia = variant["provincia"]
        variant_priority = prioridad.get(variant_provincia, 5)

        if variant_priority < best_priority:
            best_priority = variant_priority
            best_variant = variant

    if best_variant:
        logging.info(
            f"[VARIANT_SELECTION] Seleccionada: '{best_variant['name']}' "
            f"({best_variant['provincia']}) -> ID: {best_variant['id']}"
        )
        return best_variant["name"], best_variant["id"]

    return matched_name, GlobalDataManager.titulaciones.get(matched_name, "")


def analyze_titulacion_local(
    titulacion: str,
    provincia_usuario: Optional[str] = None
) -> str:
    """
    Busca el ID de una titulación usando fuzzy matching y selecciona la variante
    más apropiada según la provincia del usuario.
    
    El matching se realiza contra versiones normalizadas (sin "GRADO EN", etc.)
    para mejorar la precisión cuando la entrada no incluye estos prefijos.
    """
    if not titulacion:
        return ""

    GlobalDataManager.load()

    titulacion_normalizada = _normalize_titulacion_input(titulacion)

    results = process.extract(
        titulacion_normalizada,
        GlobalDataManager.titulaciones_normalized_names,
        scorer=fuzz.token_set_ratio,
        processor=utils.default_process,
        limit=10
    )

    if not results:
        return ""

    valid_matches = [(name, score, idx) for name, score, idx in results if score >= TITULACION_MATCH_THRESHOLD]

    if not valid_matches:
        return ""

    high_score_matches = [(name, score, idx) for name, score, idx in valid_matches if score >= 95]

    if high_score_matches:
        if len(high_score_matches) > 1:
            max_score = max(x[1] for x in high_score_matches)
            similar_matches = [x for x in high_score_matches if max_score - x[1] < 5]

            if len(similar_matches) > 1:
                strict_scores = []
                for name, orig_score, idx in similar_matches:
                    strict_score = fuzz.ratio(titulacion_normalizada, name, processor=utils.default_process)
                    strict_scores.append((name, orig_score, strict_score, idx))

                best_match = max(strict_scores, key=lambda x: (x[2], x[1], len(x[0])))
                matched_normalized = best_match[0]
                score = best_match[1]
            else:
                best_match = max(high_score_matches, key=lambda x: (x[1], len(x[0])))
                matched_normalized = best_match[0]
                score = best_match[1]
        else:
            matched_normalized = high_score_matches[0][0]
            score = high_score_matches[0][1]
    else:
        matched_normalized, score, _ = valid_matches[0]

    original_name = GlobalDataManager.titulaciones_normalized_to_original[matched_normalized]
    base_name, _ = GlobalDataManager._extract_province_from_name(original_name)
    tiene_variantes = len(GlobalDataManager.titulacion_groups.get(base_name, [])) > 1

    if score >= 95 and not tiene_variantes:
        titulacion_id = GlobalDataManager.titulaciones.get(original_name, "")
        logging.info(
            f"[TITULACION_MATCH] Match exacto: '{titulacion}' -> '{original_name}' -> ID: {titulacion_id}"
        )
        return titulacion_id

    final_name, titulacion_id = _select_best_variant_by_province(original_name, provincia_usuario)

    logging.info(
        f"[TITULACION_MATCH] '{titulacion}' -> '{final_name}' (Score: {score}) -> ID: {titulacion_id}"
    )
    return titulacion_id


def map_checked_degrees(
    lista_titulaciones: List[str],
    provincia_usuario: Optional[str] = None
) -> List[str]:
    """
    Mapea una lista de titulaciones detectadas por GPT a sus IDs de CRM.
    
    Returns:
        Lista de IDs de titulaciones (ej: ["123", "456", "789"])
    """
    if not lista_titulaciones or not isinstance(lista_titulaciones, list):
        logging.info("[MAP_DEGREES] Lista de titulaciones vacía o inválida")
        return []

    logging.info(f"[MAP_DEGREES] Mapeando {len(lista_titulaciones)} titulaciones (provincia: {provincia_usuario or 'N/A'})")
    ids_encontrados = []
    for titulo_detectado in lista_titulaciones:
        if _is_non_degree_checkbox_label(titulo_detectado):
            logging.info(f"[MAP_DEGREES]   - '{titulo_detectado}' ignorado (etiqueta de sede/ruido)")
            continue
        id_tit = analyze_titulacion_local(titulo_detectado, provincia_usuario)
        if id_tit:
            ids_encontrados.append(id_tit)
            logging.info(f"[MAP_DEGREES]   ✓ '{titulo_detectado}' -> ID: {id_tit}")
        else:
            logging.warning(f"[MAP_DEGREES]   ✗ '{titulo_detectado}' -> SIN MATCH")

    result = list(dict.fromkeys(ids_encontrados))
    logging.info(f"[MAP_DEGREES] Resultado: {len(result)} IDs únicos de {len(lista_titulaciones)} titulaciones")
    return result


def analyze_curso_local(curso: str) -> str:
    """Busca el ID de un curso usando fuzzy matching."""
    if not curso:
        logging.info("[CURSO_MATCH] Curso vacío, omitiendo búsqueda")
        return ""

    GlobalDataManager.load()

    result = process.extractOne(curso, GlobalDataManager.cursos.keys(), scorer=fuzz.token_set_ratio)

    if result:
        match_name, score, _ = result
        if score < COURSE_MATCH_THRESHOLD:
            logging.warning(
                f"[CURSO_MATCH] '{curso}' -> SIN MATCH suficiente "
                f"(mejor='{match_name}', score={score}, umbral={COURSE_MATCH_THRESHOLD})"
            )
            return ""
        curso_id = GlobalDataManager.cursos[match_name]
        logging.info(f"[CURSO_MATCH] '{curso}' -> '{match_name}' (Score: {score}) -> ID: {curso_id}")
        return curso_id

    logging.warning(f"[CURSO_MATCH] '{curso}' -> SIN MATCH en catálogo de cursos")
    return ""

# ————————————————————————————————————————————————————————————————————————————
# MAPEO DE CENTROS EDUCATIVOS
# ————————————————————————————————————————————————————————————————————————————

def _extract_localidad_from_center_name(center_name: str) -> str:
    """
    Extrae la localidad del nombre del centro.
    Soporta:
    - 'NOMBRE (LOCALIDAD)' o 'NOMBRE (LOCALIDAD) (PROVINCIA)'
    - 'NOMBRE - LOCALIDAD' (ej: AUSIÀS MARCH - MANISES, BLASCO IBÁÑEZ - CULLERA)
    """
    if not center_name:
        return ""
    if "(" in center_name:
        parts = re.findall(r"\(([^)]+)\)", center_name)
        if parts:
            PROVINCIAS_CRM = {
                _normalize_center_text(key)
                for key in getattr(GlobalDataManager, "centros_by_provincia", {}).keys()
            } or {"VALENCIA", "ALICANTE", "CASTELLON"}
            last = parts[-1].upper().strip()
            if _normalize_center_text(last) in PROVINCIAS_CRM and len(parts) > 1:
                return parts[-2].strip()
            return parts[-1].strip()
    if " - " in center_name:
        suffix = center_name.rsplit(" - ", 1)[-1].strip()
        if suffix and len(suffix) <= 50 and "INSTITUTO" not in suffix.upper() and "EDUCACIÓN" not in suffix.upper():
            return suffix
    return ""


def _normalize_localidad(localidad: str) -> str:
    """Normaliza localidad para comparación (mismo criterio que centros)."""
    return _normalize_center_text(localidad) if localidad else ""


def _expand_ocr_abbreviations(text: str) -> str:
    """
    Expande abreviaturas comunes en textos OCR de formularios españoles.
    Sustituciones generales, no específicas de ninguna localidad ni centro.
    """
    if not text:
        return ""
    norm = _normalize_center_text(text)
    norm = re.sub(r"\bSTA\b", "SANTA", norm)
    norm = re.sub(r"\bSTO\b", "SANTO", norm)
    norm = re.sub(r"\bSAN\b", "SAN", norm)
    norm = re.sub(r"\bNTRA\b", "NUESTRA", norm)
    norm = re.sub(r"\bNRA\b",  "NUESTRA", norm)
    norm = re.sub(r"\bSRA\b",  "SENORA", norm)
    norm = re.sub(r"\bSNRA\b", "SENORA", norm)
    norm = re.sub(r"\bCOL\b",  "COLEGIO",    norm)
    norm = re.sub(r"\bCOLEG\b","COLEGIO",    norm)
    norm = re.sub(r"\bINST\b", "INSTITUTO",  norm)
    norm = re.sub(r"\bESC\b",  "ESCUELA",    norm)
    norm = re.sub(r"\bCEIP\b", "COLEGIO",    norm)
    norm = re.sub(r"\bCPR\b",  "COLEGIO",    norm)
    norm = re.sub(r"\bDPTO\b", "DEPARTAMENTO", norm)
    norm = re.sub(r"\bAVDA\b", "AVENIDA",    norm)
    norm = re.sub(r"\bUNIV\b", "UNIVERSIDAD", norm)
    norm = re.sub(r"\bUNI\b",  "UNIVERSIDAD", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm


def _normalize_localidad_input(localidad_usuario: str, provincia: str) -> str:
    """
    Corrige la localidad del usuario usando la lista oficial del CRM de esa provincia.
    Solo busca en la provincia indicada para evitar que "Valencia" domine sobre otras.
    Útil cuando OCR o el alumno escriben mal (Alcaser -> ALCÀSSER, Manisez -> MANISES).
    Usa múltiples scorers fuzzy para tolerar variantes OCR severas.
    """
    if not localidad_usuario:
        return ""
    GlobalDataManager.load()
    prov_key = _find_province_key(provincia)
    if not prov_key:
        return localidad_usuario.strip()

    localidad_aliases = {
        "CASTELLO": "CASTELLÓN DE LA PLANA",
        "CASTELLON": "CASTELLÓN DE LA PLANA",
        "CASTELLON PLANA": "CASTELLÓN DE LA PLANA",
        "CASTELLO PLANA": "CASTELLÓN DE LA PLANA",
        "CASTILLON DE LA PLANA": "CASTELLÓN DE LA PLANA",
        "VALENCIA CAPITAL": "VALENCIA",
        "CIUDAD DE VALENCIA": "VALENCIA",
        "ALACANT": "ALICANTE",
        "ALACANTE": "ALICANTE",
        "ELCHE": "ELCHE/ELX",
        "ELX": "ELCHE/ELX",
        "ELTX": "ELCHE/ELX",
        "XATIVA": "XÀTIVA",
        "JATIVA": "XÀTIVA",
        "JATIBA": "XÀTIVA",
        "JIJONA": "XIXONA",
        "VILA REAL": "VILA-REAL",
        "VILAREAL": "VILA-REAL",
        "VILLARREAL": "VILA-REAL",
        "VILLA REAL": "VILA-REAL",
        "BURRIANA": "BORRIANA",
        "ALCOY": "ALCOI",
        "ALCIRA": "ALZIRA",
        "JAVEA": "JÁVEA",
        "XABIA": "JÁVEA",
        "VILLAJOYOSA": "VILA JOIOSA",
        "VILAJOIOSA": "VILA JOIOSA",
        "VILLAFRANCA": "VILAFRANCA",
        "VILLAFRANCA DEL CID": "VILAFRANCA",
        "PENISCOLA": "PEÑÍSCOLA",
        "XILXES": "CHILCHES",
        "CHERT": "XERT",
        "CHODOS": "XODOS",
        "BENITATXELL": "BENITACHELL",
        "SAN VICENT DEL RASPEIG": "SAN VICENTE DEL RASPEIG",
        "CAMPOLIVAR": "GODELLA",
    }
    loc_upper = _normalize_center_text(localidad_usuario)
    if loc_upper in localidad_aliases:
        alias_target = localidad_aliases[loc_upper]
        names = GlobalDataManager.localidades_by_provincia.get(prov_key, [])
        for name in names:
            if _normalize_center_text(name) == _normalize_center_text(alias_target):
                logging.info(
                    f"[LOCALIDAD_NORM] '{localidad_usuario}' -> '{name}' (alias directo)"
                )
                return name

    names = GlobalDataManager.localidades_by_provincia.get(prov_key, [])
    if not names:
        return localidad_usuario.strip()

    expanded = _expand_ocr_abbreviations(localidad_usuario)

    candidates = [
        process.extractOne(expanded, names, scorer=fuzz.ratio,           processor=utils.default_process),
        process.extractOne(expanded, names, scorer=fuzz.token_set_ratio,  processor=utils.default_process),
        process.extractOne(expanded, names, scorer=fuzz.WRatio,           processor=utils.default_process),
    ]
    best = max((c for c in candidates if c), key=lambda x: x[1], default=None)

    if best:
        match_name, score, _ = best
        if score >= 80:
            logging.info(
                f"[LOCALIDAD_NORM] '{localidad_usuario}' -> '{match_name}' (Score: {score}, prov: {prov_key})"
            )
            return match_name
    return localidad_usuario.strip()


def _localidad_matches(localidad_usuario: str, localidad_centro: str) -> bool:
    """
    Comprueba si la localidad del usuario coincide con la del centro.
    Usa normalización general, expansión de abreviaturas OCR y fuzzy matching.
    El token_set_ratio solo se acepta si comparten algún token informativo para
    evitar falsos positivos por artículos/preposiciones como "EL" o "DE".
    """
    if not localidad_usuario or not localidad_centro:
        return False
    norm_user = _expand_ocr_abbreviations(localidad_usuario)
    norm_centro = _expand_ocr_abbreviations(localidad_centro)
    if norm_user == norm_centro:
        return True
    if norm_user in norm_centro or norm_centro in norm_user:
        return True
    stop_tokens = {"A", "AL", "D", "DE", "DEL", "EL", "LA", "L", "LAS", "LOS"}
    user_tokens = set(norm_user.split()) - stop_tokens
    centro_tokens = set(norm_centro.split()) - stop_tokens
    has_meaningful_overlap = bool(user_tokens & centro_tokens)
    score_ratio  = fuzz.ratio(norm_user, norm_centro)
    score_token  = fuzz.token_set_ratio(norm_user, norm_centro)
    score_wratio = fuzz.WRatio(norm_user, norm_centro)
    return (
        score_ratio >= 80
        or (has_meaningful_overlap and score_token >= 85)
        or (score_ratio >= 70 and score_wratio >= 82)
    )


def _find_province_key(provincia: str) -> Optional[str]:
    """
    Determina la clave de provincia para búsqueda de centros.
    Usa fuzzy matching contra las claves realmente cargadas, sin prefijos hardcodeados.
    """
    if not provincia:
        return None

    prov_upper = _normalize_center_text(provincia)
    GlobalDataManager.load()
    available_keys = list(GlobalDataManager.centros_by_provincia.keys())
    if not available_keys:
        return prov_upper

    if prov_upper in available_keys:
        return prov_upper

    community_aliases = {
        "VALENCIA": "VALENCIA",
        "VALENCIANA": "VALENCIA",
        "C VALENCIANA": "VALENCIA",
        "C V": "VALENCIA",
        "CV": "VALENCIA",
        "COM VALENCIANA": "VALENCIA",
        "COMUNIDAD VALENCIANA": "VALENCIA",
        "COMUNITAT VALENCIANA": "VALENCIA",
        "COMUNITAT VAL": "VALENCIA",
        "COMUNIDAD VAL": "VALENCIA",
        "PAIS VALENCIANO": "VALENCIA",
        "REINO DE VALENCIA": "VALENCIA",
        "PROV VALENCIA": "VALENCIA",
        "PROV DE VALENCIA": "VALENCIA",
        "PROVINCIA VALENCIA": "VALENCIA",
        "PROVINCIA DE VALENCIA": "VALENCIA",
        "ALICANTE": "ALICANTE",
        "ALACANT": "ALICANTE",
        "ALACANTE": "ALICANTE",
        "ALIANTE": "ALICANTE",
        "ALIC": "ALICANTE",
        "ALI": "ALICANTE",
        "PROV ALICANTE": "ALICANTE",
        "PROV DE ALICANTE": "ALICANTE",
        "PROVINCIA ALICANTE": "ALICANTE",
        "PROVINCIA DE ALICANTE": "ALICANTE",
        "ELCHE": "ALICANTE",
        "ELX": "ALICANTE",
        "ELX ELX": "ALICANTE",
        "CASTELLON": "CASTELLON",
        "CASTELLO": "CASTELLON",
        "CASTELLON DE LA PLANA": "CASTELLON",
        "CASTELLO DE LA PLANA": "CASTELLON",
        "CASTELLON PLANA": "CASTELLON",
        "CASTELLO PLANA": "CASTELLON",
        "CASTILLON": "CASTELLON",
        "CASTELION": "CASTELLON",
        "CASTELION DE LA PLANA": "CASTELLON",
        "CAST": "CASTELLON",
        "CASTE": "CASTELLON",
        "PROV CASTELLON": "CASTELLON",
        "PROV DE CASTELLON": "CASTELLON",
        "PROVINCIA CASTELLON": "CASTELLON",
        "PROVINCIA DE CASTELLON": "CASTELLON",
    }
    if prov_upper in community_aliases:
        alias = community_aliases[prov_upper]
        if alias in available_keys:
            logging.info(f"[PROVINCE] '{provincia}' -> '{alias}' (alias)")
            return alias

    if prov_upper in GlobalDataManager.province_aliases:
        alias = GlobalDataManager.province_aliases[prov_upper]
        if alias in available_keys:
            logging.info(f"[PROVINCE] '{provincia}' -> '{alias}' (alias CRM)")
            return alias

    short = prov_upper.replace(" ", "")
    if len(short) >= 3:
        prefix_candidates = [k for k in available_keys if _normalize_center_text(k).startswith(short)]
        if len(prefix_candidates) == 1:
            logging.info(
                f"[PROVINCE] '{provincia}' -> '{prefix_candidates[0]}' (prefijo)"
            )
            return prefix_candidates[0]

    res_ratio  = process.extractOne(prov_upper, available_keys, scorer=fuzz.ratio,  processor=utils.default_process)
    res_wratio = process.extractOne(prov_upper, available_keys, scorer=fuzz.WRatio, processor=utils.default_process)
    best_result = max(
        (r for r in [res_ratio, res_wratio] if r),
        key=lambda x: x[1],
        default=None,
    )
    if best_result:
        match_name, score, _ = best_result
        if score >= PROVINCE_MATCH_THRESHOLD:
            logging.info(
                f"[PROVINCE] '{provincia}' -> '{match_name}' (fuzzy, score: {score})"
            )
            return match_name

        logging.warning(
            f"[PROVINCE] '{provincia}' no supera umbral, usando fallback '{match_name}' (score: {score})"
        )
        return match_name

    return available_keys[0] if available_keys else prov_upper


def _pick_best_by_localidad(candidates: List[str], localidad: str, search_term: str = "") -> Optional[str]:
    """
    De entre varios candidatos, elige el que coincide con la localidad del usuario.
    Regla: si el usuario indica localidad, SOLO se devuelve un centro que la coincida.
    Si localidad está vacía, elige por mayor similitud fuzzy con search_term (tiebreaker: nombre más corto).
    Si hay localidad pero ningún candidato coincide, devuelve None (ambiguo).
    """
    if not candidates:
        return None
    if not localidad:
        if search_term:
            norm_search = _normalize_center_text(search_term)
            return max(
                candidates,
                key=lambda n: (
                    fuzz.token_set_ratio(norm_search, _normalize_center_text(n)),
                    -len(_normalize_center_text(n)),
                )
            )
        return max(candidates, key=lambda n: len(_normalize_center_text(n)))
    localidad_norm = _normalize_localidad(localidad)
    if not localidad_norm:
        return max(candidates, key=lambda n: len(_normalize_center_text(n)))
    best_match = None
    best_score = -1
    for name in candidates:
        loc_centro = _extract_localidad_from_center_name(name)
        if not loc_centro:
            continue
        if _localidad_matches(localidad, loc_centro):
            score = fuzz.ratio(localidad_norm, _normalize_localidad(loc_centro))
            if score > best_score:
                best_score = score
                best_match = name
    return best_match


def _score_center_candidate(center_name: str, search_term: str, localidad: str) -> float:
    """
    Puntúa un candidato usando nombre de centro + localidad.
    - 70% similitud del nombre
    - 30% similitud de localidad (si hay localidad de entrada)
    """
    norm_center = _normalize_center_text(center_name)
    norm_search = _normalize_center_text(search_term)
    name_score = fuzz.token_set_ratio(norm_search, norm_center) if norm_search and norm_center else 0

    if not localidad:
        return float(name_score)

    center_loc = _extract_localidad_from_center_name(center_name)
    if not center_loc:
        return float(name_score * 0.85)

    norm_loc_user = _normalize_localidad(localidad)
    norm_loc_center = _normalize_localidad(center_loc)

    if not norm_loc_user or not norm_loc_center:
        return float(name_score * 0.9)

    loc_score = max(
        fuzz.ratio(norm_loc_user, norm_loc_center),
        fuzz.token_set_ratio(norm_loc_user, norm_loc_center),
    )

    return (name_score * 0.7) + (loc_score * 0.3)


def _pick_best_by_combined_score(candidates: List[str], search_term: str, localidad: str) -> Optional[str]:
    """Elige el mejor candidato por puntuación combinada nombre+localidad."""
    if not candidates:
        return None
    return max(candidates, key=lambda name: _score_center_candidate(name, search_term, localidad))


def _with_center_match_metadata(center_data: Dict[str, Any], strategy: str, score: float) -> Dict[str, Any]:
    """
    Devuelve una copia del centro CRM con metadatos internos de matching.

    Las claves internas permiten decidir flags de revisiÃ³n sin modificar el
    formato CRM final ni mutar el catÃ¡logo cargado en memoria.
    """
    result = dict(center_data)
    result["_match_strategy"] = strategy
    result["_match_score"] = round(float(score), 1)
    return result


def _search_centers_by_locality(
    search_term: str,
    subset_centers: Dict[str, Dict[str, Any]],
    localidad: str,
) -> Optional[Dict[str, Any]]:
    """
    Estrategia locality-first: filtra centros por localidad y luego hace fuzzy
    match del nombre solo entre los centros de esa localidad.
    Muy robusto contra OCR garbled porque la localidad ya acota el espacio.

    Cuando un centro solo lleva provincia entre paréntesis (sin localidad explícita),
    se considera candidato si el token de localidad del usuario aparece literalmente
    dentro del nombre del centro.
    """
    if not localidad:
        return None

    _PROVINCIAS = {
        _normalize_center_text(key)
        for key in GlobalDataManager.centros_by_provincia.keys()
    } or {"VALENCIA", "ALICANTE", "CASTELLON"}
    loc_norm = _normalize_center_text(localidad)
    locality_subset: Dict[str, Dict[str, Any]] = {}
    for name, data in subset_centers.items():
        loc = _extract_localidad_from_center_name(name)
        if loc and _localidad_matches(localidad, loc):
            locality_subset[name] = data
        elif (not loc or _normalize_center_text(loc) in _PROVINCIAS) and loc_norm:
            if loc_norm in _normalize_center_text(name):
                locality_subset[name] = data

    if not locality_subset:
        return None

    logging.info(
        f"[CENTER_MATCH] Locality-first: {len(locality_subset)} centros en localidad '{localidad}'"
    )

    if len(locality_subset) == 1:
        name = list(locality_subset.keys())[0]
        logging.info(
            f"[CENTER_MATCH] Locality-first: único centro en '{localidad}' -> '{name}'"
        )
        return _with_center_match_metadata(locality_subset[name], "locality_first", 100)

    norm_search = _expand_ocr_abbreviations(search_term)
    compact_search = norm_search.replace(" ", "")
    stop_tokens = {
        "A", "AL", "D", "DE", "DEL", "EL", "LA", "L", "LAS", "LOS",
        "COLEGIO", "COL", "IES", "INSTITUTO", "CENTRO", "EDUCATIVO",
    }

    def locality_first_rank(name: str) -> Tuple[float, int, float, float]:
        """Puntúa candidatos de una misma localidad evitando matches por tokens genéricos."""
        norm_name = _expand_ocr_abbreviations(name)
        search_tokens = set(norm_search.split()) - stop_tokens
        name_tokens = set(norm_name.split()) - stop_tokens
        overlap = search_tokens & name_tokens
        compact_hit = bool(compact_search and compact_search in norm_name.replace(" ", ""))
        overlap_weight = len(overlap) + (2 if compact_hit else 0)
        ratio = fuzz.ratio(norm_search, norm_name)
        token_score = fuzz.token_set_ratio(norm_search, norm_name) if overlap_weight else 0
        wratio = fuzz.WRatio(norm_search, norm_name) if (overlap_weight or ratio >= 70) else 0
        score = max(ratio, token_score, wratio) + min(overlap_weight, 4) * 10
        return score, overlap_weight, ratio, -len(norm_name)

    match_name = max(locality_subset.keys(), key=locality_first_rank)
    score, overlap_weight, ratio, _ = locality_first_rank(match_name)
    logging.info(
        f"[CENTER_MATCH] Locality-first: '{search_term}' -> '{match_name}' "
        f"(Score: {score:.1f}, overlap={overlap_weight}, ratio={ratio:.1f}, localidad: '{localidad}')"
    )
    return _with_center_match_metadata(locality_subset[match_name], "locality_first", score)


def _search_center_in_province(
    search_term: str,
    prov_key: str,
    localidad: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Busca un centro educativo en una provincia específica.
    Estrategias en orden:
    1. Match exacto normalizado
    2. Contains (substring)
    3. Locality-first (filtra por localidad, luego fuzzy dentro de ese subconjunto)
    4. Fuzzy global con desambiguación por localidad
    """
    subset_centers = GlobalDataManager.centros_by_provincia.get(prov_key, {})
    if not subset_centers:
        return None

    norm_input = _normalize_center_text(search_term)
    if not norm_input:
        return None

    normalized_map: List[Tuple[str, str]] = []
    for name in subset_centers.keys():
        normalized_map.append((name, _normalize_center_text(name)))

    for original_name, norm_name in normalized_map:
        if norm_name == norm_input:
            center_data = subset_centers[original_name]
            logging.info(f"[CENTER_MATCH] Exact match: '{search_term}' -> '{original_name}'")
            return _with_center_match_metadata(center_data, "exact_normalized", 100)

    contains_candidates: List[str] = []
    for original_name, norm_name in normalized_map:
        if not norm_name:
            continue
        if norm_input in norm_name or norm_name in norm_input:
            if norm_name == prov_key:
                continue
            contains_candidates.append(original_name)

    if contains_candidates:
        best_name = _pick_best_by_localidad(contains_candidates, localidad or "", search_term)
        if best_name:
            center_data = subset_centers[best_name]
            logging.info(
                f"[CENTER_MATCH] Contains match: '{search_term}' -> '{best_name}'"
                + (f" (localidad: {localidad})" if localidad else "")
            )
            return _with_center_match_metadata(center_data, "contains", 100)
        if localidad:
            chosen = _pick_best_by_combined_score(contains_candidates, search_term, localidad)
            if not chosen:
                chosen = contains_candidates[0]
            center_data = subset_centers[chosen]
            logging.warning(
                f"[CENTER_MATCH] Fallback contains por descarte (sin match localidad): '{search_term}' -> '{chosen}'"
            )
            return _with_center_match_metadata(center_data, "contains", 100)

    if localidad:
        loc_result = _search_centers_by_locality(search_term, subset_centers, localidad)
        if loc_result:
            return loc_result

    results = process.extract(
        search_term,
        subset_centers.keys(),
        scorer=fuzz.token_set_ratio,
        processor=utils.default_process,
        limit=10,
    )

    if not results:
        return None

    match_name, score, _ = results[0]
    norm_match = _normalize_center_text(match_name)

    if norm_match == prov_key and norm_input != norm_match:
        alt = next((r for r in results if _normalize_center_text(r[0]) != prov_key), None)
        if alt:
            match_name, score, _ = alt
            norm_match = _normalize_center_text(match_name)
        else:
            return None

    if localidad:
        max_score = results[0][1]
        candidates = [r[0] for r in results if r[1] >= max_score - 15]
        if candidates:
            best_name = _pick_best_by_localidad(candidates, localidad, search_term)
            if best_name:
                match_name = best_name
                logging.info(
                    f"[CENTER_MATCH] Fuzzy+localidad: '{search_term}' -> '{match_name}' (Score: {score})"
                )
            else:
                top_candidates = [r[0] for r in results[:5]]
                match_name = _pick_best_by_combined_score(top_candidates, search_term, localidad) or results[0][0]
                logging.warning(
                    f"[CENTER_MATCH] Fallback fuzzy por descarte (sin match localidad): '{search_term}' -> '{match_name}' (Score: {score})"
                )

    score = next((r[1] for r in results if r[0] == match_name), score)
    center_data = subset_centers[match_name]
    logging.info(f"[CENTER_MATCH] Fuzzy match: '{search_term}' -> '{match_name}' (Score: {score})")
    return _with_center_match_metadata(center_data, "fuzzy_global", score)


def analyze_center_optimized(provincia: str, localidad: str, nombre_centro: str) -> Dict[str, Any]:
    """
    Busca un centro educativo ÚNICAMENTE en la provincia indicada.
    
    No realiza búsqueda en otras provincias ni búsqueda global.
    Siempre devuelve el mejor match encontrado en la provincia, sin umbral mínimo.
    """
    if not nombre_centro:
        logging.info("[CENTER_SEARCH] Nombre de centro vacío, omitiendo búsqueda")
        return {}

    GlobalDataManager.load()

    prov_key = _find_province_key(provincia)
    logging.info(f"[CENTER_SEARCH] Buscando: '{nombre_centro}' en provincia '{provincia}' (key: {prov_key or 'N/A'}), localidad: '{localidad or 'N/A'}'")

    if not prov_key:
        logging.warning(f"[CENTER_SEARCH] Provincia '{provincia}' no reconocida")
        return {}

    centros_en_provincia = len(GlobalDataManager.centros_by_provincia.get(prov_key, {}))
    logging.debug(f"[CENTER_SEARCH] {centros_en_provincia} centros cargados en {prov_key}")

    localidad_corregida = _normalize_localidad_input(localidad, provincia) if localidad else ""
    if localidad and localidad_corregida != localidad:
        logging.info(f"[CENTER_SEARCH] Localidad corregida: '{localidad}' -> '{localidad_corregida}'")

    result = _search_center_in_province(nombre_centro, prov_key, localidad_corregida)
    if result:
        logging.info(f"[CENTER_SEARCH] ✓ Centro encontrado: '{result.get('Name', '')}' (ID: {result.get('Id', '')})")
        return result

    subset_centers = GlobalDataManager.centros_by_provincia.get(prov_key, {})
    if subset_centers:
        fallback_results = process.extract(
            nombre_centro,
            list(subset_centers.keys()),
            scorer=fuzz.WRatio,
            processor=utils.default_process,
            limit=10,
        )
        if fallback_results:
            fallback_name = _pick_best_by_combined_score(
                [r[0] for r in fallback_results],
                nombre_centro,
                localidad_corregida or localidad or "",
            ) or fallback_results[0][0]
            fallback_score = next((r[1] for r in fallback_results if r[0] == fallback_name), fallback_results[0][1])
            logging.warning(
                f"[CENTER_SEARCH] Fallback final por descarte: '{nombre_centro}' -> '{fallback_name}' (Score: {fallback_score})"
            )
            return _with_center_match_metadata(subset_centers[fallback_name], "fallback_wratio", fallback_score)

    logging.warning(f"[CENTER_SEARCH] ✗ Centro NO encontrado: '{nombre_centro}' en {prov_key}")
    return {}

# ————————————————————————————————————————————————————————————————————————————
# PROCESAMIENTO CON GPT (IA)
# ————————————————————————————————————————————————————————————————————————————

def _build_system_prompt() -> str:
    """
    Construye el prompt del sistema para GPT con instrucciones de extracción
    e inteligencia por campo. Optimizado para formularios CEU Cardenal Herrera.
    """
    return (
        "<task>\n"
        "You are an expert digitization assistant for CEU Cardenal Herrera University "
        "(Valencia, Spain), processing handwritten academic enrollment forms.\n\n"

        "ABOUT THE DATA SOURCES\n"
        "All input comes from Azure Document Intelligence (Azure DI), Microsoft's commercial "
        "AI trained specifically on document understanding. Azure DI excels at reading "
        "handwritten Spanish text — both print and cursive. Its readings are highly reliable; "
        "treat them as the ground truth of what the student physically wrote.\n\n"

        "IMPORTANT: Azure DI does not understand meaning — it reads and outputs raw text "
        "faithfully, including embedded ':selected:'/':unselected:' checkbox markers, "
        "key-value pairs, and confidence scores. YOUR role is the reasoning layer: "
        "interpret that raw output, resolve ambiguities, and produce a structured JSON.\n\n"

        "You receive three text-only data sources:\n\n"

        "1. OCR TEXT (front and back pages)\n"
        "   Full text stream from Azure DI. Primary source. Embedded ':selected:' / "
        "   ':unselected:' markers indicate checkbox state in reading order. Also includes "
        "   legal boilerplate (privacy clause) — ignore it, focus on student-filled fields.\n\n"

        "2. KEY-VALUE PAIRS (front side only, when present)\n"
        "   Azure DI's semantic layer: it pairs printed labels (e.g., 'Letra', 'Nombre') "
        "   with adjacent handwritten values. Confidence [%] reflects Azure DI's certainty.\n"
        "   • TRUST a KVP when the label is a recognised form field AND the value is "
        "     semantically plausible for that label — especially at high confidence.\n"
        "     Example: 'Letra: C [100%]' → the student wrote 'C'.\n"
        "   • IGNORE a KVP when the mapping is clearly impossible — Azure DI's layout "
        "     parser failed; use the raw OCR text for that field instead.\n"
        "     Example: 'Apellidos: Sofía' when OCR shows Sofía is the given name.\n"
        "   • KVP and OCR agreeing → high confidence. KVP and OCR differing → prefer "
        "     whichever is semantically correct for that field type.\n\n"

        "3. CHECKBOXES MARCADOS\n"
        "   Azure DI high-confidence (≥80%) selection marks, pre-filtered and listed. "
        "   Very reliable for student-filled boxes. Use as primary source for degrees "
        "   and course. The back-side OCR text also embeds ':selected:' inline with each "
        "   degree name — use both for cross-checking.\n\n"

        "CORE RULES\n"
        "1. Transcribe what Azure DI read — never invent data not present in the input.\n"
        "2. Correct only OCR rendering artifacts that make a token completely unreadable.\n"
        "3. Ignore form boilerplate (privacy clause, LOPD, legal text, signatures).\n"
        "4. Return ONLY the JSON object — no prose, no markdown fences.\n"
        "</task>\n\n"

        "<field_rules>\n\n"

        "dni\n"
        "  DNI: 8 digits + control letter. NIE: X/Y/Z + 7 digits + letter.\n"
        "  Source: 'DNI o NIE' field plus a separate 'Letra' box. The KVP often provides\n"
        "  both fields separately: 'DNI o NIE: [digits]' and 'Letra: [letter]' — combine.\n"
        "  Transcribe the control letter EXACTLY as Azure DI read it — do NOT compute or\n"
        "  verify the check digit mathematically. Post-processing validates the algorithm.\n"
        "  If KVP says 'Letra: C [100%]' or OCR shows 'C' in the Letra box, write 'C'.\n"
        "  If the letter box is blank or absent → include only the digits, set\n"
        "  review_data=true, include 'DNI' in fields_to_review.\n"
        "  KNOWN ISSUE — Azure DI sometimes misreads a handwritten control letter\n"
        "  (especially X, V, or angular-stroke letters) in the 'Letra' box as ':selected:'\n"
        "  in the front-side OCR stream. If you see 8 digits immediately followed by\n"
        "  ':selected:' in the DNI area and the KVP does not provide a separate 'Letra'\n"
        "  value, that ':selected:' IS the unreadable control letter — NOT a checkbox.\n"
        "  Extract the 8-digit body, flag DNI for review, never return null.\n\n"

        "nombre (REQUIRED)\n"
        "  Given name(s) only — never surnames.\n"
        "  Correct true OCR artifacts only (transposed/missing letters that make the word unreadable):\n"
        "    'Mria' → 'María', 'Edvardo' → 'Eduardo'.\n"
        "  NEVER replace an unusual name with a common Spanish alternative:\n"
        "    'Vicenza' stays 'Vicenza' (not 'Vicenta'), 'Saoirse' stays 'Saoirse'.\n"
        "  If name and surnames appear run together or crossed out, output only the given name.\n\n"

        "apellidos (REQUIRED)\n"
        "  Spanish surname(s) in the exact written order — never reorder them.\n"
        "  Correct only clear OCR artifacts: 'Garzía' → 'García', 'Fernandes' → 'Fernández'.\n"
        "  One or two surnames are acceptable.\n\n"

        "telefono\n"
        "  9-digit Spanish number (mobile: starts with 6 or 7; landline: starts with 9).\n"
        "  Transcribe digits as-is; post-processing handles OCR digit substitutions.\n\n"

        "email\n"
        "  Field labeled 'E-mail'. Students typically derive their address from nombre + apellidos.\n"
        "  Use cross-field coherence to correct OCR typos in the local part:\n"
        "    nombre='Eduardo García', OCR='edvardo.garzia@gmail.com' → 'eduardo.garcia@gmail.com'.\n"
        "  Preserve the domain exactly. Output in lowercase.\n"
        "  IMPORTANT: if the domain (part after '@') contains 'school', 'escuela',\n"
        "  'colegio', 'instituto', 'academy' or 'academia', or the email ends in '.org',\n"
        "  it is likely an institutional address (not the student's personal email).\n"
        "  Flag Email for review and set review_data=true.\n\n"

        "centro\n"
        "  Full school name from 'Nombre completo del Centro'. Transcribe literally —\n"
        "  do NOT normalise or correct; post-processing fuzzy-matches the official catalog.\n\n"

        "localidad\n"
        "  Locality of the school. Correct obvious OCR typos.\n\n"

        "provincia\n"
        "  Spanish province of the school, uppercase and without accents when clear.\n"
        "  Normalise any variant: 'Castellón' → 'CASTELLON', 'alacant' → 'ALICANTE'.\n\n"

        "curso\n"
        "  Marked checkbox. Allowed values: 4º ESO, 1º Bach, 2º Bach, 1º CFGS, 2º CFGS.\n\n"

        "titulaciones_marcadas_checkbox\n"
        "  Source: CHECKBOXES MARCADOS list (Azure DI high-confidence marks ≥ 80%) +\n"
        "  ':selected:' tokens embedded in the back-side OCR text stream.\n"
        "  1. Start from CHECKBOXES MARCADOS — reliable for student-filled boxes.\n"
        "  2. Cross-check against ':selected:' inline markers in the back OCR text.\n"
        "  3. When in doubt, INCLUDE rather than exclude.\n"
        "  Exclude campus/locality labels (ELCHE, CASTELLÓN, VALENCIA).\n"
        "  Return [] if no degree checkbox is marked.\n\n"

        "titulacion_manuscrita\n"
        "  Handwritten text in the '¿Cuál es la titulación…?' section. Transcribe literally.\n"
        "  If absent → \"\".\n\n"

        "</field_rules>\n\n"

        "<uncertainty_rules>\n"
        "You receive an OCR UNCERTAINTY block with front-side words that had low confidence,\n"
        "split into HIGH (<40%) and MEDIUM (40–80%) tiers. Decide whether a human must verify\n"
        "any critical field.\n\n"
        "Critical fields: DNI, Nombre, Apellidos, Teléfono, Email, Centro.\n\n"
        "| Field     | HIGH (<40%)  | MEDIUM (40–80%)                                          |\n"
        "| --------- | ------------ | -------------------------------------------------------- |\n"
        "| DNI       | Always flag  | Flag only if structurally wrong (garbled chars, bad length) |\n"
        "| Nombre    | Always flag  | Flag BOTH if confidence < 70%; between 70–80% only if the  |\n"
        "|           |              | word looks garbled (wrong chars, impossible combos, etc.)  |\n"
        "| Apellidos | Always flag  | Same rule as Nombre.                                       |\n"
        "| Teléfono  | Always flag  | Always flag — phone errors can't be auto-verified         |\n"
        "| Email     | Flag if the email word itself is HIGH | Flag only if structurally invalid; do NOT flag valid user@domain at MEDIUM |\n"
        "| Centro    | Let post-processing decide from CRM match strategy | Do NOT flag exact/contains CRM matches only for MEDIUM OCR |\n\n"
        "NEVER flag:\n"
        "  • Legal boilerplate / LOPD / privacy text\n"
        "  • Generic form labels ('ore', 'us', ':', 's', etc.)\n"
        "  • localidad, provincia, curso — handled automatically by post-processing\n"
        "  • Fields the student left blank — missing data is not a transcription error\n\n"
        "review_data = true if ANY critical field is flagged per the table above.\n"
        "fields_to_review: comma-separated Spanish names of flagged fields\n"
        "  (use: DNI, Nombre, Apellidos, Teléfono, Email, Centro).\n"
        "  Empty string if review_data = false.\n\n"
        "Do not propagate Nombre/Apellidos uncertainty to Email automatically. Email must\n"
        "have its own evidence: invalid structure or HIGH OCR uncertainty in the address.\n"
        "</uncertainty_rules>\n\n"

        "<output_spec>\n"
        "Return ONLY a JSON object with exactly these keys and types:\n"
        "{\n"
        "  \"dni\":                           string | null,\n"
        "  \"nombre\":                         string,\n"
        "  \"apellidos\":                      string,\n"
        "  \"telefono\":                       string | null,\n"
        "  \"email\":                          string | null,\n"
        "  \"centro\":                         string | null,\n"
        "  \"localidad\":                      string | null,\n"
        "  \"provincia\":                      string | null,\n"
        "  \"curso\":                          string | null,\n"
        "  \"titulaciones_marcadas_checkbox\": string[],\n"
        "  \"titulacion_manuscrita\":          string,\n"
        "  \"review_data\":                    boolean,\n"
        "  \"fields_to_review\":               string\n"
        "}\n"
        "For absent scalars return null; for absent arrays return []; never omit a key.\n"
        "Before returning, verify that every key listed above is present in your output.\n"
        "</output_spec>"
    )


def _build_checkbox_summary(ocr_structured: Dict[str, Any], image_index: int) -> str:
    """
    Construye un resumen de checkboxes marcados para incluir en el mensaje a GPT.

    Usa los marcadores ':selected:' embebidos en el texto OCR, filtrados por
    confianza Azure DI >= SELECTION_MARK_CONFIDENCE_THRESHOLD. Cada ':selected:'
    en el texto se corresponde 1:1 (por índice) con la lista de marcas 'selected'
    del resultado bruto de Document Intelligence.
    """
    ocr_text = ocr_structured.get("text", "")
    all_marks = ocr_structured.get("all_selection_marks_unfiltered", [])

    selected_from_ocr = _extract_selected_from_ocr(ocr_text, selection_marks=all_marks)

    if not selected_from_ocr:
        logging.info(f"[CHECKBOX_SUMMARY] Imagen {image_index + 1}: sin checkboxes marcados")
        return ""

    logging.info(f"[CHECKBOX_SUMMARY] Imagen {image_index + 1}: {len(selected_from_ocr)} checkbox(es) marcado(s):")
    for t in selected_from_ocr:
        logging.info(f"[CHECKBOX_SUMMARY]   • {t}")

    summary_lines = [f"\nCHECKBOXES MARCADOS - IMAGEN {image_index + 1}:"]
    for idx, text in enumerate(selected_from_ocr, 1):
        summary_lines.append(f"  {idx}. \"{text}\"")
    summary_lines.append("")
    return "\n".join(summary_lines)


def _build_messages_content(
    ocr_results: List[Dict[str, Any]],
    prompt_usuario: str,
    low_confidence_words: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """
    Construye el input para la Responses API usando texto OCR estructurado y
    resumen de checkboxes. GPT recibe solo texto, no imágenes.

    Incluye una sección de palabras de baja confianza OCR del anverso para
    que GPT evalúe si afectan a campos críticos.
    """
    content_parts: List[Dict[str, Any]] = []

    for i, ocr_structured in enumerate(ocr_results):
        ocr_text = ocr_structured.get("text", "")
        checkbox_summary = _build_checkbox_summary(ocr_structured, i)

        side_label = "FRONT (personal data)" if i == 0 else "BACK (degree selection)"

        text_to_send = (
            f"--- OCR {side_label} ---\n"
            f"{ocr_text}\n"
            f"--- END OCR {side_label} ---\n"
            f"{checkbox_summary}"
        )
        
        logging.info(f"[GPT_INPUT_DEBUG] Parte OCR imagen {i+1} ({side_label}): {len(text_to_send)} chars")
        logging.debug(
            f"[GPT_INPUT_DEBUG] Contenido OCR imagen {i+1}:\n"
            f"{text_to_send[:3000]}{'...[truncado en log]' if len(text_to_send) > 3000 else ''}\n"
        )

        content_parts.append({
            "type": "input_text",
            "text": text_to_send
        })

        if i == 0 and DI_ADDON_KEY_VALUE_PAIRS:
            kvp_list = ocr_structured.get("key_value_pairs", [])
            if kvp_list:
                kvp_lines = ["--- KEY-VALUE PAIRS (Front side) ---"]
                for kv in kvp_list:
                    if kv.get("key") and kv.get("value"):
                        conf_str = f" [{kv['confidence']:.0%}]" if kv.get("confidence") is not None else ""
                        kvp_lines.append(f"{kv['key']}: {kv['value']}{conf_str}")
                kvp_lines.append("--- END KEY-VALUE PAIRS ---")
                kvp_text = "\n".join(kvp_lines) + "\n"
                logging.info(f"[KEY_VALUE_PAIRS] Sección KVP enviada a GPT ({len(kvp_list)} pares):\n{kvp_text.strip()}")
                content_parts.append({
                    "type": "input_text",
                    "text": kvp_text
                })
            else:
                logging.info("[KEY_VALUE_PAIRS] Azure DI no detectó key-value pairs en el anverso")

    if low_confidence_words:
        high_tier = [w for w in low_confidence_words if w["confidence"] < WORD_CONFIDENCE_HIGH_CUTOFF]
        medium_tier = [w for w in low_confidence_words if w["confidence"] >= WORD_CONFIDENCE_HIGH_CUTOFF]
        lines = ["--- OCR UNCERTAINTY (Front side) ---"]
        if high_tier:
            lines.append(f"HIGH (< {WORD_CONFIDENCE_HIGH_CUTOFF:.0%}): OCR genuinely uncertain — flag any critical field:")
            for w in high_tier:
                lines.append(f"  • \"{w['word']}\" ({w['confidence']:.1%})")
        if medium_tier:
            lines.append(f"MEDIUM ({WORD_CONFIDENCE_HIGH_CUTOFF:.0%}–{WORD_CONFIDENCE_THRESHOLD:.0%}): OCR likely correct — flag ONLY if word looks garbled/wrong for its field:")
            for w in medium_tier:
                lines.append(f"  • \"{w['word']}\" ({w['confidence']:.1%})")
        lines.append("--- END OCR UNCERTAINTY ---")
        confidence_section = "\n".join(lines) + "\n"
        logging.info(
            f"[GPT_INPUT_DEBUG] Sección incertidumbre OCR — "
            f"{len(low_confidence_words)} palabras ({len(high_tier)} HIGH, {len(medium_tier)} MEDIUM):\n"
            f"{confidence_section}"
        )
        content_parts.append({
            "type": "input_text",
            "text": confidence_section
        })
    else:
        content_parts.append({
            "type": "input_text",
            "text": "--- OCR UNCERTAINTY (Front side) ---\nNone. Set review_data=false, fields_to_review=\"\".\n--- END OCR UNCERTAINTY ---\n"
        })

    content_parts.append({
        "type": "input_text",
        "text": (
            f"{prompt_usuario}\n\n"
            "Extract all fields from the OCR text above, applying the field-specific "
            "intelligence rules from your instructions. Return the JSON."
        )
    })

    for idx_part, part in enumerate(content_parts):
        if part["type"] == "input_text":
            logging.info(f"[GPT_INPUT_DEBUG] Parte {idx_part + 1}/{len(content_parts)} (input_text): {len(part['text'])} chars")
        else:
            logging.info(f"[GPT_INPUT_DEBUG] Parte {idx_part + 1}/{len(content_parts)} ({part['type']})")

    return [{"role": "user", "content": content_parts}]


def _parse_model_response(content: str, full_text_log: str) -> Dict[str, Any]:
    """Extrae el JSON de la respuesta del modelo."""
    content = content.replace("```json", "").replace("```", "").strip()

    start = content.find('{')
    end = content.rfind('}')

    if start == -1 or end == -1:
        logging.warning("[PARSE_RESPONSE] No se encontró JSON válido en la respuesta del modelo")
        logging.warning(f"[PARSE_RESPONSE] Contenido recibido: {content[:300]}")
        return {"ocr_text": full_text_log}

    try:
        json_str = content[start:end+1]
        result = json.loads(json_str)
        result["ocr_text"] = full_text_log

        if "centro_origen" in result and "centro" not in result:
            result["centro"] = result["centro_origen"]
        if "apellidos" in result and "apellido" not in result:
            result["apellido"] = result["apellidos"]

        logging.info(f"[PARSE_RESPONSE] JSON parseado correctamente con {len(result) - 1} campos")
        return result
    except json.JSONDecodeError as e:
        logging.error(f"[PARSE_RESPONSE] Error parseando JSON del modelo: {e}")
        logging.error(f"[PARSE_RESPONSE] JSON problemático: {json_str[:500]}")
        return {"ocr_text": full_text_log}


def _compute_word_confidence_stats(ocr_front_page: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calcula estadísticas de confianza de palabras del OCR para el anverso.

    Devuelve:
    - mean_confidence, min_confidence, total_words: métricas de log
    - low_confidence_words: palabras con confianza < WORD_CONFIDENCE_THRESHOLD,
      que se enviarán a GPT para que evalúe si afectan a campos críticos y
      active review_data=true en la respuesta.

        Solo se reportan palabras de baja confianza que pertenezcan al contenido
        manuscrito, filtrando ruido del texto preimpreso legal del formulario.
    """
    all_words = []
    for page in ocr_front_page.get("pages", []):
        for word in page.get("words", []):
            all_words.append(word)

    if not all_words:
        logging.info("[WORD_CONFIDENCE] Sin palabras para analizar")
        return {
            "mean_confidence": 0,
            "min_confidence": 0,
            "total_words": 0,
            "low_confidence_words": [],
        }

    confidences = [w["confidence"] for w in all_words]
    mean_conf = sum(confidences) / len(confidences)
    min_conf = min(confidences)

    manuscript_raw = ocr_front_page.get("handwritten_content", "")
    if manuscript_raw:
        manuscript_tokens = set(
            t for t in re.split(r"[\s|/,]+", _normalize_center_text(manuscript_raw)) if t
        )
        def in_manuscript(content: str) -> bool:
            subtokens = [t for t in _normalize_center_text(content).split() if t]
            return bool(subtokens) and all(t in manuscript_tokens for t in subtokens)
    else:
        def in_manuscript(content: str) -> bool:
            return True

    low_confidence_words = [
        {"word": w["content"], "confidence": round(w["confidence"], 3)}
        for w in all_words
        if w["confidence"] < WORD_CONFIDENCE_THRESHOLD and in_manuscript(w["content"])
    ]

    sep = "-" * 55
    level = logging.WARNING if low_confidence_words else logging.INFO
    logging.log(level, f"[WORD_CONFIDENCE] {sep}")
    logging.log(level, f"[WORD_CONFIDENCE] Anverso — palabras de baja confianza: {len(low_confidence_words)}/{len(all_words)}")
    logging.log(level, f"[WORD_CONFIDENCE]   Confianza media  : {mean_conf:.3f}")
    logging.log(level, f"[WORD_CONFIDENCE]   Confianza mínima : {min_conf:.3f}")
    logging.log(level, f"[WORD_CONFIDENCE]   Umbral aplicado  : {WORD_CONFIDENCE_THRESHOLD}")
    if low_confidence_words:
        logging.debug(f"[WORD_CONFIDENCE]   {'Palabra':<25} {'Confianza':>9}")
        logging.debug(f"[WORD_CONFIDENCE]   {'─' * 25} {'─' * 9}")
        for w in low_confidence_words:
            logging.debug(f"[WORD_CONFIDENCE]   {w['word']:<25} {w['confidence']:>9.3f}")
    logging.log(level, f"[WORD_CONFIDENCE] {sep}")

    return {
        "mean_confidence": round(mean_conf, 3),
        "min_confidence": round(min_conf, 3),
        "total_words": len(all_words),
        "low_confidence_words": low_confidence_words,
    }


def _save_debug_snapshot(snapshot: Dict[str, Any]) -> None:
    """Persiste un snapshot JSON de diagnóstico solo si SAVE_DEBUG_SNAPSHOT está activo."""
    if not SAVE_DEBUG_SNAPSHOT:
        logging.info("[DEBUG_SNAPSHOT] Desactivado (SAVE_DEBUG_SNAPSHOT no activo)")
        return
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        nombre_raw = snapshot.get("nombre_imagen", "unknown")
        nombre_safe = re.sub(r"[^\w.-]", "_", nombre_raw)
        filepath = os.path.join(DEBUG_DIR, f"{ts}_{nombre_safe}.json")
        with open(filepath, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False, indent=2)
        logging.info(f"[DEBUG_SNAPSHOT] Guardado: {os.path.relpath(filepath, PROJECT_ROOT)}")
    except Exception as exc:
        logging.warning(f"[DEBUG_SNAPSHOT] No se pudo guardar el snapshot: {exc}")


def analyze_images_with_gpt(
    images_bytes_list: List[bytes],
    prompt_usuario: str,
) -> Dict[str, Any]:
    """
    Analiza imágenes con GPT usando OCR estructurado y structured output.

    Flujo:
    1. OCR estructurado de cada imagen (texto + checkboxes + manuscrito)
    2. Cálculo de confianza por palabra (solo anverso)
    3. Envío de OCR + palabras de baja confianza + contexto a GPT
    4. GPT extrae campos y evalúa si la ficha necesita revisión
    """
    total_start = time.perf_counter()
    ocr_results = []
    full_text_log = ""
    _snapshot: Dict[str, Any] = {
        "nombre_imagen": "",
        "prompt_usuario": prompt_usuario,
        "timestamp": datetime.now().isoformat(),
        "ocr_pages": [],
        "low_confidence_words": [],
        "system_prompt": "",
        "messages_text_parts": [],
        "gpt_response_raw": "",
        "gpt_parsed_fields": {},
        "crm_result": None,
        "timing_ms": {},
        "error": None,
    }

    for idx, img_bytes in enumerate(images_bytes_list):
        logging.info(f"[GPT_ANALYZE] Procesando OCR imagen {idx + 1}/{len(images_bytes_list)} ({len(img_bytes)} bytes)...")
        is_reverso = (idx == 1)
        ocr_structured = perform_ocr_structured(
            img_bytes,
            high_resolution=is_reverso and DI_ADDON_HIGH_RESOLUTION,
            query_fields=DI_QUERY_FIELDS_FRONT if (not is_reverso and DI_ADDON_QUERY_FIELDS) else None,
            image_index=idx,
        )
        ocr_results.append(ocr_structured)

        ocr_text = ocr_structured.get("text", "")
        full_text_log += f"\n--- OCR PÁGINA {idx + 1} ---\n{ocr_text}\n"
        _snapshot["ocr_pages"].append({
            "page": idx + 1,
            "side": "anverso" if idx == 0 else "reverso",
            "chars": len(ocr_text),
            "text": ocr_text,
            "has_handwritten": ocr_structured.get("has_handwritten", False),
            "num_selection_marks": len(ocr_structured.get("selection_marks", [])),
            "kvp_count": len(ocr_structured.get("key_value_pairs", [])),
        })
        marks_total = len(ocr_structured.get("selection_marks", []))
        marks_selected = sum(1 for m in ocr_structured.get("selection_marks", []) if m.get("state") == "selected")
        logging.info(
            f"[GPT_ANALYZE] OCR imagen {idx + 1}: {len(ocr_text)} chars, "
            f"manuscrito={ocr_structured.get('has_handwritten', False)}, "
            f"selection_marks={marks_selected}/{marks_total} seleccionadas, "
            f"kvp={ocr_structured.get('kvp_count', len(ocr_structured.get('key_value_pairs', [])))}"
        )
        logging.info(f"[GPT_ANALYZE] OCR imagen {idx + 1}: texto completo disponible en snapshot si se activa")
        logging.debug(
            f"[GPT_ANALYZE] OCR imagen {idx + 1} preview:\n"
            f"{ocr_text[:2500]}{'...[truncado en log]' if len(ocr_text) > 2500 else ''}"
        )

    low_confidence_words = []
    if ocr_results:
        confidence_stats = _compute_word_confidence_stats(ocr_results[0])
        low_confidence_words = confidence_stats.get("low_confidence_words", [])

    barcodes_data: Dict[str, Any] = {}
    if len(ocr_results) > 1:
        reverso_barcodes = ocr_results[1].get("barcodes", [])
        if reverso_barcodes:
            barcodes_data = _process_barcodes(reverso_barcodes)

    kvp_confidence: Dict[str, float] = {}
    if ocr_results and DI_ADDON_KEY_VALUE_PAIRS:
        kvp_canonicos = _extract_from_key_value_pairs(ocr_results[0].get("key_value_pairs", []))
        kvp_confidence = _extract_kvp_confidence(ocr_results[0].get("key_value_pairs", []))
        if kvp_canonicos:
            logging.info(f"[KEY_VALUE_PAIRS] Campos canónicos detectados por Azure DI: {kvp_canonicos}")
        else:
            logging.info("[KEY_VALUE_PAIRS] No se pudieron mapear campos canónicos de los KVP")

    _snapshot["low_confidence_words"] = low_confidence_words

    logging.info(f"[GPT_ANALYZE] Construyendo mensajes para GPT...")
    messages_content = _build_messages_content(
        ocr_results, prompt_usuario,
        low_confidence_words=low_confidence_words
    )
    system_prompt = _build_system_prompt()
    _snapshot["system_prompt"] = system_prompt
    _snapshot["messages_text_parts"] = [
        {"type": p["type"], "text": p.get("text", "[IMAGE_OMITTED]")}
        for p in messages_content[0]["content"]
    ]
    total_input_chars = sum(len(p.get("text", "")) for p in messages_content[0]["content"])
    logging.info(
        f"[GPT_ANALYZE] System prompt: {len(system_prompt)} chars. "
        f"Input construido: {len(messages_content[0]['content'])} partes, {total_input_chars} chars totales"
    )

    try:
        client = get_openai_client()
        logging.info(f"[GPT_ANALYZE] Enviando a GPT ({OPENAI_DEPLOYMENT_NAME}), reasoning={GPT_REASONING_EFFORT}, max_tokens={GPT_MAX_OUTPUT_TOKENS}...")
        gpt_start = time.perf_counter()
        response = client.responses.create(
            model=OPENAI_DEPLOYMENT_NAME,
            instructions=system_prompt,
            input=messages_content,
            reasoning={"effort": GPT_REASONING_EFFORT},
            max_output_tokens=GPT_MAX_OUTPUT_TOKENS,
            text={"format": EXTRACTION_JSON_FORMAT},
        )

        content = response.output_text or ""
        gpt_elapsed_ms = round((time.perf_counter() - gpt_start) * 1000, 2)
        total_elapsed_ms = round((time.perf_counter() - total_start) * 1000, 2)
        logging.info(f"[GPT_ANALYZE] Respuesta GPT recibida: {len(content)} chars en {gpt_elapsed_ms} ms (total pipeline: {total_elapsed_ms} ms)")
        logging.debug(f"[GPT_ANALYZE] Respuesta GPT raw:\n{content}")
        parsed = _parse_model_response(content, full_text_log)
        if barcodes_data:
            parsed["_barcodes_data"] = barcodes_data
        parsed["_low_confidence_words"] = low_confidence_words
        parsed["_kvp_confidence"] = kvp_confidence

        campos_gpt = {k: v for k, v in parsed.items() if k not in ('ocr_text', '_barcodes_data', '_low_confidence_words')}
        logging.info(f"[GPT_ANALYZE] Campos extraídos por GPT ({len(campos_gpt)}):")
        for k, v in campos_gpt.items():
            valor_str = str(v)[:200] if v else '(vacío)'
            logging.info(f"[GPT_ANALYZE]   {k}: {valor_str}")

        _snapshot["timing_ms"] = {"gpt": gpt_elapsed_ms, "total_pipeline": total_elapsed_ms}
        _snapshot["gpt_response_raw"] = content
        _snapshot["gpt_parsed_fields"] = {k: v for k, v in campos_gpt.items()}
        parsed["_debug_snapshot"] = _snapshot
        return parsed

    except Exception as e:
        logging.error(f"[GPT] Error en llamada a OpenAI: {e}", exc_info=True)
        _snapshot["error"] = str(e)
        result = {"ocr_text": full_text_log, "_debug_snapshot": _snapshot}
        return result

# ————————————————————————————————————————————————————————————————————————————
# EXTRACCIÓN Y TRANSFORMACIÓN DE DATOS
# ————————————————————————————————————————————————————————————————————————————

def _extract_course_id(datos: Dict[str, Any]) -> str:
    """Extrae el ID del curso desde los datos del modelo o del OCR."""
    course_name_gpt = clean_text(datos.get("curso", ""))
    course_id = analyze_curso_local(course_name_gpt)

    if not course_id:
        course_name_ocr = extract_course_from_text(datos.get("ocr_text", ""))
        course_id = analyze_curso_local(course_name_ocr)

    return course_id


def _extract_basic_fields(datos: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extrae y normaliza los campos básicos del alumno: nombre, apellidos, DNI, teléfono, email.

    Devuelve un dict con los campos normalizados y los flags de corrección OCR
    (telefono_had_corrections, email_had_accents) necesarios para el post-processing
    de revisión.
    """
    nombre_raw = clean_text(datos.get("nombre", ""))
    logging.info(f"[EXTRAER_DATOS] Nombre (GPT): '{nombre_raw}'")

    telefono_raw_str = clean_text(datos.get("telefono", ""))
    _tel_check = re.sub(r'[\s\-\.\+\(\)]', '', telefono_raw_str.upper())
    telefono_had_corrections = bool(telefono_raw_str and any(c in OCR_LETTER_TO_DIGIT for c in _tel_check))
    telefono = _normalize_phone(telefono_raw_str)

    apellidos_raw = datos.get("apellido", "") or datos.get("apellidos", "")
    logging.info(f"[EXTRAER_DATOS] Apellidos (GPT): '{apellidos_raw}'")
    middlename, lastname = _split_apellidos(apellidos_raw)
    logging.info(f"[EXTRAER_DATOS] Split apellidos: middlename='{middlename}', lastname='{lastname}'")

    provincia_usuario = clean_text(datos.get("provincia", ""))
    logging.info(f"[EXTRAER_DATOS] Provincia: '{provincia_usuario}'")

    dni_raw = clean_text(datos.get("dni", ""))
    dni_normalizado = _normalize_dni_nie(dni_raw)
    _dni_raw_clean = re.sub(r"[\s\-.]", "", (dni_raw or "").upper())
    _raw_last = _dni_raw_clean[-1] if _dni_raw_clean else ""
    _norm_last = dni_normalizado[-1] if dni_normalizado else ""

    # Caso A: la posición de la letra tenía un dígito/no-alfa → el código calculó una letra
    _raw_last_was_nonalpha = bool(_raw_last and not _raw_last.isalpha())

    # Caso B: Azure DI leyó una letra real pero no coincide con el algoritmo módulo 23
    _DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
    _dni_letter_mismatch = False
    if _norm_last and _norm_last.isalpha() and dni_normalizado and len(dni_normalizado) > 1:
        _body_only = re.sub(r"[^0-9]", "", dni_normalizado)
        _is_nie_check = dni_normalizado[0] in "XYZ"
        if _is_nie_check:
            _nie_prefix = {"X": "0", "Y": "1", "Z": "2"}.get(dni_normalizado[0], "0")
            _num_calc = _nie_prefix + _body_only
        else:
            _num_calc = _body_only
        try:
            _expected_letter = _DNI_LETTERS[int(_num_calc) % 23]
            _dni_letter_mismatch = (_norm_last.upper() != _expected_letter)
        except (ValueError, IndexError):
            pass

    dni_letter_corrected = _raw_last_was_nonalpha or _dni_letter_mismatch
    if dni_letter_corrected:
        _reason = (
            f"posición letra tenía '{_raw_last}' (no-alfa) → calculada"
            if _raw_last_was_nonalpha
            else f"letra '{_norm_last}' ≠ letra esperada por módulo 23"
        )
        logging.warning(f"[DNI_NORMALIZE] DNI requerirá revisión: {_reason}")

    email_raw = datos.get("email", "")
    email_raw_clean = str(email_raw).replace(" ", "") if email_raw else ""
    email_had_accents = bool(
        email_raw_clean and
        unicodedata.normalize('NFKD', email_raw_clean).encode('ascii', 'ignore').decode('ascii') != email_raw_clean
    )
    email = _normalize_email(email_raw)

    if not telefono:
        mobilephone = ""
    elif telefono.startswith("+"):
        mobilephone = telefono
    elif len(telefono) > 9 and telefono.startswith("34"):
        mobilephone = f"+{telefono}"
    elif len(telefono) > 9 and telefono.startswith("0034"):
        mobilephone = f"+{telefono[2:]}"
    else:
        mobilephone = f"+34{telefono}"

    return {
        "firstname": nombre_raw,
        "middlename": middlename,
        "lastname": lastname,
        "provincia": provincia_usuario,
        "dni": dni_normalizado,
        "email": email,
        "mobilephone": mobilephone,
        "telefono": telefono,
        "telefono_had_corrections": telefono_had_corrections,
        "email_had_accents": email_had_accents,
        "dni_letter_corrected": dni_letter_corrected,
    }


def _extract_degrees_fields(
    datos: Dict[str, Any],
    provincia_usuario: str,
) -> Dict[str, Any]:
    """
    Mapea las titulaciones marcadas en checkboxes y la titulación manuscrita al formato CRM.

    Devuelve final_degrees (lista para Degrees[]) o final_id_study (para IdStudy),
    junto con titulacion_needs_review para el post-processing de revisión.

    Se marca revisión cuando hay titulación manuscrita sin match de catálogo o
    cuando entra en conflicto con las titulaciones marcadas en checkboxes.
    """
    titulaciones_checkbox = datos.get("titulaciones_marcadas_checkbox", [])
    logging.info(f"[EXTRAER_DATOS] Titulaciones checkbox (GPT): {titulaciones_checkbox}")
    degrees_array = map_checked_degrees(titulaciones_checkbox, provincia_usuario)

    nombre_titulacion_manuscrita = clean_text(
        datos.get("titulacion_manuscrita", "") or datos.get("titulacion_seleccionada", "")
    )
    logging.info(f"[EXTRAER_DATOS] Titulación manuscrita (GPT): '{nombre_titulacion_manuscrita}'")
    id_study_manuscrito = analyze_titulacion_local(nombre_titulacion_manuscrita, provincia_usuario)
    logging.info(f"[EXTRAER_DATOS] ID titulación manuscrita: '{id_study_manuscrito}'")

    if not id_study_manuscrito and nombre_titulacion_manuscrita and degrees_array:
        logging.info("[EXTRAER_DATOS] Titulación manuscrita sin match directo, buscando en degrees_array...")
        GlobalDataManager.load()
        titulacion_normalizada = _normalize_titulacion_input(nombre_titulacion_manuscrita)

        id_to_names: Dict[str, List[str]] = {}
        for name, tid in GlobalDataManager.titulaciones.items():
            norm_name = GlobalDataManager._normalize_titulacion_name(name)
            if tid not in id_to_names:
                id_to_names[tid] = []
            id_to_names[tid].append(norm_name)

        best_match_id = None
        best_match_score = 0
        for degree_id in degrees_array:
            names = id_to_names.get(degree_id, [])
            for name in names:
                score = fuzz.token_set_ratio(titulacion_normalizada, name)
                if score > best_match_score:
                    best_match_score = score
                    best_match_id = degree_id

        if best_match_id and best_match_score >= 50:
            id_study_manuscrito = best_match_id
            logging.info(f"[EXTRAER_DATOS] Titulación manuscrita correlacionada con degree: ID={best_match_id} (Score: {best_match_score})")
        else:
            logging.info(f"[EXTRAER_DATOS] No se pudo correlacionar titulación manuscrita (mejor score: {best_match_score})")

    titulacion_needs_review = False
    if nombre_titulacion_manuscrita:
        if not id_study_manuscrito:
            titulacion_needs_review = True
            logging.warning("[EXTRAER_DATOS] Titulación manuscrita sin match en catálogo → revisión necesaria")
        elif degrees_array and id_study_manuscrito not in degrees_array:
            titulacion_needs_review = True
            logging.warning(
                f"[EXTRAER_DATOS] Conflicto titulación: manuscrita (ID={id_study_manuscrito}) "
                f"no coincide con checkboxes marcados → revisión necesaria"
            )

    if degrees_array:
        if id_study_manuscrito and id_study_manuscrito not in degrees_array:
            degrees_array.insert(0, id_study_manuscrito)
        elif id_study_manuscrito and id_study_manuscrito in degrees_array:
            degrees_array.remove(id_study_manuscrito)
            degrees_array.insert(0, id_study_manuscrito)

        final_degrees: Optional[List[Dict[str, str]]] = [{"IdStudy": degree_id} for degree_id in degrees_array]
        final_id_study: Optional[str] = None
        logging.info(f"[EXTRAER_DATOS] Degrees finales: {final_degrees}")
    else:
        final_id_study = id_study_manuscrito if id_study_manuscrito else ""
        final_degrees = None
        logging.info(f"[EXTRAER_DATOS] IdStudy final (sin checkbox): '{final_id_study}'")

    return {
        "final_degrees": final_degrees,
        "final_id_study": final_id_study,
        "titulacion_needs_review": titulacion_needs_review,
    }


def _extract_center_fields(
    datos: Dict[str, Any],
    provincia: str,
) -> Dict[str, Any]:
    """
    Resuelve el centro de procedencia del alumno usando analyze_center_optimized().

    Devuelve los campos ProvenanceCenter*, OtherCenter y el dict raw del centro CRM.
    """
    localidad = clean_text(datos.get("localidad_centro", "") or datos.get("localidad", ""))
    nombre_centro = clean_text(datos.get("centro", "") or datos.get("centro_origen", ""))
    logging.info(f"[EXTRAER_DATOS] Centro: '{nombre_centro}', Localidad: '{localidad}', Provincia: '{provincia}'")

    json_centro = analyze_center_optimized(provincia, localidad, nombre_centro)

    return {
        "nombre_centro": nombre_centro,
        "json_centro": json_centro,
    }


def _parse_review_fields(fields_to_review: Any) -> List[str]:
    """Normaliza FieldsToReview a lista ordenada sin duplicados."""
    fields: List[str] = []
    for field in str(fields_to_review or "").split(","):
        field = field.strip()
        if field and field not in fields:
            fields.append(field)
    return fields


def _write_review_fields(result_data: Dict[str, Any], fields: List[str]) -> None:
    """Escribe FieldsToReview y ReviewData desde la lista final de campos."""
    result_data["FieldsToReview"] = ", ".join(fields)
    result_data["ReviewData"] = bool(fields)


def _email_high_uncertainty_words(
    email: str,
    low_confidence_words: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Devuelve palabras HIGH que parecen pertenecer al email normalizado.

    Evita asociar tokens demasiado cortos como TLDs aislados porque sin posiciÃ³n
    OCR pueden venir de otra zona del formulario.
    """
    email_key = re.sub(r"[^A-Za-z0-9]+", "", email or "").upper()
    matches: List[Dict[str, Any]] = []
    if not email_key:
        return matches
    for word in low_confidence_words:
        if word.get("confidence", 1.0) >= WORD_CONFIDENCE_HIGH_CUTOFF:
            continue
        raw_word = str(word.get("word", "") or "")
        word_key = re.sub(r"[^A-Za-z0-9]+", "", _normalize_email(raw_word)).upper()
        if not word_key:
            continue
        if "@" in raw_word or "." in raw_word or len(word_key) >= 3:
            if word_key in email_key or email_key in word_key:
                matches.append(word)
    return matches


_INSTITUTIONAL_EMAIL_KEYWORDS = (
    "school", "escuela", "colegio", "instituto", "academy", "academia",
)


def _email_review_reason(
    email: str,
    low_confidence_words: List[Dict[str, Any]],
    email_had_accents: bool,
) -> Optional[str]:
    """Indica por qué Email requiere revisión o None si no hay evidencia propia."""
    if not email:
        return None
    if email_had_accents:
        return "email con acentos normalizados"
    if not _email_has_valid_structure(email):
        return "estructura inválida"
    domain = email.split("@")[-1].lower() if "@" in email else ""
    if any(kw in domain for kw in _INSTITUTIONAL_EMAIL_KEYWORDS) or domain.endswith(".org"):
        return f"email institucional (dominio de centro): {domain}"
    high_words = _email_high_uncertainty_words(email, low_confidence_words)
    if high_words:
        words = ", ".join(
            f"{w.get('word')} ({float(w.get('confidence', 0)):.1%})"
            for w in high_words
        )
        return f"palabra(s) HIGH en email: {words}"
    return None


def _center_review_reason(nombre_centro: str, json_centro: Dict[str, Any]) -> Optional[str]:
    """Indica por quÃ© Centro requiere revisiÃ³n segÃºn estrategia y score."""
    if not nombre_centro:
        return None
    if not json_centro:
        return "sin match CRM; se usa OtherCenter"
    strategy = str(json_centro.get("_match_strategy", "") or "")
    score_raw = json_centro.get("_match_score")
    score = float(score_raw) if score_raw is not None else None
    if strategy in {"exact_normalized", "contains"}:
        return None
    if strategy in {"locality_first", "fuzzy_global", "fallback_wratio"}:
        if score is None or score < CENTER_REVIEW_SCORE_THRESHOLD:
            return f"estrategia {strategy} con score {score if score is not None else 'N/A'}"
        return None
    return None


def _build_crm_record(
    basic: Dict[str, Any],
    center: Dict[str, Any],
    degrees: Dict[str, Any],
    course_id: str,
    datos: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Ensambla el registro CRM final y aplica los flags de revisión programáticos.

    Los flags ReviewData y FieldsToReview provienen de dos fuentes:
    - GPT: evaluó palabras de baja confianza OCR y las mapeó a campos críticos.
    - Código: detecta correcciones OCR en teléfono, email con acentos, e inconsistencias
      en titulación, independientemente del resultado GPT.

        Reglas adicionales de negocio:
        - Si Nombre o Apellidos se flaggean, se revisan siempre juntos.
        - Email y Centro solo conservan revisión con evidencia propia.
    """
    firstname = basic["firstname"]
    middlename = basic["middlename"]
    lastname = basic["lastname"]
    dni_normalizado = basic["dni"]
    email = basic["email"]
    mobilephone = basic["mobilephone"]
    telefono = basic["telefono"]
    telefono_had_corrections = basic["telefono_had_corrections"]
    email_had_accents = basic["email_had_accents"]
    low_confidence_words = datos.get("_low_confidence_words", []) or []

    nombre_centro = center["nombre_centro"]
    json_centro = center["json_centro"]

    final_degrees = degrees["final_degrees"]
    final_id_study = degrees["final_id_study"]
    titulacion_needs_review = degrees["titulacion_needs_review"]

    result_data: Dict[str, Any] = {
        "Id": "",
        "Description": "Solicitud de información procedente de escaneo automático",
        "DNI": dni_normalizado,
        "Passport": "",
        "Firstname": firstname,
        "Middlename": middlename,
        "Lastname": lastname,
        "Mobilephone": mobilephone,
        "Email": email,
        "IdStudentCurse": course_id,
        "OtherCenter": nombre_centro if not json_centro else "",
        "ProvenanceCenterId": json_centro.get("Id", "") if json_centro else None,
        "ProvenanceCenterName": json_centro.get("Name", "") if json_centro else None,
        "ProvenanceCenterProvinceId": json_centro.get("IdProvince", "") if json_centro else None,
        "ProvenanceCenterCityId": json_centro.get("IdCity", "") if json_centro else None,
        "ProvenanceCenterCountryId": json_centro.get("IdCountry", "") if json_centro else None,
        "AccessWay": "",
        "Session": DEFAULT_SESSION_ID,
        "Campaign": DEFAULT_CAMPAIGN_ID,
        "RequestType": DEFAULT_REQUEST_TYPE,
        "Owner": DEFAULT_OWNER_ID,
        "BulkEmail": True,
        "ReviewData": bool(datos.get("review_data", False)),
        "FieldsToReview": str(datos.get("fields_to_review", "") or "").strip(),
    }

    if final_degrees is not None:
        result_data["Degrees"] = final_degrees
    else:
        result_data["IdStudy"] = final_id_study

    barcodes_data = datos.get("_barcodes_data") or {}
    dni_qr = barcodes_data.get("dni_qr", "")
    if dni_qr and dni_normalizado and dni_qr != dni_normalizado:
        logging.warning(
            f"[QR_BARCODE] Discrepancia DNI: OCR/GPT='{dni_normalizado}' vs QR='{dni_qr}' → flag DNI"
        )

    _extra_review_fields = []
    if dni_qr and dni_normalizado and dni_qr != dni_normalizado:
        _extra_review_fields.append("DNI")
    if basic.get("dni_letter_corrected") and "DNI" not in _extra_review_fields:
        _extra_review_fields.append("DNI")
        logging.warning(
            "[EXTRAER_DATOS] Letra de control DNI corregida automáticamente (módulo 23) → flag DNI"
        )
    if titulacion_needs_review:
        _extra_review_fields.append("Titulación")
    if telefono_had_corrections:
        _extra_review_fields.append("Teléfono")
    elif telefono and len(telefono) == 9 and telefono[0] not in "6789":
        logging.warning(
            f"[EXTRAER_DATOS] Teléfono con formato español inválido: '{telefono}' "
            f"(empieza por '{telefono[0]}', debe empezar por 6/7/8/9) -> flag Teléfono"
        )
        _extra_review_fields.append("Teléfono")
    email_reason = _email_review_reason(email, low_confidence_words, email_had_accents)
    center_reason = _center_review_reason(nombre_centro, json_centro)
    if email_reason:
        _extra_review_fields.append("Email")
        logging.warning(f"[EXTRAER_DATOS] Email requiere revisión: {email_reason}")
    if center_reason:
        _extra_review_fields.append("Centro")
        logging.warning(f"[EXTRAER_DATOS] Centro requiere revisión: {center_reason}")

    _fields_set = _parse_review_fields(result_data["FieldsToReview"])
    for field in _extra_review_fields:
        if field not in _fields_set:
            _fields_set.append(field)
    if _extra_review_fields:
        logging.warning(f"[EXTRAER_DATOS] Flags adicionales (código): {_extra_review_fields}")
    _KVP_CONF_NO_PROPAGATE = 0.90
    _kvp_conf = datos.get("_kvp_confidence", {})
    if "Nombre" in _fields_set or "Apellidos" in _fields_set:
        if "Nombre" not in _fields_set:
            nombre_conf = _kvp_conf.get("nombre", 0.0)
            if nombre_conf < _KVP_CONF_NO_PROPAGATE:
                _fields_set.append("Nombre")
                logging.warning("[EXTRAER_DATOS] Apellidos flaggeado → añadiendo Nombre automáticamente")
            else:
                logging.info(f"[EXTRAER_DATOS] Apellidos flaggeado pero Nombre KVP={nombre_conf:.0%} → no se propaga")
        if "Apellidos" not in _fields_set:
            apellido_conf = _kvp_conf.get("apellido", 0.0)
            if apellido_conf < _KVP_CONF_NO_PROPAGATE:
                _fields_set.append("Apellidos")
                logging.warning("[EXTRAER_DATOS] Nombre flaggeado → añadiendo Apellidos automáticamente")
            else:
                logging.info(f"[EXTRAER_DATOS] Nombre flaggeado pero Apellidos KVP={apellido_conf:.0%} → no se propaga")
    if "Email" in _fields_set and not email_reason:
        _fields_set.remove("Email")
        logging.info("[EXTRAER_DATOS] Email retirado de revisión: estructura válida y sin evidencia HIGH propia")
    if "Centro" in _fields_set and not center_reason:
        _fields_set.remove("Centro")
        strategy = json_centro.get("_match_strategy", "") if json_centro else ""
        score = json_centro.get("_match_score", "") if json_centro else ""
        logging.info(
            f"[EXTRAER_DATOS] Centro retirado de revisión: match CRM suficiente "
            f"(estrategia={strategy or 'N/A'}, score={score or 'N/A'})"
        )

    _write_review_fields(result_data, _fields_set)

    logging.info("[EXTRAER_DATOS] Validación estricta de campos obligatorios desactivada")

    logging.info("[EXTRAER_DATOS] ====== RESUMEN CRM ======")
    logging.info(f"[EXTRAER_DATOS]   DNI: '{dni_normalizado}'")
    logging.info(f"[EXTRAER_DATOS]   Firstname: '{firstname}'")
    logging.info(f"[EXTRAER_DATOS]   Middlename: '{middlename}'")
    logging.info(f"[EXTRAER_DATOS]   Lastname: '{lastname}'")
    logging.info(f"[EXTRAER_DATOS]   Mobilephone: '{mobilephone}'")
    logging.info(f"[EXTRAER_DATOS]   Email: '{email}'")
    logging.info(f"[EXTRAER_DATOS]   Centro: '{result_data.get('ProvenanceCenterName', result_data.get('OtherCenter', ''))}'")
    logging.info(f"[EXTRAER_DATOS]   Curso: '{course_id}'")
    logging.info(f"[EXTRAER_DATOS]   RequestType: {result_data['RequestType']}")
    if final_degrees is not None:
        logging.info(f"[EXTRAER_DATOS]   Degrees: {len(final_degrees)} titulaciones")
    else:
        logging.info(f"[EXTRAER_DATOS]   IdStudy: '{final_id_study}'")
    review_flag = result_data.get("ReviewData", False)
    fields_flag = result_data.get("FieldsToReview", "")
    logging.log(
        logging.WARNING if review_flag else logging.INFO,
        f"[EXTRAER_DATOS]   ReviewData: {review_flag}"
        f"{' ⚠ REQUIERE REVISIÓN HUMANA' if review_flag else ' ✓'}"
    )
    if review_flag and fields_flag:
        logging.warning(f"[EXTRAER_DATOS]   FieldsToReview: '{fields_flag}'")
    logging.info("[EXTRAER_DATOS] ====== FIN TRANSFORMACIÓN CRM ======")

    return result_data


def extraer_datos(datos: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Transforma los datos extraídos por GPT al formato final del CRM.

    Proceso de transformación:
    1. Extrae campos básicos (DNI, nombre, teléfono, email)
    2. Mapea titulaciones y curso
    3. Resuelve centro de procedencia (cascada de 5 estrategias)
    4. Construye registro CRM final
    5. Determina campos que requieren revisión humana
    """
    try:
        GlobalDataManager.load()
        logging.info("[EXTRAER_DATOS] ====== INICIO TRANSFORMACIÓN CRM ======")

        basic = _extract_basic_fields(datos)
        degrees = _extract_degrees_fields(datos, basic["provincia"])
        center = _extract_center_fields(datos, basic["provincia"])
        course_id = _extract_course_id(datos)
        logging.info(f"[EXTRAER_DATOS] Curso ID: '{course_id}'")

        result_data = _build_crm_record(basic, center, degrees, course_id, datos)
        return [result_data]

    except Exception as e:
        logging.error(f"[EXTRAER_DATOS] Error en transformación: {e}", exc_info=True)
        raise ValueError(f"Error procesando datos: {str(e)}") from e

# ————————————————————————————————————————————————————————————————————————————
# PROCESAMIENTO DE IMÁGENES Y FUNCIÓN PRINCIPAL
# ————————————————————————————————————————————————————————————————————————————

def _parse_image_number(nombre_imagen: str) -> Tuple[int, str, str]:
    """Extrae el número de imagen y calcula los nombres del par impar/par."""
    sin_ext = nombre_imagen.rsplit('.', 1)[0]
    ext = nombre_imagen.split('.')[-1]

    try:
        num_img = int(sin_ext.split('_')[-1])
    except (ValueError, IndexError):
        raise ValueError("Nombre de imagen inválido")

    parts = sin_ext.rsplit('_', 1)
    nombre_base = parts[0]

    nombre_par = f"{nombre_base}_{num_img}.{ext}"
    nombre_impar = f"{nombre_base}_{num_img-1}.{ext}"

    return num_img, nombre_par, nombre_impar


def _download_blob_pair(nombre_par: str, nombre_impar: str) -> Tuple[bytes, bytes]:
    """
    Descarga un par de imágenes desde Azure Blob Storage y las rota para orientación correcta.
    
    Cada imagen tiene una orientación diferente y necesita rotación específica:
    - Imagen IMPAR: Necesita 90° a la DERECHA (horario)
    - Imagen PAR: Necesita 270° a la DERECHA (o 90° antihorario)
    """
    blob_service = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONN_STR)

    blob_impar = blob_service.get_blob_client(
        BLOB_CONTAINER_NAME, nombre_impar
    ).download_blob().readall()

    blob_par = blob_service.get_blob_client(
        BLOB_CONTAINER_NAME, nombre_par
    ).download_blob().readall()

    blob_impar_rotated = rotate_image_if_needed(blob_impar, rotation_degrees=90)
    blob_par_rotated = rotate_image_if_needed(blob_par, rotation_degrees=270)

    return blob_impar_rotated, blob_par_rotated


def _process_image_pair(nombre_imagen: str, prompt: str) -> func.HttpResponse:
    """
    Procesa un par de imágenes (anverso y reverso) del formulario.
    Solo procesa cuando el número de imagen es par.
    """
    nombre_par = None
    nombre_impar = None
    res_ai = None
    try:
        GlobalDataManager.load()

        num_img, nombre_par, nombre_impar = _parse_image_number(nombre_imagen)
        logging.info(f"[PROCESS_PAIR] Imagen #{num_img}: par={nombre_par}, impar={nombre_impar}")

        if num_img % 2 != 0:
            logging.info(f"[PROCESS_PAIR] Imagen #{num_img} es impar, esperando par")
            return func.HttpResponse("Esperando par", status_code=202)

        logging.info(f"[PROCESS_PAIR] Descargando blobs desde container '{BLOB_CONTAINER_NAME}'...")
        blob_impar, blob_par = _download_blob_pair(nombre_par, nombre_impar)
        logging.info(f"[PROCESS_PAIR] Blobs descargados: impar={len(blob_impar)} bytes, par={len(blob_par)} bytes")

        logging.info(f"[PROCESS_PAIR] Iniciando análisis GPT...")
        res_ai = analyze_images_with_gpt([blob_impar, blob_par], prompt)
        _snapshot = res_ai.pop("_debug_snapshot", {})
        _snapshot["nombre_imagen"] = nombre_imagen
        logging.info(f"[PROCESS_PAIR] GPT devolvió {len(res_ai)} campos")

        logging.info(f"[PROCESS_PAIR] Transformando datos para CRM...")
        final_result = extraer_datos(res_ai)
        _snapshot["crm_result"] = final_result
        _save_debug_snapshot(_snapshot)
        logging.info(f"[PROCESS_PAIR] ====== RESULTADO FINAL ======")
        logging.info(f"[PROCESS_PAIR] {json.dumps(final_result, ensure_ascii=False, indent=2)}")

        return func.HttpResponse(
            json.dumps(final_result, ensure_ascii=False),
            status_code=200,
            mimetype="application/json"
        )

    except ValueError as e:
        logging.error(f"[PROCESS_PAIR] ValueError: {e}", exc_info=True)
        return func.HttpResponse(str(e), status_code=400)
    except Exception as e:
        logging.error(f"[PROCESS_PAIR] Error interno: {e}", exc_info=True)
        return func.HttpResponse(f"Error interno: {e}", status_code=500)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """Punto de entrada principal de la Azure Function."""
    try:
        req_body = req.get_json()
        if not req_body:
            logging.warning("[MAIN] Cuerpo de solicitud vacío o no es JSON")
            return func.HttpResponse("Cuerpo de solicitud inválido", status_code=400)

        nombre_imagen = req_body.get("nombre_imagen")
        prompt = req_body.get("prompt")

        logging.info(f"[MAIN] ====== NUEVA PETICIÓN ======")
        logging.info(f"[MAIN] Imagen: {nombre_imagen}")
        logging.info(f"[MAIN] Prompt: {prompt[:100] if prompt else '(vacío)'}...")

        if not nombre_imagen or not prompt:
            logging.warning("[MAIN] Faltan campos obligatorios: nombre_imagen=%s, prompt=%s", bool(nombre_imagen), bool(prompt))
            return func.HttpResponse("Faltan campos obligatorios", status_code=400)

        return _process_image_pair(nombre_imagen, prompt)

    except Exception as e:
        logging.error(f"[MAIN] Error no controlado: {e}", exc_info=True)
        return func.HttpResponse(f"Error: {e}", status_code=500)
