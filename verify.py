"""Step 5 - Check every delivered lead automatically, without contacting anyone.

Katalyx will call the leads themselves, so the pipeline never emails or phones a prospect. Instead it checks,
from public sources, that what it delivers is real and reachable:
  - company      : active in the official registry (not closed);
  - contact      : the person is listed among the registered executives, or appears on the company website;
  - website      : answers and names the company;
  - email        : its domain receives mail (MX record) and matches the company website;
  - phone        : valid French number, found on the company website;
  - job offer    : the France Travail offer that triggered the lead is still online.

Output: a "Vérification" sheet in output/leads.xlsx and output/verification.csv
"""
import csv
import re
import ssl
import subprocess
import unicodedata
import urllib.request

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

import report
from common import OUTPUT, http_json

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
CTX = ssl.create_default_context()
CTX.check_hostname, CTX.verify_mode = False, ssl.CERT_NONE


def fetch(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            return r.status, r.read(600_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def plain(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def domain(url_or_mail):
    s = (url_or_mail or "").lower()
    s = s.split("@")[-1] if "@" in s else re.sub(r"^https?://", "", s).split("/")[0]
    return s.removeprefix("www.")


def has_mx(dom):
    if not dom:
        return False
    out = subprocess.run(["nslookup", "-type=mx", dom], capture_output=True, text=True, errors="ignore").stdout
    return "mail exchanger" in out.lower()


def site_text(site):
    """Home, contact and legal-notice pages of the company website, as one lower-case text."""
    if not site:
        return ""
    base = site.rstrip("/")
    return " ".join(plain(fetch(base + p)[1]) for p in ("", "/contact", "/mentions-legales", "/nous-contacter"))


def verify(lead):
    checks = {}
    reg = http_json(f"https://recherche-entreprises.api.gouv.fr/search?q={lead['SIREN']}&per_page=1").get("results", [])
    reg = next((r for r in reg if r.get("siren") == lead["SIREN"]), None)
    checks["Entreprise active au registre"] = bool(reg and reg.get("etat_administratif") == "A")

    text = site_text(lead["Site web"])
    checks["Site web en ligne"] = bool(text) and any(w in text for w in plain(lead["Entreprise"]).split() if len(w) > 3)

    name = plain(lead["Prénom Nom"])
    last = name.split()[-1] if name.split() else ""
    in_registry = bool(reg) and any(last and last in plain(d.get("nom")) for d in reg.get("dirigeants", []))
    if lead.get("Restriction de contact"):  # registry data not usable for this company: the website must name the person
        in_registry = False
    checks["Interlocuteur confirmé (registre ou site)"] = in_registry or (bool(last) and last in text)

    mail = lead["Email pro"].split(",")[0].strip()
    checks["Email : domaine qui reçoit du courrier"] = bool(mail) and has_mx(domain(mail))
    checks["Email : même domaine que le site"] = bool(mail) and domain(mail) == domain(lead["Site web"])

    digits = re.sub(r"\D", "", lead["Téléphone"].split(",")[0]).removeprefix("33")
    digits = digits if digits.startswith("0") else "0" + digits
    checks["Téléphone au format français"] = bool(re.fullmatch(r"0[1-9]\d{8}", digits))
    checks["Téléphone publié sur le site"] = bool(digits) and digits[1:] in re.sub(r"\D", "", text)

    offers = re.findall(r"https://candidat\.francetravail\.fr/offres/recherche/detail/\w+", lead["Pourquoi ce lead est qualifié (signal)"])
    if offers:
        status, page = fetch(offers[0])
        checks["Offre d'emploi toujours en ligne"] = status == 200 and "plus disponible" not in plain(page)

    essentials = ["Entreprise active au registre", "Interlocuteur confirmé (registre ou site)"]
    reachable = checks["Email : domaine qui reçoit du courrier"] or checks["Téléphone au format français"]
    verdict = "Vérifié" if all(checks[k] for k in essentials) and reachable else "Partiel - à vérifier"
    return checks, verdict


def main():
    leads = list(csv.DictReader(open(OUTPUT / "leads.csv", encoding="utf-8-sig"), delimiter=";"))
    results = []
    for lead in leads:
        checks, verdict = verify(lead)
        results.append((lead, checks, verdict))
        ok = sum(checks.values())
        print(f"  {lead['Entreprise'][:35]:35} {verdict:22} {ok}/{len(checks)} contrôles OK", flush=True)

    keys = []
    for _, checks, _ in results:
        keys += [k for k in checks if k not in keys]
    with open(OUTPUT / "verification.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Entreprise", "Verdict"] + keys)
        for lead, checks, verdict in results:
            w.writerow([lead["Entreprise"], verdict] + [("oui" if checks[k] else "non") if k in checks else "-" for k in keys])

    wb = load_workbook(OUTPUT / "leads.xlsx")
    if "Vérification" in wb.sheetnames:
        del wb["Vérification"]
    ws = wb.create_sheet("Vérification")
    ws.append(["Entreprise", "Verdict"] + keys)
    for lead, checks, verdict in results:
        ws.append([lead["Entreprise"], verdict] + [("✔" if checks[k] else "✘") if k in checks else "-" for k in keys])
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F4E78")
        c.alignment = Alignment(wrap_text=True, vertical="top")
    for row in ws.iter_rows(min_row=2):
        row[1].fill = PatternFill("solid", fgColor="C6EFCE" if row[1].value == "Vérifié" else "FFE699")
        for c in row[2:]:
            c.alignment = Alignment(horizontal="center")
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 34, 20
    for i in range(len(keys)):
        ws.column_dimensions[chr(ord("C") + i)].width = 18
    wb.save(OUTPUT / "leads.xlsx")
    n = sum(1 for _, _, v in results if v == "Vérifié")
    print(f"{n}/{len(results)} leads vérifiés automatiquement -> feuille « Vérification » de output/leads.xlsx")
    report.main()  # the explanation document is rebuilt from this run's data


if __name__ == "__main__":
    main()
