# CLAUDE.md

This file provides guidance to AI coding assistants when working with code in this repository.

---

## ¿Qué hace este proyecto?

Azure Function (Python) que automatiza la inserción de datos en el CRM de una institución académica a partir de fichas de inscripción manuscritas escaneadas.

**Flujo completo:**
```
Escáner → Azure Blob Storage → Azure Function
    ├─ OCR estructurado (Azure Document Intelligence)
    ├─ Detección de checkboxes marcados por flujo ':selected:' (confianza >= 80%)
    ├─ Extracción de campos con GPT (Responses API)
    └─ Normalización + fuzzy matching → JSON CRM
```

Power Automate llama a esta función vía HTTP POST cuando el escáner deposita un par de imágenes JPEG en el blob container.

---

## Entorno de desarrollo

- **Python**: 3.11
- **Variables de entorno locales**: `local.settings.json` (no subir a git, ver `.example`)

### Ejecutar tests

```bash
# PYTHONPATH necesario para que los scripts encuentren procesa_ficha/
export PYTHONPATH="/ruta/al/repo"

# Suite de centros — la principal
python -m scripts.test_centros

# Suite DNI/teléfono (45 tests)
python -m scripts.test_dni_phone

# Flags de revisión
python -m scripts.test_review_flags

# Test normalización DNI/NIE
python -m scripts.test_dni_normalize

# Test errores ortográficos OCR
python -m scripts.test_misspell_cases

# Test confianza de palabras OCR
python -m scripts.test_word_confidence
```

---

## Zona geográfica cubierta

El matching automático de centros cubre las provincias con catálogo activo en `centros/` y `localidades/`: Valencia, Alicante, Castellón, Albacete, Baleares, Cuenca, Murcia y Teruel.
Las titulaciones tienen variantes para Valencia, Castellón y Elche: Murcia cae en Elche/Alicante; el resto de provincias nuevas caen en Valencia.

---

## Secciones de `procesa_ficha/__init__.py` (orden)

Toda la lógica de la función vive en este único fichero (~3000 líneas):

1. **Configuración y constantes** — umbrales, keys de env, rutas, CRM defaults
2. **Logging y clientes** — singleton `AzureOpenAI`
3. **GlobalDataManager** — singleton que cachea titulaciones, cursos, centros y localidades al arrancar
4. **Normalización de texto y datos** — DNI, teléfono, email, centros, localidades
5. **Utilidades de imagen** — rotación con Pillow
6. **Utilidades auxiliares** — polígonos, API HTTP
7. **OCR con Azure Document Intelligence** — `perform_ocr_structured`, correlación checkboxes
8. **Extracción de curso del OCR** — `extract_course_from_text`
9. **Mapeo de titulaciones y cursos (fuzzy)** — `analyze_titulacion_local`, `map_checked_degrees`, `analyze_curso_local`
10. **Mapeo de centros educativos** — cascada de 5 estrategias
11. **Procesamiento con GPT** — prompt, mensajes, llamada API, parseo respuesta
12. **Extracción y transformación a CRM** — `_extract_basic_fields`, `_extract_degrees_fields`, `_extract_center_fields`, `_build_crm_record`, `extraer_datos` (orquestador)
13. **Procesamiento de imágenes y función principal** — `main`, `_process_image_pair`

---

## API HTTP

**Request POST:**
```json
{ "nombre_imagen": "scan_fichas_2025-11-28_22.jpeg", "prompt": "Extrae todos los datos" }
```

| Código | Significado |
|--------|-------------|
| `200`  | Éxito — array con los datos procesados |
| `202`  | Esperando par — imagen impar recibida, esperando reverso |
| `400`  | Error de validación |
| `500`  | Error interno |

---

## Pipeline interno

```
main() → _process_image_pair()
    ├── _parse_image_number()             Extrae nº; si impar → HTTP 202
    ├── _download_blob_pair()             Descarga + rota ambas imágenes
    ├── analyze_images_with_gpt()
    │   ├── perform_ocr_structured() × 2  Document Intelligence v4.0 (texto + checkboxes + key_value_pairs)
    │   ├── _log_di_extract_summary()      [DI_EXTRACT_SUMMARY img=N] por imagen
    │   ├── _compute_word_confidence_stats()
    │   ├── _extract_selected_from_ocr()            ':selected:' antes del nombre, filtrado por confianza >= 80%
    │   ├── _extract_from_key_value_pairs() Mapeo canónico KVP (diagnóstico)
    │   ├── _build_messages_content() + _build_system_prompt()
    │   └── Responses API → GPT
    └── extraer_datos()                   Orquestador (~10 líneas)
        ├── _extract_basic_fields()        DNI, nombre, teléfono, email
        ├── _extract_degrees_fields()      Titulaciones checkbox + manuscrita
        ├── _extract_center_fields()       Localidad + centro → JSON CRM
        └── _build_crm_record()           Ensamblaje CRM + flags de revisión
```

**Rotación de imágenes:**
Impar (anverso) → 90° horario · Par (reverso) → 270° horario (orientación fija del escáner)

---

## Campos del JSON de respuesta

| Campo | Notas |
|-------|-------|
| `DNI` | Corrige OCR en posiciones numéricas. Si la letra final alfabética no cuadra con módulo 23, la conserva y marca revisión. Si falta letra o es dígito, la calcula y marca revisión. |
| `Firstname` | `datos["nombre"]` limpiado |
| `Middlename` / `Lastname` | Split de `datos["apellidos"]`: primera palabra / resto |
| `Mobilephone` | Corrige letras OCR; añade `+34` si falta |
| `Email` | Elimina espacios internos; valida estructura para flags, no existencia real del dominio |
| `IdStudentCurse` | `analyze_curso_local()`, fuzzy `token_set_ratio` umbral 60 |
| `ProvenanceCenter*` | `analyze_center_optimized()`, cascada de 5 estrategias |
| `OtherCenter` | Solo si no hay match en catálogo CRM |
| `IdStudy` / `Degrees[].IdStudy` | `map_checked_degrees()`, fuzzy `token_set_ratio` umbral 70. Campo dinámico: una titulación → `IdStudy`; varias → `Degrees[]`. |
| `ReviewData` / `FieldsToReview` | `true` si campo crítico requiere revisión. Email solo con evidencia propia (estructura inválida, acentos, palabra HIGH, dominio institucional/de centro). Centro depende de estrategia/score CRM. Nombre/Apellidos solo se propagan entre sí si el KVP complementario no es de alta confianza. |

---

## Umbrales clave

| Parámetro | Valor | Notas |
|---|---|---|
| `TITULACION_MATCH_THRESHOLD` | 70 | fuzzy titulaciones |
| `PROVINCE_MATCH_THRESHOLD` | 60 | fuzzy provincia (siempre devuelve algo) |
| `COURSE_MATCH_THRESHOLD` | 60 | fuzzy cursos |
| `SELECTION_MARK_CONFIDENCE_THRESHOLD` | 0.80 | checkboxes OCR — filtra indicadores de campus pre-impresos (confianza ~0.45–0.77) |
| `WORD_CONFIDENCE_THRESHOLD` | 0.80 | palabras OCR enviadas como incertidumbre a GPT |
| `WORD_CONFIDENCE_HIGH_CUTOFF` | 0.40 | separa tier HIGH de MEDIUM para flags de revisión |
| `CENTER_REVIEW_SCORE_THRESHOLD` | 80 | score mínimo para no revisar centros locality/fuzzy/fallback |
| `GPT_MAX_OUTPUT_TOKENS` | 32000 | límite de tokens en la respuesta GPT |
| Localidad normalization | ≥ 80 | 3 scorers: ratio + token_set + WRatio |
| `_localidad_matches` ratio | ≥ 80 | comparación localidad usuario vs centro |
| `_localidad_matches` token_set | ≥ 85 | — |
| `_localidad_matches` WRatio | ≥ 82 | — |

---

## Sistema de matching de centros — cascada de 5 estrategias

En `_search_center_in_province`:
1. Match exacto normalizado (sin acentos, mayúsculas, espacios colapsados)
2. Substring/contains — desambigua por localidad y score fuzzy
3. Locality-first — filtra por localidad; **WRatio** dentro del subconjunto (cubre palabras fusionadas: "Materdei" → "MATER DEI")
4. Fuzzy global `token_set_ratio` — candidatos ±15 pts, desambiguados por localidad
5. Fallback `WRatio` — siempre devuelve algo (70% nombre + 30% localidad)

### Province resolution (`_find_province_key`)
- ~50 aliases: castellano + valenciano + siglas + variantes OCR con dígitos
- Prefijo ≥3 chars → fuzzy `max(ratio, WRatio)`, umbral 60
- Cobertura: "CV", "Alacant", "Castelló", "País Valenciano", "Prov. de Valencia", "Kostellon", "Val3ncia"…

### Locality aliases (`_normalize_localidad_input`)
Pares bilingüe explícitos: Xàtiva/Játiva, Borriana/Burriana, Vila-real/Villarreal, Alcoi/Alcoy, Alzira/Alcira, Elche/Elx, Alicante/Alacant, Castellón/Castelló de la Plana.

---

## Titulaciones y checkboxes

### Detección de checkboxes marcados

**`_extract_selected_from_ocr(ocr_text, selection_marks)`:** fuente única de detección. Usa dos
datos del resultado de Azure Document Intelligence combinados:

1. El texto OCR embebe `:selected:` en el flujo de texto. Una titulación está marcada si el token
   inmediatamente anterior es `:selected:` (misma línea, o `:selected:` en la línea anterior y el
   nombre en la siguiente).
2. El i-ésimo `:selected:` en el texto se corresponde 1:1 con la i-ésima marca `state=="selected"`
   en el resultado bruto de DI. Solo se incluye si `confidence >= SELECTION_MARK_CONFIDENCE_THRESHOLD`
   (0.80). Los indicadores de campus pre-impresos tienen confianza ~0.45–0.77 y son descartados automáticamente.
3. Cuando el alumno escribe 'X' o tick: `:selected: X Titulación` → el prefijo X/tick se elimina
   y se extrae el nombre. Si 'X' está sola en una línea, se toma la línea siguiente como nombre.

**`_build_checkbox_summary()`**: extrae las marcas brutas de `raw_document_intelligence`, llama a
`_extract_selected_from_ocr()` y produce un bloque `CHECKBOXES MARCADOS` limpio para incluir en el
mensaje a GPT.

GPT recibe esta lista como base e instruye a incluir ante la duda (falso positivo preferible a falso
negativo). No recibe imágenes; solo texto OCR, KVP y resúmenes de checkboxes.

- `map_checked_degrees()` descarta etiquetas de sede con `_is_non_degree_checkbox_label()` (compara contra `GlobalDataManager.all_localidades_normalized`)
- Si hay variantes por provincia, `_select_best_variant_by_province()` aplica sede por provincia y después fallback.
- Mapeo: `ALICANTE → ELCHE`, `CASTELLON → CASTELLÓN`, `MURCIA → ELCHE`, resto de provincias nuevas → `VALENCIA`.
- La `titulacion_manuscrita` se coloca siempre como preferida. La salida es dinámica: una titulación usa `IdStudy`; dos o más usan `Degrees[]`.

---

## Formato de catálogos

```
# titulacion/titulaciones.txt
NOMBRE TITULACIÓN, IdTitulacion
NOMBRE TITULACIÓN (CASTELLÓN), IdTitulacion
NOMBRE TITULACIÓN (ELCHE), IdTitulacion
                              ← línea en blanco = separador de checkbox

# centros/PROVINCIA.txt
NOMBRE CENTRO (LOCALIDAD), Id, IdProvince, IdCity, IdCountry

# localidades/PROVINCIA.json
[{ "Id": "...", "Name": "CASTELLÓN DE LA PLANA", "Name_cat": "CASTELLÓ DE LA PLANA" }]
```

> **Nota:** Los IDs en los catálogos de este repositorio son **ficticios** (anonimizados). Deben sustituirse por los IDs reales del CRM de la institución objetivo. `scripts/procesar_centros_raw.py` lee `centros/centrosTablasCRM/*.xlsx`, preserva variantes manuales ya existentes aunque compartan `Id`, añade solo IDs nuevos y deja los casos sin `IdCity` en `centros/centrosPendientesMatching.txt`.

---

## Etiquetas de log para debugging

| Etiqueta | Qué traza |
|----------|-----------|
| `[DI_EXTRACT_SUMMARY img=N]` | Resumen por imagen: texto, confianza palabras, marcas aceptadas/rechazadas, tabla KVP |
| `[OCR_STRUCTURED]` | Resultado OCR, selection marks, estadísticas de confianza |
| `[KEY_VALUE_PAIRS]` | Pares etiqueta→valor del anverso enviados a GPT y mapeo canónico (diagnóstico) |
| `[QR_BARCODE]` | Contenido de QR/barcodes (actualmente desactivado, `DI_ADDON_BARCODES=False`) |
| `[CENTER_SEARCH]` | Estrategia usada, candidatos y score en cada paso |
| `[CENTER_MATCH]` | Centro seleccionado y puntuación final |
| `[CHECKBOX_SUMMARY]` | Lista final de checkboxes marcados enviada a GPT (fuente: `:selected:` + confianza) |
| `[GPT_ANALYZE]` | Pipeline GPT completo: input, output, campos extraídos, timing |
| `[GPT_INPUT_DEBUG]` | Texto OCR exacto enviado a GPT |
| `[TITULACION_MATCH]` | Matches con scores |
| `[VARIANT_SELECTION]` | Selección de variante provincial |
| `[LOCALIDAD_NORM]` | Normalización: input → output con score |
| `[PROVINCE]` | Resolución de provincia: alias, prefijo o fuzzy |
| `[DNI_NORMALIZE]` / `[PHONE_NORMALIZE]` | Correcciones OCR |
| `[WORD_CONFIDENCE]` | Estadísticas de confianza por palabra |
| `[EXTRAER_DATOS]` | Resumen completo de la transformación CRM |

---

## Puntos de edición frecuentes

Localizaciones exactas en `procesa_ficha/__init__.py`:

| Qué editar | Función | Línea aprox. |
|---|---|---|
| Umbrales de matching y GPT | Bloque `CONFIGURACIÓN Y CONSTANTES` | ~24 |
| Correcciones OCR letra→dígito | Dict `OCR_LETTER_TO_DIGIT` | ~46 |
| Regex de coordenadas de polígono | Constante `_POLYGON_COORD_RE` | ~66 |
| Abreviaturas OCR de centros (`INST→INSTITUTO`, `STA→SANTA`…) | `_expand_ocr_abbreviations()` | ~1182 |
| Aliases de localidad (`Burriana→BORRIANA`, `Campolivar→GODELLA`…) | `localidad_aliases` dict dentro de `_normalize_localidad_input()` | ~1211 |
| Aliases de provincia (`CV→VALENCIA`, `Alacant→ALICANTE`…) | `community_aliases` dict dentro de `_find_province_key()` | ~1333 |
| Regla detección checkboxes primaria | `_extract_selected_from_ocr()` | ~912 |
| Prompt del sistema GPT (instrucciones de campo) | `_build_system_prompt()` | ~1760 |
| Normalización DNI, teléfono, email | `_extract_basic_fields()` | ~2153 |
| Mapeo titulaciones checkbox + manuscrita | `_extract_degrees_fields()` | ~2213 |
| Resolución de centro de procedencia | `_extract_center_fields()` | ~2297 |
| Ensamblaje registro CRM + flags de revisión | `_build_crm_record()` | ~2318 |

Regla al editar aliases: **el dict usa texto ya normalizado** (sin acentos, mayúsculas). Las claves se comparan contra `_normalize_center_text(input)`. Los valores son la forma canónica oficial (con acentos y tildes).

---

## Cómo añadir tests

Los tests **no usan pytest**. Hay dos patrones según el script:

### `test_centros.py` — patrón `ok()`

```python
# Dentro de una función run_*():
print("=== Descripción del caso ===")
r = analyze_center_optimized("Provincia", "Localidad", "NombreCentro")
got = r.get("Name", "") if r else ""
passed = "FRAGMENTO_ESPERADO" in got.upper()
results.append(("Descripción corta", passed))
print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")
```

### `test_dni_phone.py` — patrón `check()`

```python
check(results, "Descripción del caso",
      _normalize_dni_nie("INPUT"), "EXPECTED_OUTPUT")
```

### Añadir una nueva suite

1. Crear función `run_nueva_suite_tests() -> bool` con su bloque `results = []` y `return passed_count == total`
2. Añadir al bloque `if __name__ == "__main__":`:
   ```python
   print("\n" + "=" * 65)
   print("SUITE X: DESCRIPCIÓN")
   print("=" * 65)
   ok_nueva = run_nueva_suite_tests()
   ```
3. Incluirla en la condición final: `overall = ok1 and ok2 and ... and ok_nueva`

---

## Estilo de programación

### Comentarios

**Docstring al inicio, sin comentarios en el cuerpo.** Cada función arranca con `"""..."""` que describe qué hace, sus reglas clave y precondiciones relevantes. El cuerpo de la función no tiene comentarios intercalados entre las líneas de código.

```python
# Correcto
def _pick_best_by_localidad(candidates, localidad, search_term=""):
    """
    De entre varios candidatos, elige el que coincide con la localidad del usuario.
    Si localidad está vacía, elige por mayor similitud fuzzy con search_term.
    Si hay localidad pero ningún candidato coincide, devuelve None (ambiguo).
    """
    if not candidates:
        return None
    if not localidad:
        return max(candidates, key=lambda n: fuzz.token_set_ratio(search_term, n))
    ...

# Incorrecto
def _pick_best_by_localidad(candidates, localidad, search_term=""):
    # Si no hay candidatos salimos
    if not candidates:
        return None
    # Caso sin localidad: usamos fuzzy
    if not localidad:
        return max(candidates, key=lambda n: fuzz.token_set_ratio(search_term, n))
```

**Excepción — dicts de alias:** los alias dicts (`localidad_aliases`, `community_aliases`) llevan comentarios de línea `# razón` porque documentan el dato, no el código:

```python
"ALACANT":  "ALICANTE",   # nombre valenciano oficial
"ALIANTE":  "ALICANTE",   # OCR pierde la 'c'
"ALIC":     "ALICANTE",   # abreviatura (4 chars → prefijo falla)
```

**Excepción — lógica genuinamente no obvia:** un comentario breve está justificado cuando el código resuelve un caso borde cuyo porqué no es deducible de leerlo. Debe ser una sola línea, no una narración.

### Estructura de `__init__.py`

Las secciones principales van delimitadas con:
```python
# ============================================================================
# NOMBRE DE SECCIÓN
# ============================================================================
```

### Convenciones

**Commits**: prefijo convencional obligatorio — `fix:` para correcciones, `feat:` para funcionalidad nueva.

**Logging**: todo código de matching o normalización nuevo debe emitir `logging.info(f"[TAG] ...")` usando la etiqueta del módulo correspondiente (ver tabla de log tags). Sin logging el comportamiento es opaco en producción.

---

## Decisiones de diseño importantes

- **GPT no recibe imágenes**, solo texto OCR + resumen de checkboxes. Reduce coste/latencia; Document Intelligence ya extrae el texto con alta precisión.
- **Sin schema estricto en GPT** (`json_object` en vez de JSON Schema): evita rechazos por campos opcionales vacíos.
- **El fallback de centros siempre devuelve algo**: preferible a vacío (el operador CRM puede corregir un match incorrecto, pero no puede recuperar un campo vacío).
- **Catálogos estáticos**: regenerar con `fetch_localidades.py` y `procesar_centros_raw.py` si el CRM cambia.
- **GlobalDataManager es singleton** con atributos de clase. Thread-safe en Azure Functions (una invocación por proceso).
- **GPT reasoning effort**: `"high"` en la llamada Responses API actual.

---

## Flujo de reporte de errores

Cuando se reporta un error de producción:

1. **Identificar el JSON**: si `SAVE_DEBUG_SNAPSHOT=true`, los archivos se guardan en `debug/` o `DEBUG_DIR` con timestamp. Contienen OCR/GPT/CRM y datos personales; no se versionan.
2. **Filtrar por etiqueta**: usar `[CENTER_SEARCH]`, `[TITULACION_MATCH]` o `[PHONE_NORMALIZE]` según el campo en `FieldsToReview`.
3. **Reproducir localmente**: el JSON tiene el OCR text completo. Copiar y ejecutar la función afectada directamente.
4. **Corrección mínima**: alias dict si es un caso OCR conocido; nuevo test si es un caso nuevo; umbral solo como último recurso.
5. **Verificar con suite completa** antes de commitear.

---

## Notas de mantenimiento

- Si se añaden titulaciones/centros al CRM: regenerar archivos en `centros/` y `titulacion/` y redesplegar.
- Si cambia la orientación del escáner: ajustar rotaciones en `_download_blob_pair`.
- Ante errores repetidos en producción: filtrar logs por `[CENTER_SEARCH]` y `[CENTER_MATCH]`.
- Documentación técnica extendida en `docs/tecnico/DOCUMENTACION_TECNICA.md` y `docs/tecnico/EXTRACTION_FLOW.md`.
