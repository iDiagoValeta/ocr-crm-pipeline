from procesa_ficha import analyze_center_optimized, _find_province_key

cases = [
    # PUIG / PUIG DE SANTA MARIA variants
    ("VALENCIA.", "EL PLUG DE STA MARIA", "COLEGIO SANTA MARIA DE EL PLIG"),
    ("COMUNIDAD VALENCIANA", "EL PUIG", "Colegio Santamaria del Puis"),
    ("VALENCIA", "PUIG DE SANTA MARIA", "SANTA MARIA DE EL PUIG"),

    # 9 D'OCTUBRE variants
    ("VALENCIA", "ALCASSER", "9 D'OCTUBRE"),
    ("VALENCIA", "ALCASSER", "9 DOCTUBRE"),
    ("VALENCIA", "ALCASSER", "9 D OCTUBRE"),

    # BLASCO IBÁÑEZ variants
    ("VALENCIA", "CULLERA", "BLASCO IBAÑEZ - CULLERA"),
    ("VALENCIA", "CULLERA", "BLASCO IBNZ"),
    ("VALENCIA", "VALENCIA", "BLASCO IBANEZ"),

    # ANTONIO MACHADO variants (VLC / XIRIVELLA)
    ("VALENCIA", "VLC", "ANTONIO MACHADO (VLC)"),
    ("ALICANTE", "ELDA", "ANTONIO MACHADO (ELDA)"),

    # ELCHE / ELX variants
    ("VALENCIA", "ELCHE", "IES ELX"),
    ("VALENCIA", "ELCHE", "IES ELCH"),
    ("VALENCIA", "ELX", "CENTRO EDUCATIVO IES ELX"),

    # Province misspellings
    ("VAL",
     "VALENCIA",
     "AUSIAS MARCH"),
    ("VALÈNCIA",
     "PICASSENT",
     "AUSIÀS MARCH"),

    # Random OCR-like corruptions
    ("CASTELLON",
     "VILLARREAL",
     "BOTANIC CALDUCH"),
    ("CASTELLÓN",
     "VILLARREAL",
     "BOTÀNIC CALDUCH"),

    # DIOCESANO MATER DEI (CASTELLON): correcto + variantes incorrectas
    ("CASTELLON", "CASTELLON", "DIOCESANO MATER DEI"),
    ("CASTELLON", "CASTELLON", "MATER DEI"),
    ("CASTELLON", "CASTELLON", "MATERDEI"),
    ("CASTELLON", "CASTELLON", "DIOCESANO MTR DEI"),
    ("CASTELLON", "CASTELLON", "DIOCESANO MADER DEI"),
    ("CASTELLON", "CASTELLON", "DIOSESANO MATER DEI"),
    ("CASTELLON", "CASTELLON", "DIOCESANO MATER DAY"),
    ("CASTELLON", "CASTELLON", "MTR DEI"),
    ("CASTELLÓN", "CASTELLÓN", "DIOCESANO MATER DEI"),
    ("CASTELLON.", "CASTELLON", "MATER DEI"),
    ("CAST", "CASTELLON", "MATER DEI"),
    ("CASTELLON", "CASTEYON", "MATER DEI"),
    ("CASTELLON", "CASTELLO", "MATER DEI"),
    ("COMUNIDAD VALENCIANA", "CASTELLON", "MATER DEI"),
]

print('\n=== Misspelling test cases ===\n')

for prov, loc, center in cases:
    prov_key = _find_province_key(prov)
    print(f"Input province: '{prov}' (resolved: {prov_key}), locality: '{loc}', center: '{center}'")
    res = analyze_center_optimized(prov, loc, center)
    print("=>", res)
    print('-' * 60)

print('\nDone.')
