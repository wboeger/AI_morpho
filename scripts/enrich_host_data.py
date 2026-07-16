"""One-time enrichment: fill Specimen host/locality columns from GenBank + GBIF.

For every specimen with a resolvable GenBank accession, fetches the record's
`source` feature (host + geo_loc_name/country qualifiers), then resolves the
host species against the GBIF taxonomic backbone for family/order and against
GBIF species profiles for habitat (freshwater/marine/brackish).

Accession is taken from, in order:
  1. DNASequence.accession rows on the specimen.
  2. The project's reference tree (project.tree_newick), or the latest
     completed NJ tree from a PhylogenyJob if no reference tree is set —
     tip labels are 'ACCESSION|Genus_species', matched to the specimen the
     same way the Character Matrix orders its rows (app.routes.matrix).

Only fills columns that are currently empty — safe to re-run; never overwrites
a value a user has since hand-edited. Skips specimens with no accession found
via either source.

Run from the project root:
    python scripts/enrich_host_data.py [--dry-run] [--project-id ID] [--email you@example.com]
"""
import argparse
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import create_app, db
from app.models import Specimen, DNASequence, PhylogenyJob

GBIF_MATCH = 'https://api.gbif.org/v1/species/match'
GBIF_PROFILES = 'https://api.gbif.org/v1/species/{key}/speciesProfiles'
DEFAULT_EMAIL = 'wboeger@gmail.com'  # Entrez requires a contact email


def _genbank_source_quals(accession: str, email: str) -> dict:
    """Return {'host': ..., 'geo': ...} parsed from the record's source feature."""
    from Bio import Entrez
    Entrez.email = email
    key = os.environ.get('NCBI_API_KEY', '')
    Entrez.api_key = key or None
    handle = Entrez.efetch(db='nuccore', id=accession, rettype='gb', retmode='xml')
    try:
        records = Entrez.read(handle)
    finally:
        handle.close()
    if not records:
        return {}
    quals = {}
    for feature in records[0].get('GBSeq_feature-table', []):
        if feature.get('GBFeature_key') != 'source':
            continue
        for q in feature.get('GBFeature_quals', []):
            name = q.get('GBQualifier_name')
            value = q.get('GBQualifier_value')
            if name == 'host':
                quals['host'] = value
            elif name in ('geo_loc_name', 'country') and 'geo' not in quals:
                quals['geo'] = value
    return quals


def _gbif_lookup(host_name: str) -> dict:
    """Return {'family': ..., 'order': ..., 'habitat': ...} for a host species name."""
    resp = requests.get(GBIF_MATCH, params={'name': host_name}, timeout=15)
    resp.raise_for_status()
    match = resp.json()
    if match.get('matchType') == 'NONE' or 'usageKey' not in match:
        return {}
    out = {'family': match.get('family'), 'order': match.get('order')}

    prof_resp = requests.get(GBIF_PROFILES.format(key=match['usageKey']), timeout=15)
    if prof_resp.ok:
        habitats = set()
        for p in prof_resp.json().get('results', []):
            if p.get('habitat'):
                habitats.add(p['habitat'].strip().title())
            else:
                for flag, label in (('marine', 'Marine'), ('freshwater', 'Freshwater'),
                                     ('terrestrial', 'Terrestrial')):
                    if p.get(flag):
                        habitats.add(label)
        if habitats:
            out['habitat'] = ' / '.join(sorted(habitats))
    return out


def _project_ordering_newick(project):
    """Same tree-selection order as matrix_view(): reference tree, else latest
    completed NJ tree from a phylogeny job."""
    if project.tree_newick:
        return project.tree_newick
    latest_nj = (PhylogenyJob.query
                 .filter_by(project_id=project.id)
                 .filter(PhylogenyJob.nj_newick.isnot(None))
                 .order_by(PhylogenyJob.submitted_at.desc())
                 .first())
    return latest_nj.nj_newick if latest_nj else None


_TREE_LEAF_CACHE = {}  # project_id -> (leaf_order, alias_map)


def _tree_leaves_for_project(project):
    if project.id not in _TREE_LEAF_CACHE:
        from app.routes.matrix import _parse_leaf_order, _load_alias_map
        newick = _project_ordering_newick(project)
        leaves = _parse_leaf_order(newick) if newick else []
        _TREE_LEAF_CACHE[project.id] = (leaves, _load_alias_map(project.id))
    return _TREE_LEAF_CACHE[project.id]


def _tree_accession_for_specimen(specimen):
    """Find a GenBank accession for this specimen from its project's tree tip
    labels ('ACCESSION|Genus_species'), or None if no tree/match."""
    from app.routes.matrix import _match_leaf
    leaves, alias_map = _tree_leaves_for_project(specimen.project)
    for leaf in leaves:
        if '|' not in leaf:
            continue
        if _match_leaf(leaf, specimen.species_name, alias_map):
            acc = leaf.split('|')[0]
            return re.sub(r'^_R_', '', acc, flags=re.IGNORECASE)
    return None


def enrich(project_id=None, dry_run=False, email=DEFAULT_EMAIL, delay=0.4):
    app = create_app()
    with app.app_context():
        query = Specimen.query
        if project_id:
            query = query.filter_by(project_id=project_id)
        specimens = query.all()

        updated = skipped_no_accession = errors = 0

        for sp in specimens:
            needs = not all([sp.host_species, sp.host_habitat, sp.host_family,
                              sp.host_order, sp.geographic_area])
            if not needs:
                continue

            seq = next((d for d in sp.dna_sequences if d.accession), None)
            accession = seq.accession if seq else _tree_accession_for_specimen(sp)
            if not accession:
                skipped_no_accession += 1
                continue

            try:
                quals = _genbank_source_quals(accession, email)
                time.sleep(delay)
            except Exception as exc:
                print(f'  [genbank error] {sp.species_name} ({accession}): {exc}')
                errors += 1
                continue

            host = quals.get('host')
            geo = quals.get('geo')

            gbif = {}
            if host:
                try:
                    gbif = _gbif_lookup(host)
                except Exception as exc:
                    print(f'  [gbif error] {sp.species_name} host={host!r}: {exc}')

            changed = []
            if host and not sp.host_species:
                sp.host_species = host
                changed.append('host_species')
            if geo and not sp.geographic_area:
                sp.geographic_area = geo
                changed.append('geographic_area')
            if gbif.get('family') and not sp.host_family:
                sp.host_family = gbif['family']
                changed.append('host_family')
            if gbif.get('order') and not sp.host_order:
                sp.host_order = gbif['order']
                changed.append('host_order')
            if gbif.get('habitat') and not sp.host_habitat:
                sp.host_habitat = gbif['habitat']
                changed.append('host_habitat')

            if changed:
                updated += 1
                print(f'  {sp.species_name}: {", ".join(changed)}')

        if dry_run:
            print(f'\n[dry-run] would update {updated} specimen(s); '
                  f'{skipped_no_accession} without accession; {errors} error(s).')
            db.session.rollback()
        else:
            db.session.commit()
            print(f'\nUpdated {updated} specimen(s); '
                  f'{skipped_no_accession} without accession; {errors} error(s).')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--project-id', type=int, default=None)
    parser.add_argument('--email', default=DEFAULT_EMAIL)
    args = parser.parse_args()
    enrich(project_id=args.project_id, dry_run=args.dry_run, email=args.email)
