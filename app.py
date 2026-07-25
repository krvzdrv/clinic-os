"""
Главная точка входа — Flask-приложение (UI-слой, Слой 7).

Слои под капотом:
  db.py            — подключение к БД (Supabase/SQLite)
  fhir_store.py    — репозиторий FHIR-ресурсов
  rules_engine.py  — клинические правила (Слой 3)
  drug_service.py  — проверка лекарств (Слой 2)
  protocol_cap.py  — регламент лечения ВП (Слой 3b, независимый)
  care_plan_service.py — план/цель/цикл (Слой 4)
  cds_service.py   — CDS-карточки (Слой 5)

UI отражает реальный процесс приёма: жалоба → осмотр/измерения → заказы
анализов → результаты → диагноз → назначение лечения (с проверкой) →
цель → контрольный визит → оценка результата → коррекция.

Все сценарии — по протоколу внебольничной пневмонии (КП МЗ РБ №768, взрослые).
"""
from dotenv import load_dotenv
load_dotenv()  # подгружает .env из корня проекта, если он есть
import os
from datetime import date

from flask import (
    Flask, render_template, jsonify, request, redirect, url_for, Response,
)

import fhir_store as fs
import rules_engine as re
import drug_service
import protocol_cap as pcap
import care_plan_service as cps
import cds_service as cds
from terminology import (
    LOINC, STUDIES, CLINICAL_FLAGS,
    loinc_display, loinc_unit, loinc_reference, interpret_value, sane_range,
    atc_display, study_display, study_category,
    ICD10, ALLERGEN_GROUPS, allergen_display,
    flag_category_display,
    EXAM_LOINC, LAB_LOINC, is_exam_loinc, is_lab_loinc,
    GENERAL_CONDITION, GENERAL_CONDITION_ORDER, general_condition_display, general_condition_needs_inpatient,
)

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
fs.init_db()


def _wants_json():
    """Запрос пришёл через fetch (AJAX), а не обычной отправкой формы."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest" or \
           "application/json" in (request.headers.get("Accept") or "")


def _ok(chip_html, **extra):
    """Успешный JSON-ответ для AJAX-формы: готовый HTML чипа для вставки в DOM."""
    payload = {"ok": True, "chip_html": chip_html}
    payload.update(extra)
    return jsonify(payload)


def _err(msg):
    return jsonify({"ok": False, "error": msg})


def _parse_numeric(raw):
    """Извлекает первое число из строки: «36,6» / «36.6 мм» / «38.5» → float.
    Текст после числа игнорируется; некорректный ввод → None."""
    if raw is None:
        return None
    s = raw.replace(",", ".").strip()
    buf = ""
    seen_dot = False
    for ch in s:
        if ch.isdigit():
            buf += ch
        elif ch == "." and not seen_dot and buf:
            buf += ch
            seen_dot = True
        elif ch == "-" and not buf:
            buf += ch
        elif buf:
            break
    try:
        return float(buf) if buf not in ("", "-", ".", "-.") else None
    except ValueError:
        return None


def _short_name(p):
    """ФИО с инициалами, безопасно к пустым имени/отчеству."""
    parts = [(p.get("family") or "").strip()]
    g = (p.get("given") or "").strip()
    pat = (p.get("patronymic") or "").strip()
    if g:
        parts.append(f"{g[0]}.")
    if pat:
        parts.append(f"{pat[0]}.")
    return " ".join(x for x in parts if x)


# ---------- Дашборд ----------
@app.route("/")
def dashboard():
    measure = re.quality_measure_cap()
    caches = {c["patient_id"]: c for c in fs.get_all_cap_caches()}
    pathways = fs.get_all_pathways()
    rows = []
    for p in fs.get_all_patients():
        pid = p["id"]
        c = caches.get(pid)
        applicable = bool(c and c["applicable"])
        rows.append({
            "id": pid,
            "name": _short_name(p),
            "full": f"{p.get('family','')} {p.get('given','')} {p.get('patronymic','')}".strip(),
            "age": fs.get_age(pid),
            "gender": p["gender"],
            "has_pneumonia": applicable,
            "severity": c["severity"] if c and c["applicable"] else None,
            "setting": c["setting"] if c and c["applicable"] else None,
            "compliant": bool(c["compliant"]) if c and c["applicable"] else None,
            "state_label": (pathways.get(pid) or {}).get("label") or "—",
        })

    # Сортировка по клиническому приоритету: кто требует внимания сейчас.
    sev_rank = {"severe": 0, "mild": 1, None: 2}
    def _priority(r):
        # тяжёлая ВП — выше всего
        s = 0 if r["severity"] == "severe" else (1 if r["severity"] == "mild" else 2)
        # отклонения от протокола поднимают вверх
        if r["compliant"] is False:
            s -= 1
        # стационарные чуть выше амбулаторных при равной тяжести
        if r["setting"] == "inpatient":
            s -= 0.5
        return (s, r["name"].lower())
    rows.sort(key=_priority)

    # Фильтры и поиск
    q = (request.args.get("q", "") or "").strip().lower()
    f_sev = request.args.get("severity", "")
    f_set = request.args.get("setting", "")
    f_com = request.args.get("compliant", "")
    if q:
        rows = [r for r in rows if q in r["full"].lower() or q in r["id"].lower()]
    if f_sev in ("severe", "mild"):
        rows = [r for r in rows if r["severity"] == f_sev]
    if f_set in ("inpatient", "ambulatory"):
        rows = [r for r in rows if r["setting"] == f_set]
    if f_com == "no":
        rows = [r for r in rows if r["compliant"] is False]
    elif f_com == "yes":
        rows = [r for r in rows if r["compliant"] is True]

    return render_template("dashboard.html", measure=measure, patients=rows,
                           q=q, f_sev=f_sev, f_set=f_set, f_com=f_com,
                           total=len(caches),
                           demo_mode=os.environ.get("DEMO_MODE", "") == "1")


# ---------- Новый пациент ----------
@app.route("/patient/new", methods=["GET", "POST"])
def new_patient():
    if request.method == "POST":
        pid = fs.add_patient(
            family=request.form.get("family", "").strip(),
            given=request.form.get("given", "").strip(),
            patronymic=request.form.get("patronymic", "").strip(),
            gender=request.form.get("gender", "male"),
            birth_date=request.form.get("birth_date") or None,
        )
        return redirect(url_for("patient_detail", pid=pid))
    return render_template("new_patient.html")


# ---------- Карта пациента (рабочее место врача) ----------
@app.route("/patient/<pid>")
def patient_detail(pid):
    p = fs.get_patient(pid)
    if not p:
        return "Пациент не найден", 404
    fs.load_pid_cache(pid)
    try:
        cap = pcap.evaluate_cap(pid)
        fs.save_cap_cache(pid, cap)
        goals = fs.get_goals(pid)
        care_plans = fs.get_care_plans(pid)

        # Группировка ресурсов по приёму (encounter_id) — лента приёмов.
        encounters = fs.get_encounters(pid)  # уже ORDER BY start DESC
        enc_by_id = {e["id"]: e for e in encounters}
        buckets = {e["id"]: {"obs": [], "sr": [], "rep": [], "med": [], "flag": [], "cond": []} for e in encounters}
        unassigned = {"obs": [], "sr": [], "rep": [], "med": [], "flag": [], "cond": []}

        def _place(item, eid, key):
            if eid and eid in buckets:
                buckets[eid][key].append(item)
            else:
                unassigned[key].append(item)

        for o in fs.get_observations(pid):
            _place(o, o.get("encounter_id"), "obs")
        for s in fs.get_service_requests(pid):
            _place(s, s.get("encounter_id"), "sr")
        for r in fs.get_diagnostic_reports(pid):
            _place(r, r.get("encounter_id"), "rep")
        for m in fs.get_all_medications(pid):
            _place(m, m.get("encounter_id"), "med")
        for f in fs.get_flags(pid):
            _place(f, f.get("encounter_id"), "flag")
        for c in fs.get_conditions(pid):
            _place(c, c.get("encounter_id"), "cond")

        encounters_data = []
        for e in encounters:
            b = buckets[e["id"]]
            encounters_data.append({"encounter": e, **b})

        has_unassigned = any(unassigned[k] for k in unassigned)

        return render_template(
            "patient.html",
            patient=p,
            full_name=f"{p['family']} {p['given']} {p['patronymic']}",
            age=fs.get_age(pid),
            condition=fs.get_condition(pid),
            conditions=fs.get_conditions(pid),
            observations=fs.get_observations(pid),
            reports=fs.get_diagnostic_reports(pid),
            service_requests=fs.get_service_requests(pid),
            meds=fs.get_medications(pid),
            allergies=fs.get_allergies(pid),
            flags=fs.get_flags(pid),
            encounters=encounters,
            encounters_data=encounters_data,
            unassigned=unassigned,
            has_unassigned=has_unassigned,
            followups=cps.get_followups(pid),
            care_plans=care_plans,
            goals=goals,
            pathway=fs.get_pathway(pid),
            cards=cds.cds_patient_view(pid),
            cap=cap,
            has_pneumonia=re.has_pneumonia(pid),
            diabetes=re.has_diabetes(pid),
            general_condition=re.general_condition(pid),
            loinc_options=sorted(LOINC.keys()),
            exam_loinc=EXAM_LOINC,
            lab_loinc=LAB_LOINC,
            is_exam_loinc=is_exam_loinc,
            is_lab_loinc=is_lab_loinc,
            general_condition_options=GENERAL_CONDITION,
            general_condition_order=GENERAL_CONDITION_ORDER,
            general_condition_display=general_condition_display,
            general_condition_needs_inpatient=general_condition_needs_inpatient,
            study_options=sorted(STUDIES.keys()),
            flag_options=CLINICAL_FLAGS,
            drug_catalog=fs.get_drug_catalog(),
            loinc_display=loinc_display,
            loinc_unit=loinc_unit,
            loinc_reference=loinc_reference,
            sane_range=sane_range,
            study_display=study_display,
            study_category=study_category,
            icd10=ICD10,
            allergen_groups=ALLERGEN_GROUPS,
            allergen_display=allergen_display,
            flag_category_display=flag_category_display,
            today=date.today().isoformat(),
        )
    finally:
        fs.clear_pid_cache(pid)


# ---------- Приём (encounter) ----------
@app.route("/patient/<pid>/encounter", methods=["POST"])
def add_encounter_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    fs.add_encounter(
        pid,
        practitioner_id=request.form.get("practitioner_id") or None,
        cls=request.form.get("class", "ambulatory"),
        start=request.form.get("start") or None,
        complaint=request.form.get("complaint", "").strip() or None,
        reason_code=request.form.get("reason_code", "").strip() or None,
    )
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Закрыть приём ----------
@app.route("/patient/<pid>/encounter/<eid>/finish", methods=["POST"])
def finish_encounter_route(pid, eid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    fs.finish_encounter(eid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Запись измерения/анализа (observation) ----------
@app.route("/patient/<pid>/observation", methods=["POST"])
def add_observation_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    code = request.form.get("code")
    value = _parse_numeric(request.form.get("value_numeric"))
    sr = sane_range(code)
    if value is None:
        if _wants_json():
            return _err("Некорректное значение")
        return redirect(url_for("patient_detail", pid=pid))
    if sr and (value < sr[0] or value > sr[1]):
        if _wants_json():
            lo, hi = sr
            return _err("Недопустимое значение. Допустимо: %s–%s %s" % (lo, hi, loinc_unit(code)))
        return redirect(url_for("patient_detail", pid=pid))
    low, high = loinc_reference(code)
    interp = interpret_value(code, value)
    oid = fs.add_observation(
        pid, code, loinc_display(code),
        value_numeric=value,
        value_unit=loinc_unit(code),
        ref_low=low,
        ref_high=high,
        interpretation=interp,
        obs_date=request.form.get("date") or None,
        encounter_id=request.form.get("encounter_id") or None,
    )
    # Если результат привязан к заказу исследования — отметим заказ выполненным.
    sid = request.form.get("service_request_id")
    if sid:
        fs.complete_service_request(sid)
    if _wants_json():
        arrow = ""
        if interp == "high":
            arrow = '<span class="flag-up">↑</span>'
        elif interp == "low":
            arrow = '<span class="flag-up">↓</span>'
        unit = loinc_unit(code) or ""
        chip = ('<span class="chip">%s %s%s %s'
                '<form method="POST" action="%s" style="display:inline;">'
                '<button class="chip-x" type="submit" title="Удалить" aria-label="Удалить">×</button>'
                '</form></span>') % (
            loinc_display(code), value, unit, arrow,
            url_for("delete_observation_route", pid=pid, oid=oid),
        )
        return _ok(chip, id=oid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Заказ исследования (service request) ----------
@app.route("/patient/<pid>/service_request", methods=["POST"])
def add_service_request_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    code = request.form.get("code")
    display = study_display(code) if code in STUDIES else loinc_display(code)
    sid = fs.add_service_request(
        pid, code, display,
        occurrence_date=request.form.get("occurrence_date") or None,
        reason_code=request.form.get("reason_code", "").strip() or None,
        encounter_id=request.form.get("encounter_id") or None,
    )
    if _wants_json():
        cat = study_category(code) if code in STUDIES else "lab"
        chip = ('<span class="it"><span class="st ord">заказ</span> %s'
                '<form method="POST" action="%s" style="display:inline;">'
                '<button class="chip-x" type="submit" title="Удалить" aria-label="Удалить">×</button>'
                '</form></span>') % (display, url_for("delete_service_request_route", pid=pid, sid=sid))
        return _ok(chip, id=sid, code=code, display=display, category=cat)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Результат исследования (diagnostic report) ----------
@app.route("/patient/<pid>/report", methods=["POST"])
def add_report_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    code = request.form.get("code")
    display = study_display(code)
    conclusion = request.form.get("conclusion", "").strip() or None
    rid = fs.add_diagnostic_report(
        pid, code, display,
        conclusion=conclusion,
        rep_date=request.form.get("date") or None,
        encounter_id=request.form.get("encounter_id") or None,
    )
    sid = request.form.get("service_request_id")
    if sid:
        fs.complete_service_request(sid)
    if _wants_json():
        cat = study_category(code)
        chip = ('<span class="it"><span class="st done">рез-т</span> %s%s'
                '<form method="POST" action="%s" style="display:inline;">'
                '<button class="chip-x" type="submit" title="Удалить" aria-label="Удалить">×</button>'
                '</form></span>') % (
            display, (': <span class="res">%s</span>' % conclusion) if conclusion else '',
            url_for("delete_report_route", pid=pid, rid=rid))
        return _ok(chip, id=rid, code=code, display=display, category=cat,
                   service_request_id=sid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Назначение препарата (с проверкой) ----------
@app.route("/patient/<pid>/medication", methods=["POST"])
def add_medication_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    code = request.form.get("code", "").strip().upper()
    drug = fs.get_drug(code) if code else None
    display = request.form.get("display", "").strip()
    if not display and drug:
        display = drug.get("name") or ""
    dose = request.form.get("dose", "").strip() or (drug.get("default_dose") if drug else None)
    frequency = request.form.get("frequency", "").strip() or (drug.get("default_frequency") if drug else None)
    # CDS: проверка препарата ДО сохранения. hard-stop (аллергия на класс,
    # противопоказание) — не сохраняем молча, требуем осознанного подтверждения.
    verdict = drug_service.evaluate_medication(pid, code)
    confirmed = request.form.get("confirm", "") == "1"
    if not verdict["safe"] and not confirmed:
        if _wants_json():
            return jsonify({"ok": False, "need_confirm": True, "cds": _cds_summary(verdict)})
        return redirect(url_for("patient_detail", pid=pid))
    dpd_raw = request.form.get("dose_per_day", "").strip()
    try:
        dpd = float(dpd_raw.replace(",", ".")) if dpd_raw else None
    except ValueError:
        dpd = None
    mid = fs.add_medication(
        pid, code, display,
        dose=dose,
        frequency=frequency,
        route=request.form.get("route", "").strip() or None,
        med_date=request.form.get("med_date") or None,
        period_end=request.form.get("period_end") or None,
        encounter_id=request.form.get("encounter_id") or None,
        dose_per_day=dpd,
    )
    if _wants_json():
        route_val = request.form.get("route", "").strip() or None
        chip = ('<span class="chip">%s%s%s'
                '<form method="POST" action="%s" style="display:inline;">'
                '<button class="chip-x" type="submit" title="Отменить" aria-label="Отменить">×</button>'
                '</form></span>') % (
            display, (': %s' % dose) if dose else '',
            (' <span class="chip-sub">%s</span>' % route_val) if route_val else '',
            url_for("stop_medication_route", pid=pid, mid=mid))
        resp = {"ok": True, "chip_html": chip, "id": mid}
        # При успехе показываем предупреждения CDS (warning/info) как подсказку.
        warns = _cds_summary(verdict)
        if warns:
            resp["cds"] = warns
        return jsonify(resp)
    return redirect(url_for("patient_detail", pid=pid))


def _cds_summary(verdict):
    """Сводка CDS-вердикта для UI: список замечаний с уровнем и текстом."""
    return [{
        "severity": i["severity"],
        "category": i.get("category", ""),
        "message": i["message"],
    } for i in verdict.get("issues", [])]


# ---------- Предварительная проверка препарата (без сохранения) ----------
@app.route("/patient/<pid>/medication/check", methods=["POST"])
def check_medication_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    code = (request.form.get("code", "") or "").strip().upper()
    if not code:
        return jsonify({"ok": True, "cds": []})
    verdict = drug_service.evaluate_medication(pid, code)
    return jsonify({"ok": True, "safe": verdict["safe"], "cds": _cds_summary(verdict)})


# ---------- Отмена препарата ----------
@app.route("/patient/<pid>/medication/<mid>/stop", methods=["POST"])
def stop_medication_route(pid, mid):
    fs.stop_medication(mid)
    if _wants_json():
        return jsonify({"ok": True})
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Удаление записей приёма (× у каждого значения) ----------
@app.route("/patient/<pid>/observation/<oid>/delete", methods=["POST"])
def delete_observation_route(pid, oid):
    fs.delete_observation(oid)
    if _wants_json():
        return jsonify({"ok": True})
    return redirect(url_for("patient_detail", pid=pid))


@app.route("/patient/<pid>/condition/<cid>/delete", methods=["POST"])
def delete_condition_route(pid, cid):
    fs.delete_condition(cid)
    if _wants_json():
        return jsonify({"ok": True})
    return redirect(url_for("patient_detail", pid=pid))


@app.route("/patient/<pid>/service_request/<sid>/delete", methods=["POST"])
def delete_service_request_route(pid, sid):
    fs.delete_service_request(sid)
    if _wants_json():
        return jsonify({"ok": True})
    return redirect(url_for("patient_detail", pid=pid))


@app.route("/patient/<pid>/report/<rid>/delete", methods=["POST"])
def delete_report_route(pid, rid):
    fs.delete_report(rid)
    if _wants_json():
        return jsonify({"ok": True})
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Диагноз ----------
@app.route("/patient/<pid>/condition", methods=["POST"])
def add_condition_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    code = request.form.get("code", "").strip().upper()
    display = request.form.get("display", "").strip()
    cid = fs.add_condition(
        pid, code, display,
        onset_date=request.form.get("onset_date") or None,
        encounter_id=request.form.get("encounter_id") or None,
    )
    if _wants_json():
        chip = ('<span class="chip">%s %s'
                '<form method="POST" action="%s" style="display:inline;">'
                '<button class="chip-x" type="submit" title="Удалить" aria-label="Удалить">×</button>'
                '</form></span>') % (code, display, url_for("delete_condition_route", pid=pid, cid=cid))
        return _ok(chip, id=cid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Аллергия ----------
@app.route("/patient/<pid>/allergy", methods=["POST"])
def add_allergy_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    fs.add_allergy(pid, request.form.get("code", "").strip().upper(),
                    request.form.get("display", "").strip(),
                    reaction_type=request.form.get("reaction_type", "unknown"))
    return redirect(url_for("patient_detail", pid=pid))


# ---------- План лечения + цель (ВП, КП №768) ----------
@app.route("/patient/<pid>/careplan", methods=["POST"])
def create_careplan_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    cps.create_cap_plan(pid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- План лечения ВП (КП №768) ----------
@app.route("/patient/<pid>/cap/plan", methods=["POST"])
def create_cap_plan_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    cps.create_cap_plan(pid)
    return redirect(url_for("patient_detail", pid=pid))


@app.route("/patient/<pid>/cap/followup", methods=["POST"])
def cap_followup_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    days = int(request.form.get("days", 3))
    cps.schedule_cap_followup(pid, days=days)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Клинические флаги (анамнез/осмотр/контекст ВП) ----------
@app.route("/patient/<pid>/flag", methods=["POST"])
def add_flag_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    from terminology import CLINICAL_FLAGS
    key = request.form.get("key", "").strip()
    eid = request.form.get("encounter_id") or None
    if key in CLINICAL_FLAGS:
        label, category = CLINICAL_FLAGS[key]
        existing = fs.get_flags(pid)
        for f in existing:
            if f["key"] == key and (f["encounter_id"] or None) == (eid or None):
                if _wants_json():
                    return _err("Уже добавлено")
                return redirect(url_for("patient_detail", pid=pid))
        fid = fs.add_flag(pid, key, value="true", category=category, encounter_id=eid)
        if _wants_json():
            chip = ('<span class="chip">%s'
                    '<form method="POST" action="%s" style="display:inline;">'
                    '<button class="chip-x" type="submit" title="Удалить" aria-label="Удалить">×</button>'
                    '</form></span>') % (label, url_for("clear_flag_route", pid=pid, fid=fid))
            return _ok(chip, id=fid)
    return redirect(url_for("patient_detail", pid=pid))


@app.route("/patient/<pid>/flag/<fid>/clear", methods=["POST"])
def clear_flag_route(pid, fid):
    fs.delete_flag(fid)
    if _wants_json():
        return jsonify({"ok": True})
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Анамнез в свободной форме (не оценивается протоколом) ----------
@app.route("/patient/<pid>/anamnesis", methods=["POST"])
def add_anamnesis_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    text = (request.form.get("text", "") or "").strip()
    eid = request.form.get("encounter_id") or None
    if not text:
        if _wants_json():
            return _err("Пустой анамнез")
        return redirect(url_for("patient_detail", pid=pid))
    # Сохраняем как флаг категории anamnesis: ключ — сам текст, не оценивается протоколом.
    fid = fs.add_flag(pid, text[:500], value="true", category="anamnesis", encounter_id=eid)
    if _wants_json():
        chip = ('<div class="anam-note">%s'
                '<form method="POST" action="%s" style="display:inline;">'
                '<button class="chip-x" type="submit" title="Удалить" aria-label="Удалить запись анамнеза">×</button>'
                '</form></div>') % (text.replace('<', '&lt;').replace('>', '&gt;'),
                                   url_for("clear_flag_route", pid=pid, fid=fid))
        return _ok(chip, id=fid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Общее состояние (клиническая оценка врача при осмотре) ----------
@app.route("/patient/<pid>/general_condition", methods=["POST"])
def set_general_condition_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    from terminology import (GENERAL_CONDITION, GENERAL_CONDITION_ORDER,
                             general_condition_display, general_condition_needs_inpatient)
    key = (request.form.get("key", "") or "").strip()
    eid = request.form.get("encounter_id") or None
    if key not in GENERAL_CONDITION:
        if _wants_json():
            return _err("Неизвестное значение общего состояния")
        return redirect(url_for("patient_detail", pid=pid))
    # Удаляем прежнюю оценку этого же приёма — общее состояние одно на приём (последнее).
    if eid:
        for f in fs.get_flags(pid, "general_condition"):
            if (f.get("encounter_id") or None) == (eid or None):
                fs.delete_flag(f["id"])
    fid = fs.add_flag(pid, key, value="true", category="general_condition", encounter_id=eid)
    label = general_condition_display(key)
    needs_inp = general_condition_needs_inpatient(key)
    if _wants_json():
        cls = "badge red" if needs_inp else "badge green"
        chip = ('<span class="chip"><b>Общее состояние:</b> <span class="%s">%s</span>'
                '<form method="POST" action="%s" style="display:inline;">'
                '<button class="chip-x" type="submit" title="Изменить" aria-label="Изменить оценку общего состояния">×</button>'
                '</form></span>') % (cls, label,
                                    url_for("clear_flag_route", pid=pid, fid=fid))
        return _ok(chip, id=fid, key=key, needs_inpatient=needs_inp)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Госпитализация / выписка (КП №768) ----------
@app.route("/patient/<pid>/cap/admit", methods=["POST"])
def cap_admit_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    cps.admit_inpatient(pid)
    return redirect(url_for("patient_detail", pid=pid))


@app.route("/patient/<pid>/cap/discharge", methods=["POST"])
def cap_discharge_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    cps.discharge_inpatient(pid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Удаление пациента ----------
@app.route("/patient/<pid>/delete", methods=["POST"])
def delete_patient_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    fs.delete_patient(pid)
    return redirect(url_for("dashboard"))


# ---------- Плановый контроль (ВП) ----------
@app.route("/patient/<pid>/followup", methods=["POST"])
def schedule_followup_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    days = int(request.form.get("days", 3))
    cps.schedule_cap_followup(pid, days=days)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Оценка достижения цели (ВП) ----------
@app.route("/patient/<pid>/evaluate", methods=["POST"])
def evaluate_goal_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    cps.evaluate_cap_goal(pid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Демо-данные ----------
@app.route("/seed", methods=["POST"])
def seed_route():
    fs.seed_demo()
    return redirect(url_for("dashboard"))


# ---------- Экспорт CSV ----------
@app.route("/export")
def export_csv():
    import csv, io
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["ФИО", "Возраст", "Пол", "Диагноз", "Код", "Тяжесть",
                     "Условия", "Соответствие", "Этап"])
    # Берём готовые оценки из cap_cache (прогревается при открытии карты/дашборда),
    # чтобы не пересчитывать evaluate_cap для каждого пациента (медленно на Supabase).
    caches = {c["patient_id"]: c for c in fs.get_all_cap_caches()}
    for p in fs.get_all_patients():
        pid = p["id"]
        cond = fs.get_condition(pid)
        cap = caches.get(pid) or {}
        pw = fs.get_pathway(pid)
        writer.writerow([
            f"{p['family']} {p['given']} {p['patronymic']}",
            fs.get_age(pid), "М" if p["gender"] == "male" else "Ж",
            cond["display"] if cond else "—", cond["code"] if cond else "—",
            cap.get("severity", "—") if cap.get("applicable") else "—",
            cap.get("setting", "—") if cap.get("applicable") else "—",
            "да" if cap.get("compliant") else ("нет" if cap.get("applicable") else "—"),
            pw["label"],
        ])
    return Response(output.getvalue(), mimetype="text/csv",
                     headers={"Content-Disposition": "attachment; filename=patients_report.csv"})


# ---------- CDS Hooks API ----------
@app.route("/cds-services/patient-view", methods=["POST"])
def cds_hook_patient_view():
    data = request.get_json() or {}
    pid = data.get("context", {}).get("patientId", "")
    return jsonify({"cards": cds.cds_patient_view(pid)})


@app.route("/cds-services/order-sign", methods=["POST"])
def cds_hook_order_sign():
    data = request.get_json() or {}
    pid = data.get("context", {}).get("patientId", "")
    med = data.get("context", {}).get("medicationCode", "")
    return jsonify({"cards": cds.cds_order_sign(pid, med)})


@app.route("/api/measure")
def api_measure():
    return jsonify(re.quality_measure_cap())


@app.route("/api/protocol-cap/<pid>")
def api_protocol_cap(pid):
    return jsonify(pcap.evaluate_cap(pid))


if __name__ == "__main__":
    _debug = os.getenv("FLASK_DEBUG", "1") != "0"
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5566")),
            debug=_debug, use_reloader=_debug)
