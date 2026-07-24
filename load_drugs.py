"""
Загрузка кэша справочника лекарств из openFDA Drug Label API.

openFDA — бесплатный REST API (https://api.fda.gov), без ключа (40 запросов/мин).
Возвращает структурированные разделы инструкций: indications_and_usage,
contraindications, drug_interactions, pregnancy.

Скрипт пробегает список часто встречающихся кардио/эндо препаратов,
забирает их инструкции из openFDA и кладёт в таблицу medication_knowledge.
Дальше система работает с нашим кэшем — внешние вызовы на каждый запрос
врача не нужны (латентность, лимиты, зависимость).

Запуск:  python3 load_drugs.py
(нужен исходящий интернет; без ключа — 40 запросов/мин, для этого списка хватит)
"""
import json
import urllib.request
import urllib.parse

import fhir_store as fs

# (ATC-код, название для поиска в openFDA)
# openFDA — американские данные, ищем по генерическому имени (англ.).
_DRUGS = [
    ("C09AA01", "enalapril"),
    ("C09AA02", "ramipril"),
    ("C09AA03", "lisinopril"),
    ("C09CA01", "losartan"),
    ("C09CA02", "valsartan"),
    ("C07AB02", "bisoprolol"),
    ("C07AB05", "metoprolol"),
    ("C08CA01", "amlodipine"),
    ("C08CA02", "felodipine"),
    ("C03AA03", "hydrochlorothiazide"),
    ("C03DA01", "spironolactone"),
    ("C10AA01", "simvastatin"),
    ("C10AA02", "atorvastatin"),
    ("A10BA02", "metformin"),
]


def _fetch_label(drug_name):
    """Ищет инструкцию по генерическому имени. Возвращает dict с разделами или None."""
    q = urllib.parse.quote(f'openfda.generic_name:"{drug_name}"')
    url = f"https://api.fda.gov/drug/label.json?search={q}&limit=1"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.load(resp)
    except Exception as e:
        print(f"  ! не удалось получить {drug_name}: {e}")
        return None
    results = data.get("results", [])
    if not results:
        # fallback: поиск по brand_name
        q2 = urllib.parse.quote(f'openfda.brand_name:"{drug_name}"')
        url2 = f"https://api.fda.gov/drug/label.json?search={q2}&limit=1"
        try:
            with urllib.request.urlopen(url2, timeout=10) as resp:
                data = json.load(resp)
            results = data.get("results", [])
        except Exception:
            return None
    if not results:
        return None
    r = results[0]
    return {
        "indications": _join(r.get("indications_and_usage")),
        "contraindications": _join(r.get("contraindications")),
        "interactions": _join(r.get("drug_interactions")),
        "pregnancy": _join(r.get("pregnancy")),
    }


def _join(val):
    if not val:
        return None
    if isinstance(val, list):
        return "\n".join(val)
    return str(val)


def main():
    fs.init_db()
    ok = 0
    for atc, name in _DRUGS:
        print(f"loading {name} ({atc}) ...")
        label = _fetch_label(name)
        if not label:
            print("  — нет данных, пропускаю")
            continue
        fs.upsert_medication_knowledge(
            atc_code=atc, name=name,
            indications=label["indications"],
            contraindications=label["contraindications"],
            interactions=label["interactions"],
            pregnancy_category=label["pregnancy"],
        )
        ok += 1
    print(f"\nГотово: загружено {ok} из {len(_DRUGS)} препаратов в кэш medication_knowledge.")


if __name__ == "__main__":
    main()
