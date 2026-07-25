#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Заполняет drug_catalog взрослыми дозами по КП МЗ РБ №768 (из terminology.ADULT_DOSES)
+ опционально текст инструкций из openFDA.

Каталог — справочник для формы (название, доза, маршрут).
Выбор «какую АБТ» — docs/protocols/cap_abt_rules.yaml, не этот скрипт.

Запуск:
  python3 tools/seed_drug_catalog.py           # только кураторские взрослые дозы
  python3 tools/seed_drug_catalog.py --fda     # + openFDA (нужен интернет)
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fhir_store as fs
from terminology import ADULT_DOSES, ATC_DRUGS, ATC_GROUPS, atc_group


# ATC → (search_name_en, category, default_frequency, note)
# default_dose берётся из ADULT_DOSES (фиксированная взрослая), не мг/кг.
CURATED_META = {
    "J01CA04": ("amoxicillin", "antibiotic_outpatient", "3 раза в день", "амбулаторно, без факторов риска"),
    "J01CR02": ("amoxicillin clavulanate", "antibiotic_outpatient", "2–3 раза в день", "факторы риска / аспирация"),
    "J01FA09": ("clarithromycin", "antibiotic_outpatient", "2 раза в день", "макролид"),
    "J01FA10": ("azithromycin", "antibiotic_outpatient", "1 раз в день", "макролид при IgE-аллергии"),
    "J01DC02": ("cefuroxime", "antibiotic_outpatient", "3 раза в день", "не-IgE реакция на β-лактамы"),
    "J01AA02": ("doxycycline", "antibiotic_outpatient", "2 раза в день", "атипичная этиология"),
    "J01DD04": ("ceftriaxone", "antibiotic_inpatient", "1 раз в день", "цефалоспорин III в/в"),
    "J01DD01": ("cefotaxime", "antibiotic_inpatient", "2–3 раза в день", "цефалоспорин III в/в"),
    "J01XA01": ("vancomycin", "antibiotic_inpatient", "2 раза в день", "MRSA"),
    "J01XX08": ("linezolid", "antibiotic_inpatient", "2 раза в день", "MRSA"),
    "J01DH02": ("meropenem", "antibiotic_inpatient", "3 раза в день", "аспирация / резерв"),
    "J01DH03": ("ertapenem", "antibiotic_inpatient", "1 раз в день", "аспирация / резерв"),
    "J01DH51": ("imipenem cilastatin", "antibiotic_inpatient", "3–4 раза в день", "резерв"),
    "J01MA12": ("levofloxacin", "antibiotic_inpatient", "1 раз в день", "резерв: респираторный фторхинолон"),
    "J01MA14": ("moxifloxacin", "antibiotic_inpatient", "1 раз в день", "резерв: респираторный фторхинолон"),
    "J01GB06": ("amikacin", "antibiotic_inpatient", "1 раз в день", "грам(-) флора"),
    "J01XD01": ("metronidazole", "antibiotic_inpatient", "каждые 8 ч", "аспирация"),
    "J05AH02": ("oseltamivir", "antiviral", "2 раза в день", "подозрение на грипп"),
    # Симптоматика — без ADULT_DOSES, фиксированные взрослые значения здесь
    "R05CB01": ("acetylcysteine", "symptomatic", "3 раза в день", "муколитик"),
    "R05CB02": ("ambroxol", "symptomatic", "3 раза в день", "муколитик"),
    "R05CB03": ("carbocisteine", "symptomatic", "2 раза в день", "муколитик"),
    "R03AC02": ("salbutamol", "symptomatic", "по потребности", "бронходилататор"),
    "R03AK03": ("fenoterol ipratropium", "symptomatic", "по потребности", "бронходилататор"),
    "R03DA05": ("aminophylline", "symptomatic", "2 раза в день", "ксантин"),
    "H02AB06": ("prednisolone", "symptomatic", "1 раз в день", "ГКС"),
    "H02AB04": ("methylprednisolone", "symptomatic", "1 раз в день", "ГКС"),
}

# Симптоматика / препараты без записи в ADULT_DOSES: (route, dose_note, default_dose, max_daily_mg)
SYMPTOMATIC_DOSES = {
    "R05CB01": ("oral", "ацетилцистеин 200 мг 3 р/сут", "200 мг", 600),
    "R05CB02": ("oral", "амброксол 30 мг 3 р/сут", "30 мг", 120),
    "R05CB03": ("oral", "карбоцистеин 750 мг 2 р/сут", "750 мг", 1500),
    "R03AC02": ("inh", "сальбутамол 100–200 мкг ингаляционно по потребности", "100–200 мкг", None),
    "R03AK03": ("inh", "фенотерол/ипратропиум 1 доза ингаляционно по потребности", "1 доза", None),
    "R03DA05": ("iv", "аминофиллин по схеме стационара", "по схеме", None),
    "H02AB06": ("oral", "преднизолон по показаниям", "по показаниям", None),
    "H02AB04": ("oral", "метилпреднизолон по показаниям", "по показаниям", None),
    "J05AH02": ("oral", "осельтамивир 75 мг 2 р/сут", "75 мг", 150),
}


def _join(val):
    if not val:
        return None
    if isinstance(val, list):
        return "\n".join(val)
    return str(val)


def _fetch_label(search_name):
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ctx),
    )
    base = "https://api.fda.gov/drug/label.json"
    q = urllib.parse.quote('openfda.generic_name:"%s"' % search_name)
    try:
        with opener.open("%s?search=%s&limit=1" % (base, q), timeout=10) as resp:
            data = json.load(resp)
    except Exception:
        try:
            q2 = urllib.parse.quote('openfda.brand_name:"%s"' % search_name)
            with opener.open("%s?search=%s&limit=1" % (base, q2), timeout=10) as resp:
                data = json.load(resp)
        except Exception:
            return None
    results = (data or {}).get("results", [])
    if not results:
        return None
    r = results[0]
    of = r.get("openfda", {}) or {}
    return {
        "generic_name": _join(of.get("generic_name")),
        "dosage_form": _join(of.get("dosage_form")),
        "indications": _join(r.get("indications_and_usage")),
        "contraindications": _join(r.get("contraindications")),
        "interactions": _join(r.get("drug_interactions")),
        "pregnancy": _join(r.get("pregnancy")),
        "dosage_text": _join(r.get("dosage_and_administration")),
    }


def _short_dose(dose_note: str) -> str:
    """Короткая строка для поля формы из полного текста дозы."""
    if not dose_note:
        return ""
    # «амоксициллин 500 мг 3 р/сут» → «500 мг»
    parts = dose_note.split()
    for i, p in enumerate(parts):
        if p.replace(",", ".").replace(".", "", 1).isdigit() or any(c.isdigit() for c in p):
            # взять число + следующая единица, если есть
            chunk = [p]
            if i + 1 < len(parts) and parts[i + 1] in ("мг", "г", "мкг", "мг/кг"):
                chunk.append(parts[i + 1])
            return " ".join(chunk)
    return dose_note[:40]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fda", action="store_true", help="подтянуть тексты openFDA")
    args = ap.parse_args()

    fs.init_db()
    # Очистить устаревшие поля каталога, если колонки ещё есть в старой схеме
    try:
        db_mod = __import__("db")
        for col in ("dose_mg_per_kg", "dose_unit", "protocol_role"):
            if db_mod._has_column("drug_catalog", col):
                db_mod.execute(f"UPDATE drug_catalog SET {col} = NULL")
    except Exception:
        pass

    ok = fetched = 0
    codes = sorted(set(ADULT_DOSES) | set(CURATED_META) | set(SYMPTOMATIC_DOSES))
    # Убрать хвосты старого каталога, которых нет в текущем сиде
    try:
        existing = {r["atc_code"] for r in fs.get_drug_catalog()}
        stale = existing - set(codes)
        for atc in sorted(stale):
            db_mod = __import__("db")
            db_mod.execute("DELETE FROM drug_catalog WHERE atc_code = %s", (atc,))
            print("  − удалён устаревший: %s" % atc)
    except Exception:
        pass
    for atc in codes:
        meta = CURATED_META.get(atc)
        if not meta:
            continue
        search_name, category, default_freq, note = meta
        name_ru = ATC_DRUGS.get(atc, atc)
        prefix, group_ru = atc_group(atc)
        group_ru = group_ru or ATC_GROUPS.get(prefix or "", "")

        if atc in ADULT_DOSES:
            route, dose_note, _min_mg, max_mg = ADULT_DOSES[atc]
            form = route
            routes = route
            default_dose = _short_dose(dose_note)
        else:
            route, dose_note, default_dose, max_mg = SYMPTOMATIC_DOSES[atc]
            form = route
            routes = route

        label = _fetch_label(search_name) if args.fda else None
        if label:
            fetched += 1
            print("  + openFDA: %s" % name_ru)
        else:
            print("  · %s (%s)" % (name_ru, atc))

        fs.upsert_drug_catalog_entry(
            atc_code=atc,
            name=name_ru,
            generic_name=(label or {}).get("generic_name") if label else None,
            group_name=group_ru,
            dosage_form=(label or {}).get("dosage_form") if label else None,
            form=form,
            route_options=routes,
            indications=(label or {}).get("indications") if label else None,
            contraindications=(label or {}).get("contraindications") if label else None,
            interactions=(label or {}).get("interactions") if label else None,
            pregnancy=(label or {}).get("pregnancy") if label else None,
            dosage_text=(label or {}).get("dosage_text") if label else None,
            dose_note=dose_note,
            frequency=default_freq,
            max_daily_mg=max_mg,
            default_dose=default_dose,
            default_frequency=default_freq,
            protocol_ref="КП №768",
            note=note,
            category=category,
            verify_flag=0,
        )
        ok += 1

    print("\nГотово: %d препаратов (взрослые дозы КП №768), openFDA: %d." % (ok, fetched))
    print("Всего в каталоге: %d" % len(fs.get_drug_catalog()))
    print("Правила выбора АБТ: docs/protocols/cap_abt_rules.yaml")


if __name__ == "__main__":
    main()
