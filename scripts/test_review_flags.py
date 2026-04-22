"""Pruebas focalizadas de post-procesado de FieldsToReview."""
import logging

logging.disable(logging.CRITICAL)

from procesa_ficha import _build_crm_record


def _basic(email: str) -> dict:
    return {
        "firstname": "Ana",
        "middlename": "Garcia",
        "lastname": "Lopez",
        "provincia": "VALENCIA",
        "dni": "12345678Z",
        "email": email,
        "mobilephone": "+34612345678",
        "telefono": "612345678",
        "telefono_had_corrections": False,
        "email_had_accents": False,
        "dni_letter_corrected": False,
    }


def _center(strategy: str = "contains", score: float = 100, matched: bool = True) -> dict:
    json_centro = {}
    if matched:
        json_centro = {
            "Id": "center-id",
            "Name": "CENTRO TEST (VALENCIA)",
            "IdProvince": "province-id",
            "IdCity": "city-id",
            "IdCountry": "country-id",
            "_match_strategy": strategy,
            "_match_score": score,
        }
    return {
        "nombre_centro": "Centro Test",
        "json_centro": json_centro,
    }


def _degrees() -> dict:
    return {
        "final_degrees": None,
        "final_id_study": "",
        "titulacion_needs_review": False,
    }


def _record(
    email: str = "ana.garcia@example.com",
    fields: str = "",
    low_words=None,
    center=None,
) -> dict:
    datos = {
        "review_data": bool(fields),
        "fields_to_review": fields,
        "_low_confidence_words": low_words or [],
    }
    return _build_crm_record(_basic(email), center or _center(), _degrees(), "", datos)


def check(results: list, name: str, condition: bool, details: str = "") -> None:
    results.append((name, condition))
    status = "OK" if condition else "FAIL"
    print(f"  {status}: {name}")
    if not condition and details:
        print(f"      {details}")


def run_tests() -> bool:
    results = []

    r = _record(
        fields="Email",
        low_words=[{"word": "ana.garcia@example.com", "confidence": 0.65}],
    )
    check(results, "Email MEDIUM valido se retira", "Email" not in r["FieldsToReview"], r["FieldsToReview"])
    check(results, "ReviewData queda false si no hay otros flags", r["ReviewData"] is False, str(r["ReviewData"]))

    r = _record(
        fields="",
        low_words=[{"word": "ana.garcia@example.com", "confidence": 0.25}],
    )
    check(results, "Email HIGH asociado se conserva/anade", "Email" in r["FieldsToReview"], r["FieldsToReview"])

    r = _record(email="ana.garcia.example.com")
    check(results, "Email invalido se flaggea", "Email" in r["FieldsToReview"], r["FieldsToReview"])

    r = _record(fields="Nombre")
    check(results, "Nombre anade Apellidos", "Apellidos" in r["FieldsToReview"], r["FieldsToReview"])
    check(results, "Nombre no propaga Email", "Email" not in r["FieldsToReview"], r["FieldsToReview"])

    r = _record(fields="Centro", center=_center("exact_normalized", 100))
    check(results, "Centro exacto no se flaggea", "Centro" not in r["FieldsToReview"], r["FieldsToReview"])

    r = _record(fields="Centro", center=_center("contains", 100))
    check(results, "Centro contains no se flaggea", "Centro" not in r["FieldsToReview"], r["FieldsToReview"])

    r = _record(fields="", center=_center("locality_first", 79))
    check(results, "Centro locality-first bajo se flaggea", "Centro" in r["FieldsToReview"], r["FieldsToReview"])

    r = _record(fields="", center=_center("fuzzy_global", 79))
    check(results, "Centro fuzzy global bajo se flaggea", "Centro" in r["FieldsToReview"], r["FieldsToReview"])

    r = _record(fields="", center=_center("fallback_wratio", 79))
    check(results, "Centro fallback bajo se flaggea", "Centro" in r["FieldsToReview"], r["FieldsToReview"])

    r = _record(fields="", center=_center(matched=False))
    check(results, "OtherCenter no vacio se flaggea", "Centro" in r["FieldsToReview"], r["FieldsToReview"])

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print("=" * 55)
    print(f"Resumen review flags: {passed}/{total} pruebas OK")
    return passed == total


if __name__ == "__main__":
    print("=" * 55)
    print("TEST: Review flags Email/Centro")
    print("=" * 55)
    if not run_tests():
        raise SystemExit(1)
