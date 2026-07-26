"""
Слой 5 — CDS Hooks (точка оказания помощи).

Хуки:
  patient-view — врач открыл карту. Карточки: показания к госпитализации/ОРИТ,
                 отсутствие обязательных исследований, и сводка соответствия
                 протоколу ВП (из protocol_cap — Слой 3b).
  order-sign   — врач назначает препарат. drug_service (аллергии/взаимодействия)
                 + protocol_cap.evaluate_abt_choice (АБТ не по КП №768 → hard-stop
                 с осознанным подтверждением).

Политика сигналов / override / continuous пересчёта:
  docs/processes/CDS_SIGNALING.md (якорь cds_policy в process_registry.yaml).

CDS использует правила (Слой 3), проверку лекарств (Слой 2) и регламент
(Слой 3b), но сам логику не выдумает — только превращает их вердикты в карточки.

Единый источник текста «что не так»: protocol_verdict.verdict_for_ui(assessment).
Карточки CDS не склеивают gap['message']/['recommendation'] заново — иначе
формулировки на дашборде/в карточке пациента и в CDS могут разойтись
(см. docs/processes/CDS_SIGNALING.md).
"""
import fhir_store as fs
import rules_engine as re
import drug_service
import protocol_cap as pcap
import protocol_verdict as pverdict


def cds_patient_view(pid):
    cards = []

    # --- Сводка соответствия протоколу ВП: те же checks, что в карточке/дашборде ---
    assessment = pcap.evaluate_cap(pid)
    if assessment.get("applicable"):
        verdict = pverdict.verdict_for_ui(assessment)
        if not verdict.get("ok"):
            problems = list(verdict.get("checks_primary") or [])
            problems += [c for c in (verdict.get("checks_more") or []) if c.get("level") == "problem"]
            if problems:
                detail = "\n".join(
                    f"• {c['title']}" + (f" → {c['action']}" if c.get("action") else "")
                    for c in problems
                )
                cards.append({
                    "uuid": f"card-cap-{pid}",
                    "summary": verdict.get("headline") or f"Отклонения от протокола ВП: {len(problems)}",
                    "detail": detail,
                    "indicator": "critical" if verdict.get("tier") == "critical" else "warning",
                    "source": {"label": "Регламент ВП (КП №768, единый вердикт)"},
                    "type": "info",
                })

        # --- Показания к госпитализации ---
        if assessment.get("hospitalization"):
            cards.append({
                "uuid": f"card-cap-hosp-{pid}",
                "summary": "Показания к госпитализации: " + "; ".join(assessment["hospitalization"]),
                "detail": "Госпитализация (КП №768).",
                "indicator": "warning",
                "source": {"label": "Регламент ВП (КП №768)"},
                "type": "suggestion",
            })

        # --- Показания к ОРИТ ---
        if assessment.get("icu"):
            cards.append({
                "uuid": f"card-cap-icu-{pid}",
                "summary": "Показания к переводу в ОРИТ: " + "; ".join(assessment["icu"]),
                "detail": "Перевод в отделение реанимации (КП №768).",
                "indicator": "critical",
                "source": {"label": "Регламент ВП (КП №768)"},
                "type": "suggestion",
            })

        # --- Нет АБТ при диагностированной ВП ---
        if not _has_active_antibiotic(pid):
            exp = assessment.get("expected_regimen", {})
            name = exp.get("name") or (exp.get("primary", {}) or {}).get("name")
            cards.append({
                "uuid": f"card-cap-noabt-{pid}",
                "summary": "ВП диагностирована, АБТ не назначена",
                "detail": f"Назначить АБТ первой линии: {name}" if name else "Назначить АБТ.",
                "indicator": "warning",
                "source": {"label": "Регламент ВП (КП №768)"},
                "type": "suggestion",
            })

    # --- Ко-морбидность с диабетом (info) — влияет на тяжесть фона ---
    if re.has_diabetes(pid):
        cards.append({
            "uuid": f"card-diabetes-{pid}",
            "summary": "Сопутствующий сахарный диабет — фактор тяжёлого течения",
            "detail": "Учитывается при решении о госпитализации и выборе режима АБТ.",
            "indicator": "info",
            "source": {"label": "Регламент ВП (КП №768)"},
            "type": "info",
        })

    return cards


def cds_order_sign(pid, medication_code):
    """Хук order-sign: drug_service + соответствие АБТ протоколу ВП."""
    verdict = drug_service.evaluate_medication(pid, medication_code)
    issues = list(verdict.get("issues") or [])
    issues.extend(pcap.evaluate_abt_choice(pid, medication_code))

    cards = []
    hard_stops = [i for i in issues if i["severity"] == "hard-stop"]
    warnings = [i for i in issues if i["severity"] == "warning"]

    if hard_stops:
        cards.append({
            "uuid": f"card-hardstop-{pid}-{medication_code}",
            "summary": hard_stops[0]["message"],
            "detail": "Hard-stop: обязательная текстовая причина назначения.",
            "indicator": "critical",
            "source": {"label": "Проверка лекарств (drug_service)"},
            "type": "hard-stop",
            "overrideAction": "Назначить несмотря на риск",
        })

    if warnings:
        proto = any(i.get("category") == "not_first_line_abt" for i in warnings)
        detail = "\n".join(f"• {i['message']}" for i in warnings)
        cards.append({
            "uuid": f"card-warn-{pid}-{medication_code}",
            "summary": (
                warnings[0]["message"] if len(warnings) == 1
                else f"Отклонение от протокола / предостережения: {len(warnings)}"
            ),
            "detail": detail,
            "indicator": "warning",
            "source": {
                "label": (
                    "Регламент ВП (КП №768)" if proto
                    else "Проверка лекарств (drug_service)"
                )
            },
            "type": "soft-stop",
            "overrideAction": "Назначить всё равно",
            "suggestions": [{"label": "Назначить с подтверждением отклонения",
                             "actions": [{"type": "create", "resource": "MedicationRequest"}]}],
        })

    return cards


def _has_active_antibiotic(pid):
    return any(m["code"].startswith("J01") for m in fs.get_medications(pid))
