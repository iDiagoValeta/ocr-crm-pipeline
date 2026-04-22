# fichas_si_papel — Pipeline de Extracción y Transformación

> [!NOTE]
> Este documento describe el pipeline completo: desde la petición HTTP hasta el objeto `result_data`
> enviado al CRM. Cubre OCR estructurado, correlación de checkboxes, extracción por LLM y
> normalización de campos.

---

## 🗺️ Diagrama del flujo

![Pipeline fichas_si_papel](pipeline_fichas.png)

---

## 📋 Resumen general

El sistema procesa pares de imágenes (anverso/reverso) descargadas desde Azure Blob Storage,
aplica OCR estructurado (Azure Document Intelligence), correlaciona checkboxes con texto,
envía contexto a la Responses API (LLM) y transforma/normaliza los campos para construir
`result_data` destinado al CRM.

---

## 🔢 Flujo paso a paso

1. **Entrada** — Petición HTTP con `nombre_imagen` y `prompt`.
2. **`_process_image_pair`** — Extrae el número de imagen; solo procesa pares. Descarga dos blobs y los rota.
3. **`perform_ocr_structured`** — Devuelve `text`, `selection_marks`, `all_selection_marks_unfiltered`, `tables`, `pages`, `has_handwritten`, `key_value_pairs` (si `DI_ADDON_KEY_VALUE_PAIRS=True`). Filtra checkboxes por `SELECTION_MARK_CONFIDENCE_THRESHOLD = 0.80`. Al terminar, emite bloque `[DI_EXTRACT_SUMMARY img=N]` con estadísticas completas.
4. **`_build_checkbox_summary`** — Extrae los marcadores `:selected:` embebidos en el texto OCR y los filtra por confianza `>= SELECTION_MARK_CONFIDENCE_THRESHOLD (0.80)`. El i-ésimo `:selected:` en el texto se corresponde 1:1 con la i-ésima marca `selected` en `all_selection_marks_unfiltered`. Genera el resumen que se pasa al LLM.
5. **`analyze_images_with_gpt`** — Envía OCR limpio + checkboxes + KEY-VALUE PAIRS (anverso) + `system_prompt`. GPT recibe solo texto. Espera JSON; si falla, intenta parsear del texto libre.
6. **`extraer_datos`** — Construye `result_data` con campos normalizados y mapeados al CRM.

---

## 📦 Campos de `result_data`

| Campo | Fuente LLM | Función de normalización | Notas |
|---|---|---|---|
| `DNI` | `datos["dni"]` | `_normalize_dni_nie()` | Corrige OCR en posiciones numéricas; conserva letras finales alfabéticas inválidas y marca revisión; calcula letra si falta o es dígito |
| `Description` | constante | `_build_crm_record()` | `Solicitud de información procedente de escaneo automático` |
| `Firstname` | `datos["nombre"]` | `clean_text()` | — |
| `Middlename` | `datos["apellido"]` | Split por espacio → primera palabra | — |
| `Lastname` | `datos["apellido"]` | Split por espacio → resto | — |
| `Mobilephone` | `datos["telefono"]` | `_normalize_phone()` | Corrige letras OCR, añade `+34` si procede |
| `Email` | `datos["email"]` | `_normalize_email()` | Elimina espacios internos; valida estructura para flags |
| `IdStudentCurse` | `datos["curso"]` | `analyze_curso_local()` | Fuzzy `token_set_ratio` vs `cursos.txt`, umbral 60 |
| `OtherCenter` | `nombre_centro` | Texto literal | Solo si **no** se encontró centro en CRM |
| `ProvenanceCenter*` | provincia + localidad + nombre_centro | `analyze_center_optimized()` | Ver sección de centros |
| `IdStudy` / `Degrees` | checkboxes + `titulacion_manuscrita` | `map_checked_degrees()` → `analyze_titulacion_local()` | Dinámico: una titulación → `IdStudy`; varias → `Degrees[]` |

Nota: Si el valor corresponde a un NIE (empieza por `X`, `Y` o `Z`), la letra inicial se preserva y no se sustituirá usando los mapeos de OCR; solo se corrigen caracteres en las posiciones numéricas intermedias.

---

## 🏫 Búsqueda de provincia / localidad / centro

### `_find_province_key(provincia)`

- Normaliza con `_normalize_center_text()` (sin acentos, mayúsculas).
- Comprueba `community_aliases` (~50 entradas): siglas (`CV`), castellano/valenciano
  (`ALACANT→ALICANTE`, `CASTELLÓ→CASTELLON`), variantes históricas (`PAIS VALENCIANO`),
  OCR garbled (`COSTELLAN`, `CASTILLON`), formatos provincia (`PROV DE VALENCIA`), etc.
- Añade aliases dinámicos desde `localidades/provinceIds.json` para las provincias cargadas.
- Fallback: prefijo sin espacios → `max(fuzz.ratio, fuzz.WRatio)` con umbral 60 →
  **mejor match garantizado** aunque esté por debajo del umbral.

### `_normalize_localidad_input(localidad, provincia)`

- Comprueba primero el diccionario `localidad_aliases` (bilingüe castellano/valenciano):
  `ALACANT→ALICANTE`, `XATIVA→XÀTIVA`, `VILLARREAL→VILA-REAL`, `BURRIANA→BORRIANA`, etc.
- Expande abreviaturas: `STA→SANTA`, `INST→INSTITUTO`, etc. (`_expand_ocr_abbreviations`).
- Ejecuta tres scorers: `fuzz.ratio`, `fuzz.token_set_ratio` y `fuzz.WRatio`; acepta el
  mejor si `score >= 80`.
- Si no hay match suficiente, devuelve el texto original.

### `_search_center_in_province` — estrategias en orden

1. **Match exacto** normalizado.
2. **Substring/contains** — si hay varios candidatos, prioriza con `_pick_best_by_localidad()`.
3. **Locality-first** — filtra centros cuya localidad coincide con la del usuario; fuzzy solo en ese subconjunto.
4. **Fuzzy global** con `fuzz.token_set_ratio` — puntuación combinada 70 % nombre + 30 % localidad.
5. **Fallback** en `analyze_center_optimized`: `fuzz.WRatio` sobre toda la provincia — **siempre devuelve un resultado** si existen centros.

> [!TIP]
> `_localidad_matches` considera match si: igualdad exacta tras expansión de abreviaturas,
> substring (uno contenido en otro), `fuzz.ratio >= 80`, `fuzz.token_set_ratio >= 85`
> o `fuzz.WRatio >= 82`.

El resultado de centro lleva metadatos internos `_match_strategy` y `_match_score`.
`_build_crm_record()` los usa para `FieldsToReview`: exact/contains no se revisan solo por
confianza OCR media; locality/fuzzy/fallback se revisan si `score < 80`, y `OtherCenter`
se revisa siempre que el alumno escribió un centro no resuelto.

---

## 🎓 Titulaciones y checkboxes

- `map_checked_degrees()` descarta etiquetas de sede/localidad mediante `_is_non_degree_checkbox_label()` (compara con `GlobalDataManager.all_localidades_normalized`).
- `analyze_titulacion_local()` normaliza etiquetas (quita prefijos como `GRADO EN`) y usa `process.extract(..., scorer=fuzz.token_set_ratio)` con umbral 70.
- Si hay variantes por provincia, `_select_best_variant_by_province()` desambigua.
- Las titulaciones solo tienen variantes Valencia, Castellón y Elche: Murcia cae en Elche/Alicante; Albacete, Baleares, Cuenca, Teruel y provincias sin variante propia caen en Valencia.
- `titulacion_manuscrita` también se busca; si no hay match, intenta correlacionar con `degrees_array` (score ≥ 50).
- La salida de titulaciones es dinámica: una sola titulación resuelta se serializa como `IdStudy`; dos o más se serializan como `Degrees: [{"IdStudy": ...}, ...]`.

---

## ⚙️ Umbrales configurables

| Parámetro | Valor | Usado en |
|---|---|---|
| `SELECTION_MARK_CONFIDENCE_THRESHOLD` | `0.80` | Filtrado de checkboxes en OCR |
| `WORD_CONFIDENCE_THRESHOLD` | `0.80` | Palabras con confianza < umbral → enviadas a GPT como incertidumbre OCR |
| `WORD_CONFIDENCE_HIGH_CUTOFF` | `0.40` | Tier HIGH para evidencia OCR fuerte en flags |
| `CENTER_REVIEW_SCORE_THRESHOLD` | `80` | Score mínimo para no revisar centros locality/fuzzy/fallback |
| `PROVINCE_MATCH_THRESHOLD` | `60` | `_find_province_key` |
| `COURSE_MATCH_THRESHOLD` | `60` | `analyze_curso_local` |
| `TITULACION_MATCH_THRESHOLD` | `70` | `analyze_titulacion_local` |
| Localidad normalization | `>= 80` | `_normalize_localidad_input` (tres scorers: ratio, token_set, WRatio) |
| `_localidad_matches` (ratio) | `>= 80` | Match de localidad en centros |
| `_localidad_matches` (token_set) | `>= 85` | Match de localidad en centros |
| `_localidad_matches` (WRatio) | `>= 82` | Match de localidad en centros (scorer añadido) |

> [!WARNING]
> Ajustar umbrales hacia abajo aumenta falsos positivos en el matching de centros.
> Revisar telemetría en producción antes de modificarlos.

## Flags de revisión

- `Email` se marca solo por evidencia propia: estructura inválida, acentos normalizados,
  palabra HIGH asociada al email o dominio institucional/de centro (`school`, `colegio`,
  `instituto`, `academy`, `.org`, etc.). No hereda automáticamente flags de Nombre/Apellidos.
- `Nombre` y `Apellidos` se pueden propagar entre sí, salvo que la confianza KVP del campo
  complementario sea alta (`>= 90%`).
- `Centro` se decide por estrategia de matching: exact/contains no se revisan por MEDIUM OCR;
  locality/fuzzy/fallback se revisan si el score queda por debajo de `CENTER_REVIEW_SCORE_THRESHOLD`.

---

## 🐛 Logs y debugging

| Etiqueta | Qué traza |
|---|---|
| `[DI_EXTRACT_SUMMARY img=N]` | Resumen por imagen al finalizar OCR: texto, confianza de palabras, marcas aceptadas/rechazadas, tabla completa KVP |
| `[OCR_STRUCTURED]` | Resultado bruto del OCR y selection marks |
| `[KEY_VALUE_PAIRS]` | Pares etiqueta→valor del anverso enviados a GPT y mapeo canónico diagnóstico |
| `[WORD_CONFIDENCE]` | Estadísticas de confianza por palabra |
| `[CHECKBOX_SUMMARY]` | Lista de checkboxes marcados enviada a GPT (fuente única: `:selected:` + confianza) |
| `[CENTER_SEARCH]` | Candidatos y scores en cada estrategia |
| `[CENTER_MATCH]` | Centro seleccionado y puntuación final |
| `[LOCALIDAD_NORM]` | Normalización de localidad de entrada |
| `[TITULACION_MATCH]` | Matches de titulaciones y scores |
| `[PHONE_NORMALIZE]` | Correcciones OCR en el teléfono |
| `[DNI_NORMALIZE]` | Sustituciones de caracteres en el DNI |

> [!TIP]
> Para auditar casos problemáticos, filtra por `[CENTER_SEARCH]` y `[CENTER_MATCH]`
> donde se muestran todos los candidatos con sus scores antes de la selección final.

---

## 💻 Comandos locales

```powershell
# Test de casos con errores ortográficos
$env:PYTHONPATH = "/ruta/al/repo"
python scripts/test_misspell_cases.py

# Test de búsqueda de centros
$env:PYTHONPATH = "/ruta/al/repo"
python scripts/test_centros.py

# Test de flags de revision
$env:PYTHONPATH = "/ruta/al/repo"
python scripts/test_review_flags.py

```

---

## 📌 Siguientes pasos

- [x] Migración SDK Azure DI v3.1 → v4.0 (Standard tier, `azure-ai-documentintelligence`)
- [x] Add-on Key-Value Pairs activado — pares etiqueta→valor del anverso enviados a GPT como contraste
- [x] Logging completo por imagen (`[DI_EXTRACT_SUMMARY]`) para diagnóstico en producción
- [x] Registrar alias puntuales (MATERDEI, COMUNITAT VALENCIANA, ALACANT, etc.) — resuelto con WRatio y alias dict
- [ ] **Fase 2**: Activar `DI_ADDON_HIGH_RESOLUTION=True` para reverso tras validar latencia (comparar checkboxes con/sin add-on en fichas reales)
- [ ] **Fase 3**: Activar `DI_ADDON_QUERY_FIELDS=True` (add-on de pago) para confianza por campo nativa
- [ ] **Fase 4**: `_reconcile_review_flags()` — reducir falsos positivos/negativos con confianza DI por campo (depende Fase 3)
- [ ] **Fase 5**: Entrenar Custom Neural Model con 50+ fichas etiquetadas en Azure DI Studio — elimina dependencia de GPT para campos básicos y da confianza por campo sin add-ons de pago
- [ ] Añadir campo `confidence` en `result_data` indicando si el match de centro fue forzado por fallback
- [ ] Ajustar umbrales según telemetría en producción

---
> *Documento generado desde la inspección del código en `procesa_ficha/__init__.py`.*
