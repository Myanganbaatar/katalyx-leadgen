"""Step 1 - Detect companies showing a need for a Katalyx LAB IA agent.

Signal: La Bonne Boite (France Travail) predicts which companies are about to hire for a given job.
A company about to hire an ADV assistant, a quality technician or an admin clerk has a growing
repetitive workload: exactly what the Katalyx agents (Devis, Qualite, Administratif...) automate.

Output: output/signals.csv, one row per company, with every matching job and the agent it points to.
"""
import csv
import urllib.parse

from common import OUTPUT, france_travail_token, http_json

# Job (ROME code) -> Katalyx agent it maps to
JOBS = {
    "D1401": ("Assistanat commercial / ADV", "Devis"),
    "D1407": ("Relation technico-commerciale", "Devis"),
    "D1402": ("Relation commerciale grands comptes", "Commercial"),
    "H1502": ("Management et ingenierie qualite industrielle", "Qualite"),
    "H1506": ("Intervention technique qualite (mecanique, metaux)", "Qualite"),
    "H1504": ("Intervention technique en controle essai qualite (electricite, electronique)", "Qualite"),
    "H1206": ("Management et ingenierie etudes, R&D industriel (chiffrage)", "Devis"),
    "F1106": ("Ingenierie et etudes du BTP (metreur, chiffreur)", "Devis"),
    "M1602": ("Operations administratives", "Administratif"),
    "M1607": ("Secretariat", "Administratif"),
    "M1203": ("Comptabilite", "Administratif"),
    "M1204": ("Controle de gestion", "Reporting"),
}

# Region Sud (Provence-Alpes-Cote d'Azur): Katalyx is in Aix-en-Provence
DEPARTMENTS = ["13", "83", "84", "06", "04", "05"]


def katalyx_sector(naf):
    """Sectors listed on katalyx.fr/secteurs: industry, agri-food, aerospace, maritime, construction."""
    if not naf or not naf[:2].isdigit():
        return None
    div = int(naf[:2])
    if naf.startswith(("303",)):
        return "Aeronautique"
    if naf.startswith(("301", "5020", "5040", "5222")):
        return "Maritime"
    if div in (10, 11):
        return "Agroalimentaire"
    if 12 <= div <= 33:
        return "Industrie manufacturiere"
    if 41 <= div <= 43:
        return "BTP et construction"
    if naf.startswith(("4662", "4663", "4669", "4672", "4673", "4674")):  # industrial supplies wholesale: quote-heavy
        return "Negoce industriel"
    return None


def search(rome, department):
    """All La Bonne Boite companies for one job in one department (paginated)."""
    rows, page = [], 1
    while True:
        q = urllib.parse.urlencode({"rome": rome, "department_number": department, "page": page, "page_size": 100})
        r = http_json(f"https://api.francetravail.io/partenaire/labonneboite/v2/recherche?{q}",
                      headers={"Authorization": "Bearer " + france_travail_token()})
        rows += r.get("items", [])
        if len(rows) >= r.get("hits", 0) or not r.get("items"):
            return rows
        page += 1


def main():
    companies = {}
    for rome, (job, agent) in JOBS.items():
        for dep in DEPARTMENTS:
            for c in search(rome, dep):
                sector = katalyx_sector(c.get("naf", "").replace(".", ""))
                if not sector:
                    continue
                e = companies.setdefault(c["siret"], {
                    "siret": c["siret"], "company": c["company_name"], "naf": c["naf"], "naf_label": c["naf_label"],
                    "sector": sector, "city": c["city"], "postcode": c["postcode"], "department": c["department"],
                    "jobs": [], "agents": set(), "hiring_potential": 0.0, "high_potential": False,
                })
                e["jobs"].append(f"{job} ({c['hiring_potential']:.1f})")
                e["agents"].add(agent)
                e["hiring_potential"] = max(e["hiring_potential"], c["hiring_potential"])
                e["high_potential"] |= bool(c.get("is_high_potential"))
        print(f"  {job} : {len(companies)} entreprises au total", flush=True)

    # real offers found by jobs.py (step 0): the strongest signal, added on top of the predictions
    jobs_file = OUTPUT / "jobs.csv"
    live = 0
    if jobs_file.exists():
        for o in csv.DictReader(open(jobs_file, encoding="utf-8-sig"), delimiter=";"):
            sector = katalyx_sector(o["naf"].replace(".", ""))
            if not o.get("siret") or not sector:
                continue
            e = companies.setdefault(o["siret"], {
                "siret": o["siret"], "company": o["company"], "naf": o["naf"], "naf_label": "", "sector": sector,
                "city": o["city"], "postcode": o["postcode"], "department": o["postcode"][:2],
                "jobs": [], "agents": set(), "hiring_potential": 0.0, "high_potential": False,
            })
            e["agents"].add(o["agent"])
            e.setdefault("offers", []).append(f"{o['job_title']} ({o['published'] or 'date ?'}) {o['offer_url']}")
            live += 1
        print(f"{live} live job offers merged from jobs.csv")

    # companies with a live offer first, then the number of matching agents, then the predicted hiring
    rows = sorted(companies.values(), key=lambda e: (not e.get("offers"), -len(e["agents"]), -e["hiring_potential"]))
    path = OUTPUT / "signals.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["siret", "company", "sector", "naf", "naf_label", "city", "postcode", "department",
                    "agents", "jobs_about_to_hire", "max_hiring_potential", "high_potential", "live_offers"])
        for e in rows:
            w.writerow([e["siret"], e["company"], e["sector"], e["naf"], e["naf_label"], e["city"], e["postcode"],
                        e["department"], ", ".join(sorted(e["agents"])), " | ".join(e["jobs"]) or "aucune prédiction",
                        f"{e['hiring_potential']:.1f}", e["high_potential"], " | ".join(e.get("offers", []))])
    print(f"{len(rows)} companies written to {path}")


if __name__ == "__main__":
    main()
