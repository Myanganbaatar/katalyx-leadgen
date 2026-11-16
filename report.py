"""Step 6 - Build the explanation document (output/rapport.html and output/rapport.pdf) from the last run.

Everything that changes from one run to the next (funnel figures, exclusions, the 10 leads, verification,
cost of the AI calls) is read from the output files, so the report is always in sync with the data.
"""
import csv
import json
import subprocess
import time
from datetime import date
from html import escape
from pathlib import Path

from common import OUTPUT

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def rows(name):
    p = OUTPUT / name
    return list(csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";")) if p.exists() else []


def count_by(items, key):
    out = {}
    for r in items:
        out[r[key]] = out.get(r[key], 0) + 1
    return out


def research_cost():
    """Equivalent API cost reported by Claude Code for each research run of the last execution."""
    files = list((OUTPUT / "research").glob("*.json"))
    if not files:
        return 0.0, 0, 0
    last = max(f.stat().st_mtime for f in files)
    recent = [f for f in files if f.stat().st_mtime > last - 3 * 3600]
    cost = secs = 0.0
    for f in recent:
        d = json.loads(f.read_text(encoding="utf-8"))
        cost += d.get("total_cost_usd") or 0
        secs += (d.get("duration_ms") or 0) / 1000
    return cost, len(recent), secs


def table(headers, data):
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in data)
    return f"<table><tr>{head}</tr>{body}</table>"


def main():
    jobs, signals, enriched = rows("jobs.csv"), rows("signals.csv"), rows("enriched.csv")
    researched, scored, leads, ver = rows("researched.csv"), rows("all_scored.csv"), rows("leads.csv"), rows("verification.csv")
    kept = [r for r in enriched if not r["excluded_because"]]
    excluded = count_by([r for r in enriched if r["excluded_because"]], "excluded_because")
    heat = count_by(leads, "Chaleur")
    cost, n_runs, secs = research_cost()
    per_company = cost / n_runs if n_runs else 0
    verdict = {r["Entreprise"]: r["Verdict"] for r in ver}

    funnel = table(["Étape", "Résultat"], [
        ["0. Offres d'emploi réelles (France Travail)", f"{len(jobs)} offres d'employeurs directs, &lt; 45 jours"],
        ["1. Signaux (La Bonne Boite + offres)", f"{len(signals)} entreprises des secteurs Katalyx en Région Sud"],
        ["2. Registre officiel", f"{len(kept)} retenues sur {len(enriched)}, dont {sum(1 for r in kept if r.get('contact_restriction'))} à contacter via leur site uniquement ; écartées : "
                                 + ", ".join(f"{escape(k)} ({v})" for k, v in excluded.items())],
        ["3. Recherche par l'agent Claude", f"les {len(researched)} plus prometteuses étudiées sur le web (offre réelle d'abord)"],
        ["4. Qualification", f"{len(scored)} notées ; {len(leads)} leads livrés : "
                             + ", ".join(f"{v} {escape({'chaud': 'besoin confirmé', 'tiède': 'besoin probable', 'froid': 'à explorer'}.get(k, k))}" for k, v in heat.items())],
        ["5. Vérification automatique", f"{sum(1 for v in verdict.values() if v == 'Vérifié')} / {len(ver)} leads vérifiés"],
    ])
    lead_rows = [[f'<b>{escape(r["Score de priorité /100"])}</b>', escape(r["Entreprise"]) + ("<br><small>contact via le site uniquement</small>" if r.get("Restriction de contact") else ""),
                  escape(r["Localisation"]), escape(r["Prénom Nom"]) + "<br><small>" + escape(r["Poste"]) + "</small>",
                  escape(r["Offre Katalyx visée"].replace("LAB IA - ", "")),
                  f'<b class="{escape(r["Chaleur"])}">{escape(r["Niveau de besoin"])}</b><br><small>({escape(r["Chaleur"])})</small>',
                  escape(verdict.get(r["Entreprise"], "-"))] for r in leads]

    html = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Katalyx - test génération de leads</title>
<style>@page{{size:A4;margin:16mm 15mm}}body{{font:10.5pt/1.5 "Segoe UI",Arial,sans-serif;color:#1d2330}}
h1{{font-size:19pt;color:#1f4e78;margin:0 0 2mm}}h2{{font-size:13pt;color:#1f4e78;border-bottom:2px solid #2bb3a3;padding-bottom:1mm;margin:7mm 0 3mm}}
h3{{font-size:11pt;margin:4mm 0 1mm}}.sub{{color:#667085;margin-bottom:5mm}}table{{border-collapse:collapse;width:100%;font-size:9.5pt;margin:2mm 0}}
th{{background:#1f4e78;color:#fff;text-align:left}}th,td{{padding:4px 6px;border:1px solid #d0d5dd;vertical-align:top}}
.chaud{{color:#c4320a}}.tiède{{color:#b88a00}}.froid{{color:#2e6fb8}}code{{background:#f2f4f7;padding:0 3px}}ul{{margin:1mm 0 2mm;padding-left:5mm}}
.box{{background:#f2f7fb;border-left:4px solid #1f4e78;padding:3mm 4mm;margin:3mm 0}}</style></head><body>
<h1>Machine de génération de leads B2B — Katalyx LAB IA</h1>
<div class="sub">Test technique · Barsbold Myanganbaatar · rapport généré automatiquement le {date.today():%d/%m/%Y}</div>

<div class="box"><b>En une phrase :</b> le pipeline repère en Région Sud les PME et ETI de l'industrie et du BTP qui recrutent
sur des postes que les agents Katalyx automatisent (ADV, chiffrage, qualité, administratif, contrôle de gestion), vérifie au registre
qu'elles sont des cibles légitimes, laisse un agent Claude trouver l'interlocuteur et la preuve du besoin, puis livre
{len(leads)} leads classés et vérifiés. Une commande (<code>python run.py</code>) ou un clic sur le tableau de bord suffit pour le relancer.</div>

<h2>1. L'offre choisie et la cible (ICP)</h2>
<p><b>Offre :</b> LAB IA, agents IA sur mesure. C'est le cœur de Katalyx, et chaque agent du site (Devis, Qualité, Administratif,
Reporting, Commercial) correspond à un poste répétitif qu'une entreprise recrute quand la charge augmente.</p>
<ul><li><b>Secteurs :</b> ceux de katalyx.fr/secteurs : industrie manufacturière, BTP, agroalimentaire, aéronautique, maritime, plus le négoce industriel (forte charge de devis).</li>
<li><b>Taille :</b> PME et ETI (de 6 à environ 5 000 salariés) ; grands groupes exclus.</li>
<li><b>Zone :</b> Région Sud (13, 83, 84, 06, 04, 05), autour d'Aix-en-Provence.</li>
<li><b>Signal clé :</b> une entreprise qui recrute un ou une ADV, un chiffreur ou un technicien qualité a une charge répétitive qui grossit : c'est précisément le besoin qu'un agent absorbe.</li></ul>

<h2>2. Du signal au lead : les étapes</h2>
{funnel}
<h3>Une seule décision par lead : niveau de besoin et score de priorité</h3>
<p>Claude et Gemini donnent chacun leur avis, mais la décision finale suit une règle fixe fondée sur les preuves, pour que deux modèles ne laissent jamais un lead indécis :</p>
<ul><li><b class="chaud">Besoin confirmé</b> (chaud) : offre d'emploi réelle publiée il y a moins de 90 jours, ou événement de croissance daté de moins de 6 mois avec sa source ;</li>
<li><b class="tiède">Besoin probable</b> (tiède) : signal réel mais indirect (prédiction de recrutement France Travail, offre ou événement plus ancien) ;</li>
<li><b class="froid">À explorer</b> (froid) : aucun signal confirmé.</li></ul>
<p>Le <b>score de priorité sur 100</b> classe les leads : force et fraîcheur de la preuve (50), taille adaptée à Katalyx (15), contacts complets (20) et avis indépendant de Gemini (15).</p>
<p><b>Entreprises opposées à la prospection au registre :</b> les données du registre ne sont pas utilisées pour elles ; elles ne sont retenues
qu'avec les coordonnées qu'elles publient elles-mêmes (site, offre d'emploi), ce qui relève de l'intérêt légitime en B2B. Elles sont signalées dans les livrables.</p>

<h2>3. Architecture</h2>
{table(["Brique", "Outil", "Pourquoi"], [
 ["Offres d'emploi", "Recherche publique France Travail (offres et partenaires)", "Preuve la plus forte d'un besoin actuel ; agences d'intérim et organismes publics filtrés"],
 ["Prédiction de recrutement", "API France Travail - La Bonne Boite", "Entreprises sur le point de recruter sur les postes ciblés"],
 ["Registre officiel", "recherche-entreprises.api.gouv.fr", "Taille, dirigeants, opposition à la prospection (RGPD), entreprise active"],
 ["Agent de recherche", "Claude (Claude Code en mode non interactif, recherche web)", "Site, contacts publics, interlocuteur, preuve datée ; consigne : ne rien inventer"],
 ["Second avis", "Gemini (palier gratuit)", "Note 0-100 indépendante, sans accès web, sur les mêmes preuves"],
 ["Vérification", "Registre, DNS (MX), sites web, page de l'offre", "Contrôle que chaque lead est réel et joignable, sans contacter personne"],
 ["Interface", "Tableau de bord local (Python, HTML) + export Excel", "Lancer, suivre l'avancement, consulter chaque étape"],
])}
<p>Code : Python sans dépendance lourde (bibliothèque standard + openpyxl). Chaque étape lit le fichier de la précédente et peut être relancée seule ;
si un agent échoue (quota, réseau), l'étape s'arrête sans écraser le dernier résultat valide. Les entreprises déjà livrées sont mémorisées :
relancer le lendemain produit 10 <b>nouveaux</b> leads.</p>

<h2>4. Les {len(leads)} leads livrés</h2>
{table(["Score", "Entreprise", "Lieu", "Interlocuteur", "Agent", "Besoin (chaleur)", "Vérification"], lead_rows)}
<p>Le détail complet (email, téléphone, site, justification, sources) est dans <code>leads.xlsx</code>, feuille « 10 leads », et les contrôles dans la feuille « Vérification ».</p>

<h2>5. Temps et coût</h2>
{table(["Poste", "Valeur"], [
 ["Durée d'une exécution complète", "environ 20 à 25 minutes, dont 15 à 20 pour l'agent de recherche"],
 ["APIs France Travail, registre, DNS", "gratuites"],
 ["Gemini (second avis)", "0 € (palier gratuit)"],
 ["Claude (agent de recherche)", f"inclus dans l'abonnement Claude Pro utilisé ; coût API équivalent mesuré : {cost:.2f} $ pour {n_runs} entreprises, soit {per_company:.2f} $ par entreprise étudiée"],
 ["Coût pour 10 leads supplémentaires", f"environ {per_company * 25:.1f} $ en coût API équivalent (environ 25 entreprises étudiées pour 10 leads joignables), 0 € avec l'abonnement"],
 ["Temps de conception et de développement", "à compléter par le candidat"],
])}

<h2>6. Limites rencontrées et améliorations</h2>
<ul>
<li><b>API « Offres d'emploi » de France Travail non accessible</b> avec un compte étudiant : contournement par la recherche publique (offres et sites partenaires).</li>
<li><b>Premier essai trop « tiède » :</b> en partant des seules prédictions La Bonne Boite, presque aucun lead n'avait de preuve directe. Le pipeline part désormais des offres réelles, ce qui a fait apparaître des leads chauds.</li>
<li><b>Beaucoup d'entreprises opposées à la prospection</b> au registre, dont plusieurs avec des offres réelles : plutôt que de les perdre, elles sont gardées avec leurs seules coordonnées publiées (aucune donnée du registre).</li>
<li><b>LinkedIn :</b> jamais lu automatiquement (conditions d'utilisation) ; le profil n'est repris que s'il apparaît dans les résultats de recherche. À compléter à la main ou via une source autorisée (Sales Navigator, API d'enrichissement).</li>
<li><b>Recherche web dépendante du quota du modèle :</b> l'étape est conçue pour reprendre sans perte.</li>
<li><b>Avec plus de temps :</b> veille quotidienne planifiée, scoring appris sur les retours d'appels de Katalyx, enrichissement email nominatif via un fournisseur conforme, envoi des leads directement dans le CRM.</li>
</ul>
<p style="color:#667085;font-size:9pt">Outil de développement : le code a été écrit avec l'assistance de Claude Code ; les choix d'architecture, de signaux et de règles sont expliqués ci-dessus.</p>
</body></html>"""
    out = OUTPUT / "rapport.html"
    out.write_text(html, encoding="utf-8")
    pdf = OUTPUT / "rapport.pdf"
    if Path(CHROME).exists():
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        "--user-data-dir=" + str(OUTPUT / ".chrome"), "--print-to-pdf=" + str(pdf), out.as_uri()],
                       capture_output=True, timeout=120)
    print(f"Rapport : {out.name}" + (f" et {pdf.name}" if pdf.exists() else ""))


if __name__ == "__main__":
    main()
