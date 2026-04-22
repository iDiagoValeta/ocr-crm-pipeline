from procesa_ficha import _normalize_dni_nie
import re

cases = [
    # Correct DNIs
    "12345678Z",
    "12345678z",
    # DNI with final digit instead of letter
    "123456785",
    "12 345 678 5",
    # NIE correct and with final digit
    "X1234567L",
    "x12345675",
    # OCR-like confusions in body (O->0, I->1, etc.)
    "I2345678Z",  # I -> 1 at start of body for DNI (not NIE)
    "O2345678Z",  # O->0
    # Example from logs (ambiguous): try various splits
    "2391967045",
    "23919670S",
    "23919670 45",
]

LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def expected_letter_for(raw: str) -> str:
    s = re.sub(r"[\s\-.]", "", raw.upper())
    if not s:
        return ""
    is_nie = s[0] in ("X", "Y", "Z")
    # extract body digits
    if is_nie:
        body = ''.join(ch for ch in s[1:-1] if ch.isdigit())
        if len(body) != 7:
            return ""
        prefix_map = {"X": "0", "Y": "1", "Z": "2"}
        num = prefix_map.get(s[0], '0') + body
    else:
        body = ''.join(ch for ch in s[:-1] if ch.isdigit())
        if len(body) != 8:
            return ""
        num = body
    try:
        idx = int(num) % 23
        return LETTERS[idx]
    except Exception:
        return ""


if __name__ == '__main__':
    print("=== DNI/NIE normalization tests ===\n")
    for c in cases:
        out = _normalize_dni_nie(c)
        exp = expected_letter_for(c)
        print(f"Input: '{c}' -> Normalized: '{out}' | Expected letter (if computable): '{exp}'")
    print('\nDone.')
