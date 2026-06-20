#!/usr/bin/env python3
"""
Create Gyrodactylidae-MCO project from MCO data in projects 1 and 2.

Strategy:
- Project 1 has 57 specimens with MCO values; project 2 has 41 (all overlap with proj 1).
- New project takes project 1 data for all overlapping species, plus any species
  unique to project 2 (there are none currently).
- Copies: specimens, mco structures, character_values, character_definitions (M01-M06).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from app import create_app, db
from app.models import (
    Project, ProjectMembership, Specimen, Structure,
    CharacterDefinition, CharacterValue, User
)
from sqlalchemy import text

SOURCE_PROJECT_IDS = [1, 2]
NEW_PROJECT_NAME   = "Gyrodactylidae-MCO"
OWNER_USER_ID      = 1   # WalterB


def run():
    app = create_app()
    with app.app_context():
        # ── 1. Guard: don't create twice ──────────────────────────────────────
        existing = Project.query.filter_by(name=NEW_PROJECT_NAME).first()
        if existing:
            print(f"Project '{NEW_PROJECT_NAME}' already exists (id={existing.id}). Abort.")
            return

        # ── 2. Create project ─────────────────────────────────────────────────
        proj = Project(
            name=NEW_PROJECT_NAME,
            description="MCO morphology only — merged from '18S' and 'ITS' projects",
            created_by=OWNER_USER_ID,
            created_at=datetime.utcnow(),
        )
        db.session.add(proj)
        db.session.flush()   # get proj.id
        new_proj_id = proj.id
        print(f"Created project id={new_proj_id}")

        # ── 3. Membership ─────────────────────────────────────────────────────
        mem = ProjectMembership(user_id=OWNER_USER_ID, project_id=new_proj_id, role="owner")
        db.session.add(mem)

        # ── 4. Copy MCO character definitions from project 1 ─────────────────
        src_chars = (CharacterDefinition.query
                     .filter_by(project_id=1, structure_type="mco")
                     .order_by(CharacterDefinition.code)
                     .all())

        char_id_map = {}   # old_id → new_id
        for sc in src_chars:
            nc = CharacterDefinition(
                project_id=new_proj_id,
                code=sc.code,
                name=sc.name,
                structure_type="mco",
                computation_type=sc.computation_type or "manual",
                geometric_operation=sc.geometric_operation,
                formula=sc.formula,
                states_json=sc.states_json,
                display_order=sc.display_order,
            )
            db.session.add(nc)
            db.session.flush()
            char_id_map[sc.id] = nc.id
            print(f"  Char {sc.code} ({sc.name}): old id={sc.id} → new id={nc.id}")

        # ── 5. Collect specimens to copy ──────────────────────────────────────
        # Priority: project 1 for duplicates; add project 2 extras (currently none).
        # Build set of species covered by project 1 MCO data.
        proj1_mco_specimens = (
            db.session.query(Specimen)
            .join(Structure, Structure.specimen_id == Specimen.id)
            .join(CharacterValue, CharacterValue.structure_id == Structure.id)
            .join(CharacterDefinition, CharacterDefinition.id == CharacterValue.character_id)
            .filter(CharacterDefinition.structure_type == "mco",
                    Specimen.project_id == 1)
            .distinct()
            .all()
        )

        proj2_mco_specimens = (
            db.session.query(Specimen)
            .join(Structure, Structure.specimen_id == Specimen.id)
            .join(CharacterValue, CharacterValue.structure_id == Structure.id)
            .join(CharacterDefinition, CharacterDefinition.id == CharacterValue.character_id)
            .filter(CharacterDefinition.structure_type == "mco",
                    Specimen.project_id == 2)
            .distinct()
            .all()
        )

        proj1_names = {s.species_name for s in proj1_mco_specimens}
        to_copy = list(proj1_mco_specimens)

        added_from_p2 = 0
        for sp in proj2_mco_specimens:
            if sp.species_name not in proj1_names:
                to_copy.append(sp)
                added_from_p2 += 1

        print(f"\nSpecimens: {len(proj1_mco_specimens)} from project 1, "
              f"{added_from_p2} unique from project 2 → total {len(to_copy)}")

        # ── 6. Copy each specimen + MCO structures + character_values ─────────
        copied_specimens = 0
        copied_structures = 0
        copied_values = 0

        for sp in to_copy:
            # New specimen
            new_sp = Specimen(
                project_id=new_proj_id,
                species_name=sp.species_name,
                specimen_id_label=sp.specimen_id_label,
                notes=sp.notes,
                created_by=sp.created_by or OWNER_USER_ID,
                created_at=sp.created_at or datetime.utcnow(),
                image_path=sp.image_path,
            )
            db.session.add(new_sp)
            db.session.flush()
            copied_specimens += 1

            # Find MCO structures on original specimen
            mco_structs = (Structure.query
                           .filter_by(specimen_id=sp.id, structure_type="mco")
                           .all())

            for st in mco_structs:
                new_st = Structure(
                    specimen_id=new_sp.id,
                    structure_type="mco",
                    image_path=st.image_path,
                    landmarks_json=st.landmarks_json,
                    landmarks_confirmed=st.landmarks_confirmed,
                    boundary_json=st.boundary_json,
                    boundary_confirmed=st.boundary_confirmed,
                    landmark_count=st.landmark_count,
                )
                db.session.add(new_st)
                db.session.flush()
                copied_structures += 1

                # Copy character values for MCO characters only
                for cv in CharacterValue.query.filter_by(structure_id=st.id).all():
                    new_char_id = char_id_map.get(cv.character_id)
                    if new_char_id is None:
                        continue   # skip non-MCO characters
                    new_cv = CharacterValue(
                        structure_id=new_st.id,
                        character_id=new_char_id,
                        raw_value=cv.raw_value,
                        state=cv.state,
                        confidence=cv.confidence,
                        auto_assigned=cv.auto_assigned,
                    )
                    db.session.add(new_cv)
                    copied_values += 1

        db.session.commit()
        print(f"\nDone.")
        print(f"  Specimens:         {copied_specimens}")
        print(f"  Structures (mco):  {copied_structures}")
        print(f"  Character values:  {copied_values}")
        print(f"  New project id:    {new_proj_id}")


if __name__ == "__main__":
    run()
