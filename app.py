"""
Главная точка входа — Flask-приложение.

Слои:
  fhir_store.py   → Слой 0: Хранилище данных (SQLite, FHIR-подобная модель)
  rules_engine.py  → Слой 3: Движок правил (CQL-like)
  cds_service.py   → Слой 5: CDS Hooks сервис (точка помощи)

Маршруты:
  /                  → Дашборд (метрика качества + список пациентов)
  /patient/<id>     → Карта пациента (с CDS-карточками)
  /patient/<id>/bp  → Записать новое измерение АД (форма)
  /export            → Экспорт списка пациентов в CSV
  /cds/patient-view  → CDS Hooks API (JSON)
  /cds/order-sign    → CDS Hooks API (JSON)
  /api/measure       → API метрики качества (JSON)
"""
import csv
import io
from flask import Flask, render_template, jsonify, request, redirect, url_for, Response
from fhir_store import (
    init_db, get_all_patients, get_patient, get_condition, get_last_bp,
    get_medications, get_pathway, get_age, is_fertile_female, add_bp_observation
)
from rules_engine import (
    has_hypertension, uncontrolled_bp, bp_overdue, has_diabetes,
    dual_ace_therapy, quality_measure_controlled, ace_inhibitor_contraindicated
)
from cds_service import cds_patient_view, cds_order_sign

app = Flask(__name__)
init_db()  # создаёт БД и заполняет тестовыми данными при первом запуске

# --- Дашборд ---
@app.route("/")
def dashboard():
    measure = quality_measure_controlled()
    patients = get_all_patients()
    patient_rows = []
    for p in patients:
        pid = p["id"]
        bp = get_last_bp(pid)
        pathway = get_pathway(pid)
        patient_rows.append({
            "id": pid,
            "name": f"{p['family']} {p['given'][0]}. {p['patronymic'][0]}.",
            "age": get_age(pid),
            "gender": p["gender"],
            "bp": f"{bp['systolic']}/{bp['diastolic']}" if bp else "—",
            "bp_date": bp["date"] if bp else "—",
            "controlled": not uncontrolled_bp(pid),
            "overdue": bp_overdue(pid),
            "diabetes": has_diabetes(pid),
            "state": pathway["state"],
            "state_label": pathway["label"],
        })
    return render_template("dashboard.html", measure=measure, patients=patient_rows)

# --- Карта пациента ---
@app.route("/patient/<pid>")
def patient_detail(pid):
    p = get_patient(pid)
    if not p:
        return "Пациент не найден", 404
    condition = get_condition(pid)
    bp = get_last_bp(pid)
    meds = get_medications(pid)
    pathway = get_pathway(pid)
    cards = cds_patient_view(pid)
    return render_template("patient.html",
        patient=p,
        full_name=f"{p['family']} {p['given']} {p['patronymic']}",
        age=get_age(pid),
        condition=condition,
        bp=bp,
        meds=meds,
        pathway=pathway,
        cards=cards,
        uncontrolled=uncontrolled_bp(pid),
        overdue=bp_overdue(pid),
        diabetes=has_diabetes(pid),
        fertile=is_fertile_female(pid),
    )

# --- Записать новое измерение АД ---
@app.route("/patient/<pid>/bp", methods=["GET", "POST"])
def record_bp(pid):
    p = get_patient(pid)
    if not p:
        return "Пациент не найден", 404
    if request.method == "POST":
        systolic = request.form.get("systolic")
        diastolic = request.form.get("diastolic")
        obs_date = request.form.get("date")
        if systolic and diastolic:
            add_bp_observation(pid, systolic, diastolic, obs_date or None)
        return redirect(url_for("patient_detail", pid=pid))
    return render_template("record_bp.html",
        patient=p,
        full_name=f"{p['family']} {p['given']} {p['patronymic']}",
    )

# --- Экспорт в CSV ---
@app.route("/export")
def export_csv():
    patients = get_all_patients()
    output = io.StringIO()
    output.write("\ufeff")  # BOM для корректного Excel
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["ФИО", "Возраст", "Пол", "Диагноз", "Код МКБ",
                     "Последнее АД", "Дата измерения", "Контроль",
                     "Передержка", "Диабет", "Этап пути"])
    for p in patients:
        pid = p["id"]
        bp = get_last_bp(pid)
        condition = get_condition(pid)
        pathway = get_pathway(pid)
        writer.writerow([
            f"{p['family']} {p['given']} {p['patronymic']}",
            get_age(pid),
            "М" if p["gender"] == "male" else "Ж",
            condition["display"] if condition else "—",
            condition["code"] if condition else "—",
            f"{bp['systolic']}/{bp['diastolic']}" if bp else "—",
            bp["date"] if bp else "—",
            "Контролируется" if not uncontrolled_bp(pid) else "Не контролируется",
            "Да" if bp_overdue(pid) else "Нет",
            "Да" if has_diabetes(pid) else "Нет",
            pathway["label"],
        ])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=patients_report.csv"}
    )

# --- CDS Hooks API ---
@app.route("/cds-services/patient-view", methods=["POST"])
def cds_hook_patient_view():
    data = request.get_json()
    pid = data.get("context", {}).get("patientId", "")
    cards = cds_patient_view(pid)
    return jsonify({"cards": cards})

@app.route("/cds-services/order-sign", methods=["POST"])
def cds_hook_order_sign():
    data = request.get_json()
    pid = data.get("context", {}).get("patientId", "")
    med_code = data.get("context", {}).get("medicationCode", "")
    cards = cds_order_sign(pid, med_code)
    return jsonify({"cards": cards})

@app.route("/api/measure")
def api_measure():
    return jsonify(quality_measure_controlled())

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5566)
