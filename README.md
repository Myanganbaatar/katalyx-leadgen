# Katalyx Lead Generator

Machine de génération de leads B2B pour l'offre **LAB IA** de [Katalyx](https://katalyx.fr) :
elle repère, en Région Sud, les PME et ETI de l'industrie et du BTP qui ont un besoin actuel
d'un agent IA (Devis, Qualité, Administratif, Reporting, Commercial), puis livre 10 leads qualifiés
avec contact, preuve du besoin et niveau de chaleur.

Réalisé par Barsbold Myanganbaatar pour le test technique Katalyx (novembre 2026).
Le document d'explication complet est dans [`DOCUMENT.md`](DOCUMENT.md).

## Démarrer en 3 minutes

Prérequis : Python 3.11+, et pour l'étape 3 l'outil [Claude Code](https://claude.com/claude-code) connecté (`claude` dans le terminal).

```bash
git clone <ce dépôt> && cd katalyx-leadgen
cp .env.example .env        # puis renseigner les clés (voir ci-dessous)
python check_keys.py        # vérifie les clés sans les afficher
python app.py               # tableau de bord : http://localhost:8800
```

Sur le tableau de bord, **▶ Tout lancer** exécute le pipeline et affiche le journal en direct.
Sans interface : `python run.py`. Aucune dépendance externe hormis `openpyxl` (export Excel) :
`pip install openpyxl`.

### Clés nécessaires (toutes gratuites)

| Variable | Où l'obtenir |
|---|---|
| `FRANCE_TRAVAIL_CLIENT_ID` / `_SECRET` | [francetravail.io](https://francetravail.io) → créer une application → ajouter l'API **La Bonne Boite** |
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com) → Get API key (palier gratuit) |
| Claude | aucune clé : l'étape 3 appelle Claude Code en mode non interactif (`claude -p`) |

## Le pipeline

| Étape | Fichier | Rôle | Sortie |
|---|---|---|---|
| 0 | `jobs.py` | Offres d'emploi réelles et récentes (France Travail) sur les postes que les agents automatisent | `output/jobs.csv` |
| 1 | `signals.py` | Entreprises qui vont recruter sur ces postes (API La Bonne Boite) + offres de l'étape 0 | `output/signals.csv` |
| 2 | `enrich.py` | Registre officiel : taille, dirigeants ; exclut grands groupes, TPE et entreprises opposées à la prospection | `output/enriched.csv` |
| 3 | `research.py` | Agent Claude avec recherche web : site, contacts publics, interlocuteur, preuve datée du besoin | `output/researched.csv` |
| 4 | `qualify.py` | Second avis Gemini, classement, export des 10 meilleurs leads | `output/leads.xlsx`, `output/leads.csv` |

Chaque étape lit le fichier de la précédente : on peut relancer une étape seule.
Les entreprises déjà livrées sont mémorisées dans `output/delivered_history.csv` : relancer le pipeline
le lendemain produit **10 nouveaux leads** (supprimer ce fichier pour repartir de zéro).

Pour une exécution quotidienne sans intervention (Windows) :
`schtasks /create /tn KatalyxLeads /tr "python C:\chemin\katalyx-leadgen\run.py" /sc daily /st 07:00`
Si un agent échoue (quota, réseau), l'étape s'arrête **sans écraser** le dernier résultat valide.

## Conformité

- Sources publiques et officielles uniquement ; aucune page LinkedIn ou Indeed n'est lue automatiquement.
- Les entreprises inscrites comme **opposées à la prospection commerciale** au registre sont exclues.
- Aucune donnée personnelle non professionnelle ; chaque contact est accompagné de sa source.
- `.env` (les clés) et `output/` sont exclus du dépôt (`.gitignore`).
