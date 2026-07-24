"""
Слой 0 — Хранилище данных (SQLite, FHIR-подобная модель).

Постоянное хранилище. Бэкап = скопировать файл clinic.db.
В реальной системе здесь был бы FHIR-сервер (HAPI FHIR),
но для маленькой клиники без IT-команды SQLite — разумный выбор:
один файл, без сервера, переносится на любой компьютер.

Модель данных близка к FHIR: Patient, Condition, Observation, MedicationRequest.
Это значит, что при росте можно мигрировать на настоящий FHIR-сервер
без переписывания бизнес-логики.
"""
import sqlite3
import os
from datetime import datetime, date

DB_PATH = os.path.join(os.path.dirname(__file__), "clinic.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Создаёт таблицы и заполняет тестовыми данными, если БД пустая."""
    conn = get_db()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS patient (
        id TEXT PRIMARY KEY,
        family TEXT, given TEXT, patronymic TEXT,
        gender TEXT, birth_date TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS condition_ (
        id TEXT PRIMARY KEY,
        patient_id TEXT, code_system TEXT, code TEXT, display TEXT,
        clinical_status TEXT, onset_date TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS observation (
        id TEXT PRIMARY KEY,
        patient_id TEXT, code TEXT, systolic INTEGER, diastolic INTEGER,
        status TEXT, date TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS medication_request (
        id TEXT PRIMARY KEY,
        patient_id TEXT, code TEXT, display TEXT, status TEXT, date TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS pathway (
        patient_id TEXT PRIMARY KEY,
        state TEXT, label TEXT
    )""")

    # Заполняем, если пусто
    c.execute("SELECT COUNT(*) FROM patient")
    if c.fetchone()[0] == 0:
        _seed(c)

    conn.commit()
    conn.close()

def _seed(c):
    """Тестовые данные: 10 пациентов с гипертонией."""
    patients = [
        ("p1","Иванов","Иван","Иванович","male","1965-03-15"),
        ("p2","Петрова","Мария","Сергеевна","female","1980-07-22"),
        ("p3","Сидоров","Пётр","Алексеевич","male","1958-11-03"),
        ("p4","Кузнецова","Анна","Владимировна","female","1990-02-14"),
        ("p5","Смирнов","Алексей","Дмитриевич","male","1972-09-30"),
        ("p6","Волкова","Елена","Игоревна","female","1968-05-18"),
        ("p7","Морозов","Дмитрий","Николаевич","male","1955-12-01"),
        ("p8","Орлова","Татьяна","Михайловна","female","1985-04-25"),
        ("p9","Новиков","Сергей","Андреевич","male","1978-08-10"),
        ("p10","Зайцева","Ольга","Петровна","female","1963-06-07"),
    ]
    c.executemany("INSERT INTO patient VALUES (?,?,?,?,?,?)", patients)

    conditions = [
        ("c1","p1","ICD-10","I10","Гипертензивная болезнь","active","2020-01-15"),
        ("c2","p2","ICD-10","I10","Гипертензивная болезнь","active","2021-06-20"),
        ("c3","p3","ICD-10","I10","Гипертензивная болезнь","active","2018-03-10"),
        ("c4","p4","ICD-10","I10","Гипертензивная болезнь","active","2022-09-05"),
        ("c5","p5","ICD-10","I10","Гипертензивная болезнь","active","2019-11-12"),
        ("c6","p6","ICD-10","I10","Гипертензивная болезнь","active","2020-04-18"),
        ("c7","p7","ICD-10","I10","Гипертензивная болезнь","active","2017-02-28"),
        ("c8","p8","ICD-10","I10","Гипертензивная болезнь","active","2023-01-15"),
        ("c9","p9","ICD-10","I10","Гипертензивная болезнь","active","2021-10-03"),
        ("c10","p10","ICD-10","I10","Гипертензивная болезнь","active","2019-07-22"),
    ]
    c.executemany("INSERT INTO condition_ VALUES (?,?,?,?,?,?,?)", conditions)

    observations = [
        ("o1","p1","BP",155,95,"final","2026-07-15"),
        ("o2","p2","BP",128,82,"final","2026-07-10"),
        ("o3","p3","BP",162,98,"final","2026-07-12"),
        ("o4","p4","BP",135,85,"final","2026-07-08"),
        ("o5","p5","BP",148,92,"final","2026-07-14"),
        ("o6","p6","BP",130,80,"final","2026-07-09"),
        ("o7","p7","BP",170,105,"final","2026-07-11"),
        ("o8","p8","BP",138,88,"final","2026-07-13"),
        ("o9","p9","BP",158,96,"final","2026-07-16"),
        ("o10","p10","BP",132,82,"final","2026-07-07"),
    ]
    c.executemany("INSERT INTO observation VALUES (?,?,?,?,?,?,?)", observations)

    meds = [
        ("m1","p1","C09AA01","Эналаприл 10 мг","active","2024-01-10"),
        ("m2","p2","C09AA01","Эналаприл 10 мг","active","2023-06-15"),
        ("m3","p3","C09AA01","Эналаприл 10 мг","active","2022-03-20"),
        ("m4","p4","C09AA01","Эналаприл 5 мг","active","2024-09-10"),
        ("m5","p5","C09AA01","Эналаприл 10 мг","active","2023-11-15"),
        ("m6","p6","C07AB02","Бисопролол 5 мг","active","2023-04-20"),
        ("m7","p7","C09AA01","Эналаприл 20 мг","active","2021-02-28"),
        ("m8","p8","C07AB02","Бисопролол 5 мг","active","2025-01-15"),
        ("m9","p9","C09AA01","Эналаприл 10 мг","active","2023-10-03"),
        ("m10","p10","C07AB02","Бисопролол 2.5 мг","active","2022-07-22"),
    ]
    c.executemany("INSERT INTO medication_request VALUES (?,?,?,?,?,?)", meds)

    pathways = [
        ("p1","monitoring","Мониторинг"),
        ("p2","controlled","Контролируется"),
        ("p3","adjustment","Коррекция терапии"),
        ("p4","controlled","Контролируется"),
        ("p5","monitoring","Мониторинг"),
        ("p6","controlled","Контролируется"),
        ("p7","adjustment","Коррекция терапии"),
        ("p8","controlled","Контролируется"),
        ("p9","monitoring","Мониторинг"),
        ("p10","controlled","Контролируется"),
    ]
    c.executemany("INSERT INTO pathway VALUES (?,?,?)", pathways)

# --- API ---

def get_all_patients():
    conn = get_db()
    rows = conn.execute("SELECT * FROM patient ORDER BY family").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_patient(pid):
    conn = get_db()
    r = conn.execute("SELECT * FROM patient WHERE id=?", (pid,)).fetchone()
    conn.close()
    return dict(r) if r else None

def get_condition(pid):
    conn = get_db()
    r = conn.execute("SELECT * FROM condition_ WHERE patient_id=? AND clinical_status='active'", (pid,)).fetchone()
    conn.close()
    return dict(r) if r else None

def get_last_bp(pid):
    conn = get_db()
    r = conn.execute(
        "SELECT * FROM observation WHERE patient_id=? AND code='BP' ORDER BY date DESC LIMIT 1",
        (pid,)
    ).fetchone()
    conn.close()
    return dict(r) if r else None

def get_medications(pid):
    conn = get_db()
    rows = conn.execute("SELECT * FROM medication_request WHERE patient_id=? AND status='active'", (pid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_pathway(pid):
    conn = get_db()
    r = conn.execute("SELECT * FROM pathway WHERE patient_id=?", (pid,)).fetchone()
    conn.close()
    return dict(r) if r else {"state":"unknown","label":"—"}

def add_bp_observation(pid, systolic, diastolic, obs_date=None):
    """Записывает новое измерение АД."""
    if obs_date is None:
        obs_date = date.today().isoformat()
    conn = get_db()
    oid = f"o-{pid}-{int(datetime.now().timestamp())}"
    conn.execute(
        "INSERT INTO observation (id, patient_id, code, systolic, diastolic, status, date) VALUES (?,?,?,?,?,?,?)",
        (oid, pid, "BP", int(systolic), int(diastolic), "final", obs_date)
    )
    conn.commit()
    conn.close()

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
