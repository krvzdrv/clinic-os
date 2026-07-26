"""
Слой 0/1 — Репозиторий FHIR-подобных ресурсов.

Единственный модуль, знающий структуру таблиц. Ходит в БД только через db.py.
Правила (rules_engine), проверка лекарств (drug_service), регламент
(protocol_cap), CDS (cds_service) и путь пациента (care_plan_service)
вызывают функции этого модуля и никогда не трогают БД напрямую.

Модель — FHIR R4-подобная: Patient, Practitioner, Encounter, Condition,
Observation, DiagnosticReport, ServiceRequest, MedicationRequest,
MedicationKnowledge, AllergyIntolerance, CarePlan, Goal, Pathway.

Фейковых пациентов нет: БД стартует пустой. Демо-данные — опционально (seed_demo).
"""
import uuid
from datetime import datetime, date

import db


def init_db():
    db.init_schema()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _today():
    return date.today().isoformat()


# ============ In-memory кэш ресурсов пациента ============
# Чтобы evaluate_cap (и правила) не делали ~100 запросов к удалённой БД
# на одного пациента. Загружаем все таблицы один раз за запрос.
# Кэш однопроцессный, перезаписывается при load_pid_cache(pid).

_PID_CACHE = {}


def load_pid_cache(pid):
    """Загрузить все ресурсы пациента в память.

    Postgres: один round-trip с json_agg.
    SQLite: отдельные SELECT (json_agg/row_to_json недоступны) — иначе карточка падает.
    """
    if db.backend() == "sqlite":
        _PID_CACHE[pid] = {
            "patient": db.fetchone("SELECT * FROM patient WHERE id = %s", (pid,)),
            "encounters": db.fetchall(
                "SELECT * FROM encounter WHERE patient_id = %s ORDER BY start DESC", (pid,)),
            "conditions": db.fetchall(
                "SELECT * FROM condition_ WHERE patient_id = %s ORDER BY onset_date DESC", (pid,)),
            "observations": db.fetchall(
                "SELECT * FROM observation WHERE patient_id = %s ORDER BY date DESC", (pid,)),
            "diagnostic_reports": db.fetchall(
                "SELECT * FROM diagnostic_report WHERE patient_id = %s ORDER BY date DESC", (pid,)),
            "service_requests": db.fetchall(
                "SELECT * FROM service_request WHERE patient_id = %s ORDER BY occurrence_date DESC", (pid,)),
            "medications_all": db.fetchall(
                "SELECT * FROM medication_request WHERE patient_id = %s ORDER BY date DESC", (pid,)),
            "flags": db.fetchall(
                "SELECT * FROM clinical_flag WHERE patient_id = %s ORDER BY recorded_date DESC", (pid,)),
            "allergies": db.fetchall(
                "SELECT * FROM allergy_intolerance WHERE patient_id = %s ORDER BY recorded_date DESC", (pid,)),
            "goals": db.fetchall(
                "SELECT * FROM goal WHERE patient_id = %s ORDER BY start_date DESC", (pid,)),
            "care_plans": db.fetchall(
                "SELECT * FROM care_plan WHERE patient_id = %s ORDER BY created_date DESC", (pid,)),
            "pathway": db.fetchone("SELECT * FROM pathway WHERE patient_id = %s", (pid,)),
        }
        return

    sql = (
        "SELECT "
        "(SELECT row_to_json(t) FROM (SELECT * FROM patient WHERE id = %s) t) AS patient, "
        "(SELECT json_agg(t) FROM (SELECT * FROM encounter WHERE patient_id = %s ORDER BY start DESC) t) AS encounters, "
        "(SELECT json_agg(t) FROM (SELECT * FROM condition_ WHERE patient_id = %s ORDER BY onset_date DESC) t) AS conditions, "
        "(SELECT json_agg(t) FROM (SELECT * FROM observation WHERE patient_id = %s ORDER BY date DESC) t) AS observations, "
        "(SELECT json_agg(t) FROM (SELECT * FROM diagnostic_report WHERE patient_id = %s ORDER BY date DESC) t) AS diagnostic_reports, "
        "(SELECT json_agg(t) FROM (SELECT * FROM service_request WHERE patient_id = %s ORDER BY occurrence_date DESC) t) AS service_requests, "
        "(SELECT json_agg(t) FROM (SELECT * FROM medication_request WHERE patient_id = %s ORDER BY date DESC) t) AS medications_all, "
        "(SELECT json_agg(t) FROM (SELECT * FROM clinical_flag WHERE patient_id = %s ORDER BY recorded_date DESC) t) AS flags, "
        "(SELECT json_agg(t) FROM (SELECT * FROM allergy_intolerance WHERE patient_id = %s ORDER BY recorded_date DESC) t) AS allergies, "
        "(SELECT json_agg(t) FROM (SELECT * FROM goal WHERE patient_id = %s ORDER BY start_date DESC) t) AS goals, "
        "(SELECT json_agg(t) FROM (SELECT * FROM care_plan WHERE patient_id = %s ORDER BY created_date DESC) t) AS care_plans, "
        "(SELECT row_to_json(t) FROM (SELECT * FROM pathway WHERE patient_id = %s) t) AS pathway"
    )
    row = db.fetchone(sql, (pid,) * 12)
    if row is None:
        _PID_CACHE[pid] = {k: None for k in
                           ("patient", "encounters", "conditions", "observations",
                            "diagnostic_reports", "service_requests", "medications_all",
                            "flags", "allergies", "goals", "care_plans", "pathway")}
        return

    def _arr(col):
        v = row.get(col)
        return v if isinstance(v, list) else (v or [])

    def _one(col):
        return row.get(col)

    _PID_CACHE[pid] = {
        "patient": _one("patient"),
        "encounters": _arr("encounters"),
        "conditions": _arr("conditions"),
        "observations": _arr("observations"),
        "diagnostic_reports": _arr("diagnostic_reports"),
        "service_requests": _arr("service_requests"),
        "medications_all": _arr("medications_all"),
        "flags": _arr("flags"),
        "allergies": _arr("allergies"),
        "goals": _arr("goals"),
        "care_plans": _arr("care_plans"),
        "pathway": _one("pathway"),
    }


def clear_pid_cache(pid=None):
    if pid is None:
        _PID_CACHE.clear()
    else:
        _PID_CACHE.pop(pid, None)


def _cached(pid, key):
    c = _PID_CACHE.get(pid)
    return c.get(key) if c else None


# ============ Practitioner ============

def get_all_practitioners():
    return db.fetchall("SELECT * FROM practitioner ORDER BY family")

def get_practitioner(pr_id):
    return db.fetchone("SELECT * FROM practitioner WHERE id = %s", (pr_id,))

def add_practitioner(family, given, specialty):
    pid = _new_id("dr")
    db.execute(
        "INSERT INTO practitioner (id, family, given, specialty) VALUES (%s,%s,%s,%s)",
        (pid, family, given, specialty))
    return pid


# ============ Patient ============

def get_all_patients():
    return db.fetchall("SELECT * FROM patient ORDER BY family")

def get_patient(pid):
    c = _cached(pid, "patient")
    if c is not None:
        return c
    return db.fetchone("SELECT * FROM patient WHERE id = %s", (pid,))

def get_age(pid):
    p = get_patient(pid)
    if not p:
        return 0
    born = datetime.strptime(p["birth_date"], "%Y-%m-%d").date()
    today = date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))

def is_fertile_female(pid):
    p = get_patient(pid)
    if not p or p["gender"] != "female":
        return False
    return 15 <= get_age(pid) <= 49

def add_patient(family, given, patronymic, gender, birth_date):
    pid = _new_id("p")
    db.execute(
        "INSERT INTO patient (id, family, given, patronymic, gender, birth_date) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (pid, family, given, patronymic, gender, birth_date))
    db.execute("INSERT INTO pathway (patient_id, state, label) VALUES (%s,'screening','Скрининг')",
                (pid,))
    return pid


def delete_patient(pid):
    """Каскадное удаление пациента и всех его ресурсов (FHIR-подобная модель)."""
    # encounter_reason: по encounter пациента (нет patient_id)
    for e in db.fetchall("SELECT id FROM encounter WHERE patient_id = %s", (pid,)) or []:
        db.execute("DELETE FROM encounter_reason WHERE encounter_id = %s", (e["id"],))
    for tbl in (
        "cds_override_log",
        "observation", "diagnostic_report", "service_request",
        "medication_request", "condition_", "clinical_flag",
        "allergy_intolerance", "goal", "care_plan", "encounter",
        "pathway", "cap_cache",
    ):
        try:
            db.execute(f"DELETE FROM {tbl} WHERE patient_id = %s", (pid,))
        except Exception:
            pass
    db.execute("DELETE FROM patient WHERE id = %s", (pid,))
    clear_pid_cache(pid)


# ============ Encounter (приём) ============

def get_encounters(pid):
    c = _cached(pid, "encounters")
    if c is not None:
        return c
    return db.fetchall("SELECT * FROM encounter WHERE patient_id = %s ORDER BY start DESC", (pid,))

def get_encounter(eid):
    return db.fetchone("SELECT * FROM encounter WHERE id = %s", (eid,))

def add_encounter(pid, practitioner_id=None, status="in-progress", cls="ambulatory",
                  start=None, reason_code=None, complaint=None):
    eid = _new_id("e")
    if not start:
        start = _today()
    db.execute(
        "INSERT INTO encounter (id, patient_id, practitioner_id, status, class, start, ended_at, reason_code, complaint) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (eid, pid, practitioner_id, status, cls, start, None, reason_code, complaint))
    return eid

def finish_encounter(eid, end=None):
    if not end:
        end = _today()
    db.execute("UPDATE encounter SET status='finished', ended_at=%s WHERE id=%s", (end, eid))


# ============ Condition (диагноз) ============

def get_condition(pid):
    rows = _cached(pid, "conditions")
    if rows is None:
        rows = db.fetchall(
            "SELECT * FROM condition_ WHERE patient_id = %s ORDER BY recorded_date DESC", (pid,))
    for c in rows:
        if c.get("clinical_status") == "active":
            return c
    return None

def get_conditions(pid):
    c = _cached(pid, "conditions")
    if c is not None:
        return c
    return db.fetchall(
        "SELECT * FROM condition_ WHERE patient_id = %s ORDER BY onset_date DESC", (pid,))

def add_condition(pid, code, display, onset_date=None, encounter_id=None,
                  code_system="ICD-10", clinical_status="active",
                  verification_status="confirmed",
                  source_kind=None, source_id=None, source_label=None):
    """source_kind/source_id/source_label — провенанс: диагноз поставлен по
    конкретному результату (report/observation), а не «просто так». Используется
    для подсказки в UI, откуда взялся диагноз."""
    cid = _new_id("c")
    if not onset_date:
        onset_date = _today()
    db.execute(
        "INSERT INTO condition_ (id, patient_id, encounter_id, code_system, code, display, "
        "clinical_status, verification_status, onset_date, recorded_date, "
        "source_kind, source_id, source_label) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (cid, pid, encounter_id, code_system, code, display,
         clinical_status, verification_status, onset_date, _today(),
         source_kind, source_id, source_label))
    if encounter_id:
        link_encounter_condition(encounter_id, cid)
    clear_pid_cache(pid)
    return cid


# ============ Observation (числовые измерения и анализы) ============

def get_observations(pid, code=None, limit=None):
    cached = _cached(pid, "observations")
    if cached is not None:
        rows = [o for o in cached if code is None or o["code"] == code]
        if limit:
            rows = rows[:limit]
        return rows
    sql = "SELECT * FROM observation WHERE patient_id = %s"
    params = [pid]
    if code:
        sql += " AND code = %s"
        params.append(code)
    sql += " ORDER BY date DESC"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    return db.fetchall(sql, tuple(params))

def get_last_observation(pid, code):
    cached = _cached(pid, "observations")
    if cached is not None:
        for o in cached:
            if o["code"] == code:
                return o
        return None
    return db.fetchone(
        "SELECT * FROM observation WHERE patient_id = %s AND code = %s ORDER BY date DESC LIMIT 1",
        (pid, code))

def add_observation(pid, code, display, value_numeric=None, value_unit=None,
                    value_text=None, ref_low=None, ref_high=None, interpretation=None,
                    obs_date=None, encounter_id=None, status="final"):
    oid = _new_id("o")
    if not obs_date:
        obs_date = _today()
    db.execute(
        "INSERT INTO observation (id, patient_id, encounter_id, code, display, "
        "value_numeric, value_unit, value_text, ref_low, ref_high, interpretation, status, date) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (oid, pid, encounter_id, code, display, value_numeric, value_unit,
         value_text, ref_low, ref_high, interpretation, status, obs_date))
    return oid


# ============ DiagnosticReport (ЭКГ, УЗИ, холтер) ============

def get_diagnostic_reports(pid):
    c = _cached(pid, "diagnostic_reports")
    if c is not None:
        return c
    return db.fetchall("SELECT * FROM diagnostic_report WHERE patient_id = %s ORDER BY date DESC", (pid,))

def add_diagnostic_report(pid, code, display, conclusion=None, attachment_url=None,
                          status="final", rep_date=None, encounter_id=None):
    rid = _new_id("r")
    if not rep_date:
        rep_date = _today()
    db.execute(
        "INSERT INTO diagnostic_report (id, patient_id, encounter_id, code, display, status, "
        "conclusion, attachment_url, date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (rid, pid, encounter_id, code, display, status, conclusion, attachment_url, rep_date))
    return rid


# ============ ServiceRequest (заказы) ============

def get_service_requests(pid, status=None):
    cached = _cached(pid, "service_requests")
    if cached is not None:
        return [sr for sr in cached if status is None or sr["status"] == status]
    sql = "SELECT * FROM service_request WHERE patient_id = %s"
    params = [pid]
    if status:
        sql += " AND status = %s"
        params.append(status)
    sql += " ORDER BY occurrence_date DESC"
    return db.fetchall(sql, tuple(params))

def add_service_request(pid, code, display, practitioner_id=None, occurrence_date=None,
                         reason_code=None, encounter_id=None, status="active", intent="order"):
    sid = _new_id("sr")
    if not occurrence_date:
        occurrence_date = _today()
    db.execute(
        "INSERT INTO service_request (id, patient_id, encounter_id, practitioner_id, code, display, "
        "status, intent, occurrence_date, reason_code) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (sid, pid, encounter_id, practitioner_id, code, display, status, intent,
         occurrence_date, reason_code))
    return sid

def complete_service_request(sid):
    db.execute("UPDATE service_request SET status='completed' WHERE id=%s", (sid,))


# ============ MedicationRequest ============

def get_medications(pid, status="active"):
    cached = _cached(pid, "medications_all")
    if cached is not None:
        return [m for m in cached if status is None or m["status"] == status]
    return db.fetchall(
        "SELECT * FROM medication_request WHERE patient_id = %s AND status = %s ORDER BY date DESC",
        (pid, status))

def get_all_medications(pid):
    """Все назначения препаратов (включая stopped) — для группировки по приёмам."""
    c = _cached(pid, "medications_all")
    if c is not None:
        return c
    return db.fetchall(
        "SELECT * FROM medication_request WHERE patient_id = %s ORDER BY date DESC",
        (pid,))

def add_medication(pid, code, display, dose=None, frequency=None, period_start=None,
                   period_end=None, med_date=None, encounter_id=None, route=None, status="active",
                   dose_per_day=None, cds_override=False, cds_override_detail=None):
    mid = _new_id("m")
    if not med_date:
        med_date = _today()
    if not period_start:
        period_start = med_date
    db.execute(
        "INSERT INTO medication_request (id, patient_id, encounter_id, code, display, status, "
        "dose, frequency, route, period_start, period_end, date, dose_per_day, "
        "cds_override, cds_override_detail) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (mid, pid, encounter_id, code, display, status, dose, frequency,
         route, period_start, period_end, med_date, dose_per_day,
         1 if cds_override else 0, cds_override_detail))
    clear_pid_cache(pid)
    return mid

def stop_medication(mid):
    row = db.fetchone("SELECT patient_id FROM medication_request WHERE id=%s", (mid,))
    db.execute("UPDATE medication_request SET status='stopped' WHERE id=%s", (mid,))
    if row:
        clear_pid_cache(row["patient_id"])


def add_cds_override_log(
    pid,
    severity,
    category=None,
    issue_message=None,
    reason=None,
    encounter_id=None,
    medication_request_id=None,
):
    """Append-only запись осознанного CDS override (без update/delete API)."""
    from datetime import datetime, timezone
    oid = _new_id("ov")
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.execute(
        "INSERT INTO cds_override_log "
        "(id, patient_id, encounter_id, medication_request_id, severity, category, "
        "issue_message, reason, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            oid, pid, encounter_id, medication_request_id, severity,
            category, issue_message, reason, created,
        ),
    )
    return oid


def get_cds_override_logs(pid):
    return db.fetchall(
        "SELECT * FROM cds_override_log WHERE patient_id = %s ORDER BY created_at DESC",
        (pid,),
    )


def link_encounter_condition(encounter_id, condition_id):
    """reasonReference: encounter ↔ condition (idempotent)."""
    if not encounter_id or not condition_id:
        return
    exists = db.fetchone(
        "SELECT 1 FROM encounter_reason WHERE encounter_id=%s AND condition_id=%s",
        (encounter_id, condition_id),
    )
    if exists:
        return
    db.execute(
        "INSERT INTO encounter_reason (encounter_id, condition_id) VALUES (%s,%s)",
        (encounter_id, condition_id),
    )


def get_encounter_reasons(encounter_id):
    """Condition ids linked to encounter via reasonReference (+ legacy encounter_id)."""
    rows = db.fetchall(
        "SELECT condition_id FROM encounter_reason WHERE encounter_id=%s",
        (encounter_id,),
    )
    ids = [r["condition_id"] for r in (rows or [])]
    if ids:
        return ids
    legacy = db.fetchall(
        "SELECT id FROM condition_ WHERE encounter_id=%s",
        (encounter_id,),
    )
    return [r["id"] for r in (legacy or [])]


def get_condition_encounters(condition_id):
    rows = db.fetchall(
        "SELECT encounter_id FROM encounter_reason WHERE condition_id=%s",
        (condition_id,),
    )
    return [r["encounter_id"] for r in (rows or [])]


def delete_observation(oid):
    db.execute("DELETE FROM observation WHERE id = %s", (oid,))


def delete_condition(cid):
    db.execute("DELETE FROM condition_ WHERE id = %s", (cid,))


def delete_service_request(sid):
    db.execute("DELETE FROM service_request WHERE id = %s", (sid,))


def delete_report(rid):
    db.execute("DELETE FROM diagnostic_report WHERE id = %s", (rid,))


# ============ MedicationKnowledge (кэш справочника) ============

def get_medication_knowledge(atc_code):
    return db.fetchone("SELECT * FROM medication_knowledge WHERE atc_code = %s", (atc_code,))
def upsert_medication_knowledge(atc_code, name, indications=None, contraindications=None,
                                interactions=None, pregnancy_category=None, dose_info=None):
    db.execute(
        "INSERT INTO medication_knowledge (atc_code, name, indications, contraindications, "
        "interactions, pregnancy_category, dose_info, fetched_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(atc_code) DO UPDATE SET name=EXCLUDED.name, indications=EXCLUDED.indications, "
        "contraindications=EXCLUDED.contraindications, interactions=EXCLUDED.interactions, "
        "pregnancy_category=EXCLUDED.pregnancy_category, dose_info=EXCLUDED.dose_info, "
        "fetched_at=EXCLUDED.fetched_at",
        (atc_code, name, indications, contraindications, interactions,
         pregnancy_category, dose_info, _today()))


# ============ Каталог препаратов (взрослые, КП №768) ============
# Справочник для UI. Выбор АБТ — docs/protocols/cap_abt_rules.yaml.

def get_drug_catalog():
    """Все записи каталога, отсортированы по группе затем названию."""
    return db.fetchall(
        "SELECT * FROM drug_catalog ORDER BY group_name, name")


def get_drug(atc_code):
    """Одна запись каталога по ATC-коду или None."""
    return db.fetchone("SELECT * FROM drug_catalog WHERE atc_code = %s", (atc_code,))


def upsert_drug_catalog_entry(atc_code, name, generic_name=None, group_name=None,
                              dosage_form=None, form=None, route_options=None,
                              indications=None, contraindications=None, interactions=None,
                              pregnancy=None, dosage_text=None, dose_note=None,
                              frequency=None, max_daily_mg=None, default_dose=None,
                              default_frequency=None, protocol_ref=None, note=None,
                              category=None, verify_flag=None):
    db.execute(
        "INSERT INTO drug_catalog (atc_code, name, generic_name, group_name, dosage_form, "
        "form, route_options, indications, contraindications, interactions, pregnancy, "
        "dosage_text, dose_note, frequency, max_daily_mg, "
        "default_dose, default_frequency, protocol_ref, note, category, verify_flag) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(atc_code) DO UPDATE SET name=EXCLUDED.name, generic_name=EXCLUDED.generic_name, "
        "group_name=EXCLUDED.group_name, dosage_form=EXCLUDED.dosage_form, form=EXCLUDED.form, "
        "route_options=EXCLUDED.route_options, indications=EXCLUDED.indications, "
        "contraindications=EXCLUDED.contraindications, interactions=EXCLUDED.interactions, "
        "pregnancy=EXCLUDED.pregnancy, dosage_text=EXCLUDED.dosage_text, "
        "dose_note=EXCLUDED.dose_note, frequency=EXCLUDED.frequency, "
        "max_daily_mg=EXCLUDED.max_daily_mg, default_dose=EXCLUDED.default_dose, "
        "default_frequency=EXCLUDED.default_frequency, protocol_ref=EXCLUDED.protocol_ref, "
        "note=EXCLUDED.note, category=EXCLUDED.category, "
        "verify_flag=EXCLUDED.verify_flag",
        (atc_code, name, generic_name, group_name, dosage_form, form, route_options,
         indications, contraindications, interactions, pregnancy, dosage_text, dose_note,
         frequency, max_daily_mg, default_dose, default_frequency,
         protocol_ref, note, category, verify_flag))


# ============ AllergyIntolerance ============

def get_allergies(pid):
    c = _cached(pid, "allergies")
    if c is not None:
        return c
    return db.fetchall("SELECT * FROM allergy_intolerance WHERE patient_id = %s", (pid,))

def betalactam_allergy_type(pid):
    """
    Возвращает тип реакции на β-лактамы: 'ige' / 'non-ige' / None.
    Используется протоколом ВП для выбора пути (п.19 vs п.21).
    """
    rows = get_allergies(pid)
    for a in rows:
        code = (a.get("code") or "").lower()
        disp = (a.get("display") or "").lower()
        is_bl = any(t in code or t in disp for t in
                    ("beta-lactam", "penicillin", "cephalosporin", "амокс", "β-лакт"))
        if is_bl:
            rt = (a.get("reaction_type") or "unknown").lower()
            if rt == "non-ige":
                return "non-ige"
            if rt == "ige":
                return "ige"
            return "ige"  # при неизвестном типе — перестраховываемся как IgE (п.19)
    return None

def add_allergy(pid, code, display, criticality="high", reaction_type="unknown", recorded_date=None):
    aid = _new_id("a")
    if not recorded_date:
        recorded_date = _today()
    db.execute(
        "INSERT INTO allergy_intolerance (id, patient_id, code, display, criticality, reaction_type, recorded_date) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (aid, pid, code, display, criticality, reaction_type, recorded_date))
    return aid


# ============ CarePlan + Goal ============

def get_care_plans(pid, status="active"):
    cached = _cached(pid, "care_plans")
    if cached is not None:
        return [cp for cp in cached if status is None or cp["status"] == status]
    return db.fetchall("SELECT * FROM care_plan WHERE patient_id = %s AND status = %s",
                       (pid, status))

def add_care_plan(pid, condition_id=None, period_start=None, period_end=None):
    cpid = _new_id("cp")
    if not period_start:
        period_start = _today()
    db.execute(
        "INSERT INTO care_plan (id, patient_id, condition_id, status, intent, period_start, period_end, created_date) "
        "VALUES (%s,%s,%s,'active','plan',%s,%s,%s)",
        (cpid, pid, condition_id, period_start, period_end, _today()))
    return cpid

def get_goals(pid, status=None):
    cached = _cached(pid, "goals")
    if cached is not None:
        return [g for g in cached if status is None or g["status"] == status]
    sql = "SELECT * FROM goal WHERE patient_id = %s"
    params = [pid]
    if status:
        sql += " AND status = %s"
        params.append(status)
    return db.fetchall(sql, tuple(params))

def add_goal(pid, care_plan_id, description, target_metric, target_value, target_unit=None):
    gid = _new_id("g")
    db.execute(
        "INSERT INTO goal (id, patient_id, care_plan_id, description, target_metric, target_value, "
        "target_unit, status, start_date) VALUES (%s,%s,%s,%s,%s,%s,%s,'in-progress',%s)",
        (gid, pid, care_plan_id, description, target_metric, target_value, target_unit, _today()))
    return gid

def set_goal_status(gid, status, achievement_date=None):
    if not achievement_date:
        achievement_date = _today()
    db.execute("UPDATE goal SET status=%s, achievement_date=%s WHERE id=%s",
                (status, achievement_date, gid))


# ============ Pathway ============

def get_pathway(pid):
    c = _cached(pid, "pathway")
    if c is not None:
        return c or {"state": "unknown", "label": "—"}
    return db.fetchone("SELECT * FROM pathway WHERE patient_id = %s", (pid,)) \
        or {"state": "unknown", "label": "—"}

def set_pathway(pid, state, label):
    existing = get_pathway(pid)
    if existing and existing.get("state") != "unknown":
        db.execute("UPDATE pathway SET state=%s, label=%s WHERE patient_id=%s", (state, label, pid))
    else:
        db.execute("INSERT INTO pathway (patient_id, state, label) VALUES (%s,%s,%s)", (pid, state, label))


# ============ Clinical flags (анамнез/осмотр/контекст) ============

def get_flags(pid, category=None):
    """Все флаги пациента (опционально по категории)."""
    cached = _cached(pid, "flags")
    if cached is not None:
        return [f for f in cached if category is None or f["category"] == category]
    if category:
        return db.fetchall(
            "SELECT * FROM clinical_flag WHERE patient_id = %s AND category = %s ORDER BY recorded_date DESC",
            (pid, category))
    return db.fetchall(
        "SELECT * FROM clinical_flag WHERE patient_id = %s ORDER BY recorded_date DESC", (pid,))


def get_flag(pid, key):
    """Последнее значение флага по ключу или None."""
    cached = _cached(pid, "flags")
    if cached is not None:
        for f in cached:
            if f["key"] == key:
                return f
        return None
    return db.fetchone(
        "SELECT * FROM clinical_flag WHERE patient_id = %s AND key = %s "
        "ORDER BY recorded_date DESC LIMIT 1",
        (pid, key))


def has_flag(pid, key, value="true"):
    """True, если последний флаг с этим ключом равен value (по умолчанию 'true')."""
    r = get_flag(pid, key)
    return bool(r) and r["value"] == value


def add_flag(pid, key, value="true", category="exam", encounter_id=None, recorded_date=None):
    fid = _new_id("f")
    if not recorded_date:
        recorded_date = _today()
    db.execute(
        "INSERT INTO clinical_flag (id, patient_id, encounter_id, key, value, category, recorded_date) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (fid, pid, encounter_id, key, value, category, recorded_date))
    return fid


def delete_flag(fid):
    db.execute("DELETE FROM clinical_flag WHERE id = %s", (fid,))


# ============ Кэш оценки по протоколу ВП ============

def save_cap_cache(pid, verdict):
    """Сохраняет сводку CAP-оценки + UI-шаг, чтобы дашборд не делал N+1."""
    from protocol_verdict import verdict_for_ui

    applicable = 1 if verdict.get("applicable") else 0
    compliant = 1 if verdict.get("compliant") else 0
    severity = verdict.get("severity")
    setting = verdict.get("setting") if verdict.get("applicable") else None
    ui = verdict_for_ui(verdict)
    next_step = (ui.get("next_step") or "")[:240] or None
    headline = (ui.get("headline") or "")[:160] or None
    db.execute(
        "INSERT INTO cap_cache (patient_id, applicable, severity, setting, compliant, "
        "computed_at, next_step, headline) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(patient_id) DO UPDATE SET applicable=EXCLUDED.applicable, "
        "severity=EXCLUDED.severity, setting=EXCLUDED.setting, compliant=EXCLUDED.compliant, "
        "computed_at=EXCLUDED.computed_at, next_step=EXCLUDED.next_step, "
        "headline=EXCLUDED.headline",
        (pid, applicable, severity, setting, compliant, _today(), next_step, headline))


def get_cap_cache(pid):
    return db.fetchone("SELECT * FROM cap_cache WHERE patient_id = %s", (pid,))


def get_all_cap_caches():
    return db.fetchall("SELECT * FROM cap_cache")


def get_all_pathways():
    """Все pathway сразу (для дашборда) — одним запросом."""
    return {r["patient_id"]: r for r in db.fetchall("SELECT * FROM pathway")}


# ============ Опциональные демо-данные ============

def seed_demo():
    """Если БД пуста — быстрый набор Орлов/Б/В (кнопка на дашборде).

    Полные 10 сценариев: `python3 tools/seed_ten.py` (или prepare_demo_db.py).
    """
    if get_all_patients():
        return
    from _seed_data import seed_all
    seed_all()


