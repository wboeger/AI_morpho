"""One-time image consolidation.

Deduplicates structure images by content and moves the single surviving copy
into  data/uploads/structures/<SUBDIR>/  where SUBDIR is one of:

    MCO  hook  bar  haptor  _unsorted

Classification (per unique image content, based on the structure_type of every
DB row that references it):

    'anchor' in types OR more than one type  -> haptor   (whole-mount photos)
    only 'mco'                               -> MCO
    only 'hook'                              -> hook
    only bar types                           -> bar
    not referenced by any structure row      -> _unsorted

Filenames drop any leading "<digits>_" prefix; collisions with different
content get a _2/_3 suffix.

Run with  --apply  to perform the move + DB update; without it, dry-run only.
"""
import os
import re
import sys
import shutil
import hashlib
import collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from app.models import Structure

UP = '/Users/walterapboeger/Desktop/Gyromorphometry/AI_morpho2/data/uploads'
TARGET_REL = 'structures'                 # under UP
TARGET_ABS = os.path.join(UP, TARGET_REL)

APPLY = '--apply' in sys.argv

BAR_TYPES = {'superficial_bar', 'deep_bar'}


def md5(path):
    with open(path, 'rb') as fh:
        return hashlib.md5(fh.read()).hexdigest()


def strip_prefix(basename):
    return re.sub(r'^\d+_', '', basename)


def classify(types):
    if 'anchor' in types or len(types) > 1:
        return 'haptor'
    if 'mco' in types:
        return 'MCO'
    if 'hook' in types:
        return 'hook'
    if types & BAR_TYPES:
        return 'bar'
    return '_unsorted'


def gather_disk_images():
    """All upload image files except backups, phylogeny, and the new target dir."""
    out = []
    for root, _, files in os.walk(UP):
        if 'phylogeny' in root:
            continue
        if os.path.abspath(root).startswith(os.path.abspath(TARGET_ABS)):
            continue
        for f in files:
            if f.lower().endswith(('.png', '.jpg', '.jpeg')):
                out.append(os.path.relpath(os.path.join(root, f), UP))
    return out


def main():
    app = create_app()
    with app.app_context():
        rows = [s for s in Structure.query
                .filter(Structure.image_path != None, Structure.image_path != '')
                .all()]

        # hash -> set(structure_types)  and  hash -> referenced relpath (one, for naming)
        ref_hash_types = collections.defaultdict(set)
        ref_hash_path = {}
        path_hash = {}            # relpath -> hash (for referenced)
        for s in rows:
            ap = os.path.join(UP, s.image_path)
            if not os.path.exists(ap):
                print('  WARN missing referenced file:', s.image_path)
                continue
            h = md5(ap)
            path_hash[s.image_path] = h
            ref_hash_types[h].add(s.structure_type)
            ref_hash_path.setdefault(h, s.image_path)

        # every disk image -> hash (catches orphans)
        disk = gather_disk_images()
        hash_disk_paths = collections.defaultdict(list)
        for rel in disk:
            h = md5(os.path.join(UP, rel))
            hash_disk_paths[h].append(rel)

        # Decide target for each unique content hash
        used_names = collections.defaultdict(dict)   # sub -> {name: hash}
        hash_target = {}                             # hash -> target relpath
        plan = []
        # referenced first (so they win nice names), then pure orphans
        ordered = list(ref_hash_types.keys()) + \
            [h for h in hash_disk_paths if h not in ref_hash_types]
        for h in ordered:
            if h in hash_target:
                continue
            if h in ref_hash_types:
                sub = classify(ref_hash_types[h])
                rep = ref_hash_path[h]
            else:
                sub = '_unsorted'
                rep = hash_disk_paths[h][0]
            name = strip_prefix(os.path.basename(rep))
            stem, ext = os.path.splitext(name)
            cand, n = name, 1
            while cand in used_names[sub] and used_names[sub][cand] != h:
                n += 1
                cand = f'{stem}_{n}{ext}'
            used_names[sub][cand] = h
            target_rel = os.path.join(TARGET_REL, sub, cand)
            hash_target[h] = target_rel
            plan.append((h, sub, rep, target_rel))

        # Report
        by_sub = collections.Counter(p[1] for p in plan)
        print(f'Unique image contents: {len(plan)}')
        print('Destination subdir counts:', dict(by_sub))
        print(f'DB structure rows to repoint: {len(path_hash)}')
        print()
        for sub in ('MCO', 'hook', 'bar', 'haptor', '_unsorted'):
            sample = [p for p in plan if p[1] == sub][:4]
            for h, s, rep, tgt in sample:
                print(f'  [{sub:9}] {rep}  ->  {tgt}')
        print()

        if not APPLY:
            print('DRY RUN — re-run with --apply to execute.')
            return

        # Copy single surviving file to target
        for h, sub, rep, target_rel in plan:
            dst = os.path.join(UP, target_rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if not os.path.exists(dst):
                shutil.copy2(os.path.join(UP, rep), dst)

        # Repoint DB
        repointed = 0
        for s in rows:
            h = path_hash.get(s.image_path)
            if h and h in hash_target:
                s.image_path = hash_target[h]
                repointed += 1
        db.session.commit()
        print(f'Repointed {repointed} structure rows.')

        # Verify
        bad = 0
        for s in Structure.query.filter(Structure.image_path != None,
                                        Structure.image_path != '').all():
            if not os.path.exists(os.path.join(UP, s.image_path)):
                bad += 1
                print('  MISSING after migrate:', s.image_path)
        print(f'Broken refs after migrate: {bad}')

        if bad == 0:
            # Delete old structure files + now-empty old dirs (not the new target)
            removed = 0
            for rel in set(disk):
                if rel.startswith(TARGET_REL + os.sep):
                    continue
                ap = os.path.join(UP, rel)
                if os.path.exists(ap):
                    os.remove(ap)
                    removed += 1
            # prune empty dirs under UP (except target + phylogeny)
            for root, dirs, files in os.walk(UP, topdown=False):
                if os.path.abspath(root).startswith(os.path.abspath(TARGET_ABS)):
                    continue
                if 'phylogeny' in root:
                    continue
                if root == UP:
                    continue
                if not os.listdir(root):
                    os.rmdir(root)
            print(f'Removed {removed} old image files; pruned empty dirs.')
        else:
            print('NOT deleting old files — broken refs present. Investigate.')


if __name__ == '__main__':
    main()
