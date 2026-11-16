"""Run the whole lead generation pipeline: python run.py

0. jobs.py     - real job offers online now (France Travail public search), direct employers only
1. signals.py  - France Travail / La Bonne Boite: companies about to hire for jobs a Katalyx agent automates, + step 0 offers
2. enrich.py   - official registry: size, executives, opt-out of prospecting; keep PME/ETI only
3. research.py - Claude research agent (web search): website, public contacts, decision maker, proof of need
4. qualify.py  - Gemini second opinion, ranking, 10 best leads -> output/leads.xlsx
5. verify.py   - automatic checks of each lead (registry, website, email domain, phone, offer still online)

Each step reads the previous step's file in output/, so a step can be re-run alone.
"""
import time

import enrich
import jobs
import qualify
import research
import signals
import verify

if __name__ == "__main__":
    t0 = time.time()
    for step in (jobs, signals, enrich, research, qualify, verify):
        print(f"\n=== {step.__name__} ===", flush=True)
        step.main()
    print(f"\nDone in {(time.time() - t0) / 60:.1f} min")
