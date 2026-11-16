"""Step 3 - A Claude research agent qualifies the most promising companies on the open web.

Companies are ranked first (real job offer > several matching agents > predicted hiring > SME size) and only the
best MAX_RESEARCH are researched per run, to keep each run fast and within the model's quota. A company already
researched less than CACHE_DAYS ago with the current prompt is not researched again.

For each company, Claude Code runs headless (`claude -p`) with web search and web fetch only, and must return
strict JSON: official website, public business contact details, the right person for the matching agent, and
dated evidence (with URLs) of the need. Rules: public professional sources only, nothing invented, null when unsure.
Companies opposed to prospecting in the registry are researched with the contacts they publish themselves only.

Output: output/researched.csv (+ the raw JSON of each run in output/research/)
"""
import csv
import json
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from common import OUTPUT

CLAUDE = shutil.which("claude") or "claude"
RAW = OUTPUT / "research"
RAW.mkdir(exist_ok=True)
PROMPT_VERSION = "v3"   # bump when the prompt changes: cached answers of older prompts are ignored
CACHE_DAYS = 30
MAX_RESEARCH = 45       # companies researched per run, best first
PARALLEL = 5

AGENTS = {
    "Devis": ("prépare les devis à partir des demandes clients et du catalogue",
              "directeur commercial, responsable ADV ou administration des ventes, responsable du chiffrage"),
    "Qualite": ("rédige les fiches de non-conformité et propose les actions correctives",
                "responsable qualité / QSE, directeur industriel ou de production"),
    "Administratif": ("trie les emails, extrait les informations et met à jour les tableaux",
                      "directeur administratif et financier, responsable administratif"),
    "Reporting": ("assemble les indicateurs de la semaine et rédige la synthèse",
                  "directeur administratif et financier, contrôleur de gestion"),
    "Commercial": ("qualifie les demandes entrantes et prépare les relances",
                   "directeur commercial, responsable des ventes"),
}

PROMPT = """Tu es un agent de recherche commerciale pour Katalyx (studio IA à Aix-en-Provence qui conçoit des agents IA sur mesure pour PME industrielles et BTP).

Entreprise à étudier :
- Raison sociale : {legal_name} (SIREN {siren})
- Secteur : {sector} - {naf_label}
- Établissement : {city} ({postcode}) ; siège : {hq_address}
- Effectif : {headcount} ({category})
- Dirigeants au registre : {executives}
- Signal : France Travail (La Bonne Boite) prédit des recrutements sur : {jobs}
- Offres d'emploi déjà repérées (à vérifier en priorité, sont-elles toujours en ligne ?) : {live_offers}
- Agent(s) Katalyx correspondant(s) : {agents_desc}
{restriction}
Avec la recherche web, trouve :
1. le site officiel de l'entreprise ;
2. un email et un téléphone professionnels publiés par l'entreprise (site, mentions légales, annuaires officiels) ;
3. LA BONNE PERSONNE pour cet agent : {roles}. Dans une PME de moins de 50 salariés, le dirigeant convient.
   Donne son poste exact et la source. Puis cherche son profil LinkedIn avec la requête web
   "<prénom nom> {legal_name} LinkedIn" : reprends l'URL linkedin.com/in/... seulement si le nom ET l'entreprise correspondent
   dans le résultat de recherche (n'ouvre jamais linkedin.com) ;
4. une preuve publique et datée du besoin, avec son URL. Cherche d'abord une OFFRE D'EMPLOI en cours de cette entreprise
   pour un poste lié à l'agent ({jobs_short}) :
   - page carrières / recrutement du site officiel ;
   - recherches web du type "{legal_name} offre d'emploi {city}", "{legal_name} recrute", et sur
     candidat.francetravail.fr, welcometothejungle.com, hellowork.com, apec.fr ;
   Sinon, cherche un autre signal récent : levée de fonds, rachat, nouvelle usine ou extension, gros contrat, certification ISO en cours,
   forte croissance d'effectif, appel d'offres remporté.
   Indique la date de publication (format AAAA-MM-JJ si possible).

Règles : sources publiques et professionnelles uniquement, n'invente rien, mets null quand tu n'es pas sûr. Pas d'email personnel (gmail, etc.).

Réponds UNIQUEMENT par un objet JSON :
{{"website": str|null, "email": str|null, "phone": str|null,
  "contact_name": str|null, "contact_role": str|null, "contact_linkedin": str|null, "contact_source": str|null,
  "need_evidence": str|null, "need_source_url": str|null, "need_date": str|null,
  "heat": "chaud"|"tiède"|"froid", "heat_reason": str}}
Niveaux (respecte-les strictement) :
- "chaud" = offre d'emploi sur un poste lié à l'agent, publiée il y a moins de 90 jours OU encore en ligne aujourd'hui sur
  France Travail ou le site de l'entreprise (une offre pourvue y est retirée), OU événement de croissance daté de moins de 6 mois
  qui augmente directement la charge visée (nouveau site, gros contrat, rachat) ;
- "tiède" = signal réel mais indirect ou plus ancien (seulement la prédiction France Travail, offre de plus de 90 jours, croissance générale) ;
- "froid" = aucun signal confirmé, ou entreprise en difficulté."""

RESTRICTED = """- ATTENTION : cette entreprise est opposée à la prospection au registre officiel. N'utilise AUCUNE donnée du registre ni
  d'annuaire d'entreprises : contact (nom, email, téléphone) uniquement s'il est publié par l'entreprise elle-même (son site,
  sa page carrières, son offre d'emploi). Si rien n'est publié, mets null.
"""


def priority(row):
    """Most promising first: real offer, several agents, predicted hiring, SME size."""
    size_bonus = 1 if any(t in row.get("headcount", "") for t in ("20-49", "50-99", "100-199", "200-249")) else 0
    return (bool(row.get("live_offers")), len(row["agents"].split(", ")), float(row.get("max_hiring_potential") or 0), size_bonus)


def cached(siren):
    f = RAW / f"{siren}.{PROMPT_VERSION}.json"
    if f.exists() and time.time() - f.stat().st_mtime < CACHE_DAYS * 86400:
        return json.loads(f.read_text(encoding="utf-8"))
    return None


def run_agent(row):
    agents = row["agents"].split(", ")
    agents_desc = "; ".join(f"agent {a} ({AGENTS.get(a, ('', ''))[0]})" for a in agents)
    roles = " ; ".join(AGENTS.get(a, ("", ""))[1] for a in agents)
    jobs_short = ", ".join(j.split(" (")[0] for j in row["jobs_about_to_hire"].split(" | "))
    row = {**row, "live_offers": row.get("live_offers") or "aucune", "executives": row.get("executives") or "non utilisés"}
    out = cached(row["siren"])
    if out is None:
        prompt = PROMPT.format(agents_desc=agents_desc, roles=roles, jobs=row["jobs_about_to_hire"], jobs_short=jobs_short,
                               restriction=RESTRICTED if row.get("contact_restriction") else "", **row)
        try:
            # the prompt goes through stdin: Windows' command line mangles long multi-line arguments
            p = subprocess.run([CLAUDE, "-p", "--allowedTools", "WebSearch,WebFetch", "--output-format", "json"],
                               input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=420)
            out = json.loads(p.stdout)
        except Exception as e:  # timeout or unreadable output: keep the company, flag it
            return {**row, "live_offers": row["live_offers"].replace("aucune", ""), "agent_error": str(e)[:120]}
        if not out.get("is_error"):
            (RAW / f"{row['siren']}.{PROMPT_VERSION}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    m = re.search(r"\{.*\}", out.get("result", ""), re.S)
    try:
        found = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        found = {}
    row["live_offers"] = row["live_offers"].replace("aucune", "")
    return {**row, **{f"r_{k}": ("" if v is None else v) for k, v in found.items()},
            "agent_turns": out.get("num_turns"), "agent_seconds": round(out.get("duration_ms", 0) / 1000),
            "agent_error": "" if found else "no JSON"}


def main():
    rows = [r for r in csv.DictReader(open(OUTPUT / "enriched.csv", encoding="utf-8-sig"), delimiter=";")
            if not r["excluded_because"]]
    rows.sort(key=priority, reverse=True)
    todo = rows[:MAX_RESEARCH]
    print(f"{len(rows)} entreprises éligibles ; les {len(todo)} plus prometteuses sont étudiées"
          f" ({sum(1 for r in todo if cached(r['siren']))} déjà en cache)", flush=True)
    with ThreadPoolExecutor(PARALLEL) as ex:
        out = list(ex.map(run_agent, todo))
    if out and all(r.get("agent_error") for r in out):  # e.g. usage limit reached: do not erase the last good run
        raise SystemExit("Every research agent failed: previous output/researched.csv kept unchanged")
    fields = []
    for r in out:
        fields += [k for k in r if k not in fields]
    with open(OUTPUT / "researched.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        w.writeheader()
        w.writerows(out)
    heat = {}
    for r in out:
        heat[r.get("r_heat") or "erreur"] = heat.get(r.get("r_heat") or "erreur", 0) + 1
    print(f"{len(out)} entreprises étudiées -> output/researched.csv ; avis de l'agent : {heat}")


if __name__ == "__main__":
    main()
