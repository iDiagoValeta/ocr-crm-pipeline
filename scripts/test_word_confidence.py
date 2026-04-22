"""
Tests para _compute_word_confidence_stats.
Ejecutar: python -m scripts.test_word_confidence
"""
import logging
logging.disable(logging.CRITICAL)

from procesa_ficha import (
    _compute_word_confidence_stats,
    WORD_CONFIDENCE_THRESHOLD,
)


def ok(name, result, expected_total_words, expected_low_count=None, expected_low_words=None):
    passed = True
    if result["total_words"] != expected_total_words:
        print(f"  FAIL total_words: got {result['total_words']}, expected {expected_total_words}")
        passed = False
    if expected_low_count is not None:
        actual_low = len(result["low_confidence_words"])
        if actual_low != expected_low_count:
            print(f"  FAIL low_confidence count: got {actual_low}, expected {expected_low_count}")
            passed = False
    if expected_low_words is not None:
        actual_words = [w["word"] for w in result["low_confidence_words"]]
        if actual_words != expected_low_words:
            print(f"  FAIL low_confidence words: got {actual_words}, expected {expected_low_words}")
            passed = False
    status = "OK" if passed else "FAIL"
    print(f"[{status}] {name}")
    return passed


def make_ocr_result(word_confs, handwritten_content=""):
    """Crea un resultado OCR simulado con las confianzas dadas."""
    words = [{"content": f"word{i}", "confidence": c} for i, c in enumerate(word_confs)]
    return {"pages": [{"words": words}], "handwritten_content": handwritten_content}


def run_tests():
    all_passed = True

    ocr = make_ocr_result([0.95, 0.92, 0.88, 0.99, 0.85])
    stats = _compute_word_confidence_stats(ocr)
    all_passed &= ok("Alta confianza -> 0 palabras dudosas", stats, 5, 0)

    ocr = make_ocr_result([0.30, 0.45, 0.55, 0.60, 0.65])
    stats = _compute_word_confidence_stats(ocr)
    all_passed &= ok("Baja confianza -> 5 palabras dudosas", stats, 5, 5)

    ocr = {"pages": [{"words": []}]}
    stats = _compute_word_confidence_stats(ocr)
    all_passed &= ok("Sin palabras -> total=0", stats, 0, 0)

    ocr = make_ocr_result([WORD_CONFIDENCE_THRESHOLD] * 4)
    stats = _compute_word_confidence_stats(ocr)
    all_passed &= ok(f"Justo en umbral {WORD_CONFIDENCE_THRESHOLD:.2f} -> 0 bajas", stats, 4, 0)

    ocr = make_ocr_result([0.95, 0.95, 0.95, 0.40])
    stats = _compute_word_confidence_stats(ocr)
    all_passed &= ok("Mezcla alta/baja -> 1 baja", stats, 4, 1)

    ocr = {}
    stats = _compute_word_confidence_stats(ocr)
    all_passed &= ok("OCR sin 'pages' -> total=0", stats, 0, 0)

    ocr = make_ocr_result([WORD_CONFIDENCE_THRESHOLD - 0.01, WORD_CONFIDENCE_THRESHOLD, 0.95])
    stats = _compute_word_confidence_stats(ocr)
    all_passed &= ok("Una palabra justo debajo del umbral -> 1 baja", stats, 3, 1, ["word0"])

    ocr = {
        "handwritten_content": "Nombre Sofia",
        "pages": [{
            "words": [
                {"content": "Nombre", "confidence": 0.50},
                {"content": "Sofia", "confidence": 0.45},
                {"content": "LOPD", "confidence": 0.20},
            ]
        }]
    }
    stats = _compute_word_confidence_stats(ocr)
    all_passed &= ok(
        "Filtra baja confianza fuera de manuscrito",
        stats, 3, 2, ["Nombre", "Sofia"]
    )

    ocr = make_ocr_result([0.30, 0.45, 0.55, 0.60, 0.65])
    stats = _compute_word_confidence_stats(ocr)
    assert stats["mean_confidence"] == round((0.30 + 0.45 + 0.55 + 0.60 + 0.65) / 5, 3), \
        f"Mean confidence mismatch: {stats['mean_confidence']}"
    assert stats["min_confidence"] == 0.3, \
        f"Min confidence mismatch: {stats['min_confidence']}"
    print("[OK] Valores numericos correctos (media, min)")

    return all_passed


if __name__ == "__main__":
    print(f"Umbral WORD={WORD_CONFIDENCE_THRESHOLD}")
    print("=" * 55)
    passed = run_tests()
    print("=" * 55)
    print(f"RESULTADO: {'TODAS OK' if passed else 'HAY FALLOS'}")
