"""
Главная точка входа — Flask-приложение (UI-слой, Слой 7).

Слои под капотом:
  db.py            — подключение к БД (Supabase/SQLite)
  fhir_store.py    — репозиторий FHIR-ресурсов
  rules_engine.py  — клинические правила (Слой 3)
  drug_service.py  — проверка лекарств (Слой 2)
  protocol_engine.py — регламент лечения (Слой 3b, независимый)
  care_plan_service.py — план/цель/цикл (Слой 4)
  cds_service.py   — CDS-карточки (Слой 5)

UI отражает реальный процесс приёма: жалоба → осмотр/измерения → заказы
анализов → результаты → диагноз → назначение лечения (с проверкой) →
цель → контрольный визит → оценка результата → коррекция.
"""
from dotenv import load_dotenv
load_dotenv()  # подгружает .env из корня проекта, если он есть

from flask import (
    Flask, render_template, jsonify, request, redirect, url_for, Response,
)

import fhir_store as fs
import rules_engine as re
import drug_service
import protocol_engine as pe
import protocol_cap as pcap
import care_plan_service as cps
import cds_service as cds
from terminology import LOINC, STUDIES, loinc_display, loinc_unit, interpret_value, atc_display, study_display

app = Flask(__name__)
fs.init_db()


# ---------- Дашборд ----------
@app.route("/")
def dashboard():
    measure = re.quality_measure_controlled()
    rows = []
    for p in fs.get_all_patients():
        pid = p["id"]
        bp = fs.get_last_bp(pid)
        pathway = fs.get_pathway(pid)
        rows.append({
            "id": pid,
            "name": f"{p['family']} {p['given'][0]}. {p['patronymic'][0]}.",
            "age": fs.get_age(pid),
            "gender": p["gender"],
            "bp": f"{bp['systolic']:.0f}/{bp['diastolic']:.0f}" if bp and bp["systolic"] else "—",
            "bp_date": bp["date"] if bp else "—",
            "controlled": not re.uncontrolled_bp(pid),
            "overdue": re.bp_overdue(pid),
            "diabetes": re.has_diabetes(pid),
            "state_label": pathway["label"],
        })
    return render_template("dashboard.html", measure=measure, patients=rows)


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
    bp = fs.get_last_bp(pid)
    protocol = pe.evaluate_htn(pid)
    cap = pcap.evaluate_cap(pid)
    goals = fs.get_goals(pid)
    care_plans = fs.get_care_plans(pid)
    return render_template(
        "patient.html",
        patient=p,
        full_name=f"{p['family']} {p['given']} {p['patronymic']}",
        age=fs.get_age(pid),
        condition=fs.get_condition(pid),
        conditions=fs.get_conditions(pid),
        bp=bp,
        bp_history=fs.get_bp_history(pid),
        observations=fs.get_observations(pid),
        reports=fs.get_diagnostic_reports(pid),
        service_requests=fs.get_service_requests(pid),
        meds=fs.get_medications(pid),
        allergies=fs.get_allergies(pid),
        encounters=fs.get_encounters(pid),
        followups=cps.get_followups(pid),
        care_plans=care_plans,
        goals=goals,
        pathway=fs.get_pathway(pid),
        cards=cds.cds_patient_view(pid),
        protocol=protocol,
        cap=cap,
        has_pneumonia=re.has_pneumonia(pid),
        uncontrolled=re.uncontrolled_bp(pid),
        overdue=re.bp_overdue(pid),
        diabetes=re.has_diabetes(pid),
        fertile=fs.is_fertile_female(pid),
        loinc_options=sorted(LOINC.keys()),
        study_options=sorted(STUDIES.keys()),
        loinc_display=loinc_display,
        study_display=study_display,
    )


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


# ---------- Запись измерения/анализа (observation) ----------
@app.route("/patient/<pid>/observation", methods=["POST"])
def add_observation_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    code = request.form.get("code")
    value = request.form.get("value_numeric")
    fs.add_observation(
        pid, code, loinc_display(code),
        value_numeric=float(value) if value else None,
        value_unit=loinc_unit(code),
        interpretation=interpret_value(code, float(value) if value else None),
        obs_date=request.form.get("date") or None,
        encounter_id=request.form.get("encounter_id") or None,
    )
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Запись АД (быстрый путь) ----------
@app.route("/patient/<pid>/bp", methods=["GET", "POST"])
def record_bp(pid):
    p = fs.get_patient(pid)
    if not p:
        return "Пациент не найден", 404
    if request.method == "POST":
        s = request.form.get("systolic")
        d = request.form.get("diastolic")
        if s and d:
            fs.add_bp_observation(pid, s, d, obs_date=request.form.get("date") or None,
                                   encounter_id=request.form.get("encounter_id") or None)
        return redirect(url_for("patient_detail", pid=pid))
    return render_template("record_bp.html", patient=p,
                           full_name=f"{p['family']} {p['given']} {p['patronymic']}")


# ---------- Заказ исследования (service request) ----------
@app.route("/patient/<pid>/service_request", methods=["POST"])
def add_service_request_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    code = request.form.get("code")
    fs.add_service_request(
        pid, code, study_display(code) if code in STUDIES else loinc_display(code),
        occurrence_date=request.form.get("occurrence_date") or None,
        reason_code=request.form.get("reason_code", "").strip() or None,
    )
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Результат исследования (diagnostic report) ----------
@app.route("/patient/<pid>/report", methods=["POST"])
def add_report_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    code = request.form.get("code")
    fs.add_diagnostic_report(
        pid, code, study_display(code),
        conclusion=request.form.get("conclusion", "").strip() or None,
        rep_date=request.form.get("date") or None,
    )
    # если был заказ — пометим выполненным
    sid = request.form.get("service_request_id")
    if sid:
        fs.complete_service_request(sid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Назначение препарата (с проверкой) ----------
@app.route("/patient/<pid>/medication", methods=["POST"])
def add_medication_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    code = request.form.get("code", "").strip().upper()
    verdict = drug_service.evaluate_medication(pid, code)
    # Сохраняем препарат в любом случае, но предупреждение остаётся в карточках врача.
    fs.add_medication(
        pid, code, request.form.get("display", "").strip(),
        dose=request.form.get("dose", "").strip() or None,
        frequency=request.form.get("frequency", "").strip() or None,
        med_date=request.form.get("med_date") or None,
        period_end=request.form.get("period_end") or None,
    )
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Отмена препарата ----------
@app.route("/patient/<pid>/medication/<mid>/stop", methods=["POST"])
def stop_medication_route(pid, mid):
    fs.stop_medication(mid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Диагноз ----------
@app.route("/patient/<pid>/condition", methods=["POST"])
def add_condition_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    fs.add_condition(
        pid, request.form.get("code", "").strip().upper(),
        request.form.get("display", "").strip(),
        onset_date=request.form.get("onset_date") or None,
    )
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Аллергия ----------
@app.route("/patient/<pid>/allergy", methods=["POST"])
def add_allergy_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    fs.add_allergy(pid, request.form.get("code", "").strip().upper(),
                    request.form.get("display", "").strip())
    return redirect(url_for("patient_detail", pid=pid))


# ---------- План лечения + цель ----------
@app.route("/patient/<pid>/careplan", methods=["POST"])
def create_careplan_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    cps.create_plan(pid)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- План лечения ВП (КП №204) ----------
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


# ---------- Плановый контроль ----------
@app.route("/patient/<pid>/followup", methods=["POST"])
def schedule_followup_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    days = int(request.form.get("days", 14))
    cps.schedule_followup(pid, days=days)
    return redirect(url_for("patient_detail", pid=pid))


# ---------- Оценка достижения цели ----------
@app.route("/patient/<pid>/evaluate", methods=["POST"])
def evaluate_goal_route(pid):
    if not fs.get_patient(pid):
        return "Пациент не найден", 404
    # Выбираем оценку по профилю заболевания
    if re.has_pneumonia(pid):
        cps.evaluate_cap_goal(pid)
    else:
        cps.evaluate_goal(pid)
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
    writer.writerow(["ФИО", "Возраст", "Пол", "Диагноз", "Код", "Последнее АД",
                     "Дата", "Контроль", "Передержка", "Диабет", "Этап"])
    for p in fs.get_all_patients():
        pid = p["id"]
        bp = fs.get_last_bp(pid)
        cond = fs.get_condition(pid)
        pw = fs.get_pathway(pid)
        writer.writerow([
            f"{p['family']} {p['given']} {p['patronymic']}",
            fs.get_age(pid), "М" if p["gender"] == "male" else "Ж",
            cond["display"] if cond else "—", cond["code"] if cond else "—",
            f"{bp['systolic']:.0f}/{bp['diastolic']:.0f}" if bp and bp["systolic"] else "—",
            bp["date"] if bp else "—",
            "Контролируется" if not re.uncontrolled_bp(pid) else "Не контролируется",
            "Да" if re.bp_overdue(pid) else "Нет",
            "Да" if re.has_diabetes(pid) else "Нет", pw["label"],
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
    return jsonify(re.quality_measure_controlled())


@app.route("/api/protocol/<pid>")
def api_protocol(pid):
    return jsonify(pe.evaluate_htn(pid))


@app.route("/api/protocol-cap/<pid>")
def api_protocol_cap(pid):
    return jsonify(pcap.evaluate_cap(pid))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5566, debug=True)
