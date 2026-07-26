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
from datetime import date, timedelta

from flask import (
    Flask, render_template, jsonify, request, redirect, url_for, Response,
)

import fhir_store as fs
import rules_engine as re
import drug_service
import protocol_cap as pcap
import protocol_verdict
import care_plan_service as cps
import cds_service as cds
from terminology import (
    LOINC, STUDIES, CLINICAL_FLAGS, PNEUMONIA_CODES,
    loinc_display, loinc_unit, loinc_reference, interpret_value, sane_range,
    atc_display, study_display, study_category,
    ICD10, ALLERGEN_GROUPS, allergen_display,
    flag_category_display,
    EXAM_LOINC, LAB_LOINC, is_exam_loinc, is_lab_loinc,
    GENERAL_CONDITION, GENERAL_CONDITION_ORDER, general_condition_display, general_condition_needs_inpatient,
    vital_status_label, bp_status_label, EXAM_RED_FLAG_KEYS,
    SPO2_CODE, TEMP_CODE, RR_CODE, HR_CODE, SBP_CODE, DBP_CODE,
    parse_dose_per_day,
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
    """ФИО целиком: Фамилия Имя Отчество — без обрезки отчества."""
    family = (p.get("family") or "").strip()
    given = (p.get("given") or "").strip()
    pat = (p.get("patronymic") or "").strip()
    parts = [x for x in (family, given, pat) if x]
    return " ".join(parts) if parts else "—"


def _refresh_protocol(pid):
    """Continuous CDS: пересчитать evaluate_cap после клинической записи.

    Политика: docs/processes/CDS_SIGNALING.md (cds_policy в process_registry.yaml).
    """
    try:
        fs.clear_pid_cache(pid)
        fs.save_cap_cache(pid, pcap.evaluate_cap(pid))
    except Exception:
        pass


def _json_after_clinical(pid, *, chip_html=None, reload_ui=True, **extra):
    """AJAX-ответ после записи, влияющей на протокол: refresh + reload UI."""
    _refresh_protocol(pid)
    if reload_ui:
        payload = {"ok": True, "reload": True}
        payload.update(extra)
        return jsonify(payload)
    if chip_html is not None:
        return _ok(chip_html, **extra)
    payload = {"ok": True}
    payload.update(extra)
    return jsonify(payload)


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
            # Аудит в интерфейсе: что сделать сейчас (из cap_cache, без N+1).
            "next_step": (c.get("next_step") if c else None) or None,
            "headline": (c.get("headline") if c else None) or None,
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

    # Гостевой вход: Соколов (неверная АБТ). Без fallback на Морозова/ОРИТ.
    guest = next(
        (r for r in rows if (r.get("full") or "").startswith("Соколов")),
        None,
    )

    # Фильтры и поиск
    q = (request.args.get("q", "") or "").strip().lower()
    f_sev = request.args.get("severity", "")
    f_set = request.args.get("setting", "")
    f_com = request.args.get("compliant", "")
    if q:
        rows = [r for r in rows if q in r["full"].lower() or q in r["id"].lower()]
    if f_sev in ("severe", "mild"):
        rows = [r for r in rows if r["severity"] == f_sev]
    if f_set == "inpatient":
        rows = [r for r in rows if r["setting"] == "inpatient"]
    elif f_set == "ambulatory":
        # cache: outpatient; UI/фильтр: ambulatory
        rows = [r for r in rows if r["setting"] in ("ambulatory", "outpatient")]
    if f_com == "no":
        rows = [r for r in rows if r["compliant"] is False]
    elif f_com == "yes":
        rows = [r for r in rows if r["compliant"] is True]

    return render_template("dashboard.html", measure=measure, patients=rows,
                           q=q, f_sev=f_sev, f_set=f_set, f_com=f_com,
                           total=len(caches),
                           guest=guest,
                           demo_mode=os.environ.get("DEMO_MODE", "1") == "1")


@app.route("/demo")
def demo_guest():
    """Прямая ссылка для гостя → карточка Соколов (или первый с отклонением)."""
    for p in fs.get_all_patients():
        if (p.get("family") or "") == "Соколов":
            return redirect(url_for("patient_detail", pid=p["id"]))
    caches = {c["patient_id"]: c for c in fs.get_all_cap_caches()}
    for p in fs.get_all_patients():
        c = caches.get(p["id"])
        if c and c.get("applicable") and not c.get("compliant"):
            return redirect(url_for("patient_detail", pid=p["id"]))
    return redirect(url_for("dashboard"))


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


def _order_encounters(encounters):
    """Открытые приёмы первыми, затем закрытые (start DESC внутри групп)."""
    open_e = [e for e in encounters if e.get("status") != "finished"]
    closed = [e for e in encounters if e.get("status") == "finished"]
    return open_e + closed


def _triage_from_verdict(conditions, verdict, pn_codes):
    """Агрегат issues по диагнозам для #triage-panel."""
    out = []
    if not verdict or not verdict.get("applicable") or verdict.get("ok"):
        return out
    text = verdict.get("headline") or verdict.get("next_step") or "Требуется действие по протоколу"
    primary = None
    for c in conditions or []:
        if (c.get("code") or "") in pn_codes and (c.get("clinical_status") or "active") == "active":
            primary = c
            break
    if primary:
        out.append({
            "id": primary["id"],
            "name": primary.get("display") or primary.get("code") or "Диагноз",
            "issues": [{"severity": "warning", "text": text}],
        })
    elif not conditions:
        out.append({
            "id": "empty",
            "name": "Диагноз не установлен",
            "issues": [{"severity": "warning", "text": text}],
        })
    return out


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
        verdict = protocol_verdict.verdict_for_ui(cap)
        goals = fs.get_goals(pid)
        care_plans = fs.get_care_plans(pid)

        try:
            enc_limit = max(1, min(100, int(request.args.get("limit", 20))))
        except ValueError:
            enc_limit = 20
        try:
            enc_offset = max(0, int(request.args.get("offset", 0)))
        except ValueError:
            enc_offset = 0

        # Группировка ресурсов по приёму — только для страницы списка + текущий.
        all_encounters = fs.get_encounters(pid)  # ORDER BY start DESC
        ordered = _order_encounters(all_encounters)
        enc_total = len(ordered)

        # Один рабочий приём: ?e=id или первый незакрытый, иначе последний по дате.
        enc_ids = {e["id"] for e in all_encounters}
        current_eid = request.args.get("e")
        if current_eid not in enc_ids:
            current_eid = next(
                (e["id"] for e in ordered if e.get("status") != "finished"),
                ordered[0]["id"] if ordered else None,
            )

        page = list(ordered[enc_offset:enc_offset + enc_limit])
        page_ids = {e["id"] for e in page}
        if current_eid and current_eid not in page_ids:
            cur = next((e for e in all_encounters if e["id"] == current_eid), None)
            if cur:
                page = [cur] + page
                page_ids.add(current_eid)

        buckets = {
            e["id"]: {"obs": [], "sr": [], "rep": [], "med": [], "flag": [], "cond": []}
            for e in page
        }
        unassigned = {"obs": [], "sr": [], "rep": [], "med": [], "flag": [], "cond": []}

        def _place(item, eid, key):
            if eid and eid in buckets:
                buckets[eid][key].append(item)
            elif not eid or eid not in enc_ids:
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
        for e in page:
            b = buckets[e["id"]]
            reasons = fs.get_encounter_reasons(e["id"])
            encounters_data.append({
                "encounter": e, "reason_condition_ids": reasons, **b,
            })

        current_ed = next(
            (ed for ed in encounters_data if ed["encounter"]["id"] == current_eid),
            None,
        )

        # Цель ВП — пересчёт без отдельной кнопки врача.
        if care_plans:
            try:
                cps.evaluate_cap_goal(pid)
                goals = fs.get_goals(pid)
            except Exception:
                pass

        has_unassigned = any(unassigned[k] for k in unassigned)
        conditions = fs.get_conditions(pid)
        triage_conditions = _triage_from_verdict(
            conditions, verdict, set(PNEUMONIA_CODES),
        )
        enc_has_more = enc_offset + enc_limit < enc_total

        return render_template(
            "patient.html",
            patient=p,
            full_name=f"{p['family']} {p['given']} {p['patronymic']}",
            age=fs.get_age(pid),
            condition=fs.get_condition(pid),
            conditions=conditions,
            observations=fs.get_observations(pid),
            reports=fs.get_diagnostic_reports(pid),
            service_requests=fs.get_service_requests(pid),
            meds=fs.get_medications(pid),
            active_abt=[
                m for m in fs.get_medications(pid, status="active")
                if (m.get("code") or "").upper().startswith("J01")
            ],
            allergies=fs.get_allergies(pid),
            flags=fs.get_flags(pid),
            encounters=page,
            encounters_data=encounters_data,
            encounters_total=enc_total,
            enc_limit=enc_limit,
            enc_offset=enc_offset,
            enc_has_more=enc_has_more,
            triage_conditions=triage_conditions,
            current_eid=current_eid,
            current_ed=current_ed,
            unassigned=unassigned,
            has_unassigned=has_unassigned,
            followups=cps.get_followups(pid),
            care_plans=care_plans,
            goals=goals,
            pathway=fs.get_pathway(pid),
            cards=cds.cds_patient_view(pid),
            cap=cap,
            verdict=verdict,
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
            vital_status_label=vital_status_label,
            bp_status_label=bp_status_label,
            EXAM_RED_FLAG_KEYS=EXAM_RED_FLAG_KEYS,
            SPO2_CODE=SPO2_CODE,
            TEMP_CODE=TEMP_CODE,
            RR_CODE=RR_CODE,
            HR_CODE=HR_CODE,
            SBP_CODE=SBP_CODE,
            DBP_CODE=DBP_CODE,
            study_display=study_display,
            study_category=study_category,
            icd10=ICD10,
            allergen_groups=ALLERGEN_GROUPS,
            allergen_display=allergen_display,
            flag_category_display=flag_category_display,
            today=date.today().isoformat(),
            course_end=(date.today() + timedelta(days=7)).isoformat(),
        )
    finally:
        fs.clear_pid_cache(pid)


# ---------- Приёмы: пагинированный JSON (lazy-load списка) ----------
@app.route("/patient/<pid>/encounters", methods=["GET"])
def list_encounters_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    try:
        limit = max(1, min(100, int(request.args.get("limit", 20))))
    except ValueError:
        limit = 20
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        offset = 0
    ordered = _order_encounters(fs.get_encounters(pid))
    total = len(ordered)
    page = ordered[offset:offset + limit]
    items = []
    for e in page:
        items.append({
            "id": e["id"],
            "start": e.get("start"),
            "class": e.get("class"),
            "status": e.get("status"),
            "complaint": e.get("complaint"),
            "reason_condition_ids": fs.get_encounter_reasons(e["id"]),
        })
    return jsonify({
        "ok": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
        "encounters": items,
    })


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
    _refresh_protocol(pid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Закрыть приём ----------
@app.route("/patient/<pid>/encounter/<eid>/finish", methods=["POST"])
def finish_encounter_route(pid, eid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    fs.finish_encounter(eid)
    _refresh_protocol(pid)
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
        # Continuous: vitals меняют тяжесть/показания — reload вердикта.
        return _json_after_clinical(pid, reload_ui=True, id=oid)
    _refresh_protocol(pid)
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
        return _json_after_clinical(
            pid, reload_ui=True, id=sid, code=code, display=display,
            category=study_category(code) if code in STUDIES else "lab",
        )
    _refresh_protocol(pid)
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
        return _json_after_clinical(
            pid, reload_ui=True, id=rid, code=code, display=display,
            category=study_category(code), service_request_id=sid,
        )
    _refresh_protocol(pid)
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

    # Замена АБТ с экрана протокола: сначала снять другие активные J01,
    # иначе врач получает «назначил, а вердикт тот же» (два АБТ сразу).
    replace_abt = request.form.get("replace_abt", "") == "1"
    stopped = []
    if replace_abt and code.startswith("J01"):
        for m in fs.get_medications(pid, status="active"):
            mc = (m.get("code") or "").upper()
            if mc.startswith("J01") and mc != code:
                fs.stop_medication(m["id"])
                stopped.append(m.get("display") or mc)

    # CDS order-sign: hard-stop (аллергия) / soft-stop (протокол, warning).
    verdict = _medication_order_verdict(pid, code)
    confirmed = request.form.get("confirm", "") == "1"
    ack = request.form.get("ack", "") == "1"
    override_reason = (request.form.get("override_reason") or "").strip()
    issues = verdict.get("issues") or []
    hard_stops = [i for i in issues if i.get("severity") == "hard-stop"]
    soft_stops = [i for i in issues if i.get("severity") == "warning"]
    eid = request.form.get("encounter_id") or None

    if hard_stops and not confirmed:
        if _wants_json():
            return jsonify({
                "ok": False, "need_confirm": True, "level": "hard",
                "cds": _cds_summary(verdict),
            })
        return redirect(url_for("patient_detail", pid=pid))
    if hard_stops and confirmed and not override_reason:
        if _wants_json():
            return jsonify({
                "ok": False, "need_confirm": True, "level": "hard",
                "error": "Укажите причину назначения",
                "cds": _cds_summary(verdict),
            }), 400
        return redirect(url_for("patient_detail", pid=pid))
    if soft_stops and not hard_stops and not (confirmed and ack):
        if _wants_json():
            return jsonify({
                "ok": False, "need_confirm": True, "level": "soft",
                "cds": _cds_summary(verdict),
            })
        return redirect(url_for("patient_detail", pid=pid))

    dpd_raw = request.form.get("dose_per_day", "").strip()
    try:
        dpd = float(dpd_raw.replace(",", ".")) if dpd_raw else None
    except ValueError:
        dpd = None
    # Сверка дозы (КП №768) не должна зависеть от того, заполнил ли врач скрытое
    # техническое поле dose_per_day: считаем суточную дозу из тех же «доза»/«кратность»,
    # что видит врач в форме — по умолчанию из каталога, при ручном вводе — из текста.
    if dpd is None and code.startswith("J01"):
        dpd = parse_dose_per_day(dose, frequency)
    gating = hard_stops or soft_stops
    override_detail = None
    if confirmed and gating:
        override_detail = "; ".join(
            i.get("message") or "" for i in (hard_stops or soft_stops) if i.get("message")
        )
    mid = fs.add_medication(
        pid, code, display,
        dose=dose,
        frequency=frequency,
        route=request.form.get("route", "").strip() or None,
        med_date=request.form.get("med_date") or None,
        period_end=request.form.get("period_end") or None,
        encounter_id=eid,
        dose_per_day=dpd,
        cds_override=bool(confirmed and gating),
        cds_override_detail=override_detail,
    )
    if confirmed and gating:
        sev = "hard-stop" if hard_stops else "soft-stop"
        for i in (hard_stops or soft_stops):
            fs.add_cds_override_log(
                pid,
                severity=sev,
                category=i.get("category"),
                issue_message=i.get("message"),
                reason=override_reason or None,
                encounter_id=eid,
                medication_request_id=mid,
            )
    # План/цель ВП — без отдельной кнопки врача.
    if code.startswith("J01") and not fs.get_care_plans(pid):
        try:
            cps.create_cap_plan(pid)
        except Exception:
            pass
    # Continuous: любое назначение меняет картину протокола (CDS_SIGNALING).
    _refresh_protocol(pid)
    # UI: после АБТ / override / явного reload — полная перерисовка вердикта.
    must_reload = (
        code.startswith("J01")
        or replace_abt
        or bool(confirmed and gating)
        or request.form.get("reload", "") == "1"
    )
    if _wants_json():
        if must_reload:
            return jsonify({"ok": True, "reload": True, "id": mid, "stopped": stopped})
        route_val = request.form.get("route", "").strip() or None
        chip = ('<span class="chip">%s%s%s'
                '<form method="POST" action="%s" style="display:inline;">'
                '<button class="chip-x" type="submit" title="Отменить" aria-label="Отменить">×</button>'
                '</form></span>') % (
            display, (': %s' % dose) if dose else '',
            (' <span class="chip-sub">%s</span>' % route_val) if route_val else '',
            url_for("stop_medication_route", pid=pid, mid=mid))
        resp = {"ok": True, "chip_html": chip, "id": mid, "stopped": stopped}
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


def _medication_order_verdict(pid, code):
    """drug_service + protocol_cap.evaluate_abt_choice (order-sign)."""
    verdict = drug_service.evaluate_medication(pid, code)
    issues = list(verdict.get("issues") or [])
    issues.extend(pcap.evaluate_abt_choice(pid, code))
    hard = any(i.get("severity") == "hard-stop" for i in issues)
    soft = any(i.get("severity") == "warning" for i in issues)
    out = dict(verdict)
    out["issues"] = issues
    out["safe"] = not hard and not soft
    out["level"] = "hard" if hard else ("soft" if soft else None)
    return out


# ---------- Предварительная проверка препарата (без сохранения) ----------
@app.route("/patient/<pid>/medication/check", methods=["POST"])
def check_medication_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    code = (request.form.get("code", "") or "").strip().upper()
    if not code:
        return jsonify({"ok": True, "cds": []})
    verdict = _medication_order_verdict(pid, code)
    return jsonify({"ok": True, "safe": verdict["safe"], "cds": _cds_summary(verdict)})


# ---------- Отмена препарата ----------
@app.route("/patient/<pid>/medication/<mid>/stop", methods=["POST"])
def stop_medication_route(pid, mid):
    fs.stop_medication(mid)
    if _wants_json():
        return _json_after_clinical(pid, reload_ui=True)
    _refresh_protocol(pid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Удаление записей приёма (× у каждого значения) ----------
@app.route("/patient/<pid>/observation/<oid>/delete", methods=["POST"])
def delete_observation_route(pid, oid):
    fs.delete_observation(oid)
    # Пара АД: одна × на объединённой строке может снять оба измерения.
    also = (request.form.get("also") or "").strip()
    if also and also != oid:
        fs.delete_observation(also)
    if _wants_json():
        return _json_after_clinical(pid, reload_ui=True)
    _refresh_protocol(pid)
    return redirect(url_for("patient_detail", pid=pid))


@app.route("/patient/<pid>/condition/<cid>/delete", methods=["POST"])
def delete_condition_route(pid, cid):
    fs.delete_condition(cid)
    if _wants_json():
        return _json_after_clinical(pid, reload_ui=True)
    _refresh_protocol(pid)
    return redirect(url_for("patient_detail", pid=pid))


@app.route("/patient/<pid>/service_request/<sid>/delete", methods=["POST"])
def delete_service_request_route(pid, sid):
    fs.delete_service_request(sid)
    if _wants_json():
        return _json_after_clinical(pid, reload_ui=True)
    _refresh_protocol(pid)
    return redirect(url_for("patient_detail", pid=pid))


@app.route("/patient/<pid>/report/<rid>/delete", methods=["POST"])
def delete_report_route(pid, rid):
    fs.delete_report(rid)
    if _wants_json():
        return _json_after_clinical(pid, reload_ui=True)
    _refresh_protocol(pid)
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
        return _json_after_clinical(pid, reload_ui=True, id=cid)
    _refresh_protocol(pid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Аллергия ----------
@app.route("/patient/<pid>/allergy", methods=["POST"])
def add_allergy_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    code = request.form.get("code", "").strip()
    # Группа из справочника уже даёт каноническое название — свободный текст
    # нужен только для уточнения конкретного препарата, не как основной источник display.
    display = request.form.get("display", "").strip() or allergen_display(code)
    fs.add_allergy(pid, code, display,
                    reaction_type=request.form.get("reaction_type", "unknown"))
    _refresh_protocol(pid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- План лечения + цель (ВП, КП №768) ----------
@app.route("/patient/<pid>/careplan", methods=["POST"])
def create_careplan_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    cps.create_cap_plan(pid)
    _refresh_protocol(pid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- План лечения ВП (КП №768) ----------
@app.route("/patient/<pid>/cap/plan", methods=["POST"])
def create_cap_plan_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    cps.create_cap_plan(pid)
    _refresh_protocol(pid)
    return redirect(url_for("patient_detail", pid=pid))


@app.route("/patient/<pid>/cap/followup", methods=["POST"])
def cap_followup_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    days = int(request.form.get("days", 3))
    cps.schedule_cap_followup(pid, days=days)
    _refresh_protocol(pid)
    return redirect(url_for("patient_detail", pid=pid))


@app.route("/patient/<pid>/cap/repeat_cxr", methods=["POST"])
def schedule_repeat_cxr_route(pid):
    """Плановый контроль: контрольный визит + заказ повторной R-графии ОГК через 4–6 нед."""
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    days = int(request.form.get("days", 35))
    cps.schedule_repeat_cxr(pid, days=days)
    _refresh_protocol(pid)
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
            return _json_after_clinical(pid, reload_ui=True, id=fid)
        _refresh_protocol(pid)
        return redirect(url_for("patient_detail", pid=pid))
    return redirect(url_for("patient_detail", pid=pid))


@app.route("/patient/<pid>/flag/<fid>/clear", methods=["POST"])
def clear_flag_route(pid, fid):
    # Флаги анамнеза (категории anamnesis/social_risk/context) участвуют в
    # rules_engine.diagnosis_support → evaluate_cap (gap diagnosis_unsupported),
    # поэтому пересчитываем протокол всегда, как и при добавлении флага.
    fs.delete_flag(fid)
    if _wants_json():
        return _json_after_clinical(pid, reload_ui=True)
    _refresh_protocol(pid)
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
    needs_inp = general_condition_needs_inpatient(key)
    if _wants_json():
        return _json_after_clinical(
            pid, reload_ui=True, id=fid, key=key, needs_inpatient=needs_inp,
        )
    _refresh_protocol(pid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Госпитализация / выписка (КП №768) ----------
@app.route("/patient/<pid>/cap/admit", methods=["POST"])
def cap_admit_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    cps.admit_inpatient(pid)
    _refresh_protocol(pid)
    return redirect(url_for("patient_detail", pid=pid))


@app.route("/patient/<pid>/cap/discharge", methods=["POST"])
def cap_discharge_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    cps.discharge_inpatient(pid)
    _refresh_protocol(pid)
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
    _refresh_protocol(pid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Оценка достижения цели (ВП) ----------
@app.route("/patient/<pid>/evaluate", methods=["POST"])
def evaluate_goal_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    cps.evaluate_cap_goal(pid)
    _refresh_protocol(pid)
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
