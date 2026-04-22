"""
Tests de matching de cursos.
Ejecutar: python -m scripts.test_course_matching
"""
import logging
logging.disable(logging.CRITICAL)

from procesa_ficha import analyze_curso_local


def check(results, name, got, expected):
    passed = got == expected
    results.append((name, passed))
    status = "OK" if passed else "FAIL"
    print(f"  {status} {name}")
    if not passed:
        print(f"      got      : '{got}'")
        print(f"      expected : '{expected}'")


def run_tests():
    results = []

    bach_1 = analyze_curso_local("1º Bach.")
    check(results, "Curso exacto 1 Bach devuelve ID", bool(bach_1), True)
    check(results, "Input vacio no devuelve curso", analyze_curso_local(""), "")
    check(results, "Texto no academico no fuerza curso por fallback",
          analyze_curso_local("telefono email centro"), "")

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print("=" * 55)
    print(f"RESULTADO: {passed}/{total} tests pasados")
    return passed == total


if __name__ == "__main__":
    success = run_tests()
    exit(0 if success else 1)
