"""
Migrate data/uploads/ into project-labelled subdirectories.

  data/uploads/18S/structures/   — specimens only in project 1 (Gyrodactylidae 18S)
  data/uploads/ITS/structures/   — specimens only in project 2 (Gyrodactylidae ITS)
  data/uploads/Common/structures/ — specimens present in both projects

Updates Structure.image_path and Specimen.image_path in the database.
All conflicts are identical duplicate files; one copy is kept and both DB
records are updated to the same new path.

Run from the project root:
    python scripts/migrate_uploads.py [--dry-run]
"""
import argparse, hashlib, os, shutil, sqlite3, sys
from collections import defaultdict

UPLOAD = os.path.join(os.path.dirname(__file__), '..', 'data', 'uploads')
UPLOAD = os.path.normpath(UPLOAD)
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'db.sqlite')
PROJ_LABEL = {1: '18S', 2: 'ITS'}


def sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()


def build_plan(db):
    shared = set(r[0] for r in db.execute(
        "SELECT species_name FROM specimens "
        "GROUP BY species_name HAVING COUNT(DISTINCT project_id) > 1"
    ))

    def subdir(project_id, species_name):
        return 'Common' if species_name in shared else PROJ_LABEL.get(project_id, str(project_id))

    # ── Structure images ──────────────────────────────────────────────────────
    struct_rows = db.execute("""
        SELECT st.id, st.image_path, sp.project_id, sp.species_name
        FROM structures st
        JOIN specimens sp ON st.specimen_id = sp.id
        WHERE st.image_path IS NOT NULL
        ORDER BY st.id
    """).fetchall()

    # Group by destination path to handle duplicates (same file, two DB rows)
    dst_groups = defaultdict(list)
    for r in struct_rows:
        fname = os.path.basename(r['image_path'])
        sub = subdir(r['project_id'], r['species_name'])
        new_path = f"{sub}/structures/{fname}"
        dst_groups[new_path].append({
            'struct_id': r['id'],
            'old_path': r['image_path'],
            'new_path': new_path,
        })

    # Flatten: for duplicate destinations, keep first entry as the "mover",
    # all others just get their DB path updated (no file copy needed).
    struct_plan = []  # list of dicts
    for dst_path, entries in dst_groups.items():
        for i, e in enumerate(entries):
            struct_plan.append({**e, 'move_file': (i == 0)})

    # ── Specimen images ───────────────────────────────────────────────────────
    spec_rows = db.execute("""
        SELECT sp.id, sp.image_path, sp.project_id, sp.species_name
        FROM specimens sp
        WHERE sp.image_path IS NOT NULL
        ORDER BY sp.id
    """).fetchall()

    spec_dst_groups = defaultdict(list)
    for r in spec_rows:
        fname = os.path.basename(r['image_path'])
        sub = subdir(r['project_id'], r['species_name'])
        new_path = f"{sub}/{fname}"
        spec_dst_groups[new_path].append({
            'spec_id': r['id'],
            'old_path': r['image_path'],
            'new_path': new_path,
        })

    spec_plan = []
    for dst_path, entries in spec_dst_groups.items():
        for i, e in enumerate(entries):
            spec_plan.append({**e, 'move_file': (i == 0)})

    return struct_plan, spec_plan


def run(dry_run=True):
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    struct_plan, spec_plan = build_plan(db)

    moved = skipped_missing = already_done = updated_db = 0
    errors = []

    def process(plan, table, id_col, label):
        nonlocal moved, skipped_missing, already_done, updated_db
        for e in plan:
            old_rel = e['old_path']
            new_rel = e['new_path']
            row_id  = e.get('struct_id') or e.get('spec_id')

            if old_rel == new_rel:
                already_done += 1
                continue

            src = os.path.join(UPLOAD, old_rel)
            dst = os.path.join(UPLOAD, new_rel)

            if e['move_file']:
                if not os.path.exists(src):
                    skipped_missing += 1
                    print(f"  MISSING  {old_rel!r}")
                    continue
                if not dry_run:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if os.path.abspath(src) != os.path.abspath(dst):
                        shutil.move(src, dst)
                        moved += 1
                    else:
                        already_done += 1
                else:
                    print(f"  MOVE  {old_rel!r}  →  {new_rel!r}")
                    moved += 1

            # Update DB record
            if not dry_run:
                db.execute(f"UPDATE {table} SET image_path=? WHERE {id_col}=?",
                           (new_rel, row_id))
                updated_db += 1
            else:
                updated_db += 1

    print(f"\n{'DRY RUN — ' if dry_run else ''}Processing {len(struct_plan)} structure records "
          f"and {len(spec_plan)} specimen records…\n")

    process(struct_plan, 'structures', 'id', 'structure')
    process(spec_plan,   'specimens',  'id', 'specimen')

    if not dry_run:
        db.commit()

        # Remove empty old directories
        for folder in ['1', '2', 'project_1']:
            old_dir = os.path.join(UPLOAD, folder)
            if os.path.isdir(old_dir):
                # Walk bottom-up, remove empty dirs
                for root, dirs, files in os.walk(old_dir, topdown=False):
                    if not os.listdir(root):
                        os.rmdir(root)
                        print(f"  RMDIR  {root}")

    db.close()
    print(f"\nSummary:")
    print(f"  Files {'would be ' if dry_run else ''}moved:          {moved}")
    print(f"  Already correct (skipped):  {already_done}")
    print(f"  Source missing (skipped):   {skipped_missing}")
    print(f"  DB records {'would be ' if dry_run else ''}updated:      {updated_db}")
    if errors:
        print(f"  ERRORS: {len(errors)}")
        for e in errors:
            print(f"    {e}")


if __name__ == '__main__':
    os.chdir(os.path.join(os.path.dirname(__file__), '..'))
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', default=False)
    args = parser.parse_args()
    run(dry_run=args.dry_run)
