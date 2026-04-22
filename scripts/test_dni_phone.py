"""
Tests de normalización de DNI/NIE y teléfono.

Ejecutar:
    conda run -n fichas --no-capture-output python -m scripts.test_dni_phone
"""
import logging
logging.disable(logging.CRITICAL)

from procesa_ficha import _extract_basic_fields, _normalize_dni_nie, _normalize_phone

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def control_letter_dni(digits_8: str) -> str:
    return LETTERS[int(digits_8) % 23]


def control_letter_nie(prefix: str, digits_7: str) -> str:
    prefix_map = {"X": "0", "Y": "1", "Z": "2"}
    return LETTERS[int(prefix_map[prefix] + digits_7) % 23]


def check(results: list, name: str, got, expected):
    passed = got == expected
    results.append((name, passed))
    status = "✓" if passed else "✗"
    print(f"  {status} {name}")
    if not passed:
        print(f"      got      : '{got}'")
        print(f"      expected : '{expected}'")


# ──────────────────────────────────────────────────────────────────────────────
# Suite A — DNI/NIE
# ──────────────────────────────────────────────────────────────────────────────

def run_dni_nie_tests() -> list:
    results = []

    # Pre-compute correct letters for two fixed bodies
    B1 = "12345678"
    L1 = control_letter_dni(B1)   # Z

    B2 = "87654321"
    L2 = control_letter_dni(B2)   # X

    print("\n=== Suite A — DNI/NIE ===\n")

    # --- Casos básicos ---
    print("--- Básicos ---")
    check(results, "DNI válido completo",
          _normalize_dni_nie(f"{B1}{L1}"), f"{B1}{L1}")
    check(results, "DNI vacío → ''",
          _normalize_dni_nie(""), "")
    check(results, "DNI None → ''",
          _normalize_dni_nie(None), "")

    # --- Limpieza de formato ---
    print("\n--- Limpieza de formato ---")
    check(results, "DNI con espacios",
          _normalize_dni_nie(f"12 345 678 {L1}"), f"{B1}{L1}")
    check(results, "DNI con puntos y guión",
          _normalize_dni_nie(f"12.345.678-{L1}"), f"{B1}{L1}")
    check(results, "DNI minúsculas → mayúsculas",
          _normalize_dni_nie(f"{B1}{L1.lower()}"), f"{B1}{L1}")

    # --- Corrección de letra de control ---
    print("\n--- Corrección letra de control ---")
    wrong = "A" if L1 != "A" else "B"
    check(results, "DNI letra alfabética inválida → conservada (Azure DI)",
          _normalize_dni_nie(f"{B1}{wrong}"), f"{B1}{wrong}")
    check(results, "DNI dígito en lugar de letra control → corregido",
          _normalize_dni_nie(f"{B1}5"), f"{B1}{L1}")

    # --- Corrección OCR en cuerpo numérico ---
    print("\n--- Corrección OCR en cuerpo ---")
    # I→1: I2345678 → 12345678, control letter recalculated
    check(results, "'I' → '1' en pos 0 del cuerpo",
          _normalize_dni_nie(f"I2345678{L1}"), f"{B1}{L1}")
    check(results, "'O' → '0' en pos 0 (letra conservada)",
          _normalize_dni_nie(f"O2345678{L1}"),
          f"02345678{L1}")
    check(results, "'S' → '5' en cuerpo",
          _normalize_dni_nie(f"1234S678{L1}"), f"{B1}{L1}")
    check(results, "'B' → '8' en cuerpo",
          _normalize_dni_nie(f"1234567B{L1}"), f"{B1}{L1}")
    check(results, "'Z' → '2' en cuerpo",
          _normalize_dni_nie(f"1Z345678{L1}"),
          f"12345678{control_letter_dni('12345678')}")

    # --- NIE prefijos X / Y / Z ---
    print("\n--- NIE ---")
    nie7 = "1234567"
    lx = control_letter_nie("X", nie7)
    ly = control_letter_nie("Y", nie7)
    lz = control_letter_nie("Z", nie7)

    check(results, "NIE X válido",
          _normalize_dni_nie(f"X{nie7}{lx}"), f"X{nie7}{lx}")
    check(results, "NIE Y válido",
          _normalize_dni_nie(f"Y{nie7}{ly}"), f"Y{nie7}{ly}")
    check(results, "NIE Z válido",
          _normalize_dni_nie(f"Z{nie7}{lz}"), f"Z{nie7}{lz}")
    check(results, "NIE minúsculas → mayúsculas",
          _normalize_dni_nie(f"x{nie7}{lx.lower()}"), f"X{nie7}{lx}")
    check(results, "NIE letra alfabética inválida → conservada (Azure DI)",
          _normalize_dni_nie(f"X{nie7}A"), f"X{nie7}A")
    check(results, "NIE X con OCR 'O'→'0' en cuerpo (letra conservada)",
          _normalize_dni_nie(f"X12345O7{lx}"),
          f"X1234507{lx}")

    return results


# ──────────────────────────────────────────────────────────────────────────────
# Suite B — Teléfono
# ──────────────────────────────────────────────────────────────────────────────

def run_phone_tests() -> list:
    results = []

    print("\n=== Suite B — Teléfono ===\n")

    # --- Casos básicos ---
    print("--- Básicos ---")
    check(results, "Móvil 6xx (9 dígitos)",
          _normalize_phone("612345678"), "612345678")
    check(results, "Móvil 7xx (9 dígitos)",
          _normalize_phone("712345678"), "712345678")
    check(results, "Fijo 9xx (9 dígitos)",
          _normalize_phone("912345678"), "912345678")
    check(results, "Vacío → ''",
          _normalize_phone(""), "")
    check(results, "Demasiado corto (5 dígitos) → ''",
          _normalize_phone("61234"), "")

    # --- Limpieza de formato ---
    print("\n--- Limpieza de formato ---")
    check(results, "Con espacios",
          _normalize_phone("612 345 678"), "612345678")
    check(results, "Con guiones",
          _normalize_phone("612-345-678"), "612345678")
    check(results, "Con paréntesis",
          _normalize_phone("(612)345678"), "612345678")
    check(results, "Con puntos",
          _normalize_phone("612.345.678"), "612345678")

    # --- Prefijos internacionales ---
    print("\n--- Prefijos internacionales ---")
    check(results, "+34 preservado",
          _normalize_phone("+34612345678"), "+34612345678")
    check(results, "+34 con espacios",
          _normalize_phone("+34 612 345 678"), "+34612345678")
    check(results, "Francés +33 (9 dígitos nacionales)",
          _normalize_phone("+33612345678"), "+33612345678")
    check(results, "Italiano +39 (10 dígitos nacionales)",
          _normalize_phone("+390612345678"), "+390612345678")
    check(results, "+34 con OCR 'O'→'0'",
          _normalize_phone("+346O2345678"), "+34602345678")

    # --- Corrección OCR en dígitos ---
    print("\n--- Corrección OCR ---")
    check(results, "'O' → '0'",
          _normalize_phone("6O2345678"), "602345678")
    check(results, "'I' → '1'",
          _normalize_phone("6I2345678"), "612345678")
    check(results, "'S' → '5'",
          _normalize_phone("6S2345678"), "652345678")
    check(results, "'B' → '8'",
          _normalize_phone("612345B78"), "612345878")
    check(results, "'A' → '4'",
          _normalize_phone("6A2345678"), "642345678")
    check(results, "Múltiples OCR: 'O'→0 'A'→4 'G'→6",
          _normalize_phone("6O23A5G78"), "602345678")

    # --- Números inválidos ---
    print("\n--- Inválidos ---")
    check(results, "Letras sin mapeo → demasiado corto → ''",
          _normalize_phone("CFHIJKLMN"), "")
    check(results, "+34 muy corto (4 dígitos nacionales) → ''",
          _normalize_phone("+3461234"), "")
    check(results, "Solo ceros (9) → válido (sin prefijo)",
          _normalize_phone("000000000"), "000000000")

    return results


def run_basic_field_review_tests() -> list:
    results = []

    print("\n=== Suite C — Flags de revisión DNI ===\n")

    check(results, "DNI válido no marca corrección de letra",
          _extract_basic_fields({"dni": "12345678Z"}).get("dni_letter_corrected"), False)
    check(results, "DNI con letra alfabética inválida conserva valor pero marca revisión",
          _extract_basic_fields({"dni": "12345678A"}).get("dni_letter_corrected"), True)
    check(results, "DNI con dígito final calcula letra y marca revisión",
          _extract_basic_fields({"dni": "123456785"}).get("dni_letter_corrected"), True)

    return results


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("TEST: DNI/NIE y Teléfono")
    print("=" * 60)

    all_results = run_dni_nie_tests() + run_phone_tests() + run_basic_field_review_tests()

    passed = sum(1 for _, ok in all_results if ok)
    total = len(all_results)
    failed = [name for name, ok in all_results if not ok]

    print(f"\n{'=' * 60}")
    print(f"RESULTADO: {passed}/{total} tests pasados")
    if failed:
        print("\nFALLIDOS:")
        for name in failed:
            print(f"  ✗ {name}")
    else:
        print("¡Todos los tests pasaron!")
    print("=" * 60)
    return len(failed) == 0


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
