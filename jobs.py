"""Step 0 - Find REAL job offers that are online now (the strongest "hot" signal).

Source: the public job search of France Travail (candidat.francetravail.fr), which also lists the offers of its
partner job boards. For each target job we read the most recent offers in the Region Sud, keep direct employers
(temporary-work and recruitment agencies, public bodies and big groups are dropped) and match each employer to
the official company registry (SIRET), so the next steps can enrich it.

Why not an LLM here: a first version asked a Claude agent to search the web for offers. It was slow and too
cautious (it often returned nothing when a date could not be read). Reading the official listing directly is
faster, free and reproducible; the LLMs are kept for the steps where judgement is needed (steps 3 and 4).

Output: output/jobs.csv
"""
import csv
import html
import re
import subprocess
import time
import urllib.parse
from datetime import date, timedelta

from common import OUTPUT, http_json

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
REGION = "93R"  # Provence-Alpes-Cote d'Azur
MAX_AGE_DAYS = 60

# (search keywords, Katalyx agent)
TARGETS = [
    ("assistant ADV", "Devis"), ("administration des ventes", "Devis"), ("chiffreur", "Devis"),
    ("deviseur", "Devis"), ("technico-commercial sédentaire", "Devis"),
    ("technicien qualité", "Qualite"), ("contrôleur qualité", "Qualite"), ("responsable qualité", "Qualite"),
    ("assistant administratif", "Administratif"), ("gestionnaire administratif", "Administratif"),
    ("contrôleur de gestion", "Reporting"),
]

# employers that are not the company with the need: agencies, job boards, public sector
NOT_DIRECT = re.compile(
    r"int[ée]rim|recrut|staff|talent|adecco|randstad|manpower|synergie|proman|start people|crit\b|samsic|"
    r"actual|asap\.work|hays|michael page|robert half|expectra|fed |page personnel|job ?link|acass|cabinet|"
    r"cnrs|mairie|commune|minist|h[ôo]pital|chu\b|universit|r[ée]gion|d[ée]partement|m[ée]tropole|"
    r"entreprise anonyme|non communiqu", re.I)


def fetch(url):
    r = subprocess.run(["curl", "-s", "-m", "25", "-A", UA, url], capture_output=True)
    return r.stdout.decode("utf-8", "ignore")


def clean(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def published(text):
    """'Publié aujourd'hui / hier / il y a 5 jours / le 03/11/2026' -> date"""
    t = text.lower()
    if "aujourd" in t:
        return date.today()
    if "hier" in t:
        return date.today() - timedelta(days=1)
    m = re.search(r"il y a (\d+) jour", t)
    if m:
        return date.today() - timedelta(days=int(m.group(1)))
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", t)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def search(keywords):
    """The 60 most recent offers for these keywords (3 pages of 20)."""
    offers = []
    for start in (0, 20, 40):
        q = urllib.parse.urlencode({"motsCles": keywords, "lieux": REGION, "offresPartenaires": "true", "tri": "1",
                                    "range": f"{start}-{start + 19}"})
        page_offers = parse(fetch(f"https://candidat.francetravail.fr/offres/recherche?{q}"))
        offers += page_offers
        if len(page_offers) < 20:
            break
    return offers


def parse(page):
    offers = []
    for item in re.findall(r'(?s)<li[^>]*class="result"[^>]*>(.*?)</li>', page):
        link = re.search(r'href="(/offres/recherche/detail/[A-Z0-9]+)"', item)
        title = re.search(r'(?s)class="media-heading-title">(.*?)</span>', item)
        sub = re.search(r'(?s)class="subtext">(.*?)</p>', item)
        when = re.search(r'(?s)class="date">(.*?)</p>', item)
        contract = re.search(r'(?s)class="contrat visible-xs">(.*?)</p>', item)
        if not (link and title and sub):
            continue
        parts = [p.strip() for p in clean(sub.group(1)).split(" - ")]
        employer = parts[0] if len(parts) >= 3 else ""
        offers.append({"job_title": clean(title.group(1)), "employer": employer,
                       "department": parts[-2] if len(parts) >= 2 else "", "city": parts[-1],
                       "published": published(clean(when.group(1)) if when else ""),
                       "contract": clean(contract.group(1)) if contract else "",
                       "offer_url": "https://candidat.francetravail.fr" + link.group(1)})
    return offers


def match_registry(name, city):
    """SIRET of the establishment of this employer in the offer's town (Region Sud only)."""
    q = urllib.parse.urlencode({"q": name, "region": "93", "per_page": 5})
    try:
        hits = http_json(f"https://recherche-entreprises.api.gouv.fr/search?{q}").get("results", [])
    except Exception:
        return None
    town = city.lower().split(" ")[0]
    for h in hits:
        for e in h.get("matching_etablissements", []):
            if town and town in (e.get("libelle_commune") or "").lower():
                return h, e
    return None  # no establishment in that town: too uncertain to keep


def main():
    seen_urls, rows, dropped = set(), [], {"agence / public": 0, "trop ancienne": 0, "intérim": 0, "registre": 0}
    for keywords, agent in TARGETS:
        found = search(keywords)
        print(f"  Recherche « {keywords} » en Région Sud : {len(found)} offres récentes lues", flush=True)
        for o in found:
            if o["offer_url"] in seen_urls:
                continue
            seen_urls.add(o["offer_url"])
            if not o["employer"] or NOT_DIRECT.search(o["employer"]):
                dropped["agence / public"] += 1
                continue
            if "intérim" in o["contract"].lower() or "interim" in o["contract"].lower():
                dropped["intérim"] += 1
                continue
            if not o["published"] or (date.today() - o["published"]).days > MAX_AGE_DAYS:
                dropped["trop ancienne"] += 1
                continue
            m = match_registry(o["employer"], o["city"])
            time.sleep(0.15)  # be gentle with the public registry API
            if not m:
                dropped["registre"] += 1
                continue
            company, est = m
            rows.append({"siret": est.get("siret", ""), "company": company["nom_complet"],
                         "naf": est.get("activite_principale") or company.get("activite_principale", ""),
                         "city": est.get("libelle_commune", ""), "postcode": est.get("code_postal", ""),
                         "agent": agent, "job_title": o["job_title"], "published": o["published"].isoformat(),
                         "offer_url": o["offer_url"], "offer_source": "France Travail (offres et partenaires)"})
    if not rows:
        raise SystemExit("No usable offer found: previous output/jobs.csv kept unchanged")
    with open(OUTPUT / "jobs.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(seen_urls)} offres lues, {len(rows)} retenues (employeur direct, < {MAX_AGE_DAYS} jours, au registre)"
          f" -> output/jobs.csv ; écartées : {dropped}")


if __name__ == "__main__":
    main()
