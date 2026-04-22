import logging
logging.disable(logging.CRITICAL)

from procesa_ficha import GlobalDataManager, _select_best_variant_by_province

GlobalDataManager.load()

cases = [
    ("CASTELLON",  "EDUCACION INFANTIL"),
    ("CASTELLON",  "EDUCACION PRIMARIA"),
    ("CASTELLON",  "EDUCACION PRIMARIA + EDUCACION INFANTIL"),
    ("ALICANTE",   "EDUCACION INFANTIL"),
    ("VALENCIA",   "EDUCACION INFANTIL"),
    ("",           "EDUCACION INFANTIL"),
]

print(f"{'Provincia':<15} {'Titulación buscada':<45} {'Variante seleccionada'}")
print("-" * 90)
for prov, tit in cases:
    name, tid = _select_best_variant_by_province(tit, prov)
    print(f"{prov or '(vacío)':<15} {tit:<45} {name}")
