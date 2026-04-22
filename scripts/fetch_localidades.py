"""
Script para obtener las localidades por provincia desde la API del CRM CEU.
Ejecutar desde la red CEU (intranet) para que las URLs sean accesibles.

Los IdProvince deben coincidir con localidades/provinceIds.json.

Uso: python scripts/fetch_localidades.py
"""
import json
import os
import urllib.request

API_BASE = "https://appsintranet.ceu.es/Ceu.Crm.WebApi/api/cities"
PROVINCIAS = {
    "VALENCIA": "7e5130de-eed1-e311-bdd3-d89d6763fc38",
    "ALICANTE": "ebc435d8-eed1-e311-bdd3-d89d6763fc38",
    "CASTELLON": "ddc435d8-eed1-e311-bdd3-d89d6763fc38",
    "MURCIA": "0fc535d8-eed1-e311-bdd3-d89d6763fc38",
    "ALBACETE": "e3c435d8-eed1-e311-bdd3-d89d6763fc38",
    "TERUEL": "11c535d8-eed1-e311-bdd3-d89d6763fc38",
    "CUENCA": "05c535d8-eed1-e311-bdd3-d89d6763fc38",
    "BALEARES": "13c535d8-eed1-e311-bdd3-d89d6763fc38",
}

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "localidades")


def fetch_cities(province_id: str) -> list:
    url = f"{API_BASE}/{province_id}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    total = 0
    for prov_name, prov_id in PROVINCIAS.items():
        print(f"Fetching {prov_name}...")
        cities = fetch_cities(prov_id)
        data = [
            {
                "Name": c.get("Name", ""),
                "Name_cat": c.get("Name_cat", ""),
                "IdCity": c.get("IdCity", ""),
                "IdProvince": c.get("IdProvince", ""),
            }
            for c in cities
        ]
        out_path = os.path.join(OUTPUT_DIR, f"{prov_name}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  -> {len(data)} localidades en {out_path}")
        total += len(data)
    print(f"\nTotal: {total} localidades en {len(PROVINCIAS)} archivos")


if __name__ == "__main__":
    main()
