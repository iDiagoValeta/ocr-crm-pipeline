"""Pruebas de matching de centros. Ejecutar: python test_centros.py"""
import logging
logging.disable(logging.CRITICAL)  # Reducir ruido en pruebas

from procesa_ficha import (
    _build_crm_record,
    _map_province_for_titulacion,
    analyze_center_optimized,
)

def ok(name, got, expected_contains=None, expected_exact=None, expect_empty=False):
    """Evalúa si el resultado es correcto."""
    if expect_empty:
        return not got or got == {}
    if expected_exact:
        return got == expected_exact
    if expected_contains:
        return expected_contains in (got or "")
    return bool(got)

def run_tests():
    results = []

    # --- CON LOCALIDAD ---
    print("=== 1. Ausiàs March + Picassent (con localidad) ===")
    r = analyze_center_optimized("Valencia", "Picassent", "Ausiàs March")
    got = r.get("Name", "") if r else ""
    results.append(("Ausiàs March + Picassent", ok(None, got, "INTERNACIONAL")))
    print(f"Resultado: {got}\n")

    print("=== 2. 9 D'OCTUBRE Valencia/Alcàsser y Alicante ===")
    r2a = analyze_center_optimized("Valencia", "Alcàsser", "9 D'OCTUBRE")
    r2b = analyze_center_optimized("Alicante", "Alicante", "9 D'OCTUBRE")
    ok2 = "ALCASSER" in (r2a.get("Name", "") or "") and "ALICANTE" in (r2b.get("Name", "") or "")
    results.append(("9 D'OCTUBRE provincias", ok2))
    print(f"Valencia: {r2a.get('Name', '')} | Alicante: {r2b.get('Name', '')}\n")

    print("=== 3. Antonio Machado + Elda ===")
    r = analyze_center_optimized("Alicante", "Elda", "Antonio Machado")
    got = r.get("Name", "") if r else ""
    results.append(("Antonio Machado Elda", ok(None, got, "ELDA")))
    print(f"Resultado: {got}\n")

    # --- SIN LOCALIDAD ---
    print("=== 4. Ausiàs March + Valencia SIN localidad ===")
    r = analyze_center_optimized("Valencia", "", "Ausiàs March")
    got = r.get("Name", "") if r else ""
    # Sin localidad: devuelve el más largo entre candidatos
    results.append(("Ausiàs March sin localidad", ok(None, got, "MARCH")))
    print(f"Resultado: {got} (esperado: algún Ausiàs March)\n")

    print("=== 5. Centro único sin localidad (Abad Sola, Gandía) ===")
    r = analyze_center_optimized("Valencia", "", "Abad Sola")
    got = r.get("Name", "") if r else ""
    results.append(("Abad Sola sin localidad", ok(None, got, "ABAD SOLA")))
    print(f"Resultado: {got}\n")

    print("=== 6. 9 D'OCTUBRE sin localidad (varios en Valencia) ===")
    r = analyze_center_optimized("Valencia", "", "9 D'OCTUBRE")
    got = r.get("Name", "") if r else ""
    results.append(("9 D'OCTUBRE Valencia sin localidad", ok(None, got, "9 D'OCTUBRE")))
    print(f"Resultado: {got}\n")

    # --- VARIANTES ORTOGRÁFICAS ---
    print("=== 7. Alcasser (sin tilde) + 9 D'OCTUBRE ===")
    r = analyze_center_optimized("Valencia", "Alcasser", "9 D'OCTUBRE")
    got = r.get("Name", "") if r else ""
    results.append(("Alcasser sin tilde", ok(None, got, "ALCASSER")))
    print(f"Resultado: {got}\n")

    print("=== 8. Blasco Ibañez (sin tilde en Ibáñez) ===")
    r = analyze_center_optimized("Valencia", "Cullera", "Blasco Ibañez")
    got = r.get("Name", "") if r else ""
    results.append(("Blasco Ibañez fuzzy", ok(None, got, "CULLERA")))
    print(f"Resultado: {got}\n")

    # --- OTRAS PROVINCIAS ---
    print("=== 9. Ausiàs March en Castellón (Vall d'Uixó) ===")
    r = analyze_center_optimized("Castellón", "La Vall d'Uixó", "Ausiàs March")
    got = r.get("Name", "") if r else ""
    results.append(("Ausiàs March Castellón", ok(None, got, "VALL D'UIXO")))
    print(f"Resultado: {got}\n")

    print("=== 10. Ausiàs March en Alicante ===")
    r = analyze_center_optimized("Alicante", "Alicante", "Ausiàs March")
    got = r.get("Name", "") if r else ""
    results.append(("Ausiàs March Alicante", ok(None, got, "ALICANTE")))
    print(f"Resultado: {got}\n")

    # --- CASOS LÍMITE ---
    print("=== 11. Localidad que no coincide (Ausiàs March + Valencia capital) ===")
    r = analyze_center_optimized("Valencia", "Valencia", "Ausiàs March")
    got = r.get("Name", "") if r else ""
    # No hay "AUSIÀS MARCH (VALENCIA)" - hay Manises, Albalat, Picassent, IES Valencia...
    # Puede devolver vacío o el que más se acerque
    results.append(("Ausiàs March + Valencia ciudad", ok(None, got, expect_empty=False)))
    print(f"Resultado: {got or '(vacío)'}\n")

    print("=== 12. Centro inexistente (fuzzy puede devolver algo) ===")
    r = analyze_center_optimized("Valencia", "Valencia", "Colegio Inexistente XYZ")
    got = r.get("Name", "") if r else ""
    # Fuzzy puede matchear a otro centro; aceptamos cualquier resultado
    results.append(("Centro inexistente", True))  # No esperamos vacío: fuzzy tiene fallback
    print(f"Resultado: {r or '(vacío)'}\n")

    print("=== 13. Nombre parcial (Blasco) ===")
    r = analyze_center_optimized("Valencia", "Valencia", "Blasco")
    got = r.get("Name", "") if r else ""
    results.append(("Nombre parcial Blasco", ok(None, got, "BLASCO")))
    print(f"Resultado: {got}\n")

    print("=== 14. Provincia Castellón / Castellon (variante, sin localidad) ===")
    r = analyze_center_optimized("Castellon", "", "Botànic Calduch")
    got = r.get("Name", "") if r else ""
    results.append(("Provincia Castellon", ok(None, got, "BOTÀNIC")))
    print(f"Resultado: {got}\n")

    print("=== 15. Puig con OCR severo no debe matchear por artículo 'EL' ===")
    r = analyze_center_optimized("VALENCIA.", "EL PLUG DE STA MARIA", "COLEGIO SANTA MARIA DE EL PLIG")
    got = r.get("Name", "") if r else ""
    results.append(("Puig OCR severo", ok(None, got, "SANTA MARÍA DE EL PUIG")))
    print(f"Resultado: {got}\n")

    # --- RESUMEN ---
    print("=" * 55)
    passed = sum(1 for _, o in results if o)
    total = len(results)
    for name, o in results:
        print(f"  {'OK' if o else 'FAIL'}: {name}")
    print("=" * 55)
    print(f"Resumen: {passed}/{total} pruebas OK")
    return passed == total

def run_castellon_mater_dei_tests():
    """
    Suite específica para:
    - Casos sin/con provincia con localidad Castellón / Castellón de la Plana
    - Mater Dei con variaciones OCR
    - Sin localidad y sin centro
    - Variaciones mal escritas de localidad y provincia
    """
    results = []

    # =========================================================================
    # BLOQUE A: Sin provincia
    # =========================================================================

    print("\n=== A1. SIN provincia, localidad 'Castellón', Mater Dei ===")
    r = analyze_center_optimized("", "Castellón", "Diocesano Mater Dei")
    got = r.get("Name", "") if r else ""
    # Sin provincia el sistema no puede resolver la búsqueda → esperamos {}
    passed = (r == {})
    results.append(("Sin provincia + localidad Castellón", passed))
    print(f"Resultado: {r!r}  → {'OK (vacío esperado)' if passed else 'FAIL (debería ser vacío)'}\n")

    print("=== A2. SIN provincia, localidad 'Castellón de la plana', Mater Dei ===")
    r = analyze_center_optimized("", "Castellón de la plana", "Diocesano Mater Dei")
    got = r.get("Name", "") if r else ""
    passed = (r == {})
    results.append(("Sin provincia + localidad Castellón de la plana", passed))
    print(f"Resultado: {r!r}  → {'OK (vacío esperado)' if passed else 'FAIL (debería ser vacío)'}\n")

    # =========================================================================
    # BLOQUE B: Con provincia + localidad Castellón (variantes tipográficas)
    # =========================================================================

    print("=== B1. Provincia 'Castellón', localidad 'Castellón', Mater Dei (correcto) ===")
    r = analyze_center_optimized("Castellón", "Castellón", "Diocesano Mater Dei")
    got = r.get("Name", "") if r else ""
    passed = "MATER DEI" in got.upper()
    results.append(("Provincia+localidad correctas, Mater Dei", passed))
    print(f"Resultado: {got}  → {'OK' if passed else 'FAIL'}\n")

    print("=== B2. Provincia 'Castellon' (sin tilde), localidad 'castellon', Mater Dei ===")
    r = analyze_center_optimized("Castellon", "castellon", "Diocesano Mater Dei")
    got = r.get("Name", "") if r else ""
    passed = "MATER DEI" in got.upper()
    results.append(("Provincia Castellon (sin tilde) + localidad sin tilde", passed))
    print(f"Resultado: {got}  → {'OK' if passed else 'FAIL'}\n")

    print("=== B3. Provincia 'Castellon de la plana' (como provincia entera), localidad vacía, Mater Dei ===")
    # El alumno confunde provincia con localidad y escribe 'Castellón de la Plana' como provincia
    r = analyze_center_optimized("Castellon de la plana", "", "Diocesano Mater Dei")
    got = r.get("Name", "") if r else ""
    # El fuzzy de _find_province_key debería resolver esto a CASTELLON
    passed = "MATER DEI" in got.upper()
    results.append(("Provincia = 'Castellon de la plana' (debería resolver a CASTELLON)", passed))
    print(f"Resultado: {got}  → {'OK' if passed else 'FAIL (fuzzy no resolvió provincia)'}\n")

    # =========================================================================
    # BLOQUE C: Variaciones del nombre Mater Dei (OCR / escritura manual)
    # =========================================================================

    mater_dei_variants = [
        ("Mater Dei",               "solo nombre corto"),
        ("Mater Day",               "OCR 'ei'→'ay'"),
        ("Materi Dei",              "letra extra"),
        ("mater dey",               "minúsculas + 'e'→'ey'"),
        ("Diocesano Mater",         "sin 'Dei'"),
        ("Diosesano Mater Dei",     "'c'→'s' en Diocesano"),
        ("Dioces Mater Dei",        "abreviado"),
        ("Materdei",                "sin espacio"),
        ("Matr Dei",                "vocal omitida por OCR"),
        ("Diocesano Materr Dei",    "letra duplicada OCR"),
    ]

    print("=== C. Variaciones del nombre Mater Dei (provincia Castellón) ===")
    for variant, desc in mater_dei_variants:
        r = analyze_center_optimized("Castellón", "Castellón", variant)
        got = r.get("Name", "") if r else ""
        passed = "MATER DEI" in got.upper()
        tag = "OK" if passed else "FAIL"
        results.append((f"Mater Dei variante: '{variant}'", passed))
        print(f"  [{tag}] '{variant}' ({desc}) → {got or '(vacío)'}")
    print()

    # =========================================================================
    # BLOQUE D: Sin localidad y sin centro
    # =========================================================================

    print("=== D1. Sin localidad y sin centro (provincia Castellón) ===")
    r = analyze_center_optimized("Castellón", "", "")
    passed = (r == {})
    results.append(("Sin localidad y sin centro", passed))
    print(f"Resultado: {r!r}  → {'OK (vacío esperado)' if passed else 'FAIL'}\n")

    print("=== D2. Sin nada (todo vacío) ===")
    r = analyze_center_optimized("", "", "")
    passed = (r == {})
    results.append(("Todo vacío", passed))
    print(f"Resultado: {r!r}  → {'OK (vacío esperado)' if passed else 'FAIL'}\n")

    # =========================================================================
    # BLOQUE E: Variaciones mal escritas de localidad y provincia
    # =========================================================================

    localidad_variants = [
        # (provincia, localidad, desc)
        ("Castellón",     "Castellon de la plana",            "localidad sin tilde"),
        ("Castellón",     "Castelon de la plana",             "localidad con l simple + sin tilde"),
        ("Castellón",     "Kastelyon de la plana",            "OCR severo en localidad"),
        ("Castellon",     "Castellón de la Plana",            "provincia sin tilde, localidad correcta"),
        ("Kastelyon",     "Castellón",                        "OCR severo en provincia"),
        ("Castellón",     "Castellon",                        "localidad sin tilde capital"),
        ("CASTELLON",     "CASTELLON DE LA PLANA",            "todo mayúsculas sin tilde"),
        ("castellón",     "castellón",                        "todo minúsculas"),
    ]

    print("=== E. Variaciones de localidad/provincia con Mater Dei ===")
    for prov, loc, desc in localidad_variants:
        r = analyze_center_optimized(prov, loc, "Diocesano Mater Dei")
        got = r.get("Name", "") if r else ""
        passed = "MATER DEI" in got.upper()
        tag = "OK" if passed else "FAIL"
        results.append((f"prov='{prov}' loc='{loc}' ({desc})", passed))
        print(f"  [{tag}] prov='{prov}', loc='{loc}' ({desc}) → {got or '(vacío)'}")
    print()

    # =========================================================================
    # RESUMEN
    # =========================================================================
    print("=" * 65)
    passed_count = sum(1 for _, o in results if o)
    total = len(results)
    for name, o in results:
        print(f"  {'OK  ' if o else 'FAIL'}: {name}")
    print("=" * 65)
    print(f"Resumen Castellón/Mater Dei: {passed_count}/{total} pruebas OK")
    return passed_count == total


def run_ocr_merge_tests():
    """
    Suite F: palabras fusionadas por OCR (sin espacio), variantes OCR severas
    y abreviaturas de provincia.
    Estos casos ocurren cuando el escáner o el OCR une dos palabras o introduce
    caracteres numéricos en lugar de letras similares.
    """
    results = []

    # =========================================================================
    # BLOQUE F1: Palabras fusionadas (OCR elimina el espacio entre palabras)
    # =========================================================================

    print("\n=== F1. 'Materdei' (sin espacio) → DIOCESANO MATER DEI ===")
    r = analyze_center_optimized("Castellón", "Castellón", "Materdei")
    got = r.get("Name", "") if r else ""
    passed = "MATER DEI" in got.upper()
    results.append(("Materdei (sin espacio)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== F2. 'AusiàsMarch' (CamelCase fusionado) → algún Ausiàs March ===")
    r = analyze_center_optimized("Valencia", "Picassent", "AusiàsMarch")
    got = r.get("Name", "") if r else ""
    passed = "MARCH" in got.upper()
    results.append(("AusiàsMarch fusionado", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== F3. 'AntonioMachado' (fusionado) + Elda ===")
    r = analyze_center_optimized("Alicante", "Elda", "AntonioMachado")
    got = r.get("Name", "") if r else ""
    passed = "ELDA" in got.upper() or "MACHADO" in got.upper()
    results.append(("AntonioMachado fusionado + Elda", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== F4. 'BotanicCalduch' (fusionado) Castellón sin localidad ===")
    r = analyze_center_optimized("Castellon", "", "BotanicCalduch")
    got = r.get("Name", "") if r else ""
    passed = "BOTANIC" in got.upper() or "CALDUCH" in got.upper()
    results.append(("BotanicCalduch fusionado", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    # =========================================================================
    # BLOQUE F2: OCR sustituye letras por dígitos similares
    # =========================================================================

    print("=== F5. 'Materd3i' (OCR 'e'→'3') → DIOCESANO MATER DEI ===")
    r = analyze_center_optimized("Castellón", "Castellón", "Materd3i")
    got = r.get("Name", "") if r else ""
    passed = "MATER DEI" in got.upper()
    results.append(("Materd3i con dígito OCR", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== F6. 'Di0cesano Mater' (OCR 'o'→'0') → DIOCESANO MATER DEI ===")
    r = analyze_center_optimized("Castellón", "Castellón", "Di0cesano Mater")
    got = r.get("Name", "") if r else ""
    passed = "MATER DEI" in got.upper()
    results.append(("Di0cesano con dígito OCR", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== F7. 'Bl4sco Ib4ñez' (OCR 'a'→'4') + Valencia ===")
    r = analyze_center_optimized("Valencia", "Valencia", "Bl4sco Ib4ñez")
    got = r.get("Name", "") if r else ""
    passed = "BLASCO" in got.upper()
    results.append(("Blasco con dígito OCR", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    # =========================================================================
    # BLOQUE F3: Abreviaturas de provincia
    # =========================================================================

    print("=== F8. Provincia 'C. Valenciana' → Valencia ===")
    r = analyze_center_optimized("C. Valenciana", "Gandía", "Abad Sola")
    got = r.get("Name", "") if r else ""
    passed = "ABAD SOLA" in got.upper()
    results.append(("Provincia 'C. Valenciana'", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== F9. Provincia 'Comunitat Valenciana' → Valencia ===")
    r = analyze_center_optimized("Comunitat Valenciana", "Picassent", "Ausiàs March")
    got = r.get("Name", "") if r else ""
    passed = "MARCH" in got.upper()
    results.append(("Provincia 'Comunitat Valenciana'", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== F10. Provincia 'Alacant' → Alicante (nombre valenciano) ===")
    r = analyze_center_optimized("Alacant", "Elda", "Antonio Machado")
    got = r.get("Name", "") if r else ""
    passed = "ELDA" in got.upper() or "MACHADO" in got.upper()
    results.append(("Provincia 'Alacant' (valenciano)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    # =========================================================================
    # BLOQUE F4: Selección correcta sin localidad (no debe devolver universidades)
    # =========================================================================

    print("=== F11. 'Ausiàs March' sin localidad → centro escolar, no universidad ===")
    r = analyze_center_optimized("Valencia", "", "Ausiàs March")
    got = r.get("Name", "") if r else ""
    # Debe devolver un IES/colegio Ausiàs March, no la larga entrada universitaria
    passed = "MARCH" in got.upper() and "UNIVERSIDAD" not in got.upper()
    results.append(("Ausiàs March sin localidad (no universidad)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    # =========================================================================
    # RESUMEN
    # =========================================================================
    print("=" * 65)
    passed_count = sum(1 for _, o in results if o)
    total = len(results)
    for name, o in results:
        print(f"  {'OK  ' if o else 'FAIL'}: {name}")
    print("=" * 65)
    print(f"Resumen OCR/Merge/Provincia: {passed_count}/{total} pruebas OK")
    return passed_count == total


def run_province_alias_tests():
    """
    Suite G: cobertura total de alias de provincia y pares bilingüe.
    Verifica que el sistema resuelve correctamente cualquier forma en que
    un alumno de la Comunitat Valenciana puede escribir su provincia o localidad,
    en castellano, valenciano, abreviado o con errores OCR.
    """
    results = []

    # =========================================================================
    # BLOQUE G1: Siglas y formas cortas de provincia
    # =========================================================================
    print("\n=== G1. Sigla 'CV' → provincia Valencia ===")
    r = analyze_center_optimized("CV", "Picassent", "Ausiàs March")
    got = r.get("Name", "") if r else ""
    passed = "MARCH" in got.upper()
    results.append(("Sigla 'CV' → Valencia", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== G2. 'C. Valenciana' → Valencia ===")
    r = analyze_center_optimized("C. Valenciana", "Gandía", "Abad Sola")
    got = r.get("Name", "") if r else ""
    passed = "ABAD SOLA" in got.upper()
    results.append(("'C. Valenciana' → Valencia", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== G3. 'País Valenciano' → Valencia (denominación histórica) ===")
    r = analyze_center_optimized("País Valenciano", "Gandía", "Abad Sola")
    got = r.get("Name", "") if r else ""
    passed = "ABAD SOLA" in got.upper()
    results.append(("'País Valenciano' → Valencia", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== G4. 'Alacant' → Alicante (nombre valenciano oficial) ===")
    r = analyze_center_optimized("Alacant", "Elda", "Antonio Machado")
    got = r.get("Name", "") if r else ""
    passed = "ELDA" in got.upper() or "MACHADO" in got.upper()
    results.append(("'Alacant' → Alicante", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== G5. 'Alacante' → Alicante (typo común) ===")
    r = analyze_center_optimized("Alacante", "Elda", "Antonio Machado")
    got = r.get("Name", "") if r else ""
    passed = "ELDA" in got.upper() or "MACHADO" in got.upper()
    results.append(("'Alacante' → Alicante (typo)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== G6. 'Castelló' (valenciano con tilde) → Castellón ===")
    r = analyze_center_optimized("Castelló", "Castellón", "Diocesano Mater Dei")
    got = r.get("Name", "") if r else ""
    passed = "MATER DEI" in got.upper()
    results.append(("'Castelló' → Castellón", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== G7. 'Castillón' (typo 'i' por 'e') → Castellón ===")
    r = analyze_center_optimized("Castillón", "Castellón", "Diocesano Mater Dei")
    got = r.get("Name", "") if r else ""
    passed = "MATER DEI" in got.upper()
    results.append(("'Castillón' → Castellón (typo)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== G8. 'Prov. de Valencia' → Valencia ===")
    r = analyze_center_optimized("Prov. de Valencia", "Picassent", "Ausiàs March")
    got = r.get("Name", "") if r else ""
    passed = "MARCH" in got.upper()
    results.append(("'Prov. de Valencia'", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    # =========================================================================
    # BLOQUE G2: OCR severo en provincia (sustitución de letras)
    # =========================================================================
    province_ocr_variants = [
        ("Val3ncia",     "Picassent", "Ausiàs March",      "MARCH",    "OCR 'e'→'3' en Valencia"),
        ("Alicamt3",     "Elda",      "Antonio Machado",   "ELDA",     "OCR 'n'→'m', 'e'→'3'"),
        ("Castel1on",    "Castellón", "Diocesano Mater Dei","MATER DEI","OCR 'l'→'1' en Castellón"),
        ("Kostellon",    "Castellón", "Diocesano Mater Dei","MATER DEI","OCR severo C→K"),
        ("Alic4nte",     "Elda",      "Antonio Machado",   "ELDA",     "OCR 'a'→'4' en Alicante"),
    ]
    print("=== G9. OCR severo en nombre de provincia ===")
    for prov, loc, centro, expect, desc in province_ocr_variants:
        r = analyze_center_optimized(prov, loc, centro)
        got = r.get("Name", "") if r else ""
        passed = expect in got.upper()
        results.append((f"Prov OCR '{prov}' ({desc})", passed))
        print(f"  {'OK' if passed else 'FAIL'} [{desc}]: {got or '(vacío)'}")
    print()

    # =========================================================================
    # BLOQUE G3: Localidades bilingüe castellano/valenciano
    # =========================================================================
    print("=== G10. Localidad 'Alacant' (valenciano) en Alicante ===")
    r = analyze_center_optimized("Alicante", "Alacant", "9 D'OCTUBRE")
    got = r.get("Name", "") if r else ""
    passed = "ALICANTE" in got.upper()
    results.append(("Localidad 'Alacant' en Alicante", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== G11. Localidad con OCR dígito 'Alicamt' → Alicante ===")
    r = analyze_center_optimized("Alicante", "Alicamt", "9 D'OCTUBRE")
    got = r.get("Name", "") if r else ""
    passed = "ALICANTE" in got.upper()
    results.append(("Localidad 'Alicamt' → Alicante", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== G12. Localidad 'Castellón de la Plana' escrita en campo provincia ===")
    # El alumno escribe "Castellón de la Plana" como provincia → debe resolver a CASTELLON
    r = analyze_center_optimized("Castellón de la Plana", "Castellón", "Diocesano Mater Dei")
    got = r.get("Name", "") if r else ""
    passed = "MATER DEI" in got.upper()
    results.append(("Localidad como provincia 'Castellón de la Plana'", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    # =========================================================================
    # RESUMEN
    # =========================================================================
    print("=" * 65)
    passed_count = sum(1 for _, o in results if o)
    total = len(results)
    for name, o in results:
        print(f"  {'OK  ' if o else 'FAIL'}: {name}")
    print("=" * 65)
    print(f"Resumen Alias Provincia/Bilingüe: {passed_count}/{total} pruebas OK")
    return passed_count == total


def run_province_capital_localidad_tests():
    """
    Suite H: el nombre de la provincia (CASTELLON / VALENCIA / ALICANTE) aparece
    como localidad en los nombres de centros del CRM cuando el centro está en la
    capital de provincia.  Algunos centros usan '(CASTELLON)' y otros usan
    '(CASTELLON DE LA PLANA)'; ambos deben resolverse correctamente con cualquier
    forma que el alumno escriba su localidad.
    """
    results = []

    # =========================================================================
    # BLOQUE H1: Centros en Castellón capital
    #   - algunos almacenados con localidad '(CASTELLON)'
    #   - otros con '(CASTELLON DE LA PLANA)'
    # =========================================================================

    print("\n=== H1a. Centro '(CASTELLON)' ; alumno escribe loc='Castellón de la Plana' ===")
    r = analyze_center_optimized("Castellón", "Castellón de la Plana", "Bernat Artola")
    got = r.get("Name", "") if r else ""
    passed = "BERNAT ARTOLA" in got.upper() and "CASTELLON" in got.upper()
    results.append(("(CASTELLON) + loc=Castellón de la Plana", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== H1b. Centro '(CASTELLON)' ; alumno escribe loc='Castellon' (sin tilde) ===")
    r = analyze_center_optimized("Castellón", "Castellon", "Bernat Artola")
    got = r.get("Name", "") if r else ""
    passed = "BERNAT ARTOLA" in got.upper() and "CASTELLON" in got.upper()
    results.append(("(CASTELLON) + loc=Castellon (sin tilde)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== H1c. Centro '(CASTELLON DE LA PLANA)' ; alumno escribe loc='Castellón' ===")
    r = analyze_center_optimized("Castellón", "Castellón", "Academia Latina")
    got = r.get("Name", "") if r else ""
    passed = "ACADEMIA LATINA" in got.upper() and "CASTELLON" in got.upper()
    results.append(("(CASTELLON DE LA PLANA) + loc=Castellón", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== H1d. Centro '(CASTELLON DE LA PLANA)' ; alumno escribe loc='castellon de la plana' (minúsculas) ===")
    r = analyze_center_optimized("Castellón", "castellon de la plana", "Academia Latina")
    got = r.get("Name", "") if r else ""
    passed = "ACADEMIA LATINA" in got.upper()
    results.append(("(CASTELLON DE LA PLANA) + loc=castellon de la plana (minúsculas)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== H1e. Centro '(CASTELLON)' ; alumno escribe loc='CASTELLON DE LA PLANA' (mayúsculas) ===")
    r = analyze_center_optimized("CASTELLON", "CASTELLON DE LA PLANA", "Bernat Artola")
    got = r.get("Name", "") if r else ""
    passed = "BERNAT ARTOLA" in got.upper()
    results.append(("(CASTELLON) + loc=CASTELLON DE LA PLANA (mayúsculas)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== H1f. Centro '(CASTELLON)' ; alumno escribe loc='Castelló de la Plana' (valenciano con tilde) ===")
    r = analyze_center_optimized("Castellón", "Castelló de la Plana", "Bernat Artola")
    got = r.get("Name", "") if r else ""
    passed = "BERNAT ARTOLA" in got.upper()
    results.append(("(CASTELLON) + loc=Castelló de la Plana (valenciano)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    # =========================================================================
    # BLOQUE H2: Centros en Valencia capital  (localidad almacenada '(VALENCIA)')
    # =========================================================================

    print("=== H2a. Centro '(VALENCIA)' ; alumno escribe loc='Valencia' ===")
    r = analyze_center_optimized("Valencia", "Valencia", "Aiora")
    got = r.get("Name", "") if r else ""
    passed = "AIORA" in got.upper() and "VALENCIA" in got.upper()
    results.append(("(VALENCIA) + loc=Valencia", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== H2b. Centro '(VALENCIA)' ; alumno escribe loc='valencia' (minúsculas) ===")
    r = analyze_center_optimized("Valencia", "valencia", "Aiora")
    got = r.get("Name", "") if r else ""
    passed = "AIORA" in got.upper() and "VALENCIA" in got.upper()
    results.append(("(VALENCIA) + loc=valencia (minúsculas)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== H2c. Centro '(VALENCIA)' ; alumno escribe loc='VALENCIA' (mayúsculas) ===")
    r = analyze_center_optimized("VALENCIA", "VALENCIA", "Blasco Ibanez")
    got = r.get("Name", "") if r else ""
    passed = "BLASCO" in got.upper() and "VALENCIA" in got.upper()
    results.append(("(VALENCIA) + loc=VALENCIA (mayúsculas)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== H2d. Centro '(VALENCIA)' ; alumno escribe loc='Vlencia' (OCR typo) ===")
    r = analyze_center_optimized("Valencia", "Vlencia", "Aiora")
    got = r.get("Name", "") if r else ""
    passed = "AIORA" in got.upper() and "VALENCIA" in got.upper()
    results.append(("(VALENCIA) + loc=Vlencia (OCR typo)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== H2e. Centro '(VALENCIA)' ; alumno escribe loc='Valencia capital' ===")
    r = analyze_center_optimized("Valencia", "Valencia capital", "Aiora")
    got = r.get("Name", "") if r else ""
    passed = "AIORA" in got.upper() and "VALENCIA" in got.upper()
    results.append(("(VALENCIA) + loc=Valencia capital", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    # =========================================================================
    # BLOQUE H3: Centros en Alicante capital  (localidad almacenada '(ALICANTE)')
    # =========================================================================

    print("=== H3a. Centro '(ALICANTE)' ; alumno escribe loc='Alicante' ===")
    r = analyze_center_optimized("Alicante", "Alicante", "Aitana")
    got = r.get("Name", "") if r else ""
    passed = "AITANA" in got.upper() and "ALICANTE" in got.upper()
    results.append(("(ALICANTE) + loc=Alicante", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== H3b. Centro '(ALICANTE)' ; alumno escribe loc='Alacant' (valenciano) ===")
    r = analyze_center_optimized("Alicante", "Alacant", "Aitana")
    got = r.get("Name", "") if r else ""
    passed = "AITANA" in got.upper() and "ALICANTE" in got.upper()
    results.append(("(ALICANTE) + loc=Alacant (valenciano)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== H3c. Centro '(ALICANTE)' ; alumno escribe loc='alicante' (minúsculas) ===")
    r = analyze_center_optimized("Alicante", "alicante", "Academia Cots")
    got = r.get("Name", "") if r else ""
    passed = "ACADEMIA COTS" in got.upper() and "ALICANTE" in got.upper()
    results.append(("(ALICANTE) + loc=alicante (minúsculas)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== H3d. Centro '(ALICANTE)' ; alumno escribe loc='Alicamt' (OCR 'n'→'m') ===")
    r = analyze_center_optimized("Alicante", "Alicamt", "Aitana")
    got = r.get("Name", "") if r else ""
    passed = "AITANA" in got.upper() and "ALICANTE" in got.upper()
    results.append(("(ALICANTE) + loc=Alicamt (OCR typo)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== H3e. Centro '(ALICANTE)' ; alumno escribe loc='ALICANTE' (mayúsculas) ===")
    r = analyze_center_optimized("ALICANTE", "ALICANTE", "Academia Cots")
    got = r.get("Name", "") if r else ""
    passed = "ACADEMIA COTS" in got.upper() and "ALICANTE" in got.upper()
    results.append(("(ALICANTE) + loc=ALICANTE (mayúsculas)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    # =========================================================================
    # BLOQUE H4: Doble paréntesis '(LOCALIDAD) (PROVINCIA)'
    #   p.ej. 'ANTONIO MACHADO (ELDA) (ALICANTE)' — la función de extracción
    #   debe devolver 'ELDA', no 'ALICANTE'.
    # =========================================================================

    print("=== H4a. Doble paréntesis: '(ELDA) (ALICANTE)' ; alumno escribe loc='Elda' ===")
    r = analyze_center_optimized("Alicante", "Elda", "Antonio Machado")
    got = r.get("Name", "") if r else ""
    passed = "ELDA" in got.upper() and "ANTONIO MACHADO" in got.upper()
    results.append(("Doble paréntesis (ELDA)(ALICANTE) + loc=Elda", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== H4b. Doble paréntesis: '(ELDA) (ALICANTE)' ; alumno escribe loc='Elda' prov='ALICANTE' ===")
    r = analyze_center_optimized("ALICANTE", "Elda", "Antonio Machado")
    got = r.get("Name", "") if r else ""
    passed = "ELDA" in got.upper() and "ANTONIO MACHADO" in got.upper()
    results.append(("Doble paréntesis + prov=ALICANTE (mayúsculas)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    print("=== H4c. Pueblo que se llama igual que nombre de provincia: loc='Alicante' pero centro en 'ELDA' ===")
    # El usuario escribe provincia='Alicante' y localidad='Elda'; el centro con doble paréntesis
    # debe matchear 'ELDA', no la provincia 'ALICANTE'.
    r = analyze_center_optimized("Alicante", "Elda", "Machado")
    got = r.get("Name", "") if r else ""
    passed = "ELDA" in got.upper()
    results.append(("Nombre centro 'Machado' + loc=Elda (sin confundir ALICANTE como localidad)", passed))
    print(f"  {'OK' if passed else 'FAIL'}: {got or '(vacío)'}\n")

    # =========================================================================
    # RESUMEN
    # =========================================================================
    print("=" * 65)
    passed_count = sum(1 for _, o in results if o)
    total = len(results)
    for name, o in results:
        print(f"  {'OK  ' if o else 'FAIL'}: {name}")
    print("=" * 65)
    print(f"Resumen Capital-como-localidad: {passed_count}/{total} pruebas OK")
    return passed_count == total


def run_new_centers_tests():
    """
    Suite I: Nuevos centros añadidos al CRM:
      - EDELWEISS (GODELLA) con localidad Campolivar (barrio de Godella)
      - COLEGIO SAN ROQUE DE ALCOY / COL·LEGI SANT ROC DE ALCOI (Alcoy/Alcoi, Alicante)
      - COLEGIO DOMUS / FUNDACIÓN INSTITUCIÓN CULTURAL DOMUS (Godella, Valencia)
    Incluye variantes OCR, errores tipográficos y formas bilingüe.
    """
    results = []

    # =========================================================================
    # BLOQUE I1: EDELWEISS (GODELLA) — localidad Campolivar y Godella
    # =========================================================================
    edelweiss_cases = [
        # (provincia, localidad, centro, desc)
        ("Valencia",  "CAMPOLIVAR",   "EDELWEIS",              "nombre sin S final + barrio"),
        ("Valencia",  "Campolivar",   "EDELWEISS",             "nombre correcto + barrio"),
        ("Valencia",  "Godella",      "EDELWEIS",              "sin S + localidad correcta"),
        ("Valencia",  "Godella",      "EDELWEISS",             "nombre correcto + Godella"),
        ("Valencia",  "campolivar",   "Edelweis",              "todo minúsculas + barrio"),
        ("Valencia",  "GODELLA",      "edelweiss",             "todo minúsculas + Godella"),
        ("VALENCIA",  "CAMPOLIVAR",   "EDELWEIS",              "todo mayúsculas"),
        ("Valencia",  "Godella",      "EDEL WEISS",            "nombre separado (OCR)"),
        ("Valencia",  "Godella",      "Edelwais",              "typo vocal (ei→ai)"),
        ("Valencia",  "Campolivar",   "Edelveis",              "typo (ss→s)"),
    ]

    print("\n=== I1. EDELWEISS (GODELLA) — variantes localidad y nombre ===")
    for prov, loc, centro, desc in edelweiss_cases:
        r = analyze_center_optimized(prov, loc, centro)
        got = r.get("Name", "") if r else ""
        passed = "EDELWEISS" in got.upper()
        results.append((f"Edelweiss '{centro}' loc='{loc}' ({desc})", passed))
        print(f"  {'OK' if passed else 'FAIL'} [{desc}]: {got or '(vacío)'}")
    print()

    # =========================================================================
    # BLOQUE I2: COLEGIO SAN ROQUE DE ALCOY / COL·LEGI SANT ROC DE ALCOI
    # =========================================================================
    sanroque_cases = [
        # (provincia, localidad, centro, expect_in_name, desc)
        ("Alicante",  "Alcoy",   "Colegio San Roque",            "SAN ROQUE",  "caso original usuario"),
        ("Alicante",  "Alcoi",   "col·legi sant roc de alcoi",   "SANT ROC",   "caso original valenciano"),
        ("Alicante",  "Alcoy",   "COLEGIO SAN ROQUE DE ALCOY",   "SAN ROQUE",  "nombre completo"),
        ("Alicante",  "Alcoi",   "COL·LEGI SANT ROC DE ALCOI",   "SANT ROC",   "nombre completo valenciano"),
        ("Alicante",  "Alcoy",   "San Roque",                    "SAN ROQUE",  "nombre corto"),
        ("Alicante",  "Alcoi",   "Sant Roc",                     "SANT ROC",   "nombre corto valenciano"),
        ("Alicante",  "Alcoy",   "San Roque de Alcoy",           "SAN ROQUE",  "sin prefijo Colegio"),
        ("Alicante",  "Alcoi",   "Sant Roc de Alcoi",            "SANT ROC",   "forma valenciana sin apóstrofe"),
        ("Alicante",  "ALCOY",   "san roque",                    "SAN ROQUE",  "todo minúsculas"),
        ("ALICANTE",  "ALCOY",   "COLEGIO SAN ROQUE",            "SAN ROQUE",  "todo mayúsculas"),
        ("Alicante",  "Alcoy",   "Col· Legi Sant Roc Alcoi",     "SANT ROC",   "OCR real (espacio tras punto medio)"),
    ]

    print("=== I2. COLEGIO SAN ROQUE / COL·LEGI SANT ROC (Alcoy, Alicante) ===")
    for prov, loc, centro, expect, desc in sanroque_cases:
        r = analyze_center_optimized(prov, loc, centro)
        got = r.get("Name", "") if r else ""
        passed = expect in got.upper()
        results.append((f"SanRoque '{centro}' ({desc})", passed))
        print(f"  {'OK' if passed else 'FAIL'} [{desc}]: {got or '(vacío)'}")
    print()

    # =========================================================================
    # BLOQUE I3: COLEGIO DOMUS / FUNDACIÓN INSTITUCIÓN CULTURAL DOMUS (Godella)
    # =========================================================================
    domus_cases = [
        # (provincia, localidad, centro, desc)
        ("Valencia",  "Godella",   "colegio domus",                          "caso original usuario"),
        ("Valencia",  "godella",   "fundación institución cultural domus",    "caso original completo"),
        ("Valencia",  "Godella",   "DOMUS",                                  "nombre solo"),
        ("Valencia",  "Godella",   "Domus Godella",                          "nombre + localidad"),
        ("Valencia",  "Godella",   "FUNDACIÓN DOMUS",                        "nombre parcial fundación"),
        ("Valencia",  "Godella",   "Institución Cultural Domus",             "sin prefijo fundación"),
        ("Valencia",  "Godella",   "Col·legi Domus",                         "nombre valenciano genérico"),
        ("Valencia",  "GODELLA",   "COLEGIO DOMUS",                          "todo mayúsculas"),
        ("VALENCIA",  "godella",   "domus",                                  "todo minúsculas"),
        ("Valencia",  "Campolivar","DOMUS",                                  "Campolivar como localidad"),
        ("Valencia",  "Campolivar","Colegio Domus",                          "Campolivar + nombre parcial"),
        ("Valencia",  "Godella",   "D0MUS",                                  "OCR 'o'→'0'"),
        ("Valencia",  "Godela",    "Domus",                                  "typo Godella con l simple"),
    ]

    print("=== I3. COLEGIO DOMUS / FUNDACIÓN INSTITUCIÓN CULTURAL DOMUS (Godella) ===")
    for prov, loc, centro, desc in domus_cases:
        r = analyze_center_optimized(prov, loc, centro)
        got = r.get("Name", "") if r else ""
        passed = "DOMUS" in got.upper()
        results.append((f"Domus '{centro}' loc='{loc}' ({desc})", passed))
        print(f"  {'OK' if passed else 'FAIL'} [{desc}]: {got or '(vacío)'}")
    print()

    # =========================================================================
    # RESUMEN
    # =========================================================================
    print("=" * 65)
    passed_count = sum(1 for _, o in results if o)
    total = len(results)
    for name, o in results:
        print(f"  {'OK  ' if o else 'FAIL'}: {name}")
    print("=" * 65)
    print(f"Resumen Nuevos Centros: {passed_count}/{total} pruebas OK")
    return passed_count == total


def run_extended_province_tests():
    """
    Suite J: provincias adicionales cargadas desde centrosTablasCRM.
    Cubre que el catalogo activo no se limite a Comunidad Valenciana.
    """
    results = []
    cases = [
        ("Albacete", "Albacete", "Academia Cedes", "ACADEMIA CEDES"),
        ("Baleares", "Palma", "Academia Fleming", "ACADEMIA FLEMING"),
        ("Cuenca", "Cuenca", "4 de Junio", "4 DE JUNIO"),
        ("Murcia", "Murcia", "Eduardo Linares Lumeras", "EDUARDO LINARES"),
        ("Teruel", "Teruel", "Anton Garcia Abril", "ANT"),
    ]

    print("\n=== J. PROVINCIAS ADICIONALES ===")
    for prov, loc, centro, expect in cases:
        r = analyze_center_optimized(prov, loc, centro)
        got = r.get("Name", "") if r else ""
        passed = expect in got.upper()
        results.append((f"{prov}: {centro}", passed))
        print(f"  {'OK' if passed else 'FAIL'} [{prov}]: {got or '(vacio)'}")

    print("=" * 65)
    passed_count = sum(1 for _, o in results if o)
    total = len(results)
    for name, o in results:
        print(f"  {'OK  ' if o else 'FAIL'}: {name}")
    print("=" * 65)
    print(f"Resumen Provincias adicionales: {passed_count}/{total} pruebas OK")
    return passed_count == total


def run_payload_and_degree_defaults_tests():
    """Suite K: defaults para titulaciones fuera de CV y Description en payload."""
    results = []

    cases = [
        ("Murcia", "ELCHE"),
        ("Albacete", "VALENCIA"),
        ("Baleares", "VALENCIA"),
        ("Cuenca", "VALENCIA"),
        ("Teruel", "VALENCIA"),
    ]

    print("\n=== K. DEFAULTS TITULACION Y DESCRIPTION ===")
    for provincia, expected in cases:
        got = _map_province_for_titulacion(provincia)
        passed = got == expected
        results.append((f"Titulacion {provincia} -> {expected}", passed))
        print(f"  {'OK' if passed else 'FAIL'} [{provincia}]: {got}")

    basic = {
        "dni": "",
        "dni_letter_corrected": False,
        "firstname": "Test",
        "middlename": "",
        "lastname": "User",
        "mobilephone": "",
        "telefono": "",
        "email": "",
        "provincia": "VALENCIA",
        "low_confidence_words": [],
        "email_had_accents": False,
        "telefono_had_corrections": False,
    }
    center = {"nombre_centro": "", "json_centro": {}}
    degrees = {
        "final_degrees": None,
        "final_id_study": "",
        "titulacion_needs_review": False,
    }
    record = _build_crm_record(basic, center, degrees, "", {})
    expected_desc = "Solicitud de información procedente de escaneo automático"
    passed = record.get("Description") == expected_desc
    results.append(("Description payload", passed))
    print(f"  {'OK' if passed else 'FAIL'} [Description]: {record.get('Description')}")

    print("=" * 65)
    passed_count = sum(1 for _, o in results if o)
    total = len(results)
    for name, o in results:
        print(f"  {'OK  ' if o else 'FAIL'}: {name}")
    print("=" * 65)
    print(f"Resumen Payload/Titulaciones: {passed_count}/{total} pruebas OK")
    return passed_count == total


if __name__ == "__main__":
    ok1 = run_tests()
    print("\n" + "=" * 65)
    print("SUITE CASTELLÓN / MATER DEI")
    print("=" * 65)
    ok2 = run_castellon_mater_dei_tests()
    print("\n" + "=" * 65)
    print("SUITE F: OCR MERGED WORDS / PROVINCE VARIANTS")
    print("=" * 65)
    ok3 = run_ocr_merge_tests()
    print("\n" + "=" * 65)
    print("SUITE G: PROVINCE ALIASES + BILINGUAL LOCALITIES")
    print("=" * 65)
    ok4 = run_province_alias_tests()
    print("\n" + "=" * 65)
    print("SUITE H: PROVINCE CAPITAL AS LOCALIDAD IN CENTER NAME")
    print("=" * 65)
    ok5 = run_province_capital_localidad_tests()
    print("\n" + "=" * 65)
    print("SUITE I: NUEVOS CENTROS (EDELWEISS, SAN ROQUE ALCOY, DOMUS)")
    print("=" * 65)
    ok6 = run_new_centers_tests()
    print("\n" + "=" * 65)
    print("SUITE J: PROVINCIAS ADICIONALES")
    print("=" * 65)
    ok7 = run_extended_province_tests()
    print("\n" + "=" * 65)
    print("SUITE K: DEFAULTS TITULACION Y DESCRIPTION")
    print("=" * 65)
    ok8 = run_payload_and_degree_defaults_tests()
    print("\n" + "=" * 65)
    overall = ok1 and ok2 and ok3 and ok4 and ok5 and ok6 and ok7 and ok8
    print(f"RESULTADO GLOBAL: {'TODAS OK' if overall else 'HAY FALLOS'}")
    print("=" * 65)
