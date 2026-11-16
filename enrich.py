"""Step 2 - Enrich each signalled company and keep only real Katalyx targets.

For every company from output/signals.csv:
  1. Official registry (recherche-entreprises.api.gouv.fr, free, no key): size category, headcount,
     executives (name + role), head-office address, and the "opposition to commercial prospecting" flag.
  2. Filter: SMEs and mid-caps only (Katalyx sells to PME/ETI), and drop companies that opted out of prospecting.
  3. Website: guess candidate domains from the company name and keep one whose page mentions the company.
  4. Public contact details scraped from that website (home, contact, legal notice pages).

Output: output/enriched.csv
"""
import csv
import re
import ssl
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from common import OUTPUT, http_json

TRANCHES = {"00": "0", "01": "1-2", "02": "3-5", "03": "6-9", "11": "10-19", "12": "20-49", "21": "50-99",
            "22": "100-199", "31": "200-249", "32": "250-499", "41": "500-999", "42": "1000-1999",
            "51": "2000-4999", "52": "5000-9999", "53": "10000+", "NN": "non renseigné"}
TOO_SMALL = {"00", "01", "02"}  # fewer than 6 employees: no repetitive back-office worth an agent
LEGAL_WORDS = {"sas", "sa", "sarl", "eurl", "sasu", "snc", "france", "societe", "ste", "groupe", "group", "industries",
               "industrie", "et", "de", "des", "du", "la", "le", "les", "d", "l"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
CTX = ssl.create_default_context()
CTX.check_hostname, CTX.verify_mode = False, ssl.CERT_NONE
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}")
PHONE = re.compile(r"(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}")


def registry(siren):
    r = http_json(f"https://recherche-entreprises.api.gouv.fr/search?q={siren}&per_page=1")
    hits = [x for x in r.get("results", []) if x.get("siren") == siren]
    return hits[0] if hits else None


def people(company):
    """Executives who are natural persons, in registry order (the legal representative comes first)."""
    out = []
    for d in company.get("dirigeants", []):
        # auditors (commissaires aux comptes) are listed as executives but are not decision makers
        if d.get("type_dirigeant") == "personne physique" and d.get("nom") and "commissaire" not in (d.get("qualite") or "").lower():
            first = (d.get("prenoms") or "").split(" ")[0].title()
            out.append(f"{first} {d['nom'].title()} ({d.get('qualite') or '?'})")
    return out


def fetch(url, n=400_000):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10, context=CTX) as r:
            return r.geturl(), r.read(n).decode("utf-8", "ignore")
    except Exception:
        return None, ""


def plain(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def candidate_domains(name):
    words = [w for w in re.findall(r"[a-z0-9]+", plain(name)) if w not in LEGAL_WORDS]
    if not words:
        return []
    stems = {"".join(words), "-".join(words), words[0], "".join(words[:2]), "-".join(words[:2])}
    return [f"{s}.{tld}" for s in stems if len(s) > 2 for tld in ("fr", "com")]


def find_website(name, city, postcode):
    """Keep the first candidate domain whose page names the company and its town or postcode."""
    key = max((w for w in re.findall(r"[a-z0-9]+", plain(name)) if w not in LEGAL_WORDS), key=len, default="")
    for dom in candidate_domains(name):
        url, html = fetch(f"https://{dom}/")
        text = plain(html)
        if html and key in text and (plain(city) in text or postcode in text):
            return url
    return ""


def contacts(site):
    """Public emails and phone numbers from the site's home, contact and legal-notice pages."""
    emails, phones = [], []
    base = site.rstrip("/")
    for path in ("", "/contact", "/nous-contacter", "/mentions-legales"):
        _, html = fetch(base + path)
        html = html.replace("&#64;", "@").replace("[at]", "@")
        for e in EMAIL.findall(html):
            e = e.lower()
            if e not in emails and not re.search(r"\.(png|jpg|svg|webp)$|example|sentry|wixpress", e):
                emails.append(e)
        for p in PHONE.findall(html):
            p = re.sub(r"[\s.-]", "", p)
            if p not in phones:
                phones.append(p)
    return emails[:4], phones[:3]


def enrich(row):
    c = registry(row["siret"][:9])
    if not c:
        return None
    size = c.get("categorie_entreprise") or "?"
    tranche = c.get("tranche_effectif_salarie") or "NN"
    reason = ("grande entreprise" if size == "GE" else
              "moins de 6 salariés" if tranche in TOO_SMALL else "")
    # Opposition to commercial prospecting = the registry's data may not be reused for prospecting.
    # The company is kept, but only with what it publishes itself (website contacts), never registry names.
    restricted = bool(c.get("opposition_diffusion_commerciale"))
    site = "" if reason else find_website(c["nom_complet"], row["city"], row["postcode"])
    emails, phones = contacts(site) if site else ([], [])
    return {**row, "siren": c["siren"], "legal_name": c["nom_complet"], "category": size,
            "headcount": TRANCHES.get(tranche, tranche), "created": c.get("date_creation", ""),
            "hq_address": c["siege"].get("adresse", ""),
            "executives": "" if restricted else " | ".join(people(c)[:3]),
            "contact_restriction": "Contact uniquement via les coordonnées publiées par l'entreprise (opposée à la prospection au registre)" if restricted else "",
            "website": site, "public_emails": ", ".join(emails), "public_phones": ", ".join(phones),
            "excluded_because": reason}


def main():
    rows = list(csv.DictReader(open(OUTPUT / "signals.csv", encoding="utf-8-sig"), delimiter=";"))
    seen, unique = set(), []
    for r in rows:  # several establishments of one company -> keep the strongest signal
        if r["siret"][:9] not in seen:
            seen.add(r["siret"][:9])
            unique.append(r)
    with ThreadPoolExecutor(6) as ex:
        out = [e for e in ex.map(enrich, unique) if e]
    kept = [e for e in out if not e["excluded_because"]]
    path = OUTPUT / "enriched.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]), delimiter=";")
        w.writeheader()
        w.writerows(sorted(out, key=lambda e: (bool(e["excluded_because"]), not e["website"])))
    reasons = {}
    for e in out:
        if e["excluded_because"]:
            reasons[e["excluded_because"]] = reasons.get(e["excluded_because"], 0) + 1
    print(f"{len(unique)} entreprises, {len(kept)} retenues ; écartées : {reasons} -> {path}")


if __name__ == "__main__":
    main()
