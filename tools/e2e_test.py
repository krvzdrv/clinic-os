#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2E-тест: 5 взрослых пациентов (КП №768), разные исходы."""
import json, re, sys, urllib.request, urllib.parse, urllib.error

BASE = "http://127.0.0.1:5566"

class _NoFollow(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)
    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoFollow())
urllib.request.install_opener(_opener)

def post(path, data=None):
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=15)
        return r.status, r.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location", "") if hasattr(e, "headers") else ""

def post_json(path, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(BASE + path, data=body, method="POST",
                                  headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=15)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")

def get(path):
    try:
        r = urllib.request.urlopen(BASE + path, timeout=15)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")

def pid_from_loc(loc):
    m = re.search(r"/patient/(p-[a-f0-9]+)", loc)
    return m.group(1) if m else None

def parse_ids(html):
    encs = re.findall(r'name="encounter_id"><option value="([^"]+)"', html)
    meds = re.findall(r'/medication/(m-[a-f0-9]+)/stop', html)
    flags = re.findall(r'/flag/(f-[a-f0-9]+)/clear', html)
    return encs, meds, flags

def cap_api(pid):
    st, body = get(f"/api/protocol-cap/{pid}")
    if st == 200:
        return json.loads(body)
    return {}

def act(label, status, expect=302):
    ok = (status == expect) or (expect == "30x" and 300 <= status < 400)
    print(f"  [{'OK' if ok else 'FAIL'}] {label}: {status}")
    return ok

def new_patient(family, given, pat, gender, bd):
    st, loc = post("/patient/new", {"family": family, "given": given,
        "patronymic": pat, "gender": gender, "birth_date": bd})
    return pid_from_loc(loc)

def common_buttons(pid):
    st, _ = post(f"/patient/{pid}/cap/plan"); act("cap/plan", st)
    st, _ = post(f"/patient/{pid}/cap/followup", {"days": 3}); act("cap/followup", st)
    st, _ = post(f"/patient/{pid}/followup", {"days": 5}); act("followup", st)
    st, _ = post(f"/patient/{pid}/evaluate"); act("evaluate", st)
    st, _ = post(f"/patient/{pid}/careplan"); act("careplan", st)
    st, body = get(f"/api/protocol-cap/{pid}"); act("api/protocol-cap", st, 200)
    st, _ = post_json("/cds-services/patient-view", {"context": {"patientId": pid}}); act("cds patient-view", st, 200)
    st, body = get(f"/patient/{pid}"); act("GET card", st, 200)
    return body

def add_encounter(pid, cls, complaint, start=None):
    st, _ = post(f"/patient/{pid}/encounter", {"class": cls, "complaint": complaint, **({"start": start} if start else {})})
    act(f"encounter({cls})", st)
    st, body = get(f"/patient/{pid}")
    encs, _, _ = parse_ids(body)
    return encs[0] if encs else None

def obs(pid, eid, code, val, date="2026-07-24"):
    st, _ = post(f"/patient/{pid}/observation",
        {"encounter_id": eid, "code": code, "value_numeric": str(val), "date": date}); act(f"obs {code}={val}", st)
def sr(pid, eid, code, date="2026-07-24"):
    st, _ = post(f"/patient/{pid}/service_request",
        {"encounter_id": eid, "code": code, "occurrence_date": date}); act(f"order {code}", st)
def med(pid, eid, code, display, route, period_end, dose="500 мг", freq="3 раза в день", start="2026-07-24"):
    st, _ = post(f"/patient/{pid}/medication",
        {"encounter_id": eid, "code": code, "display": display, "dose": dose, "frequency": freq,
         "route": route, "med_date": start, "period_end": period_end}); act(f"med {code}", st)
def flag(pid, eid, key):
    st, _ = post(f"/patient/{pid}/flag", {"encounter_id": eid, "key": key}); act(f"flag {key}", st)
def cond(pid, code, disp, onset):
    st, _ = post(f"/patient/{pid}/condition", {"code": code, "display": disp, "onset_date": onset}); act(f"dx {code}", st)

def expect(pid, label, setting=None, severity=None, compliant=None, warn_codes=None, no_warn=None):
    cap = cap_api(pid)
    fails = []
    if setting is not None and cap.get("setting") != setting:
        fails.append(f"setting={cap.get('setting')} != {setting}")
    if severity is not None and cap.get("severity") != severity:
        fails.append(f"severity={cap.get('severity')} != {severity}")
    if compliant is not None and bool(cap.get("compliant")) != bool(compliant):
        fails.append(f"compliant={cap.get('compliant')} != {compliant}")
    gaps = cap.get("gaps", [])
    wcodes = {g["code"] for g in gaps if g.get("severity") == "warning"}
    if warn_codes:
        for c in warn_codes:
            if c not in wcodes:
                fails.append(f"ожидался warning-зазор '{c}', нет (есть {sorted(wcodes)})")
    if no_warn:
        for c in no_warn:
            if c in wcodes:
                fails.append(f"не ожидался warning-зазор '{c}', но он есть")
    if fails:
        print(f"  [FAIL] ИСХОД {label}: " + "; ".join(fails))
        return False
    print(f"  [ OK ] ИСХОД {label}: setting={cap.get('setting')} severity={cap.get('severity')} compliant={cap.get('compliant')} warnings={sorted(wcodes)}")
    return True

def p1():
    """Эталон: амбулаторная нетяжёлая ВП, амоксициллин per os 10д."""
    pid = new_patient("Амбулаторов", "Антон", "Петрович", "male", "1985-03-12")
    eid = add_encounter(pid, "ambulatory", "Кашель, лихорадка 3 дня", "2026-07-24")
    cond(pid, "J18.9", "Пневмония неуточненная", "2026-07-22")
    obs(pid, eid, "8310-5", 38.6); obs(pid, eid, "59408-5", 96); obs(pid, eid, "9279-1", 22); obs(pid, eid, "8867-4", 90)
    sr(pid, eid, "CBC"); sr(pid, eid, "CRP"); sr(pid, eid, "CXR_REPEAT")
    med(pid, eid, "J01CA04", "Амоксициллин 500мг", "oral", "2026-08-03", dose="500 мг")
    common_buttons(pid)
    expect(pid, "P1 compliant outpatient", setting="outpatient", severity="mild", compliant=True,
           no_warn=["no_abt","not_first_line_abt","parenteral_in_outpatient","course_too_short","hospitalization_indicated","icu_indicated"])
    st, _ = post(f"/patient/{pid}/encounter/{eid}/finish"); act("finish encounter", st)
    return pid

def p2():
    """Нетяжёлая ВП в стационаре, цефтриаксон в/в (без ОРИТ)."""
    pid = new_patient("Стационаров", "Сергей", "Иванович", "male", "1970-06-01")
    eid = add_encounter(pid, "inpatient", "Госпитализация: ВП", "2026-07-24")
    cond(pid, "J18.1", "Долевая пневмония", "2026-07-19")
    obs(pid, eid, "8310-5", 37.0); obs(pid, eid, "59408-5", 96); obs(pid, eid, "9279-1", 22); obs(pid, eid, "8867-4", 80)
    sr(pid, eid, "CBC"); sr(pid, eid, "CRP"); sr(pid, eid, "URINE"); sr(pid, eid, "ECG")
    sr(pid, eid, "BLOOD_CULT"); sr(pid, eid, "CXR"); sr(pid, eid, "CXR_REPEAT")
    med(pid, eid, "J01DD04", "Цефтриаксон", "iv", "2026-08-03", dose="1–2 г", freq="1 раз в день")
    st, _ = post(f"/patient/{pid}/cap/admit"); act("cap/admit", st)
    common_buttons(pid)
    expect(pid, "P2 compliant inpatient", setting="inpatient", severity="mild", compliant=True,
           no_warn=["no_abt","not_inpatient_first_line","oral_in_inpatient","course_too_short",
                    "hospitalization_indicated","icu_indicated"])
    st, _ = post(f"/patient/{pid}/cap/discharge"); act("cap/discharge", st)
    return pid

def p3():
    """IgE-аллергия, назначен амоксициллин → not_first_line_abt."""
    pid = new_patient("Аллергов", "Алиса", "Сергеевна", "female", "1988-02-20")
    post(f"/patient/{pid}/allergy", {"code": "beta-lactam", "display": "Пенициллины", "reaction_type": "ige"})
    eid = add_encounter(pid, "ambulatory", "Кашель, t 38.5", "2026-07-24")
    cond(pid, "J13", "Пневмококковая пневмония", "2026-07-23")
    obs(pid, eid, "8310-5", 38.5); obs(pid, eid, "59408-5", 95); obs(pid, eid, "9279-1", 22); obs(pid, eid, "8867-4", 90)
    sr(pid, eid, "CBC"); sr(pid, eid, "CRP"); sr(pid, eid, "CXR_REPEAT")
    med(pid, eid, "J01CA04", "Амоксициллин", "oral", "2026-08-03")
    common_buttons(pid)
    expect(pid, "P3 wrong drug (allergy)", setting="outpatient", severity="mild", compliant=False,
           warn_codes=["not_first_line_abt"])
    st, body = get(f"/patient/{pid}"); _, meds, _ = parse_ids(body)
    if meds:
        st, _ = post(f"/patient/{pid}/medication/{meds[0]}/stop"); act("stop medication", st)
    return pid

def p4():
    """Фактор риска (АБТ 3 мес), но дан цефтриаксон в/в коротким курсом."""
    pid = new_patient("Факторов", "Фёдор", "Игоревич", "male", "1978-09-15")
    eid = add_encounter(pid, "ambulatory", "Затяжной кашель, лихорадка", "2026-07-24")
    cond(pid, "J18.0", "Бронхопневмония", "2026-07-21")
    flag(pid, eid, "abt_3mo")
    obs(pid, eid, "8310-5", 38.2); obs(pid, eid, "59408-5", 94); obs(pid, eid, "9279-1", 24); obs(pid, eid, "8867-4", 100)
    sr(pid, eid, "CBC"); sr(pid, eid, "CRP"); sr(pid, eid, "CXR_REPEAT")
    med(pid, eid, "J01DD04", "Цефтриаксон", "iv", "2026-07-29", dose="1 г", freq="1 раз в день")
    common_buttons(pid)
    expect(pid, "P4 wrong drug+route+course", setting="outpatient", severity="mild", compliant=False,
           warn_codes=["not_first_line_abt", "parenteral_in_outpatient", "course_too_short"])
    st, body = get(f"/patient/{pid}"); _, _, flags = parse_ids(body)
    if flags:
        st, _ = post(f"/patient/{pid}/flag/{flags[0]}/clear"); act("clear flag", st)
    return pid

def p5():
    """Тяжёлая ВП амбулаторно без АБТ → hospitalization + no_abt."""
    pid = new_patient("Тяжёлов", "Тимур", "Александрович", "male", "1965-11-05")
    eid = add_encounter(pid, "ambulatory", "Выраженная одышка, t 39.5", "2026-07-24")
    cond(pid, "J18.9", "Пневмония неуточненная", "2026-07-22")
    obs(pid, eid, "8310-5", 39.5); obs(pid, eid, "59408-5", 87); obs(pid, eid, "9279-1", 34); obs(pid, eid, "8867-4", 130)
    sr(pid, eid, "CBC"); sr(pid, eid, "CRP")
    common_buttons(pid)
    expect(pid, "P5 severe outpatient, no ABT", setting="inpatient", severity="severe", compliant=False,
           warn_codes=["hospitalization_indicated", "no_abt"])
    return pid

def main():
    st, _ = get("/"); act("dashboard GET", st, 200)
    st, _ = get("/export"); act("export CSV", st, 200)
    st, body = get("/api/measure"); act("api/measure", st, 200)
    flows = [("P1 Амбулаторов (эталон)", p1), ("P2 Стационов (compliant inpatient)", p2),
             ("P3 Аллергов (wrong drug)", p3), ("P4 Факторов (wrong+route+course)", p4),
             ("P5 Тяжёлов (severe, no ABT)", p5)]
    pids = []
    for name, f in flows:
        print(f"\n=== {name} ===")
        pids.append(f())
    pid_del = new_patient("Удалён", "Умберто", "Эдуардович", "male", "1980-01-01")
    st, _ = post(f"/patient/{pid_del}/delete"); act("delete patient", st)
    st, _ = get(f"/patient/{pid_del}"); act("deleted gone (404)", st, 404)
    print("\n=== ИТОГ ===")
    print(f"Создано пациентов: {len(pids)}; удалено: 1 (тест кнопки удаления)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
