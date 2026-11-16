"""Step 4 - Second opinion from Gemini, then pick the 10 best leads and export them.

Claude (step 3) researched each company on the web and gave a heat. Gemini now reads the same evidence,
without web access, and scores the fit from 0 to 100. Two models that agree make a lead more reliable:
when they disagree, the lead is marked for a human check instead of being trusted blindly.

Each lead gets ONE decision, readable by a sales team:
  - a need level (Besoin confirmé / Besoin probable / À explorer), which is also Katalyx's chaud / tiède / froid;
  - a priority score out of 100 combining the strength and freshness of the evidence, the company size,
    how complete the contact details are, and Gemini's independent opinion.
Leads are ranked by score.
Output: output/leads.xlsx and output/leads.csv (10 best), output/all_scored.csv (every company)
"""
import csv
import json
import re
import time
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from common import ENV, OUTPUT, http_json

MODEL = "gemini-flash-lite-latest"  # free tier
HEAT_RANK = {"chaud": 0, "tiède": 1, "tiede": 1, "froid": 2}
NEED = {"chaud": "Besoin confirmé", "tiède": "Besoin probable", "froid": "À explorer"}

PROMPT = """Tu évalues un prospect pour Katalyx, studio IA d'Aix-en-Provence qui conçoit des agents IA sur mesure
pour les PME industrielles et du BTP (agents Devis, Qualité, Administratif, Reporting, Commercial).

Prospect : {legal_name} - {sector} ({naf_label}) - {city} - effectif {headcount} ({category})
Agent proposé : {agents}
Signal de recrutement (France Travail) : {jobs_about_to_hire}
Offres d'emploi réelles repérées : {live_offers}
Preuve trouvée sur le web : {r_need_evidence} (source : {r_need_source_url}, date : {r_need_date})
Contact identifié : {r_contact_name}, {r_contact_role}

Donne une note d'adéquation de 0 à 100 (taille, secteur, intensité du besoin, fraîcheur et solidité de la preuve)
et un niveau : "chaud", "tiède" ou "froid". Réponds uniquement en JSON :
{{"score": int, "heat": "chaud"|"tiède"|"froid", "why": "une phrase"}}"""


def gemini(row):
    body = {"contents": [{"parts": [{"text": PROMPT.format(**row)}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
    for attempt in range(4):
        try:
            r = http_json(f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
                          json.dumps(body).encode(),
                          {"Content-Type": "application/json", "x-goog-api-key": ENV["GEMINI_API_KEY"]})
            text = r["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        except Exception as e:
            if "429" in str(e) and attempt < 3:  # free tier rate limit: wait and retry
                time.sleep(20)
                continue
            return {"score": None, "heat": "", "why": f"Gemini error: {str(e)[:80]}"}


def days_ago(text):
    """Age in days of the most recent YYYY-MM-DD date found in a text, or None."""
    dates = []
    for y, m, d in re.findall(r"(20\d\d)-(\d\d)-(\d\d)", text or ""):
        try:
            dates.append(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    return (date.today() - max(dates)).days if dates else None


def decide_heat(r):
    """One final decision from the evidence, so the two models never leave a lead undecided.

    The models gather and weigh evidence; the heat itself follows a fixed, auditable rule:
    - chaud : a real job offer for the targeted role published less than 90 days ago, or a dated growth
              event (new site, contract, acquisition...) of less than 180 days with its source URL;
    - tiède : a real but indirect signal (France Travail hiring prediction, older offer or event);
    - froid : no confirmed signal.
    """
    offer_age = days_ago(r.get("live_offers"))
    if offer_age is not None and offer_age <= 90:
        return "chaud", f"offre d'emploi publiée il y a {offer_age} jours"
    event_age = days_ago(r.get("r_need_date"))
    if r.get("r_need_source_url") and event_age is not None and event_age <= 180:
        return "chaud", f"signal daté de {event_age} jours avec source"
    if r.get("live_offers") or r.get("jobs_about_to_hire", "aucune prédiction") != "aucune prédiction" or r.get("r_need_source_url"):
        return "tiède", "signal réel mais indirect ou ancien"
    return "froid", "aucun signal confirmé"


def priority_score(r):
    """0-100 priority: evidence (max 50) + size fit (15) + reachability (20) + Gemini opinion (15)."""
    score = 0
    offer_age = days_ago(r.get("live_offers"))
    event_age = days_ago(r.get("r_need_date"))
    if offer_age is not None and offer_age <= 90:
        score += 50 - offer_age // 10                      # real recent offer: 41-50
    elif r.get("r_need_source_url") and event_age is not None and event_age <= 180:
        score += 40 - event_age // 20                      # dated growth event: 31-40
    elif r.get("live_offers") or r.get("r_need_source_url"):
        score += 22
    if r.get("jobs_about_to_hire", "aucune prédiction") != "aucune prédiction":
        score += 8 if r.get("high_potential") == "True" else 5
    score += min(5 * (len(r.get("agents", "").split(", ")) - 1), 10)
    size = r.get("headcount", "")
    score += 15 if any(t in size for t in ("20-49", "50-99", "100-199", "200-249")) else 10 if any(
        t in size for t in ("10-19", "250-499", "500-999")) else 5
    score += 6 * bool(r.get("r_contact_name")) + 5 * bool(r.get("r_email")) + 5 * bool(r.get("r_phone"))         + 4 * bool(r.get("r_contact_linkedin"))
    score += round(15 * (r.get("g_score") or 0) / 100)
    return min(score, 100)


def completeness(r):
    return sum(bool(r.get(k)) for k in ("r_contact_name", "r_email", "r_phone", "r_contact_linkedin", "r_need_source_url"))


COLUMNS = [  # (header, key) - the fields Katalyx asked for, in their order
    ("Score de priorité /100", "priority"), ("Niveau de besoin", "need_level"),
    ("Prénom Nom", "r_contact_name"), ("Poste", "r_contact_role"), ("Entreprise", "legal_name"),
    ("Site web", "r_website"), ("Secteur", "naf_label"), ("Taille", "headcount"), ("Localisation", "city"),
    ("Email pro", "r_email"), ("Téléphone", "r_phone"), ("LinkedIn", "r_contact_linkedin"),
    ("Offre Katalyx visée", "offer"), ("Pourquoi ce lead est qualifié (signal)", "why_qualified"),
    ("Source du signal", "signal_sources"), ("Chaleur", "final_heat"), ("Justification de la chaleur", "heat_justification"),
    ("Note Gemini /100", "g_score"), ("Avis des modèles et décision", "agreement"), ("Source du contact", "r_contact_source"),
    ("Restriction de contact", "contact_restriction"),
    ("SIREN", "siren"),
]


def main():
    rows = [r for r in csv.DictReader(open(OUTPUT / "researched.csv", encoding="utf-8-sig"), delimiter=";")
            if not r.get("agent_error")]
    for r in rows:
        g = gemini({k: (v or "non trouvé") for k, v in r.items()})
        r["g_score"], r["g_heat"], r["g_why"] = g.get("score"), g.get("heat", ""), g.get("why", "")
        claude_heat = r.get("r_heat", "")
        r["final_heat"], r["decision_rule"] = decide_heat(r)
        r["agreement"] = (f"Claude : {claude_heat or '?'} · Gemini : {r['g_heat'] or '?'} · "
                          f"décision finale : {r['final_heat']} ({r['decision_rule']})")
        r["need_level"] = NEED.get(r["final_heat"], "")
        r["priority"] = priority_score(r)
        r["offer"] = f"LAB IA - agent {r['agents']}"
        offers = f"Offres en ligne : {r['live_offers']}. " if r.get("live_offers") else ""
        r["why_qualified"] = f"{offers}Recrutements prédits : {r['jobs_about_to_hire']}. {r.get('r_need_evidence', '')}"
        r["signal_sources"] = " | ".join(s for s in (
            "Offre d'emploi (veille Claude)" if r.get("live_offers") else "",
            "France Travail - La Bonne Boite (API)" if r["jobs_about_to_hire"] != "aucune prédiction" else "",
            r.get("r_need_source_url")) if s)
        r["heat_justification"] = f"Claude : {r.get('r_heat_reason', '')} | Gemini ({r['g_score']}/100) : {r['g_why']}"
        print(f"{r['legal_name'][:30]:30} Claude={claude_heat:6} Gemini={r['g_heat']:6} {r['g_score']} -> {r['final_heat']}", flush=True)

    rows.sort(key=lambda r: (-r["priority"], HEAT_RANK.get(r["final_heat"], 3)))
    reachable = [r for r in rows if r.get("r_contact_name") and (r.get("r_email") or r.get("r_phone"))]

    # "relaunched tomorrow to generate 10 NEW leads": companies already delivered in a previous run are skipped
    history = OUTPUT / "delivered_history.csv"
    delivered = set()
    if history.exists():
        delivered = {r["siren"] for r in csv.DictReader(open(history, encoding="utf-8-sig"), delimiter=";")}
    top = [r for r in reachable if r.get("siren") not in delivered][:10]
    new_file = not history.exists()
    with open(history, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if new_file:
            w.writerow(["siren", "company", "delivered_on"])
        w.writerows([[r["siren"], r["legal_name"], time.strftime("%Y-%m-%d")] for r in top])
    print(f"{len(delivered)} companies already delivered before and skipped")

    for name, data in (("all_scored.csv", rows), ("leads.csv", top)):
        with open(OUTPUT / name, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([h for h, _ in COLUMNS])
            w.writerows([[r.get(k, "") for _, k in COLUMNS] for r in data])

    wb = Workbook()
    ws = wb.active
    ws.title = "10 leads"
    ws.append([h for h, _ in COLUMNS])
    for r in top:
        ws.append([r.get(k, "") for _, k in COLUMNS])
    fills = {"chaud": "F8CBAD", "tiède": "FFE699", "froid": "DDEBF7"}
    heat_col = [k for _, k in COLUMNS].index("final_heat")
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F4E78")
        c.alignment = Alignment(wrap_text=True, vertical="top")
    for row in ws.iter_rows(min_row=2):
        fill = PatternFill("solid", fgColor=fills.get(row[heat_col].value, "FFFFFF"))
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
        row[0].font = Font(bold=True, size=12)
        row[1].fill = row[heat_col].fill = fill
    widths = {"priority": 10, "need_level": 16, "r_contact_name": 20, "r_contact_role": 24, "legal_name": 26,
              "r_website": 26, "naf_label": 24, "headcount": 10, "city": 14, "r_email": 26, "r_phone": 16,
              "r_contact_linkedin": 30, "offer": 22, "why_qualified": 60, "signal_sources": 40, "final_heat": 9,
              "heat_justification": 70, "g_score": 8, "agreement": 40, "r_contact_source": 40, "contact_restriction": 30}
    for i, (_, k) in enumerate(COLUMNS):
        ws.column_dimensions[ws.cell(1, i + 1).column_letter].width = widths.get(k, 12)
    ws.freeze_panes = "D2"
    wb.save(OUTPUT / "leads.xlsx")
    print(f"{len(rows)} scored, {len(reachable)} reachable, {len(top)} leads -> output/leads.xlsx")


if __name__ == "__main__":
    main()
