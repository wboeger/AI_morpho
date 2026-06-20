#!/usr/bin/env python3
"""Copy DNA sequences from projects 1 and 2 to the Gyrodactylidae-MCO project (id=3).

Matches by species_name. Priority: project 1 then project 2 if project 1 has none.
Skips duplicates (same specimen + same marker).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from app.models import Specimen, DNASequence

TARGET_PROJECT_ID  = 3
SOURCE_PROJECT_IDS = [1, 2]


def run():
    app = create_app()
    with app.app_context():
        # Build species_name → [DNASequence] map from source projects
        source_seqs = {}   # species_name → {marker: DNASequence}
        for proj_id in SOURCE_PROJECT_IDS:
            for sp in Specimen.query.filter_by(project_id=proj_id).all():
                name = sp.species_name.strip().lower()
                source_seqs.setdefault(name, {})
                for ds in sp.dna_sequences:
                    # Don't overwrite: project 1 takes priority
                    if ds.marker not in source_seqs[name]:
                        source_seqs[name][ds.marker] = ds

        added = 0
        for target_sp in Specimen.query.filter_by(project_id=TARGET_PROJECT_ID).all():
            name = target_sp.species_name.strip().lower()
            seqs_for_name = source_seqs.get(name, {})
            existing_markers = {ds.marker for ds in target_sp.dna_sequences}

            for marker, src_ds in seqs_for_name.items():
                if marker in existing_markers:
                    continue
                new_ds = DNASequence(
                    specimen_id=target_sp.id,
                    marker=src_ds.marker,
                    accession=src_ds.accession,
                    available=src_ds.available,
                )
                db.session.add(new_ds)
                added += 1
                print(f"  {target_sp.species_name} — {marker} ({src_ds.accession or 'no accession'})")

        db.session.commit()
        print(f"\nDone. Added {added} DNA sequence record(s) to project {TARGET_PROJECT_ID}.")


if __name__ == '__main__':
    run()
