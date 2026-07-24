"""
Слой 0/1 — Репозиторий FHIR-подобных ресурсов.

Единственный модуль, знающий структуру таблиц. Ходит в БД только через db.py.
Правила (rules_engine), проверка лекарств (drug_service), регламенты
(protocol_engine), CDS (cds_service) и путь пациента (care_plan_service)
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


# ============ Encounter (приём) ============

def get_encounters(pid):
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
    return db.fetchone(
        "SELECT * FROM condition_ WHERE patient_id = %s AND clinical_status = 'active' "
        "ORDER BY recorded_date DESC LIMIT 1", (pid,))

def get_conditions(pid):
    return db.fetchall(
        "SELECT * FROM condition_ WHERE patient_id = %s ORDER BY onset_date DESC", (pid,))

def add_condition(pid, code, display, onset_date=None, encounter_id=None,
                  code_system="ICD-10", clinical_status="active",
                  verification_status="confirmed"):
    cid = _new_id("c")
    if not onset_date:
        onset_date = _today()
    db.execute(
        "INSERT INTO condition_ (id, patient_id, encounter_id, code_system, code, display, "
        "clinical_status, verification_status, onset_date, recorded_date) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (cid, pid, encounter_id, code_system, code, display,
         clinical_status, verification_status, onset_date, _today()))
    return cid


# ============ Observation (числовые измерения и анализы) ============

def get_observations(pid, code=None, limit=None):
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
    return db.fetchone(
        "SELECT * FROM observation WHERE patient_id = %s AND code = %s ORDER BY date DESC LIMIT 1",
        (pid, code))

# Совместимость со старым кодом: АД как пара систола/диастола.
_BP_SYS = "85254-4"   # LOINC: Systolic BP
_BP_DIA = "8462-4"     # LOINC: Diastolic BP

def get_last_bp(pid):
    s = get_last_observation(pid, _BP_SYS)
    d = get_last_observation(pid, _BP_DIA)
    if not s and not d:
        return None
    return {
        "systolic": s["value_numeric"] if s else None,
        "diastolic": d["value_numeric"] if d else None,
        "date": (s or d)["date"],
    }

def get_bp_history(pid):
    """История АД — объединяем систолу и диастолу по дате."""
    rows = db.fetchall(
        "SELECT * FROM observation WHERE patient_id = %s AND code IN (%s,%s) ORDER BY date DESC",
        (pid, _BP_SYS, _BP_DIA))
    by_date = {}
    for r in rows:
        d = r["date"]
        by_date.setdefault(d, {"date": d, "systolic": None, "diastolic": None})
        if r["code"] == _BP_SYS:
            by_date[d]["systolic"] = r["value_numeric"]
        else:
            by_date[d]["diastolic"] = r["value_numeric"]
    return sorted(by_date.values(), key=lambda x: x["date"], reverse=True)

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

def add_bp_observation(pid, systolic, diastolic, obs_date=None, encounter_id=None):
    """Совместимый хелпер: записывает систолу и диастолу двумя observation."""
    if not obs_date:
        obs_date = _today()
    systolic = int(systolic)
    diastolic = int(diastolic)
    add_observation(pid, _BP_SYS, "АД систолическое", value_numeric=systolic,
                    value_unit="mmHg", ref_low=90, ref_high=140,
                    interpretation=_interp_bp(systolic, 140), obs_date=obs_date,
                    encounter_id=encounter_id)
    add_observation(pid, _BP_DIA, "АД диастолическое", value_numeric=diastolic,
                    value_unit="mmHg", ref_low=60, ref_high=90,
                    interpretation=_interp_bp(diastolic, 90), obs_date=obs_date,
                    encounter_id=encounter_id)

def _interp_bp(val, threshold):
    if val is None:
        return None
    return "high" if val > threshold else "normal"


# ============ DiagnosticReport (ЭКГ, УЗИ, холтер) ============

def get_diagnostic_reports(pid):
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
    return db.fetchall(
        "SELECT * FROM medication_request WHERE patient_id = %s AND status = %s ORDER BY date DESC",
        (pid, status))

def add_medication(pid, code, display, dose=None, frequency=None, period_start=None,
                   period_end=None, med_date=None, encounter_id=None, status="active"):
    mid = _new_id("m")
    if not med_date:
        med_date = _today()
    if not period_start:
        period_start = med_date
    db.execute(
        "INSERT INTO medication_request (id, patient_id, encounter_id, code, display, status, "
        "dose, frequency, period_start, period_end, date) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (mid, pid, encounter_id, code, display, status, dose, frequency,
         period_start, period_end, med_date))
    return mid

def stop_medication(mid):
    db.execute("UPDATE medication_request SET status='stopped' WHERE id=%s", (mid,))


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


# ============ AllergyIntolerance ============

def get_allergies(pid):
    return db.fetchall("SELECT * FROM allergy_intolerance WHERE patient_id = %s", (pid,))

def add_allergy(pid, code, display, criticality="high", recorded_date=None):
    aid = _new_id("a")
    if not recorded_date:
        recorded_date = _today()
    db.execute(
        "INSERT INTO allergy_intolerance (id, patient_id, code, display, criticality, recorded_date) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (aid, pid, code, display, criticality, recorded_date))
    return aid


# ============ CarePlan + Goal ============

def get_care_plans(pid, status="active"):
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
    return db.fetchone("SELECT * FROM pathway WHERE patient_id = %s", (pid,)) \
        or {"state": "unknown", "label": "—"}

def set_pathway(pid, state, label):
    existing = get_pathway(pid)
    if existing and existing.get("state") != "unknown":
        db.execute("UPDATE pathway SET state=%s, label=%s WHERE patient_id=%s", (state, label, pid))
    else:
        db.execute("INSERT INTO pathway (patient_id, state, label) VALUES (%s,%s,%s)", (pid, state, label))


# ============ Опциональные демо-данные ============

def seed_demo():
    if get_all_patients():
        return
    from _seed_data import seed_all
    seed_all()
