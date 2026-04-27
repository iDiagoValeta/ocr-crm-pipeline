# Documentación Técnica — fichas_si_papel

Documentación orientada a programadores. Describe qué hace el código, por qué lo hace,
el flujo de datos completo y cómo se conectan todas las piezas.

---

## 1. Visión general

El sistema es una **Azure Function (Python)** que recibe una petición HTTP con el nombre
de una imagen escaneada, descarga el par de imágenes (anverso + reverso) de Azure Blob
Storage, las procesa con OCR + GPT, y devuelve un JSON listo para insertar en el CRM
como una nueva Solicitud de Información (SI).

```
HTTP POST (nombre_imagen, prompt)
    │
    ▼
main()  →  _process_image_pair()
    │
    ├── _parse_image_number()          // Extraer nº y nombres del par
    ├── _download_blob_pair()          // Descargar + rotar imágenes
    │
    ├── analyze_images_with_gpt()      // Pipeline OCR + GPT
    │   ├── perform_ocr_structured()   // Azure Document Intelligence × 2
    │   ├── _build_checkbox_summary()  // Checkboxes marcados (fuente única: ':selected:' + confianza >= 80%)
    │   ├── _build_messages_content()  // Construir input textual (OCR + resumen checkboxes + KVP)
    │   ├── _build_system_prompt()     // Prompt con inteligencia por campo
    │   └── Responses API call         // GPT-5.2
    │
    └── extraer_datos()                // Orquestador (~10 líneas)
        ├── _extract_basic_fields()    // DNI, nombre, teléfono, email
        ├── _extract_degrees_fields()  // Titulaciones checkbox + manuscrita
        ├── _extract_center_fields()   // Localidad + centro → JSON CRM
        └── _build_crm_record()        // Ensamblaje CRM + flags de revisión
```

---

## 2. Arquitectura y servicios externos

### 2.1 Azure Function

- **Runtime**: Azure Functions v2, Python
- **Trigger**: HTTP (GET/POST), autenticación `anonymous`
- **Configuración**: `host.json` con Application Insights habilitado
- **Dependencias** (`requirements.txt`):
  - `azure-functions` — runtime
  - `azure-storage-blob` — acceso a Blob Storage
  - `azure-ai-documentintelligence` — Azure Document Intelligence v4.0 GA (OCR) *(migrado desde `azure-ai-formrecognizer` al activar tier Standard)*
  - `openai` — Azure OpenAI (GPT)
  - `rapidfuzz` — fuzzy matching
  - `Pillow` — rotación de imágenes
  - `requests` — llamadas HTTP genéricas

### 2.2 Servicios Azure

| Servicio | Uso | Variables de entorno |
|---|---|---|
| **Blob Storage** | Almacena imágenes JPEG escaneadas | `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_BLOB_CONTAINER` (default: `fichas-si-escaneadas`) |
| **Document Intelligence** | OCR con modelo `prebuilt-layout`, SDK v4.0 GA, tier **Standard** | `DOCUMENT_INTELLIGENCE_ENDPOINT`, `DOCUMENT_INTELLIGENCE_KEY` |
| **Azure OpenAI** | GPT-5.2, Responses API | `OPENAI_ENDPOINT`, `OPENAI_API_KEY`, `OPENAI_DEPLOYMENT_NAME`, `OPENAI_API_VERSION` (default: `2025-03-01-preview`) |

#### Extracción de Document Intelligence (tier Standard)

| Extracción | Estado | Coste |
|---|---|---|
| Texto, palabras y marcas de selección | **Activo** | Base |
| Key-Value Pairs (pares etiqueta→valor) | **Activo** | Sin coste adicional |

### 2.3 Parámetros del modelo GPT

| Constante | Valor | Descripción |
|---|---|---|
| `GPT_MODEL` | `"gpt-5.2"` | Modelo base |
| `GPT_REASONING_EFFORT` | `"high"` | Esfuerzo de razonamiento usado en la llamada Responses API |
| `GPT_MAX_OUTPUT_TOKENS` | `32000` | Máximo de tokens de salida |
| `EXTRACTION_JSON_FORMAT` | `{"type": "json_object"}` | Formato sin schema estricto (evita rechazos por campos opcionales ausentes) |

---

## 3. Constantes y umbrales

```python
PROVINCE_MATCH_THRESHOLD     = 60    # Fuzzy score mínimo para provincias
COURSE_MATCH_THRESHOLD       = 60    # Fuzzy score mínimo para cursos
TITULACION_MATCH_THRESHOLD   = 70    # Fuzzy score mínimo para titulaciones
WORD_CONFIDENCE_THRESHOLD    = 0.80  # Palabras con confianza < 0.80 → enviadas a GPT para revisión
WORD_CONFIDENCE_HIGH_CUTOFF  = 0.40  # Tier HIGH: evidencia OCR fuerte para revisión
CENTER_REVIEW_SCORE_THRESHOLD = 80   # Score mínimo para no revisar centros fuzzy/locality/fallback
SELECTION_MARK_CONFIDENCE_THRESHOLD = 0.80  # Confianza mínima checkbox OCR
API_TIMEOUT_SECONDS          = 10    # Timeout para llamadas HTTP externas

# Azure DI extrae texto, palabras, selection marks y key-value pairs
```

> **`WORD_CONFIDENCE_THRESHOLD`**: umbral de entrada para palabras de baja confianza de Azure Document Intelligence.
> Palabras del anverso cuya confianza esté por debajo de este valor se envían a GPT como
> "palabras dudosas". `WORD_CONFIDENCE_HIGH_CUTOFF` separa HIGH (`<0.40`) de MEDIUM (`0.40–0.80`).
> Email no se marca solo por MEDIUM si tiene estructura válida; Centro se decide con la estrategia
> y score de matching CRM. **`DOCUMENT_CONFIDENCE_THRESHOLD` fue eliminado** por ser redundante con
> este mecanismo por palabra.

### Umbrales de localidad y provincia

| Parámetro | Valor actual | Contexto |
|---|---|---|
| `_normalize_localidad_input` — ratio/token_set/WRatio | `≥ 80` | Normalización del texto de localidad escrito por el alumno |
| `_localidad_matches` — ratio | `≥ 80` | ¿Coincide localidad del alumno con la del centro? |
| `_localidad_matches` — token_set_ratio | `≥ 85` | Ídem |
| `_localidad_matches` — WRatio | `≥ 82` | Ídem (scorer añadido) |
| `PROVINCE_MATCH_THRESHOLD` | `60` | Paso fuzzy en `_find_province_key` |

### Corrección OCR: mapa letra→dígito

El OCR manuscrito confunde letras con dígitos visualmente similares. Se corrige en
DNI y teléfono con `OCR_LETTER_TO_DIGIT`:

```python
OCR_LETTER_TO_DIGIT = {
    'O': '0', 'Q': '0', 'D': '0',    # Formas circulares
    'I': '1', 'L': '1',              # Líneas verticales
    'Z': '2',                        # Forma angular
    'E': '3',                        # Forma invertida
    'A': '4',                        # Trazo angular
    'S': '5', 'J': '5',              # Curvas similares
    'G': '6',                        # Circular con cola
    'T': '7',                        # Trazo horizontal
    'B': '8',                        # Doble curva
    'P': '9',                        # Circular superior
}
```

### Valores por defecto CRM

```python
DESCRIPTION           = "Solicitud de información procedente de escaneo automático"
DEFAULT_SESSION_ID    = 100000036
DEFAULT_CAMPAIGN_ID   = "852c6f0a-a94d-f011-877a-7c1e52fbd0c9"
DEFAULT_REQUEST_TYPE  = 100000000
DEFAULT_OWNER_ID      = "57c8523f-a1de-e411-80f1-c4346bad129c"
```

---

## 4. GlobalDataManager — Datos maestros

Singleton que carga y cachea los datos maestros una sola vez. Se invoca con
`GlobalDataManager.load()` (idempotente por flag `loaded`).

### 4.1 Titulaciones (`titulacion/titulaciones.txt`)

- **Formato**: `NOMBRE, ID_CRM` por línea, agrupadas por bloques separados con línea en blanco
- **~100+ entradas** en 157 líneas 
- **Variantes provinciales**: Algunas titulaciones tienen variantes por sede:
  `ENFERMERÍA`, `ENFERMERÍA (CASTELLÓN)`, `ENFERMERÍA (ELCHE)`. Se agrupan en
  `titulacion_groups` por nombre base para selección posterior según provincia del alumno
- **Normalización**: `_normalize_titulacion_name()` elimina prefijos como
  "GRADO EN", "DOBLE GRADO EN", "TITULO DE ESPECIALISTA EN", etc.
- **Estructuras resultantes**:
  - `titulaciones`: `Dict[nombre, id_crm]` — mapeo directo
  - `titulaciones_names`: lista de nombres originales
  - `titulaciones_normalized_to_original`: `Dict[nombre_normalizado, nombre_original]`
  - `titulaciones_normalized_names`: lista para fuzzy matching
  - `titulacion_groups`: `Dict[nombre_base, List[variantes]]` — agrupación por sede

### 4.2 Cursos (`curso/cursos.txt`)

5 entradas, formato `ID, NOMBRE`:

```
d4c1b46e-..., 1º Bach.
e4c1b46e-..., 2º Bach.
6cdc661a-..., 1º CFGS
6edc661a-..., 2º CFGS
d3c1b46e-..., 4º ESO
```

- Estructura: `cursos`: `Dict[nombre, id_crm]`

### 4.3 Centros educativos (`centros/*.txt`)

- Archivos activos: `VALENCIA.txt`, `ALICANTE.txt`, `CASTELLON.txt`, `ALBACETE.txt`, `BALEARES.txt`, `CUENCA.txt`, `MURCIA.txt`, `TERUEL.txt`
- Los TXT de Valencia, Alicante y Castellón pueden contener variantes manuales del mismo centro con el mismo `Id`; se preservan porque mejoran el matching con nombres abreviados o bilingües.
- **Formato**: CSV con `NOMBRE, Id, IdProvince, IdCity, IdCountry` por línea
- **Estructuras**:
  - `centros_by_provincia`: `Dict[provincia, Dict[nombre, datos]]`
  - `all_centros_flat`: `Dict[nombre, datos]` — búsqueda global
  - `all_centros_names`: lista para fuzzy matching

### 4.4 Localidades (`localidades/*.json`)

- Un JSON por provincia activa más `provinceIds.json`
- Cada entrada: `{"Name": "...", "Name_cat": "...", "IdCity": "...", "IdProvince": "..."}`
- Se cargan ambos nombres (`Name` y `Name_cat`) para matching y se usan también como aliases de provincia cuando procede.
- Se complementan con localidades extraídas de los nombres de centros
  (`_extract_localidad_from_center_name()`)
- Estructura: `localidades_by_provincia`: `Dict[provincia, List[nombre]]`

### 4.5 Regeneración de centros desde CRM

`scripts/procesar_centros_raw.py` lee `centros/centrosTablasCRM/*.xlsx`, obtiene el `Id` de la columna A oculta, resuelve `IdProvince`/`IdCity` desde `localidades/` y usa `centrosCompletosAntiguos/` como respaldo cuando el XLSX no trae ciudad o la ciudad textual no encaja.

Reglas importantes:

- Conserva líneas ya existentes en `centros/{PROVINCIA}.txt`, incluidas variantes manuales con el mismo `Id`.
- Añade solo centros cuyo `Id` no estaba activo.
- No inventa `IdCity`: si no puede resolverlo, escribe el caso en `centros/centrosPendientesMatching.txt`.
- Soporta `--dry-run` para revisar totales antes de escribir.

---

## 5. Flujo de procesamiento detallado

El tiempo medio operativo de procesamiento de una ficha completa (anverso + reverso)
está en torno a **30-40 segundos** en producción.

### 5.1 Punto de entrada: `main(req)`

Recibe JSON con `nombre_imagen` y `prompt`. Valida campos obligatorios y delega a
`_process_image_pair()`.

### 5.2 Gestión del par de imágenes

**`_parse_image_number(nombre_imagen)`**: Extrae el número secuencial del nombre
(ej: `scan_fichas_2025-01-15_22.jpeg` → `22`). Los **impares son anverso**, los
**pares son reverso**.

**Comportamiento**: Si la imagen es impar, devuelve HTTP 202 ("Esperando par"). Si es
par, descarga ambas (la par y la impar anterior) y continúa el procesamiento.

**`_download_blob_pair(nombre_par, nombre_impar)`**: Descarga ambas imágenes de Blob
Storage y las rota:
- **Impar (anverso)**: 90° en sentido horario
- **Par (reverso)**: 270° en sentido horario (= 90° antihorario)

La rotación se hace con Pillow (`rotate_image_if_needed()`), calidad JPEG 95.

### 5.3 OCR con Azure Document Intelligence

**`perform_ocr_structured(image_bytes)`**:
Envía la imagen a Document Intelligence con modelo `prebuilt-layout` usando el SDK v4.0
(`DocumentIntelligenceClient`, `AnalyzeDocumentRequest`) y reutiliza un cliente singleton.
Devuelve un diccionario con:

| Campo | Tipo | Descripción |
|---|---|---|
| `text` | `str` | Texto completo del documento |
| `selection_marks` | `List[Dict]` | Checkboxes filtrados por confianza ≥ 0.80 |
| `all_selection_marks_unfiltered` | `List[Dict]` | Todas las marcas antes del filtro (para `_build_checkbox_summary`) |
| `has_handwritten` | `bool` | Si se detectó escritura manuscrita |
| `handwritten_content` | `str` | Texto manuscrito concatenado |
| `pages` | `List[Dict]` | Palabras por página con confianza OCR |
| `key_value_pairs` | `List[Dict]` | Pares `{key, value, confidence}` extraídos por DI |


**Filtrado de checkboxes**: Solo se mantienen en `selection_marks` los que superan
`SELECTION_MARK_CONFIDENCE_THRESHOLD` (0.80). Los descartados se registran en log.
`all_selection_marks_unfiltered` conserva todas las marcas para que `_build_checkbox_summary`
haga la correlación 1:1 con los `:selected:` del texto OCR.

**Cambio de SDK v3.1 → v4.0**: El polígono de líneas y marcas pasó de lista de objetos
punto (`.x`, `.y`) a lista plana de floats `[x1, y1, x2, y2, ...]`. `_format_polygon()`
gestiona ambos formatos. El resultado se obtiene con `.as_dict()` (antes `.to_dict()`).

### 5.4 Detección de checkboxes marcados

**`_extract_selected_from_ocr(ocr_text, selection_marks)`**: Fuente única para identificar
titulaciones marcadas. Combina dos datos del resultado de Azure Document Intelligence:

1. El texto OCR de `prebuilt-layout` embebe `:selected:` / `:unselected:` en el flujo de
   texto. Una titulación está marcada si el token inmediatamente anterior es `:selected:`
   (misma línea, o `:selected:` en la línea anterior y el nombre en la siguiente).
2. El i-ésimo `:selected:` en el texto se corresponde 1:1 con la i-ésima marca
   `state=="selected"` en el resultado bruto de DI. Solo se incluye si
   `confidence >= SELECTION_MARK_CONFIDENCE_THRESHOLD (0.80)`. Los indicadores de campus
   pre-impresos (Valencia/Castellón/Elche) tienen confianza ~0.45–0.77 y son descartados
   automáticamente.
3. Cuando el alumno escribe 'X' o tick junto al checkbox: `:selected: X Titulación` → el
   prefijo X/tick se elimina y se extrae el nombre de la titulación. Si la 'X' ocupa una
   línea sola, el nombre se toma de la línea siguiente.

**`_build_checkbox_summary(ocr_structured, image_index)`**: Extrae las marcas brutas de
`ocr_structured["all_selection_marks_unfiltered"]` (formato agnóstico de SDK), llama a
`_extract_selected_from_ocr()` y produce el bloque `CHECKBOXES MARCADOS - IMAGEN N` que
se incrusta en el mensaje a GPT.

### 5.5 Llamada a GPT

**`analyze_images_with_gpt(images_bytes_list, prompt_usuario)`**: Orquesta el pipeline
completo:

1. OCR de cada imagen (× 2)
2. Construye el resumen de checkboxes (`_build_checkbox_summary()`) — fuente única
3. Construye el input de texto (`_build_messages_content()`)
4. Envía a GPT

**`_build_messages_content()`**: Construye un array de `content_parts` con cuatro fuentes:
- Texto OCR delimitado con `--- OCR FRONT/BACK ---`
- Resumen de checkboxes marcados por imagen (`CHECKBOXES MARCADOS`)
- Sección `KEY-VALUE PAIRS` del anverso con los pares etiqueta→valor que Azure DI extrajo directamente (solo si `DI_ADDON_KEY_VALUE_PAIRS=True` y hay pares). GPT los usa como contraste con el texto OCR, no como fuente primaria.
- Al final: el prompt del usuario

**Nuevas funciones auxiliares**:

- **`_extract_kvp_confidence(kvp_list)`**: Extrae confianza KVP de `Nombre` y `Apellidos` para que `_build_crm_record()` evite propagar flags entre ambos cuando el otro campo tiene evidencia DI alta (`≥90%`).

**`_build_system_prompt()`**: Prompt del sistema con inteligencia por campo:
- Fuentes de datos: OCR es fuente primaria y única
- Reglas generales: No inventar datos, pero SÍ corregir errores evidentes de OCR
- **Inteligencia por campo**:
  - DNI/NIE: Formato esperado, letra de control
  - Nombre (obligatorio): Corrección inteligente de nombres españoles (Edvardo→Eduardo)
  - Apellidos (obligatorio): Mínimo un apellido, corrección inteligente
  - Teléfono: Formato español, prefijo +34; acepta números internacionales
  - Email: Coherencia con nombre/apellidos, salida en minúsculas
  - Provincia: provincia española del centro, normalizada contra las provincias cargadas
  - Localidad: corrección de typos y variantes bilingües dentro de la provincia indicada
  - Centro: Extracción literal (post-procesado hace fuzzy matching)
  - Curso: uno de `4º ESO, 1º Bach, 2º Bach, 1º CFGS, 2º CFGS`
- Campos del reverso: `titulaciones_marcadas_checkbox` (array), `titulacion_manuscrita`
- **`review_data` + `fields_to_review`**: GPT recibe la lista de palabras con confianza OCR
  < 80% y evalúa si alguna pertenece a un campo crítico no verificable desde otros campos.
  Si lo es, activa `review_data=true` y lista los campos en `fields_to_review` (en español).
  Criterio: **cuando hay duda, se flag** — un falso positivo es mucho menos dañino que
  un dato erróneo insertado silenciosamente en el CRM. Reglas especiales actuales:
  **Nombre y Apellidos** se pueden propagar entre sí, salvo que el KVP del otro campo tenga
  confianza alta (`≥90%`). **Email** no hereda flags de Nombre/Apellidos; se marca solo
  con evidencia propia: estructura inválida, acentos normalizados, palabra HIGH asociada
  al email o dominio institucional/de centro. **Centro** usa la estrategia y score del
  match CRM: exact/contains no se revisan por MEDIUM OCR; fuzzy/locality/fallback se revisan
  si caen por debajo de `CENTER_REVIEW_SCORE_THRESHOLD`.
- Formato de respuesta: JSON con claves explícitas

**Llamada a la API** (Responses API):

```python
response = client.responses.create(
    model=OPENAI_DEPLOYMENT_NAME,
    instructions=system_prompt,
    input=messages_content,
    reasoning={"effort": GPT_REASONING_EFFORT},     # "high"
    max_output_tokens=GPT_MAX_OUTPUT_TOKENS,        # 32000
    text={"format": EXTRACTION_JSON_FORMAT},        # json_object
)
```

**`_parse_model_response(content, full_text_log)`**: Extrae el JSON de la respuesta.
Maneja wrappers ```` ```json ``` ````. Añade alias: `centro_origen` → `centro`,
`apellidos` → `apellido`.

### 5.6 Transformación a formato CRM: `extraer_datos(datos)`

`extraer_datos(datos)` actúa como orquestador (~10 líneas): llama a cuatro funciones
auxiliares en orden y ensambla sus resultados en el registro CRM final.

#### 5.6.1 `_extract_basic_fields(datos)` — campos del alumno

Extrae y normaliza los campos de identificación personal. Devuelve un dict con los
campos normalizados y dos flags de post-processing (`telefono_had_corrections`,
`email_had_accents`) que `_build_crm_record` usa para activar revisión programática.

- **DNI/NIE** (`_normalize_dni_nie`): Elimina espacios/guiones/puntos. Detecta si es
  DNI (8 dígitos + letra) o NIE (X/Y/Z + 7 dígitos + letra). Aplica
  `OCR_LETTER_TO_DIGIT` en posiciones numéricas. Si la letra final alfabética no cuadra
  con módulo 23, se conserva la lectura de Azure DI y el campo se marca para revisión;
  si falta letra o hay un dígito en su posición, se calcula la letra y también se marca revisión.
- **Teléfono** (`_normalize_phone`): Limpia caracteres no numéricos. Aplica
  `OCR_LETTER_TO_DIGIT` en cada posición. Detecta prefijos internacionales (+34,
  +33, +39, etc.) y valida longitud mínima del número nacional. Si es inválido,
  devuelve cadena vacía (descarte, no invención). Formatea con prefijo `+34` si no
  tiene prefijo.
- **Email** (`_normalize_email`): Elimina todos los espacios internos. `_email_has_valid_structure()`
  valida estructura básica para flags; `_email_review_reason()` marca revisión por estructura inválida,
  acentos normalizados, palabra HIGH asociada o dominio institucional/de centro (`school`, `colegio`,
  `instituto`, `academy`, `.org`, etc.).
- **Apellidos** (`_split_apellidos`): Primera palabra → `Middlename`, resto →
  `Lastname`.

#### 5.6.2 `_extract_degrees_fields(datos, provincia_usuario)` — titulaciones

Mapea las titulaciones marcadas en checkboxes y la titulación manuscrita al formato CRM.
Devuelve `final_degrees` (lista para `Degrees[]`) o `final_id_study` (para `IdStudy`),
junto con `titulacion_needs_review` para activación de revisión programática.

Delega en `map_checked_degrees()` para los checkboxes y en `analyze_titulacion_local()`
para la titulación manuscrita.

##### Mapeo de titulaciones: `map_checked_degrees()`

Para cada titulación del array `titulaciones_marcadas_checkbox`:
1. Filtra etiquetas de ruido con `_is_non_degree_checkbox_label()` — descarta si el
   texto coincide con una localidad o provincia conocida
2. Busca con `analyze_titulacion_local()`:
   - Normaliza input (quita asteriscos, espacios extra, mayúsculas)
   - Fuzzy match con `process.extract()`, `fuzz.token_set_ratio`, limit=10
   - Filtra por `TITULACION_MATCH_THRESHOLD` (70)
   - Si hay múltiples matches ≥95, desempata con `fuzz.ratio` (más estricto)
   - Si tiene variantes provinciales, `_select_best_variant_by_province()` elige según
    provincia del alumno. Las titulaciones solo existen para sedes Valencia, Castellón y Elche:
    Murcia se envía a Elche/Alicante; Albacete, Baleares, Cuenca, Teruel y provincias sin variante propia se envían a Valencia.
3. Deduplica IDs con `dict.fromkeys()`

**Titulación manuscrita**: Si no matchea directamente y hay `degrees_array`, intenta
correlacionarla con los IDs ya encontrados (score ≥50). Si coincide, se mueve al primer
puesto del array para marcarla como preferida.

**Formato de salida dinámico**:
- Si queda una sola titulación resuelta: `"IdStudy": "id_unico"`
- Si quedan dos o más titulaciones resueltas: `"Degrees": [{"IdStudy": "id1"}, {"IdStudy": "id2"}, ...]`
- Si no hay checkboxes pero sí titulación manuscrita con match: `"IdStudy": "id_manuscrito"`

#### 5.6.3 `_extract_course_id(datos)` — curso

`_extract_course_id()`: Primero intenta con el campo `curso` de GPT vía
`analyze_curso_local()` (fuzzy match contra 5 cursos, `token_set_ratio`, sin umbral
mínimo). Si falla, intenta extraer del texto OCR con regex (`extract_course_from_text`).

#### 5.6.4 `_extract_center_fields(datos, provincia)` — centro de procedencia

Extrae localidad y nombre de centro del dict de GPT y llama a `analyze_center_optimized()`.
Devuelve `nombre_centro` (literal de GPT) y `json_centro` (dict del catálogo CRM o `None`).
`_build_crm_record` decide entre `OtherCenter` y `ProvenanceCenter*` según si `json_centro`
tiene valor.

**`analyze_center_optimized(provincia, localidad, nombre_centro)`**: Busca **solo** en
la provincia indicada. No hace búsqueda global inter-provincias.

Primero normaliza la localidad del alumno con `_normalize_localidad_input()`: aplica
el diccionario de alias bilingüe/abreviaturas (ver sección 5.6.6), luego ejecuta
los tres scorers `fuzz.ratio`, `fuzz.token_set_ratio` y `fuzz.WRatio` contra la
lista oficial de localidades del CRM; acepta el mejor resultado con score `≥ 80`.

Luego delega a `_search_center_in_province()`, que ejecuta una **cascada de 5
estrategias** (en orden, se detiene en la primera que devuelva resultado):

| # | Estrategia | Descripción |
|---|---|---|
| 1 | **Match exacto normalizado** | `_normalize_center_text()` en ambos lados. Compara strings exactos tras normalización (quitar acentos, mayúsculas, colapsar espacios) |
| 2 | **Contains (substring)** | Comprueba si el input es substring del centro o viceversa. Si hay varios candidatos, `_pick_best_by_localidad()` desambigua por localidad |
| 3 | **Locality-first** | Filtra centros por localidad (`_localidad_matches()`), luego fuzzy match solo dentro de ese subconjunto. Muy robusto contra OCR garbled |
| 4 | **Fuzzy global** | `process.extract()` con `token_set_ratio`, limit=10. Si hay localidad, usa `_pick_best_by_localidad()` para desambiguar entre candidatos de score similar (±15) |
| 5 | **Fallback final** | `process.extract()` con `fuzz.WRatio`, limit=10. Usa `_pick_best_by_combined_score()` (70% nombre + 30% localidad). Siempre devuelve algo |

El centro devuelto incluye metadatos internos `_match_strategy` y `_match_score` (no se serializan
al CRM). `_build_crm_record()` los usa para decidir `Centro` en `FieldsToReview`: `exact_normalized`
y `contains` se consideran suficientemente fiables; `locality_first`, `fuzzy_global` y
`fallback_wratio` se revisan si su score es menor que `CENTER_REVIEW_SCORE_THRESHOLD` (`80`).
Si no hay match y se usa `OtherCenter`, `Centro` se marca para revisión.

**Funciones auxiliares de la cascada**:

- `_normalize_center_text(text)`: Quita acentos (NFKD + combining), mayúsculas,
  reemplaza puntuación por espacios, colapsa espacios
- `_expand_ocr_abbreviations(text)`: Expande abreviaturas comunes (STA→SANTA,
  COL→COLEGIO, INST→INSTITUTO, etc.)
- `_extract_localidad_from_center_name(name)`: Extrae localidad del nombre del centro.
  Soporta `NOMBRE (LOCALIDAD)`, `NOMBRE (LOCALIDAD) (PROVINCIA)`, `NOMBRE - LOCALIDAD`
- `_normalize_center_text(text)`: Quita acentos (NFKD + combining), mayúsculas,
  reemplaza puntuación por espacios, colapsa espacios
- `_expand_ocr_abbreviations(text)`: Expande abreviaturas comunes:
  `STA→SANTA`, `COL→COLEGIO`, `INST→INSTITUTO`, `COLEGIO→COLEGIO` (NRA→NUESTRA,
  SNRA→SEÑORA, CEIP→COLEGIO, CPR→COLEGIO, UNIV→UNIVERSIDAD, UNI→UNIVERSIDAD, etc.)
- `_localidad_matches(user, centro)`: Normaliza + expande abreviaturas. Match si:
  iguales, substring mutuo, `fuzz.ratio ≥ 80`, `fuzz.token_set_ratio ≥ 85`, o
  `fuzz.WRatio ≥ 82`
- `_pick_best_by_localidad(candidates, localidad, search_term)`: Si hay localidad,
  filtra candidatos cuya localidad matchee. Si no hay localidad pero hay `search_term`,
  toma el candidato con mayor `token_set_ratio` contra el término de búsqueda (longitud
  como desempate inverso). Si no hay ni localidad ni term, devuelve el más largo.
  Si hay localidad pero ninguno matchea, devuelve `None` (ambiguo)
- `_score_center_candidate(name, search, localidad)`: Puntuación combinada
  `70% nombre + 30% localidad`. Sin localidad: 100% nombre (con penalización ×0.85
  si el centro no tiene localidad extraíble)
- `_find_province_key(provincia)`: Resuelve la provincia contra las claves cargadas.
  Usa un diccionario de ~50 alias explícitos que cubre siglas, bilingüismo, OCR y
  variantes históricas (ver sección 5.6.6). Fallback: prefijo ≥3 chars →
  `max(fuzz.ratio, fuzz.WRatio)` con umbral 60

#### 5.6.5 Alias de provincia y localidad

**`_find_province_key` — alias de provincia (~50 entradas)**

Cubre siglas, castellano/valenciano, variantes históricas y errores OCR frecuentes:

| Entrada del alumno | Provincia resuelta |
|---|---|
| `CV`, `C.V.`, `C V`, `Com. Valenciana` | VALENCIA |
| `Comunidad Valenciana`, `Comunitat Valenciana` | VALENCIA |
| `País Valenciano`, `Reino de Valencia` | VALENCIA |
| `Prov. de Valencia`, `Provincia de Valencia` | VALENCIA |
| `Alacant`, `Alacante`, `Aliante`, `Alic` | ALICANTE |
| `Elche`, `Elx` | ALICANTE |
| `Prov. de Alicante`, `Provincia Alicante` | ALICANTE |
| `Castelló`, `Castellón de la Plana`, `Castello` | CASTELLON |
| `Castellón Plana`, `Castelló Plana` | CASTELLON |
| `Castillón`, `Castelion`, `Costellan`, `Cast`, `Caste` | CASTELLON |
| `Prov. de Castellón`, `Provincia Castellón` | CASTELLON |

Si ningún alias coincide: prefijo ≥3 chars → `max(fuzz.ratio, fuzz.WRatio) ≥ 60` →
mejor match garantizado por debajo del umbral (fallback absoluto).

**`_normalize_localidad_input` — alias de localidad bilingüe**

Expande formas abreviadas y alternativas antes del fuzzy match:

| Entrada | Canónico CRM |
|---|---|
| `Castellon`, `Castelló`, `Castellon de la Plana`, `Castello Plana` | CASTELLÓN DE LA PLANA |
| `Alacant`, `Alacante` | ALICANTE |
| `Elche`, `Elx`, `Eltx` | ELCHE/ELX |
| `Xàtiva`, `Jàtiva`, `Xativa`, `Jativa`, `Jatiba` | XÀTIVA |
| `Xixona`, `Jijona` | XIXONA |
| `Vila Real`, `Vilareal`, `Villarreal`, `Villa Real` | VILA-REAL |
| `Borriana`, `Burriana` | BORRIANA |
| `Alcoi`, `Alcoy` | ALCOI/ALCOY |
| `Alzira`, `Alcira` | ALZIRA |
| `Gandía` | GANDÍA |

#### 5.6.6 `_build_crm_record(basic, center, degrees, course_id, datos)` — objeto CRM final

Ensambla el registro CRM a partir de los resultados de las tres funciones anteriores y
aplica los flags de revisión de dos fuentes:
- **GPT**: evaluó palabras de baja confianza OCR y determinó si afectan a campos críticos.
- **Código**: detecta correcciones OCR en teléfono (`telefono_had_corrections`), email
  con acentos (`email_had_accents`) e inconsistencias en titulación
  (`titulacion_needs_review`), independientemente del resultado GPT.

```python
{
    "Id": "",
    "DNI": "12345678A",                    # Normalizado con corrección OCR
    "Passport": "",
    "Firstname": "JUAN",
    "Middlename": "GARCÍA",                # Primer apellido
    "Lastname": "LÓPEZ",                   # Segundo apellido (puede ser vacío)
    "Mobilephone": "+34612345678",         # Con prefijo +34
    "Email": "juan@email.com",             # Espacios eliminados
    "IdStudentCurse": "...",               # ID del curso (fuzzy match)
    "OtherCenter": "...",                  # Solo si no se encontró en catálogo
    "ProvenanceCenterId": "...",           # ID del centro (si se encontró)
    "ProvenanceCenterName": "...",
    "ProvenanceCenterProvinceId": "...",
    "ProvenanceCenterCityId": "...",
    "ProvenanceCenterCountryId": "...",
    "AccessWay": "",
    "Session": 100000036,
    "Campaign": "852c6f0a-...",
    "RequestType": 100000000,
    "Owner": "57c8523f-...",
    "BulkEmail": true,
    "ReviewData": false,                   # GPT + post-procesado deciden revisión humana
    "IdStudy": "...",                      # Si hay una sola titulación resuelta
    # "Degrees": [{"IdStudy": "..."}, ...] # Alternativa si hay varias titulaciones
}
```

Se devuelve envuelto en un array: `[result_data]`.

---

## 6. Decisiones de diseño relevantes

### 6.1 ¿Por qué OCR + GPT en vez de solo GPT?

GPT es peor leyendo texto manuscrito directamente de una imagen que un OCR
especializado como Azure Document Intelligence. El OCR proporciona:
- Texto extraído con alta precisión (especialmente bueno en manuscrito)
- Checkboxes con estado y confianza numérica
- Coordenadas espaciales (polígonos) de cada elemento
- Detección de manuscrito vs impreso

GPT recibe el texto ya extraído como contexto, lo que le permite enfocarse en
**interpretar** la estructura del formulario en vez de en **leer** la imagen.

### 6.2 ¿Por qué `json_object` sin schema estricto?

Se usa `{"type": "json_object"}` en vez de un JSON Schema estricto porque con schema
estricto, si GPT no puede rellenar un campo opcional, rechaza la respuesta entera en
vez de devolver ese campo vacío.

### 6.3 ¿Por qué no hay umbral mínimo en la búsqueda de centros?

`analyze_center_optimized()` siempre devuelve un resultado (tiene fallback final).
La decisión de diseño es: es mejor devolver el centro más parecido (aunque sea
incorrecto) que dejar el campo vacío, porque al menos el operador del CRM puede
detectar un centro incorrecto y corregirlo, mientras que un campo vacío requiere
buscar la ficha original.

### 6.4 ¿Por qué se procesan solo los pares?

El escáner genera una imagen por cara de la ficha en orden secuencial. Las impares
son anversos y las pares son reversos. La función espera a tener la par (reverso) para
procesar el par completo (anverso + reverso juntos).

### 6.5 Rotación de imágenes

Las imágenes llegan giradas por la orientación de carga en el escáner. Se aplica
rotación fija:
- Impar: 90° horario
- Par: 270° horario

Esto asume que las fichas se colocan siempre en la misma orientación en la bandeja.

### 6.6 Selección de variante provincial de titulaciones

Cuando una titulación tiene variantes por sede (Enfermería, Enfermería Castellón,
Enfermería Elche), se selecciona la más apropiada según la sede asociada a la
provincia del alumno. El mapeo explícito actual es: `ALICANTE/ALACANT → ELCHE`,
`CASTELLON/CASTELLÓ → CASTELLÓN`, `MURCIA → ELCHE`; cualquier otra provincia
activa sin variante propia cae en `VALENCIA`.

---

## 7. Estructura del proyecto

```
fichas_si_papel/
├── host.json                    # Config Azure Functions v2
├── local.settings.json          # Variables de entorno locales (no en git)
├── requirements.txt             # Dependencias Python
│
├── procesa_ficha/               # Azure Function principal
│   ├── __init__.py              # Toda la lógica (~3000 líneas)
│   └── function.json            # Binding HTTP trigger
│
├── centros/                     # Catálogos de centros educativos
│   ├── VALENCIA.txt
│   ├── ALICANTE.txt
│   ├── CASTELLON.txt
│   ├── ALBACETE.txt
│   ├── BALEARES.txt
│   ├── CUENCA.txt
│   ├── MURCIA.txt
│   ├── TERUEL.txt
│   ├── centrosPendientesMatching.txt
│   ├── centrosSinProcesar.txt   # Centros pendientes de procesar
│   ├── centrosDescartados.txt   # Centros descartados
│   ├── centrosTablasCRM/        # XLSX RAW del CRM
│   └── centrosCompletosAntiguos/  # Respaldo de centros con IdCity
│
├── titulacion/
│   └── titulaciones.txt         # ~100+ titulaciones con IDs CRM
│
├── curso/
│   └── cursos.txt               # 5 cursos con IDs CRM
│
├── localidades/                 # Localidades JSON por provincia
│   ├── VALENCIA.json
│   ├── ALICANTE.json
│   ├── CASTELLON.json
│   ├── ALBACETE.json
│   ├── BALEARES.json
│   ├── CUENCA.json
│   ├── MURCIA.json
│   ├── TERUEL.json
│   └── provinceIds.json
│
├── scripts/                     # Scripts auxiliares
│   ├── fetch_localidades.py     # Obtener localidades del CRM
│   ├── procesar_centros_raw.py  # Procesar centros en bruto
│   ├── test_dni_phone.py        # 45 tests de normalización DNI/NIE, teléfono y flags básicos
│   ├── test_course_matching.py  # Matching de cursos y umbral de fallback
│   ├── test_centros.py          # matching de centros, provincias y localidades
│   ├── test_review_flags.py     # flags de revisión Email/Centro/Nombre
│   └── test_misspell_cases.py   # Tests de errores ortográficos
│
└── docs/
    ├── CLAUDE.md                # Contexto para Claude Code
    ├── tecnico/
    │   ├── DOCUMENTACION_TECNICA.md  # Este documento
    │   ├── EXTRACTION_FLOW.md        # Diagrama del pipeline
    │   └── pipeline_fichas.png
    ├── usuario/
    │   ├── GUIA_MARKETING.md         # Guía no técnica para el personal
    │   ├── GUIA_MARKETING.pdf
    │   ├── Manual_Escaneo_Fichas.pdf
    │   └── img/
    └── build/                   # Fuentes LaTeX y artefactos
```

---

## 8. Tests

### `scripts/test_dni_phone.py`

**45 tests** para la normalización de DNI/NIE, teléfono y flags básicos:

```bash
conda run -n fichas --no-capture-output python -m scripts.test_dni_phone
```

| Suite | Escenarios |
|---|---|
| **A — DNI/NIE** (20 tests) | Válido, vacío/None, espacios/puntos/guiones, minúsculas→mayúsculas, letra de control alfabética inválida conservada para revisión, dígito en lugar de letra de control, OCR: I→1 / O→0 / S→5 / B→8 / Z→2 en cuerpo, NIE X/Y/Z válidos, NIE minúsculas, NIE letra control errónea conservada, NIE OCR en cuerpo |
| **B — Teléfono** (22 tests) | Móvil 6xx/7xx, fijo 9xx, vacío, demasiado corto, espacios/guiones/paréntesis/puntos, +34 preservado, +34 con espacios, +33 francés, +39 italiano, +34 con OCR, OCR O/I/S/B/A/múltiples, letras sin mapeo, +34 muy corto, solo ceros |

### `scripts/test_centros.py`

Tests organizados en varias suites que verifican el fuzzy matching de centros,
la resolución de provincias y los casos de OCR:

```bash
# Activar entorno conda y ejecutar
conda run -n fichas --no-capture-output python -m scripts.test_centros
```

#### Suite A — Tests básicos de centros (14 tests)

| Test | Escenario |
|---|---|
| 1 | Centro con localidad exacta (Ausiàs March + Picassent) |
| 2 | Mismo nombre en dos provincias (9 D'OCTUBRE en Valencia vs Alicante) |
| 3 | Centro con localidad (Antonio Machado + Elda) |
| 4-6 | Centros sin localidad (fuzzy puro) |
| 7 | Variante ortográfica de localidad (Alcasser sin tilde) |
| 8 | Nombre con tilde incorrecta (Blasco Ibañez) |
| 9-10 | Mismo centro en diferentes provincias |
| 11 | Localidad que no coincide con ningún candidato |
| 12 | Centro completamente inexistente (fallback fuzzy) |
| 13 | Nombre parcial ("Blasco") |
| 14 | Provincia sin tilde ("Castellon") |

#### Suite B — Casos de localidad normalizada (12 tests)

Incluye: `Alcasser` sin tilde, `Albal` exacto, `Aldaia` exacto, variantes con
localidad incorrecta, nombres parciales con localidad, nombres con abreviaturas
OCR (`STA. BARBARA → SANTA BÁRBARA`), etc.

#### Suite F — Casos de OCR combinado (11 tests)

| Test | Escenario |
|---|---|
| F1 | `Materdei` (OCR sin espacio) → DIOCESANO MATER DEI |
| F2 | `AusiàsMarch` (fusión) → IES AUSIÀS MARCH |
| F3 | `AntonioMachado` (fusión) → IES ANTONIO MACHADO |
| F4 | `BotanicCalduch` → BOTÀNIC CALDUCH |
| F5 | `Materd3i` (OCR dígito) → DIOCESANO MATER DEI |
| F6 | `Di0cesano Mater` (OCR dígito) → DIOCESANO MATER DEI |
| F7 | `Bl4sco Ib4ñez` (OCR dígitos) → BLASCO IBÁÑEZ |
| F8 | Provincia `C. Valenciana` → VALENCIA |
| F9 | Provincia `Comunitat Valenciana` → VALENCIA |
| F10 | Provincia `Alacant` → ALICANTE |
| F11 | `Ausiàs March` sin localidad → IES (no universidad) |

#### Suite G — Alias de provincia completos (16 tests)

| Tests | Escenario |
|---|---|
| G1-G4 | Siglas/abreviaturas: `CV`, `C. Valenciana`, `País Valenciano`, `Prov. de Valencia` |
| G5-G6 | Valenciano: `Alacant`, `Castelló` |
| G7-G8 | OCR typo: `Alacante`, `Castillón` |
| G9-G12 | OCR severo: `Val3ncia`, `Alicamt3`, `Castel1on`, `Kostellon`, `Alic4nte` |
| G13-G16 | Localidad bilingüe: `Alacant`, `Alicamt` (OCR), `Castellón de la Plana` como provincia |

### `scripts/test_review_flags.py`

Verifica los falsos positivos corregidos en `FieldsToReview`: Email no hereda
Nombre/Apellidos, Email valido con confianza MEDIUM no se revisa, Centro exact/contains
no se revisa por MEDIUM OCR, y los casos de estructura invalida o score bajo siguen
marcados.

```bash
conda run -n fichas --no-capture-output python -m scripts.test_review_flags
```

---

## 9. Logging

Todas las funciones usan logging extensivo con prefijos por componente:

| Prefijo | Módulo |
|---|---|
| `[DI_EXTRACT_SUMMARY img=N]` | Resumen completo por imagen: texto, confianza de palabras, marcas aceptadas/rechazadas, tabla KVP |
| `[OCR_STRUCTURED]` | Azure Document Intelligence — resultado bruto, selection marks, estadísticas |
| `[KEY_VALUE_PAIRS]` | Pares etiqueta→valor del anverso: sección enviada a GPT y mapeo canónico diagnóstico |
| `[QR_BARCODE]` | Contenido de QR/barcodes del reverso (actualmente desactivado) |
| `[CHECKBOX_SUMMARY]` | Lista de checkboxes marcados enviada a GPT (fuente única: `:selected:` + confianza) |
| `[WORD_CONFIDENCE]` | Estadísticas de confianza por palabra del documento |
| `[GPT_ANALYZE]` | Pipeline GPT: input, output, campos extraídos, timing |
| `[GPT_INPUT_DEBUG]` | Texto OCR exacto enviado a GPT |
| `[PARSE_RESPONSE]` | Parseo del JSON de GPT |
| `[DNI_NORMALIZE]` | Normalización de DNI/NIE |
| `[PHONE_NORMALIZE]` | Normalización de teléfono |
| `[EMAIL_NORMALIZE]` | Normalización de email |
| `[TITULACION_MATCH]` | Fuzzy matching de titulaciones |
| `[VARIANT_SELECTION]` | Selección de variante provincial |
| `[MAP_DEGREES]` | Mapeo de titulaciones marcadas |
| `[CURSO_MATCH]` | Fuzzy matching de curso |
| `[LOCALIDAD_NORM]` | Normalización de localidad |
| `[CENTER_MATCH]` | Búsqueda de centro (todas las estrategias) |
| `[CENTER_SEARCH]` | Orquestación de búsqueda de centro |
| `[PROVINCE]` | Resolución de provincia |
| `[EXTRAER_DATOS]` | Transformación final a CRM |
| `[PROCESS_PAIR]` | Procesamiento del par de imágenes |
| `[MAIN]` | Punto de entrada |

---

## 10. Limitaciones conocidas

1. **Solo 2 caras**: El sistema asume exactamente 2 imágenes por ficha (anverso +
   reverso). No soporta formularios de 1 o 3+ páginas.

2. **Cobertura de centros acotada**: El matching automático de centros cubre las
   provincias con catálogo activo en `centros/` y `localidades/`: Valencia, Alicante,
   Castellón, Albacete, Baleares, Cuenca, Murcia y Teruel. Una provincia no catalogada
   puede producir matches incorrectos.

3. **Catálogos estáticos**: Titulaciones, centros, cursos y localidades se leen de
   archivos de texto/JSON. Si cambian en el CRM, hay que regenerar los archivos y
   redesplegar.

4. **Sin cola de reintentos**: Si falla el procesamiento (error de OCR, timeout de GPT,
   etc.), no hay reintento automático ni dead-letter queue.

5. **Sin schema estricto en GPT**: La respuesta de GPT no se valida contra un schema.
   Si GPT devuelve campos con nombres distintos (ej: `centro_origen` vs `centro`), se
   manejan con alias manuales, pero podrían surgir nuevos alias no contemplados.

6. **Singleton no thread-safe**: `GlobalDataManager` usa atributos de clase. En el
   contexto de Azure Functions (una invocación por proceso), funciona correctamente.

7. **Rotación fija**: La rotación de 90°/270° asume orientación específica en la
   bandeja del escáner. Si se cambia la forma de colocar las fichas, las imágenes
   quedan giradas y el OCR falla.

8. **Fuzzy matching sin umbral en centros**: El fallback siempre devuelve algo,
   incluso si el score es muy bajo. Un centro completamente inventado devolverá el
   centro más parecido fonéticamente, que puede ser totalmente incorrecto.

---

*Documentación generada a partir del código fuente (`procesa_ficha/__init__.py`,
~3000 líneas). Última verificación contra código: abril 2026.*
