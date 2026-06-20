"""Phylogenetic analysis pipeline.

Full flow:
  1. NCBI Entrez search + download  (or upload existing trimmed FASTA)
  2. MAFFT alignment
  3. trimAl trimming
  4. CIPRES / RAxML-NG submission
  5. Status polling → download results
  6. Root with ape::root()
  7. Import into project
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from flask import Blueprint, render_template, request, jsonify, current_app, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app import db
from app.models import Project, PhylogenyJob

phylo_bp = Blueprint('phylogeny', __name__)

# ── Defaults matching R v8 script ─────────────────────────────────────────────

DEFAULT_GENE_QUERY_18S = (
    '(small subunit ribosomal RNA[All Fields] OR 18S[All Fields]) '
    'NOT (internal transcribed spacer[All Fields])'
)

DEFAULT_GENE_QUERY_ITS = (
    '(internal transcribed spacer[All Fields] OR ITS[All Fields]) '
    'NOT (18S[All Fields] OR 28S[All Fields])'
)

DEFAULT_OUTGROUP_GENERA = [
    'Aglaiogyrodactylus', 'Onychogyrodactylus', 'Phanerothecium',
    'Tresuncinidactylus', 'Mormyrogyrodactylus', 'Diplogyrodactylus',
    'Hyperopletes', 'Oogyrodactylus',
]

DEFAULT_OUTGROUP_DEFS = [
    {'family': 'Oogyrodactylidae', 'mode': 'each_genus', 'n': 2},
]


# ── NCBI helpers ──────────────────────────────────────────────────────────────

def _ncbi_search(term, email, retmax=10000):
    from Bio import Entrez
    Entrez.email = email
    h = Entrez.esearch(db='nuccore', term=term, retmax=retmax)
    result = Entrez.read(h)
    h.close()
    return result['IdList'], int(result['Count'])


def _ncbi_fetch_batch(ids, email, batch_size=200):
    """Download FASTA records in batches. Returns dict {rec.id: SeqRecord}."""
    from Bio import Entrez, SeqIO
    Entrez.email = email
    records = {}
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i + batch_size]
        for attempt in range(3):
            try:
                h = Entrez.efetch(db='nuccore', id=','.join(batch),
                                  rettype='fasta', retmode='text')
                for rec in SeqIO.parse(h, 'fasta'):
                    records[rec.id] = rec
                h.close()
                break
            except Exception:
                if attempt < 2:
                    time.sleep(3)
                else:
                    raise
        time.sleep(0.4)   # respect NCBI rate limit (3 req/s without API key)
    return records


def _parse_species_name(description):
    """Return genus_species from a FASTA description line (2nd + 3rd word)."""
    parts = description.split()
    if len(parts) >= 3:
        return f"{parts[1]}_{parts[2]}"
    if len(parts) >= 2:
        return parts[1]
    return parts[0]


def _process_records(records, bad_accessions=None, min_length=400, max_length_factor=2.0):
    """
    Filter and de-duplicate records, keeping one per species (longest that is
    ≤ max_length_factor × mean length of the deduplicated set).
    Rename headers to  accession|Genus_species.
    Returns list of SeqRecord.
    """
    from Bio.SeqRecord import SeqRecord

    # Remove bad accessions
    if bad_accessions:
        records = {k: v for k, v in records.items()
                   if not any(b.strip() in v.description
                              for b in bad_accessions if b.strip())}

    # Minimum length
    records = {k: v for k, v in records.items() if len(v.seq) >= min_length}

    # Deduplicate by exact sequence string
    seen_seq = {}
    for rec in records.values():
        s = str(rec.seq).upper()
        if s not in seen_seq or len(rec.seq) > len(seen_seq[s].seq):
            seen_seq[s] = rec
    records = {r.id: r for r in seen_seq.values()}

    # Compute max_length_factor × mean length cutoff
    if records:
        lengths = [len(r.seq) for r in records.values()]
        max_allowed = max_length_factor * (sum(lengths) / len(lengths))
    else:
        max_allowed = float('inf')

    # Group all qualifying sequences by species, sorted longest-first
    by_species = {}
    for rec in records.values():
        sp = _parse_species_name(rec.description)
        by_species.setdefault(sp, []).append(rec)
    for sp in by_species:
        by_species[sp].sort(key=lambda r: len(r.seq), reverse=True)

    # One per species: longest sequence that does not exceed 2× mean length;
    # if all are too long, skip that species entirely
    result = []
    for sp, recs in by_species.items():
        chosen = next((r for r in recs if len(r.seq) <= max_allowed), None)
        if chosen is None:
            continue
        new_id = f"{chosen.id}|{sp}"
        result.append(SeqRecord(chosen.seq, id=new_id, name='', description=''))
    return result


def _write_fasta(records, path):
    from Bio import SeqIO
    with open(path, 'w') as fh:
        SeqIO.write(records, fh, 'fasta')


def _count_fasta(path):
    return sum(1 for line in open(path) if line.startswith('>'))


# ── Pipeline steps (run inside background thread) ─────────────────────────────

def _set_status(job, status, message):
    job.status = status
    job.status_message = message
    db.session.commit()


def _fetch_step(job):
    """NCBI search → download → filter → outgroups → write raw FASTA."""
    email      = job.ncbi_email or 'user@example.com'
    taxon      = job.target_taxon or 'Gyrodactylidae'
    gene_q     = job.gene_query or DEFAULT_GENE_QUERY_18S
    min_len    = job.min_length or 400
    max_factor = job.max_length_factor if job.max_length_factor is not None else 2.0
    bad_acc    = job.bad_accessions or []
    og_defs    = job.outgroup_definitions or DEFAULT_OUTGROUP_DEFS

    # 1. Ingroup
    query = f'"{taxon}"[Organism] AND ({gene_q})'
    _set_status(job, 'fetching', f'Searching NCBI: {query}')

    ids, count = _ncbi_search(query, email)
    _set_status(job, 'fetching', f'Found {count} records. Downloading {len(ids)}…')

    records = _ncbi_fetch_batch(ids, email)
    job.n_sequences_raw = len(records)
    db.session.commit()

    _set_status(job, 'fetching', f'Processing {len(records)} sequences…')
    ingroup = _process_records(records, bad_acc, min_len, max_factor)
    job.n_sequences_deduped = len(ingroup)
    ingroup_species = {r.id.split('|')[1] for r in ingroup if '|' in r.id}
    db.session.commit()

    # 2. Outgroups
    outgroup_records = []
    seen_species = set(ingroup_species)

    for od in og_defs:
        family = od.get('family', '').strip()
        mode   = od.get('mode', 'each_genus')
        n      = int(od.get('n', 2))
        if not family:
            continue

        _set_status(job, 'fetching', f'Fetching outgroup family: {family}…')
        fq = f'"{family}"[Organism] AND ({gene_q})'
        fids, _ = _ncbi_search(fq, email)
        frecs = _ncbi_fetch_batch(fids, email)
        candidates = _process_records(frecs, bad_acc, min_len, max_factor)

        # Exclude anything already in ingroup or previously selected outgroups
        candidates = [
            r for r in candidates
            if taxon.lower() not in r.description.lower()
            and ('|' not in r.id or r.id.split('|')[1] not in seen_species)
        ]

        if mode == 'each_genus':
            by_genus = {}
            for rec in candidates:
                sp    = rec.id.split('|')[1] if '|' in rec.id else rec.id
                genus = sp.split('_')[0]
                by_genus.setdefault(genus, []).append(rec)
            selected = []
            for genus_recs in by_genus.values():
                genus_recs.sort(key=lambda r: len(r.seq), reverse=True)
                selected.extend(genus_recs[:n])
        elif mode == 'top_species':
            candidates.sort(key=lambda r: len(r.seq), reverse=True)
            selected = candidates[:n]
        else:
            selected = candidates[:n]

        outgroup_records.extend(selected)
        for r in selected:
            sp = r.id.split('|')[1] if '|' in r.id else r.id
            seen_species.add(sp)

        _set_status(job, 'fetching',
                    f'Added {len(selected)} sequences from {family}. '
                    f'Total outgroups so far: {len(outgroup_records)}')

    # 3. Combine, deduplicate, write
    final = list(ingroup) + outgroup_records
    seen_s = {}
    final_unique = []
    for rec in final:
        s = str(rec.seq).upper()
        if s not in seen_s:
            seen_s[s] = True
            final_unique.append(rec)

    job.n_sequences_final = len(final_unique)
    raw_path = os.path.join(job.result_dir, f'{job.marker}_raw.fa')
    _write_fasta(final_unique, raw_path)
    job.raw_fasta_path = raw_path
    job.fasta_filename  = os.path.basename(raw_path)
    job.n_sequences     = len(final_unique)
    _set_status(job, 'fetched',
                f'{len(final_unique)} sequences written '
                f'({len(ingroup)} ingroup + {len(outgroup_records)} outgroup). '
                f'Review sequences and click Approve & Align.')


def _align_step(job):
    """MAFFT --auto --adjustdirection."""
    _set_status(job, 'aligning', 'Running MAFFT alignment…')
    aligned_path = os.path.join(job.result_dir, f'{job.marker}_aligned.fa')
    result = subprocess.run(
        ['mafft', '--auto', '--thread', '-1', '--adjustdirection',
         job.raw_fasta_path],
        capture_output=True, text=True, timeout=3600,
    )
    with open(aligned_path, 'w') as fh:
        fh.write(result.stdout)
    if result.returncode != 0 or not os.path.getsize(aligned_path):
        raise RuntimeError(f'MAFFT failed: {result.stderr[:400]}')
    n = _count_fasta(aligned_path)
    job.aligned_fasta_path = aligned_path
    _set_status(job, 'aligned', f'Alignment done ({n} sequences). Trimming…')


def _trim_step(job):
    """trimAl -gappyout."""
    _set_status(job, 'trimming', 'Running trimAl (-gappyout)…')
    trimmed_path = os.path.join(job.result_dir, f'{job.marker}_trimmed.fa')
    result = subprocess.run(
        ['trimal', '-in', job.aligned_fasta_path,
         '-out', trimmed_path, '-gappyout'],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f'trimAl failed: {result.stderr[:400]}')
    if not os.path.exists(trimmed_path) or not os.path.getsize(trimmed_path):
        raise RuntimeError('trimAl produced empty output.')
    n = _count_fasta(trimmed_path)
    job.trimmed_fasta_path = trimmed_path
    job.fasta_filename      = os.path.basename(trimmed_path)
    job.n_sequences         = n
    _set_status(job, 'trimmed',
                f'Trimming complete ({n} sequences). '
                f'Ready to submit to CIPRES.')


def _nj_step(job):
    """Compute a rapid NJ tree from the trimmed alignment. Non-fatal on failure."""
    _set_status(job, 'nj_running', 'Computing neighbor-joining tree…')
    try:
        from Bio import AlignIO, Phylo
        from Bio.Phylo.TreeConstruction import DistanceCalculator, DistanceTreeConstructor
        import io as _io
        alignment = AlignIO.read(job.trimmed_fasta_path, 'fasta')
        calc      = DistanceCalculator('identity')
        dm        = calc.get_distance(alignment)
        nj_tree   = DistanceTreeConstructor().nj(dm)
        buf       = _io.StringIO()
        Phylo.write(nj_tree, buf, 'newick')
        newick = buf.getvalue().strip()
        nwk_path = os.path.join(job.result_dir, 'nj_tree.nwk')
        with open(nwk_path, 'w') as f:
            f.write(newick)
        job.nj_newick = newick
        n = len(alignment)
        _set_status(job, 'nj_ready',
                    f'NJ tree ready ({n} sequences). '
                    f'Review tree, replace any problematic sequences, then approve for CIPRES.')
    except Exception as exc:
        # NJ failure is non-fatal — fall back to trimmed
        job.nj_newick = None
        _set_status(job, 'trimmed',
                    f'Trimming complete. (NJ failed: {exc}) Ready for Galaxy.')


def _concatenate_alignments(path1, marker1, path2, marker2, out_path):
    """Concatenate two trimmed alignments. Taxa missing from one marker get all-gap columns.

    Returns (n_taxa, len1, len2) where len1/len2 are per-marker alignment widths.
    Species are matched by the label after '|' in the FASTA header.
    """
    from Bio import SeqIO
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord as SR

    def _load(path):
        recs = list(SeqIO.parse(path, 'fasta'))
        by_sp = {}
        for r in recs:
            sp = r.id.split('|')[1] if '|' in r.id else r.id
            by_sp[sp] = r
        width = len(next(iter(by_sp.values())).seq) if by_sp else 0
        return by_sp, width

    by_sp1, w1 = _load(path1)
    by_sp2, w2 = _load(path2)

    all_sp = sorted(set(by_sp1) | set(by_sp2))
    gap1 = '-' * w1
    gap2 = '-' * w2

    records = []
    for sp in all_sp:
        r1 = by_sp1.get(sp)
        r2 = by_sp2.get(sp)
        seq = (str(r1.seq) if r1 else gap1) + (str(r2.seq) if r2 else gap2)
        rec_id = (r1 or r2).id
        records.append(SR(Seq(seq), id=rec_id, name='', description=''))

    _write_fasta(records, out_path)
    return len(records), w1, w2


def _fetch_marker(job, marker, gene_query, suffix):
    """Run NCBI fetch for one marker, return path to raw FASTA. Non-destructive to job fields."""
    from Bio.SeqRecord import SeqRecord
    email      = job.ncbi_email or 'user@example.com'
    taxon      = job.target_taxon or 'Gyrodactylidae'
    min_len    = job.min_length or 400
    max_factor = job.max_length_factor if job.max_length_factor is not None else 2.0
    bad_acc    = job.bad_accessions or []
    og_defs    = job.outgroup_definitions or DEFAULT_OUTGROUP_DEFS

    query = f'"{taxon}"[Organism] AND ({gene_query})'
    _set_status(job, 'fetching', f'[{marker}] Searching NCBI: {query}')

    ids, count = _ncbi_search(query, email)
    _set_status(job, 'fetching', f'[{marker}] Found {count} records. Downloading {len(ids)}…')

    records = _ncbi_fetch_batch(ids, email)
    ingroup = _process_records(records, bad_acc, min_len, max_factor)

    # Outgroups
    seen = {r.id.split('|')[1] for r in ingroup if '|' in r.id}
    og_records = []
    for od in og_defs:
        family = od.get('family', '').strip()
        mode   = od.get('mode', 'each_genus')
        n      = int(od.get('n', 2))
        if not family:
            continue
        _set_status(job, 'fetching', f'[{marker}] Outgroup: {family}…')
        fq    = f'"{family}"[Organism] AND ({gene_query})'
        fids, _ = _ncbi_search(fq, email)
        frecs   = _ncbi_fetch_batch(fids, email)
        cands   = _process_records(frecs, bad_acc, min_len, max_factor)
        cands   = [r for r in cands
                   if taxon.lower() not in r.description.lower()
                   and ('|' not in r.id or r.id.split('|')[1] not in seen)]
        if mode == 'each_genus':
            by_g = {}
            for rec in cands:
                sp = rec.id.split('|')[1] if '|' in rec.id else rec.id
                by_g.setdefault(sp.split('_')[0], []).append(rec)
            sel = []
            for grecs in by_g.values():
                grecs.sort(key=lambda r: len(r.seq), reverse=True)
                sel.extend(grecs[:n])
        else:
            cands.sort(key=lambda r: len(r.seq), reverse=True)
            sel = cands[:n]
        og_records.extend(sel)
        for r in sel:
            seen.add(r.id.split('|')[1] if '|' in r.id else r.id)

    final = ingroup + og_records
    seen_s = {}
    unique = []
    for rec in final:
        s = str(rec.seq).upper()
        if s not in seen_s:
            seen_s[s] = True
            unique.append(rec)

    raw_path = os.path.join(job.result_dir, f'{suffix}_raw.fa')
    _write_fasta(unique, raw_path)
    return raw_path, len(ingroup), len(unique)


def _align_marker(raw_path, suffix, job_dir):
    """MAFFT align one marker file. Returns aligned_path."""
    aligned_path = os.path.join(job_dir, f'{suffix}_aligned.fa')
    result = subprocess.run(
        ['mafft', '--auto', '--thread', '-1', '--adjustdirection', raw_path],
        capture_output=True, text=True, timeout=3600,
    )
    with open(aligned_path, 'w') as fh:
        fh.write(result.stdout)
    if result.returncode != 0 or not os.path.getsize(aligned_path):
        raise RuntimeError(f'MAFFT failed for {suffix}: {result.stderr[:400]}')
    return aligned_path


def _trim_marker(aligned_path, suffix, job_dir):
    """trimAl -gappyout on one marker. Returns trimmed_path."""
    trimmed_path = os.path.join(job_dir, f'{suffix}_trimmed.fa')
    result = subprocess.run(
        ['trimal', '-in', aligned_path, '-out', trimmed_path, '-gappyout'],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f'trimAl failed for {suffix}: {result.stderr[:400]}')
    if not os.path.exists(trimmed_path) or not os.path.getsize(trimmed_path):
        raise RuntimeError(f'trimAl produced empty output for {suffix}.')
    return trimmed_path


def _concatenated_pipeline_thread(app, job_id):
    """Background thread: full concatenated (18S + ITS) fetch → align → trim → concatenate → NJ."""
    with app.app_context():
        job = db.session.get(PhylogenyJob, job_id)
        if not job:
            return
        try:
            import json as _json
            queries = _json.loads(job.gene_query) if job.gene_query else {}
            q18s = queries.get('18S', DEFAULT_GENE_QUERY_18S)
            qITS = queries.get('ITS', DEFAULT_GENE_QUERY_ITS)

            # Fetch both markers
            raw18s, ing18s, tot18s = _fetch_marker(job, '18S', q18s, '18S')
            rawITS, ingITS, totITS = _fetch_marker(job, 'ITS', qITS, 'ITS')

            job.raw_fasta_path   = raw18s   # store primary for downloads
            job.n_sequences_raw  = tot18s + totITS
            job.n_sequences_final = tot18s + totITS
            _set_status(job, 'fetched',
                        f'Fetched {tot18s} 18S + {totITS} ITS sequences. '
                        f'Starting alignment…')

            # Align both
            _set_status(job, 'aligning', 'Running MAFFT on 18S…')
            aln18s = _align_marker(raw18s, '18S', job.result_dir)
            _set_status(job, 'aligning', 'Running MAFFT on ITS…')
            alnITS = _align_marker(rawITS, 'ITS', job.result_dir)
            job.aligned_fasta_path = aln18s

            # Trim both
            _set_status(job, 'trimming', 'Running trimAl on 18S…')
            trm18s = _trim_marker(aln18s, '18S', job.result_dir)
            _set_status(job, 'trimming', 'Running trimAl on ITS…')
            trmITS = _trim_marker(alnITS, 'ITS', job.result_dir)

            # Concatenate
            _set_status(job, 'trimming', 'Concatenating alignments…')
            cat_path = os.path.join(job.result_dir, 'concatenated.fa')
            n_taxa, w18s, wITS = _concatenate_alignments(trm18s, '18S', trmITS, 'ITS', cat_path)
            job.trimmed_fasta_path = cat_path
            job.fasta_filename     = 'concatenated.fa'
            job.n_sequences        = n_taxa
            _set_status(job, 'trimmed',
                        f'Concatenation done: {n_taxa} taxa, {w18s}bp 18S + {wITS}bp ITS = '
                        f'{w18s + wITS}bp total. Ready for Galaxy.')

            _nj_step(job)
            _modeltest_step(job)

        except Exception as exc:
            job.status = 'failed'
            job.status_message = str(exc)
            db.session.commit()


def _pipeline_thread(app, job_id):
    """Background thread: fetch only — waits for user approval before aligning."""
    with app.app_context():
        job = db.session.get(PhylogenyJob, job_id)
        if not job:
            return
        try:
            _fetch_step(job)
            # Stop here; user must review sequences and click Approve & Align
        except Exception as exc:
            job.status = 'failed'
            job.status_message = str(exc)
            db.session.commit()


def _align_trim_thread(app, job_id):
    """Background thread: align → trim → model test → NJ."""
    with app.app_context():
        job = db.session.get(PhylogenyJob, job_id)
        if not job:
            return
        try:
            _align_step(job)
            _trim_step(job)
            _nj_step(job)
            _modeltest_step(job)   # non-fatal; updates model fields if installed
        except Exception as exc:
            job.status = 'failed'
            job.status_message = str(exc)
            db.session.commit()


def _replace_realign_thread(app, job_id, replacements, removals):
    """Fetch replacement sequences, rewrite raw FASTA, re-align → trim → NJ."""
    with app.app_context():
        job = db.session.get(PhylogenyJob, job_id)
        if not job:
            return
        try:
            from Bio import SeqIO
            from Bio.SeqRecord import SeqRecord as BR

            _set_status(job, 'aligning', 'Applying sequence changes…')
            email = job.ncbi_email or 'user@example.com'

            # Read current raw FASTA
            with open(job.raw_fasta_path) as fh:
                current = list(SeqIO.parse(fh, 'fasta'))

            # IDs to drop — strip MAFFT _R_ prefix so trimmed IDs match raw IDs
            def _strip_r(s):
                return s[3:] if s.startswith('_R_') else s

            drop_ids = {_strip_r(r) for r in removals} | {_strip_r(r['old_id']) for r in replacements}
            kept = [rec for rec in current if _strip_r(rec.id) not in drop_ids]

            # Fetch and insert replacements
            if replacements:
                new_accs = [r['new_accession'] for r in replacements]
                _set_status(job, 'aligning',
                            f'Fetching {len(new_accs)} replacement sequence(s) from NCBI…')
                fetched = _ncbi_fetch_batch(new_accs, email)
                for rep in replacements:
                    acc     = rep['new_accession']
                    species = rep.get('species', '')
                    if acc in fetched:
                        rec    = fetched[acc]
                        new_id = f"{rec.id}|{species}" if species else rec.id
                        kept.append(BR(rec.seq, id=new_id, name='', description=''))

            _write_fasta(kept, job.raw_fasta_path)
            job.n_sequences_final = len(kept)
            job.n_sequences       = len(kept)
            db.session.commit()

            _align_step(job)
            _trim_step(job)
            _nj_step(job)
            _modeltest_step(job)

        except Exception as exc:
            job.status = 'failed'
            job.status_message = str(exc)
            db.session.commit()


# ── Galaxy helpers (usegalaxy.eu REST API) ────────────────────────────────────

def _galaxy_base():
    return current_app.config.get('GALAXY_BASE_URL', 'https://usegalaxy.eu')


def _galaxy_headers(api_key):
    return {'x-api-key': api_key, 'Accept': 'application/json'}


def _galaxy_create_history(api_key, name='GyroMorpho'):
    import requests as _req
    base = _galaxy_base()
    r = _req.post(
        f'{base}/api/histories',
        headers={**_galaxy_headers(api_key), 'Content-Type': 'application/json'},
        json={'name': name}, timeout=30,
    )
    r.raise_for_status()
    return r.json()['id']


def _galaxy_upload_file(api_key, history_id, file_path, file_type='fasta'):
    """Upload a local file to a Galaxy history. Returns (dataset_id, upload_job_id)."""
    import requests as _req
    base = _galaxy_base()
    with open(file_path, 'rb') as fh:
        r = _req.post(
            f'{base}/api/tools',
            headers=_galaxy_headers(api_key),
            data={
                'tool_id': 'upload1',
                'history_id': history_id,
                'inputs': json.dumps({
                    'files_0|NAME': os.path.basename(file_path),
                    'file_count': '1',
                    'file_type': file_type,
                    'dbkey': '?',
                }),
            },
            files={'files_0|file_data': (os.path.basename(file_path), fh)},
            timeout=180,
        )
    r.raise_for_status()
    data = r.json()
    dataset_id    = data['outputs'][0]['id']
    upload_job_id = data['jobs'][0]['id'] if data.get('jobs') else None
    return dataset_id, upload_job_id


def _galaxy_wait_for_job(api_key, job_id, max_wait=300):
    """Poll a Galaxy job until done. Returns final state string."""
    import requests as _req
    base     = _galaxy_base()
    deadline = time.time() + max_wait
    while time.time() < deadline:
        r = _req.get(f'{base}/api/jobs/{job_id}',
                     headers=_galaxy_headers(api_key), timeout=30)
        r.raise_for_status()
        state = r.json().get('state', 'running')
        if state in ('ok', 'error', 'deleted', 'paused'):
            return state
        time.sleep(5)
    return 'timeout'


def _galaxy_run_tool(api_key, history_id, tool_id, inputs):
    """Invoke a Galaxy tool. Returns the Galaxy job ID of the first job."""
    import requests as _req
    base = _galaxy_base()
    r = _req.post(
        f'{base}/api/tools',
        headers={**_galaxy_headers(api_key), 'Content-Type': 'application/json'},
        json={'tool_id': tool_id, 'history_id': history_id, 'inputs': inputs},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if data.get('err_msg'):
        raise RuntimeError(f'Galaxy tool error: {data["err_msg"]}')
    jobs = data.get('jobs') or []
    if not jobs:
        raise RuntimeError(f'Galaxy returned no jobs: {str(data)[:400]}')
    return jobs[0]['id']


def _galaxy_check_status(api_key, job_id):
    """Return (stage, message) where stage matches CIPRES convention."""
    import requests as _req
    base = _galaxy_base()
    r = _req.get(f'{base}/api/jobs/{job_id}',
                 headers=_galaxy_headers(api_key), timeout=30)
    r.raise_for_status()
    data  = r.json()
    state = data.get('state', 'unknown')
    msg   = data.get('stderr', '') or data.get('stdout', '') or state
    if state == 'ok':
        return 'COMPLETED', msg
    if state in ('error', 'deleted'):
        return 'FAILED', msg
    if state == 'paused':
        return 'SUSPENDED', msg
    return 'RUNNING', msg


def _galaxy_download_results(api_key, job_id, dest_dir):
    """Download all output datasets of a Galaxy job. Returns list of filenames."""
    import requests as _req
    base = _galaxy_base()
    os.makedirs(dest_dir, exist_ok=True)

    r = _req.get(f'{base}/api/jobs/{job_id}/outputs',
                 headers=_galaxy_headers(api_key), timeout=30)
    r.raise_for_status()
    outputs = r.json()

    downloaded = []
    for out in outputs:
        ds = out.get('dataset') or {}
        ds_id = ds.get('id') or out.get('id')
        name  = (out.get('name') or 'output').replace(' ', '_').replace('/', '_')
        if not ds_id:
            continue
        dest = os.path.join(dest_dir, f'{name}.dat')
        try:
            dl = _req.get(f'{base}/api/datasets/{ds_id}/display',
                          headers=_galaxy_headers(api_key), stream=True, timeout=300)
            dl.raise_for_status()
            with open(dest, 'wb') as fh:
                for chunk in dl.iter_content(65536):
                    fh.write(chunk)
            downloaded.append(os.path.basename(dest))
        except Exception:
            pass
    return downloaded


def _submit_to_galaxy_raxml(fasta_path, api_key, n_bootstraps=1000):
    """Upload alignment to Galaxy and submit RAxML-NG. Returns (history_id, job_id)."""
    tool_id    = current_app.config.get('GALAXY_RAXML_TOOL_ID',
                     'toolshed.g2.bx.psu.edu/repos/iuc/raxml/raxml/8.2.12+galaxy2')
    history_id = _galaxy_create_history(api_key, 'GyroMorpho_RAxML')
    ds_id, up_job = _galaxy_upload_file(api_key, history_id, fasta_path, 'fasta')
    if up_job:
        state = _galaxy_wait_for_job(api_key, up_job, max_wait=300)
        if state != 'ok':
            raise RuntimeError(f'Galaxy upload job failed (state: {state})')
    inputs = {
        'input_data': {'values': [{'id': ds_id, 'src': 'hda'}]},
        'analysis': {
            'select_analysis': 'all',
            '__current_case__': 2,
            'num_replicates': str(n_bootstraps),
        },
    }
    job_id = _galaxy_run_tool(api_key, history_id, tool_id, inputs)
    return history_id, job_id


def _submit_to_galaxy_mrbayes(nexus_path, api_key, ngen=1000000, nruns=2,
                               nchains=4, burninfrac=0.25):
    """Upload NEXUS to Galaxy and submit MrBayes. Returns (history_id, job_id)."""
    tool_id    = current_app.config.get('GALAXY_MRBAYES_TOOL_ID',
                     'toolshed.g2.bx.psu.edu/repos/iuc/mrbayes/mrbayes/3.2.7.a+galaxy0')
    history_id = _galaxy_create_history(api_key, 'GyroMorpho_MrBayes')
    ds_id, up_job = _galaxy_upload_file(api_key, history_id, nexus_path, 'nexus')
    if up_job:
        state = _galaxy_wait_for_job(api_key, up_job, max_wait=300)
        if state != 'ok':
            raise RuntimeError(f'Galaxy upload job failed (state: {state})')
    inputs = {
        'input': {'values': [{'id': ds_id, 'src': 'hda'}]},
        'ngen':      str(ngen),
        'nruns':     str(nruns),
        'nchains':   str(nchains),
        'burninfrac': str(burninfrac),
    }
    job_id = _galaxy_run_tool(api_key, history_id, tool_id, inputs)
    return history_id, job_id


def _find_best_tree(results_dir):
    """Return path to infile.txt.raxml.support, falling back to bestTree."""
    preferred = os.path.join(results_dir, 'infile.txt.raxml.support')
    if os.path.exists(preferred):
        return preferred
    fallback = os.path.join(results_dir, 'infile.txt.raxml.bestTree')
    if os.path.exists(fallback):
        return fallback
    return None


def _find_mrbayes_tree(results_dir):
    """Return path to the MrBayes consensus tree (.con.tre)."""
    for fname in os.listdir(results_dir):
        if fname.endswith('.con.tre'):
            return os.path.join(results_dir, fname)
    return None


def _find_newick_in_dir(dest_dir):
    """Scan downloaded Galaxy outputs for the best Newick tree file.

    Priority: 'support' > 'bestTree' > 'consensus' > 'con.tre' > any Newick.
    """
    candidates = []
    for fname in sorted(os.listdir(dest_dir)):
        path = os.path.join(dest_dir, fname)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, 'r', errors='replace') as fh:
                content = fh.read(100000)
            if '(' in content and ';' in content:
                candidates.append((fname.lower(), path))
        except Exception:
            pass
    for priority in ('support', 'besttree', 'consensus', 'con.tre'):
        for fname_l, path in candidates:
            if priority in fname_l:
                return path
    return candidates[0][1] if candidates else None


def _model_to_mrbayes_lset(model_str):
    """Convert a ModelTest-NG model name (e.g. GTR+I+G4) to a MrBayes lset command."""
    m = (model_str or '').upper()
    # Substitution model → nst
    if any(m.startswith(x) for x in ('GTR', 'SYM', 'TVM', 'TIM')):
        nst = 6
    elif any(m.startswith(x) for x in ('HKY', 'K80', 'K2P', 'TN', 'TPM', 'TRN')):
        nst = 2
    else:
        nst = 1  # JC, F81, etc.
    # Rate heterogeneity
    if '+I+G' in m or '+G+I' in m:
        rates = 'invgamma'
    elif '+G' in m:
        rates = 'gamma'
    elif '+I' in m:
        rates = 'propinv'
    else:
        rates = 'equal'
    return f'lset nst={nst} rates={rates}'


def _parse_modeltest_bic(stdout_text, prefix):
    """Extract the BIC-best model name from ModelTest-NG stdout or output files."""
    # Scan stdout for a BIC summary line: "  BIC   GTR+I+G4  ..."
    for line in stdout_text.splitlines():
        m = re.search(r'^\s*BIC\s+(\S+)', line)
        if m:
            return m.group(1)
    # Try the .log file
    for ext in ('.log', '.out'):
        path = prefix + ext
        if not os.path.exists(path):
            continue
        with open(path) as fh:
            content = fh.read()
        # Summary table line
        for line in content.splitlines():
            m = re.search(r'^\s*BIC\s+(\S+)', line)
            if m:
                return m.group(1)
        # "Best model according to BIC" block
        m = re.search(r'Best model according to BIC\s*[-\n]+\s*Model:\s+(\S+)',
                      content, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _modeltest_step(job):
    """Run ModelTest-NG on the trimmed alignment and store the best-fit model.

    Non-fatal: if modeltest-ng is not installed the step is silently skipped.
    The job status is not changed — only status_message and model fields are updated.
    """
    import shutil as _sh
    if not _sh.which('modeltest-ng'):
        return   # not installed — skip
    trimmed = job.trimmed_fasta_path
    if not trimmed or not os.path.exists(trimmed):
        return
    prev_msg = job.status_message or ''
    _set_status(job, job.status, prev_msg + ' | Running ModelTest-NG…')
    try:
        prefix = os.path.join(job.result_dir, 'modeltest')
        result = subprocess.run(
            ['modeltest-ng', '-i', trimmed, '-t', 'mp', '-d', 'nt',
             '-o', prefix, '--force'],
            capture_output=True, text=True, timeout=1800,
        )
        best = _parse_modeltest_bic(result.stdout + result.stderr, prefix)
        if best:
            job.best_fit_model = best
            job.mrbayes_lset   = _model_to_mrbayes_lset(best)
            _set_status(job, job.status,
                        f'Best-fit model (BIC): {best}. Ready for Galaxy.')
        else:
            _set_status(job, job.status,
                        prev_msg + ' | ModelTest-NG ran but no BIC model found.')
    except Exception as exc:
        _set_status(job, job.status,
                    prev_msg + f' | ModelTest-NG error: {exc}')


def _fasta_to_nexus(fasta_path, nexus_path, ngen=1000000, nruns=2, nchains=4,
                    burninfrac=0.25, lset_cmd=None):
    """Convert a FASTA alignment to NEXUS with an embedded MrBayes block."""
    from Bio import SeqIO, AlignIO
    from Bio.Align import MultipleSeqAlignment

    records = list(SeqIO.parse(fasta_path, 'fasta'))
    if not records:
        raise ValueError('FASTA file is empty or unreadable')

    # Sanitize sequence IDs: NEXUS taxon labels cannot contain special chars
    for rec in records:
        safe = re.sub(r'[^A-Za-z0-9_|]', '_', rec.id)
        rec.id = safe
        rec.name = safe
        rec.description = ''

    aln = MultipleSeqAlignment(records)
    seq_len = len(records[0].seq)
    ntax = len(records)

    effective_lset = lset_cmd or 'lset nst=6 rates=invgamma'
    # Ensure semicolon
    if not effective_lset.rstrip().endswith(';'):
        effective_lset += ';'
    mb_block = (
        f'\nbegin mrbayes;\n'
        f'  set autoclose=yes;\n'
        f'  {effective_lset}\n'
        f'  mcmc ngen={ngen} nruns={nruns} nchains={nchains} '
        f'samplefreq=1000 printfreq=10000 burninfrac={burninfrac} '
        f'savebrlens=yes;\n'
        f'  sumt;\n'
        f'end;\n'
    )

    with open(nexus_path, 'w') as fh:
        fh.write('#NEXUS\n\n')
        fh.write(f'begin data;\n')
        fh.write(f'  dimensions ntax={ntax} nchar={seq_len};\n')
        fh.write(f'  format datatype=dna interleave=no gap=- missing=?;\n')
        fh.write(f'  matrix\n')
        for rec in records:
            fh.write(f'    {rec.id:<40} {str(rec.seq)}\n')
        fh.write(f'  ;\nend;\n')
        fh.write(mb_block)


def _submit_mrbayes_cipres(nexus_path, api_key, ngen=1000000, nruns=2, nchains=4,
                            burninfrac=0.25, **_ignored):
    """Shim — delegates to Galaxy MrBayes. Signature kept for call-site compat."""
    return _submit_to_galaxy_mrbayes(nexus_path, api_key, ngen, nruns, nchains, burninfrac)


def _parse_mrbayes_consensus(con_tre_path):
    """Extract a plain Newick string from a MrBayes NEXUS .con.tre file."""
    try:
        from Bio import Phylo
        from io import StringIO
        bio_tree = Phylo.read(con_tre_path, 'nexus')
        buf = StringIO()
        Phylo.write(bio_tree, buf, 'newick')
        return buf.getvalue().strip()
    except Exception:
        pass

    # Manual fallback: grab the first TREE line
    try:
        with open(con_tre_path) as fh:
            content = fh.read()
        m = re.search(r'TREE\s+\S+\s*=\s*(\[.*?\])?\s*([^;]+;)', content,
                      re.IGNORECASE | re.DOTALL)
        if m:
            newick = m.group(2).strip()
            newick = re.sub(r'\[.*?\]', '', newick)
            return newick.strip()
    except Exception:
        pass
    return None


def _root_tree(tree_file, outgroup_genera, output_file):
    pattern = '|'.join(re.escape(g) for g in outgroup_genera)
    tf = tree_file.replace('\\', '/').replace("'", "\\'")
    of = output_file.replace('\\', '/').replace("'", "\\'")
    r_code = f"""
suppressMessages(suppressWarnings(library(ape)))
tree <- read.tree('{tf}')
if (inherits(tree, 'multiPhylo')) tree <- tree[[length(tree)]]
outgroup <- grep('{pattern}', tree$tip.label, value=TRUE, ignore.case=TRUE)
cat(paste('Outgroup tips found:', length(outgroup)), '\\n')
if (length(outgroup) > 0) {{
  tryCatch({{
    rooted <- root(tree, outgroup=outgroup, resolve.root=TRUE)
    write.tree(rooted, file='{of}')
    cat('SUCCESS\\n')
  }}, error=function(e) {{
    rooted <- root(tree, outgroup=outgroup[1], resolve.root=TRUE)
    write.tree(rooted, file='{of}')
    cat('FALLBACK\\n')
  }})
}} else {{
  write.tree(tree, file='{of}')
  cat('NO_OUTGROUP\\n')
}}
"""
    result = subprocess.run(
        ['Rscript', '--vanilla', '-e', r_code],
        capture_output=True, text=True, timeout=120,
    )
    msg = (result.stdout + result.stderr).strip()
    return result.returncode == 0 and os.path.exists(output_file), msg


def _filter_fasta(input_path, accessions_to_remove, output_path):
    removed, skip, buf = [], False, []
    with open(input_path) as fin:
        for line in fin:
            if line.startswith('>'):
                skip = any(acc in line for acc in accessions_to_remove)
                if skip:
                    removed.append(line.strip())
            if not skip:
                buf.append(line)
    with open(output_path, 'w') as fout:
        fout.writelines(buf)
    return removed


# ── Routes ────────────────────────────────────────────────────────────────────

@phylo_bp.route('/project/<int:project_id>/phylogeny')
@login_required
def phylogeny_view(project_id):
    project = Project.query.get_or_404(project_id)
    jobs = (PhylogenyJob.query
            .filter_by(project_id=project_id)
            .order_by(PhylogenyJob.submitted_at.desc())
            .all())
    defaults = {
        'target_taxon':     'Gyrodactylidae',
        'gene_query':       DEFAULT_GENE_QUERY_18S,
        'min_length':       400,
        'outgroup_defs':    DEFAULT_OUTGROUP_DEFS,
        'outgroup_genera':  '\n'.join(DEFAULT_OUTGROUP_GENERA),
        'galaxy_api_key':   current_app.config.get('GALAXY_API_KEY', ''),
    }
    return render_template('phylogeny/phylogeny.html',
                           project=project, jobs=jobs, defaults=defaults)


@phylo_bp.route('/project/<int:project_id>/phylogeny/create', methods=['POST'])
@login_required
def create_job(project_id):
    try:
        return _create_job_inner(project_id)
    except Exception as exc:
        import traceback
        current_app.logger.error('create_job error: %s', traceback.format_exc())
        db.session.rollback()
        return jsonify({'error': str(exc)}), 500


def _create_job_inner(project_id):
    project = Project.query.get_or_404(project_id)
    mode = request.form.get('mode', 'ncbi')   # 'ncbi' or 'upload'

    marker        = request.form.get('marker', '18S').strip()
    n_bootstraps  = max(100, min(5000, int(request.form.get('n_bootstraps', 1000) or 1000)))
    outgroup_gen  = [g.strip() for g in request.form.get('outgroup_genera', '').splitlines() if g.strip()] \
                    or DEFAULT_OUTGROUP_GENERA[:]
    galaxy_api_key = (request.form.get('galaxy_api_key', '').strip()
                      or current_app.config.get('GALAXY_API_KEY', ''))

    stamp    = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    # Save under <project_root>/phylogeny/Results/job_<timestamp>/
    proj_root = os.path.dirname(current_app.root_path)
    job_dir   = os.path.join(proj_root, 'phylogeny', 'Results', f'job_{stamp}')
    os.makedirs(job_dir, exist_ok=True)

    if mode == 'upload':
        # User supplies a pre-trimmed FASTA — skip to 'trimmed'
        fasta_file = request.files.get('fasta_file')
        if not fasta_file or not fasta_file.filename:
            return jsonify({'error': 'A FASTA file is required in upload mode.'}), 400
        bad_acc    = [s.strip() for s in request.form.get('sequences_to_remove', '').splitlines() if s.strip()]
        orig_name  = secure_filename(fasta_file.filename)
        fasta_path = os.path.join(job_dir, orig_name)
        fasta_file.save(fasta_path)

        if bad_acc:
            filtered = os.path.join(job_dir, 'input_filtered.fa')
            _filter_fasta(fasta_path, bad_acc, filtered)
            fasta_path = filtered

        n_seqs = _count_fasta(fasta_path)
        job = PhylogenyJob(
            project_id=project_id,
            submitted_by=current_user.id,
            marker=marker,
            n_bootstraps=n_bootstraps,
            n_sequences=n_seqs,
            outgroup_genera=outgroup_gen,
            result_dir=job_dir,
            trimmed_fasta_path=fasta_path,
            fasta_filename=os.path.basename(fasta_path),
            galaxy_api_key=galaxy_api_key,
            status='trimmed',
            status_message=f'Uploaded {n_seqs} sequences. Ready for Galaxy.',
        )
        db.session.add(job)
        db.session.commit()
        return jsonify({'job_id': job.id, 'status': 'trimmed',
                        'message': job.status_message})

    else:
        # Full NCBI pipeline
        import json as _json
        ncbi_email  = request.form.get('ncbi_email', '').strip()
        target_taxon = request.form.get('target_taxon', 'Gyrodactylidae').strip()
        min_length        = max(100, int(request.form.get('min_length', 400) or 400))
        max_length_factor = max(1.0, float(request.form.get('max_length_factor', 2.0) or 2.0))
        bad_acc     = [s.strip() for s in request.form.get('bad_accessions', '').splitlines() if s.strip()]

        # Parse outgroup definitions: each line "Family | mode | n"
        og_defs = []
        for line in request.form.get('outgroup_defs', '').splitlines():
            parts = [p.strip() for p in line.split('|')]
            if parts and parts[0]:
                og_defs.append({
                    'family': parts[0],
                    'mode':   parts[1] if len(parts) > 1 else 'each_genus',
                    'n':      int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 2,
                })
        if not og_defs:
            og_defs = DEFAULT_OUTGROUP_DEFS[:]

        if not ncbi_email:
            return jsonify({'error': 'NCBI email is required.'}), 400

        # Concatenated mode: store both gene queries as JSON
        if marker == 'concatenated':
            q18s = request.form.get('gene_query_18s', DEFAULT_GENE_QUERY_18S).strip()
            qITS = request.form.get('gene_query_its', DEFAULT_GENE_QUERY_ITS).strip()
            gene_query = _json.dumps({'18S': q18s, 'ITS': qITS})
        else:
            gene_query = request.form.get('gene_query', DEFAULT_GENE_QUERY_18S).strip()

        job = PhylogenyJob(
            project_id=project_id,
            submitted_by=current_user.id,
            marker=marker,
            n_bootstraps=n_bootstraps,
            outgroup_genera=outgroup_gen,
            result_dir=job_dir,
            ncbi_email=ncbi_email,
            target_taxon=target_taxon,
            gene_query=gene_query,
            min_length=min_length,
            max_length_factor=max_length_factor,
            bad_accessions=bad_acc,
            outgroup_definitions=og_defs,
            galaxy_api_key=galaxy_api_key,
            status='created',
            status_message='Starting NCBI retrieval…',
        )
        db.session.add(job)
        db.session.commit()

        # Launch background thread
        app = current_app._get_current_object()
        if marker == 'concatenated':
            t = threading.Thread(target=_concatenated_pipeline_thread, args=(app, job.id), daemon=True)
        else:
            t = threading.Thread(target=_pipeline_thread, args=(app, job.id), daemon=True)
        t.start()

        return jsonify({'job_id': job.id, 'status': 'fetching',
                        'message': 'Pipeline started. Polling for updates…'})


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/status')
@login_required
def job_status(project_id, job_id):
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()

    # Poll Galaxy for active jobs
    if job.status in ('submitted', 'running') and job.job_handle:
        try:
            api_key = (job.galaxy_api_key
                       or current_app.config.get('GALAXY_API_KEY', ''))
            stage, msg = _galaxy_check_status(api_key, job.job_handle)
            job.last_checked   = datetime.now(timezone.utc)
            job.status_message = msg or stage
            if stage == 'COMPLETED':
                job.status       = 'completed'
                job.completed_at = datetime.now(timezone.utc)
            elif stage in ('FAILED', 'SUSPENDED'):
                job.status = 'failed'
            else:
                job.status = 'running'
            db.session.commit()
        except Exception:
            pass   # return cached status

    return jsonify({
        'status':  job.status,
        'message': job.status_message or '',
        'n_sequences_raw':    job.n_sequences_raw,
        'n_sequences_deduped': job.n_sequences_deduped,
        'n_sequences_final':  job.n_sequences_final,
        'n_sequences':        job.n_sequences,
    })


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/submit_cipres',
                methods=['POST'])
@login_required
def submit_cipres(project_id, job_id):
    """Submit a trimmed alignment to Galaxy (usegalaxy.eu) for ML or Bayesian inference."""
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()

    if job.status not in ('trimmed', 'fetched', 'nj_ready'):
        return jsonify({'error': f'Job not ready (stage: {job.status})'}), 400

    submit_path = job.trimmed_fasta_path or job.raw_fasta_path
    if not submit_path or not os.path.exists(submit_path):
        return jsonify({'error': 'FASTA file not found on disk.'}), 400

    api_key = (job.galaxy_api_key or current_app.config.get('GALAXY_API_KEY', ''))
    if not api_key:
        return jsonify({'error': 'Galaxy API key is required. Set it in the job form or GALAXY_API_KEY env var.'}), 400

    body   = request.get_json(silent=True) or {}
    method = body.get('method', 'raxml').lower()

    try:
        if method == 'mrbayes':
            ngen       = int(body.get('ngen', 1000000))
            nruns      = int(body.get('nruns', 2))
            nchains    = int(body.get('nchains', 4))
            burninfrac = float(body.get('burninfrac', 0.25))
            lset_cmd   = (body.get('lset_cmd') or '').strip() or \
                         job.mrbayes_lset or 'lset nst=6 rates=invgamma'
            nexus_path = submit_path.rsplit('.', 1)[0] + '.nex'
            _fasta_to_nexus(submit_path, nexus_path, ngen, nruns, nchains,
                            burninfrac, lset_cmd=lset_cmd)
            history_id, galaxy_job_id = _submit_to_galaxy_mrbayes(
                nexus_path, api_key, ngen, nruns, nchains, burninfrac
            )
            job.phylo_method   = 'mrbayes'
            job.mrbayes_lset   = lset_cmd
            job.status_message = (f'MrBayes submitted to Galaxy (job: {galaxy_job_id}); '
                                  f'model: {job.best_fit_model or lset_cmd}')
        else:
            history_id, galaxy_job_id = _submit_to_galaxy_raxml(
                submit_path, api_key, job.n_bootstraps or 1000
            )
            job.phylo_method   = 'raxml'
            job.status_message = f'RAxML-NG submitted to Galaxy (job: {galaxy_job_id})'

        job.job_url    = history_id    # Galaxy history ID
        job.job_handle = galaxy_job_id  # Galaxy job ID (for polling)
        job.status     = 'submitted'
        db.session.commit()
        return jsonify({'status': 'submitted', 'message': job.status_message})
    except Exception as e:
        import traceback
        current_app.logger.error('Galaxy submit error: %s', traceback.format_exc())
        return jsonify({'error': str(e)[:600]}), 500


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/download',
                methods=['POST'])
@login_required
def download_and_root(project_id, job_id):
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    if job.status != 'completed' and not job.job_handle:
        return jsonify({'error': 'Job not completed yet. Check status first.'}), 400
    try:
        results_dir = job.result_dir
        api_key     = (job.galaxy_api_key or current_app.config.get('GALAXY_API_KEY', ''))
        downloaded  = _galaxy_download_results(api_key, job.job_handle, results_dir)

        method = job.phylo_method or 'raxml'

        if method == 'mrbayes':
            # Try named .con.tre first, then generic Newick scan
            tree_file = _find_mrbayes_tree(results_dir) or _find_newick_in_dir(results_dir)
            if not tree_file:
                return jsonify({'error': 'No MrBayes consensus tree in Galaxy results. Files: ' +
                                ', '.join(downloaded)}), 500
            newick = _parse_mrbayes_consensus(tree_file)
            if not newick:
                # Galaxy may deliver plain Newick directly
                with open(tree_file) as fh:
                    newick = fh.read().strip()
            rooted_file = os.path.join(results_dir, 'rooted_tree.tre')
            with open(os.path.join(results_dir, '_tmp_mb.nwk'), 'w') as fh:
                fh.write(newick)
            success, msg = _root_tree(
                os.path.join(results_dir, '_tmp_mb.nwk'),
                job.outgroup_genera or DEFAULT_OUTGROUP_GENERA, rooted_file
            )
            if success:
                with open(rooted_file) as fh:
                    newick = fh.read().strip()
        else:
            tree_file = _find_best_tree(results_dir) or _find_newick_in_dir(results_dir)
            if not tree_file:
                return jsonify({'error': 'No tree file in Galaxy results. Files: ' +
                                ', '.join(downloaded)}), 500
            rooted_file = os.path.join(results_dir, 'rooted_tree.tre')
            success, msg = _root_tree(
                tree_file, job.outgroup_genera or DEFAULT_OUTGROUP_GENERA, rooted_file
            )
            use_file = rooted_file if success else tree_file
            with open(use_file) as fh:
                newick = fh.read().strip()

        job.tree_newick    = newick
        job.status         = 'tree_ready'
        job.status_message = msg if success else f'Rooting failed — unrooted stored. ({msg})'
        db.session.commit()
        return jsonify({'status': 'tree_ready', 'rooted': success,
                        'message': job.status_message,
                        'files_downloaded': len(downloaded),
                        'newick': newick})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _sync_specimens_from_newick(newick, project_id):
    """Parse tip labels from a newick string and create any missing Specimen rows.

    Tip labels are expected to follow the pipeline format  accession|Genus_species
    (underscores → spaces for the species name).  Plain labels are also handled.
    Returns (added, already_present) counts.
    """
    from app.models import Specimen as _Specimen

    # Extract tip labels via BioPython
    try:
        from Bio import Phylo as _Phylo
        from io import StringIO as _StringIO
        bio_tree = _Phylo.read(_StringIO(newick), 'newick')
        tip_names = [t.name for t in bio_tree.get_terminals() if t.name]
    except Exception:
        # Fallback: regex for quoted and unquoted labels before ':'
        tip_names = re.findall(r"['\"]?([A-Za-z0-9_.|]+)['\"]?(?::\d)", newick)

    # Normalize to species names
    species_set = []
    seen = set()
    for label in tip_names:
        # "accession|Genus_species"  or  "Genus_species"
        part = label.split('|')[-1]
        species = part.replace('_', ' ').strip()
        if species and species not in seen:
            seen.add(species)
            species_set.append(species)

    # Fetch existing
    existing = {s.species_name for s in
                _Specimen.query.filter_by(project_id=project_id).all()}

    added = 0
    for species in species_set:
        if species not in existing:
            sp = _Specimen(
                project_id=project_id,
                species_name=species,
                created_by=current_user.id,
            )
            db.session.add(sp)
            added += 1

    if added:
        db.session.flush()   # get IDs, commit handled by caller

    return added, len(existing)


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/import',
                methods=['POST'])
@login_required
def import_tree(project_id, job_id):
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    if not job.tree_newick:
        return jsonify({'error': 'No ML tree available. Download results first.'}), 400
    project = Project.query.get_or_404(project_id)
    project.tree_newick = job.tree_newick
    added, existing = _sync_specimens_from_newick(job.tree_newick, project_id)
    db.session.commit()
    msg = 'ML tree imported into project.'
    if added:
        msg += f' {added} new specimen(s) added to Specimens.'
    return jsonify({'status': 'ok', 'message': msg, 'specimens_added': added})


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/import_nj',
                methods=['POST'])
@login_required
def import_nj_tree(project_id, job_id):
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    if not job.nj_newick:
        return jsonify({'error': 'No NJ tree available for this job.'}), 400
    project = Project.query.get_or_404(project_id)
    project.tree_newick = job.nj_newick
    added, existing = _sync_specimens_from_newick(job.nj_newick, project_id)
    db.session.commit()
    msg = 'NJ tree imported into project.'
    if added:
        msg += f' {added} new specimen(s) added to Specimens.'
    return jsonify({'status': 'ok', 'message': msg, 'specimens_added': added})


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/reroot_nj',
                methods=['POST'])
@login_required
def reroot_nj_tree(project_id, job_id):
    """Re-root the NJ tree at the MRCA of the given outgroup(s) and save it back.

    Accepts JSON: {outgroups: ["Name1", "Name2", ...]}
    Returns: {newick: "rooted newick string", outgroups: [...], warning?: "..."}
    """
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    if not job.nj_newick:
        return jsonify({'error': 'No NJ tree available for this job.'}), 400

    data = request.get_json() or {}
    outgroup_names = [s.strip() for s in data.get('outgroups', []) if s.strip()]
    if not outgroup_names:
        return jsonify({'error': 'At least one outgroup name is required.'}), 400

    try:
        from Bio import Phylo
        from io import StringIO

        bio_tree = Phylo.read(StringIO(job.nj_newick), 'newick')
        terminals = bio_tree.get_terminals()

        matched = []
        not_found = []
        for name in outgroup_names:
            # Match by substring (case-insensitive) against tip label or species portion
            t = next(
                (t for t in terminals if t.name and (
                    name.lower() in t.name.lower().replace('_', ' ') or
                    t.name.lower().replace('_', ' ') in name.lower()
                )),
                None
            )
            if t:
                matched.append(t)
            else:
                not_found.append(name)

        if not matched:
            return jsonify({'error': f'No outgroup names found in tree: {not_found}'}), 404

        outgroup_clade = matched[0] if len(matched) == 1 else bio_tree.common_ancestor(matched)
        bio_tree.root_with_outgroup(outgroup_clade)

        buf = StringIO()
        Phylo.write(bio_tree, buf, 'newick')
        new_newick = buf.getvalue().strip()

        job.nj_newick = new_newick
        db.session.commit()

        result = {'status': 'ok', 'newick': new_newick, 'outgroups': outgroup_names}
        if not_found:
            result['warning'] = f'Not found in tree (skipped): {", ".join(not_found)}'
        return jsonify(result)

    except Exception as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 500


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/newick')
@login_required
def get_newick(project_id, job_id):
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    return jsonify({
        'newick': job.tree_newick,
        'marker': job.marker,
        'taxon':  job.target_taxon or job.fasta_filename or '',
    })


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/model')
@login_required
def get_model_info(project_id, job_id):
    """Return model selection info for the Galaxy submission modal."""
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    return jsonify({
        'best_fit_model': job.best_fit_model,
        'mrbayes_lset':   job.mrbayes_lset,
        'has_model':      bool(job.best_fit_model),
    })


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/run_modeltest',
                methods=['POST'])
@login_required
def run_modeltest_route(project_id, job_id):
    """Trigger ModelTest-NG on demand for a job that already has a trimmed alignment."""
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    import shutil as _sh
    if not _sh.which('modeltest-ng'):
        return jsonify({'error': 'modeltest-ng is not installed on this server.'}), 400
    if not job.trimmed_fasta_path or not os.path.exists(job.trimmed_fasta_path):
        return jsonify({'error': 'Trimmed alignment not found. Run the alignment pipeline first.'}), 400
    app_obj = current_app._get_current_object()
    t = threading.Thread(
        target=_modeltest_thread, args=(app_obj, job_id), daemon=True
    )
    t.start()
    return jsonify({'status': 'running', 'message': 'ModelTest-NG started in background.'})


def _modeltest_thread(app, job_id):
    with app.app_context():
        job = db.session.get(PhylogenyJob, job_id)
        if not job:
            return
        _modeltest_step(job)


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/upload_tree', methods=['POST'])
@login_required
def upload_tree_as_job(project_id):
    """Upload a Newick/NEXUS tree file to the project.

    Sets project.tree_newick and creates a placeholder job record so the
    tree appears in the jobs list with a download option.
    """
    project = Project.query.get_or_404(project_id)
    tree_file = request.files.get('tree_file')
    import_to_project = request.form.get('import_to_project', '1') != '0'
    if not tree_file or not tree_file.filename:
        return jsonify({'error': 'No tree file provided.'}), 400

    try:
        content = tree_file.read().decode('utf-8', errors='replace')
        newick  = _extract_newick(content)
        if not newick:
            return jsonify({'error': 'No Newick tree found in the uploaded file.'}), 400

        stamp   = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        proj_root = os.path.dirname(current_app.root_path)
        job_dir   = os.path.join(proj_root, 'phylogeny', 'Results', f'uploaded_{stamp}')
        os.makedirs(job_dir, exist_ok=True)

        # Save the file
        safe_name = secure_filename(tree_file.filename or 'uploaded_tree.nwk')
        tree_path = os.path.join(job_dir, safe_name)
        with open(tree_path, 'w') as fh:
            fh.write(newick)

        job = PhylogenyJob(
            project_id   = project_id,
            submitted_by = current_user.id,
            marker       = request.form.get('marker', '—'),
            result_dir   = job_dir,
            fasta_filename = safe_name,
            tree_newick  = newick,
            status       = 'tree_ready',
            status_message = f'Uploaded from file: {safe_name}',
        )
        db.session.add(job)

        added = 0
        if import_to_project:
            project.tree_newick = newick
            added, _ = _sync_specimens_from_newick(newick, project_id)

        db.session.commit()
        msg = f'Tree uploaded successfully.'
        if import_to_project:
            msg += f' Imported into project ({added} new specimen(s)).'
        return jsonify({'status': 'ok', 'job_id': job.id,
                        'message': msg, 'specimens_added': added})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/file/<filetype>')
@login_required
def download_file(project_id, job_id, filetype):
    """Download a pipeline file: raw, aligned, trimmed, nj_tree, ml_tree."""
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()

    file_map = {
        'raw':     (job.raw_fasta_path,     'raw_sequences.fasta',     'text/plain'),
        'aligned': (job.aligned_fasta_path, 'aligned.fasta',           'text/plain'),
        'trimmed': (job.trimmed_fasta_path, 'trimmed_alignment.fasta', 'text/plain'),
    }

    if filetype in file_map:
        path, download_name, mimetype = file_map[filetype]
        if not path or not os.path.exists(path):
            return jsonify({'error': f'{filetype} file not found.'}), 404
        return send_file(path, as_attachment=True,
                         download_name=download_name, mimetype=mimetype)

    if filetype == 'nj_tree':
        if not job.nj_newick:
            return jsonify({'error': 'NJ tree not available.'}), 404
        from io import BytesIO
        buf = BytesIO(job.nj_newick.encode())
        return send_file(buf, as_attachment=True,
                         download_name='nj_tree.nwk', mimetype='text/plain')

    if filetype == 'ml_tree':
        if not job.tree_newick:
            return jsonify({'error': 'ML/Bayesian tree not available.'}), 404
        from io import BytesIO
        suffix = 'bayes' if (job.phylo_method or 'raxml') == 'mrbayes' else 'raxml'
        buf = BytesIO(job.tree_newick.encode())
        return send_file(buf, as_attachment=True,
                         download_name=f'{suffix}_tree.nwk', mimetype='text/plain')

    return jsonify({'error': f'Unknown file type: {filetype}'}), 400


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/nj_preview')
@login_required
def nj_preview(project_id, job_id):
    """Return NJ newick + sequence list for the NJ review modal."""
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    if not job.nj_newick:
        return jsonify({'error': 'NJ tree not available.'}), 404
    sequences = []
    if job.trimmed_fasta_path and os.path.exists(job.trimmed_fasta_path):
        from Bio import SeqIO
        with open(job.trimmed_fasta_path) as fh:
            for rec in SeqIO.parse(fh, 'fasta'):
                sp = rec.id.split('|')[1].replace('_', ' ') if '|' in rec.id else rec.id
                sequences.append({'id': rec.id, 'species': sp, 'length': len(rec.seq)})
    return jsonify({
        'newick':     job.nj_newick,
        'sequences':  sequences,
        'n_sequences': len(sequences),
        'status_message': job.status_message or '',
    })


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/fetch_alternatives')
@login_required
def fetch_alternatives(project_id, job_id):
    """Fetch alternative NCBI sequences for a given species name."""
    job     = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    species = request.args.get('species', '').strip()   # e.g. "Gyrodactylus salaris"
    if not species:
        return jsonify({'error': 'species parameter required'}), 400
    email   = job.ncbi_email or 'user@example.com'
    gene_q  = job.gene_query or DEFAULT_GENE_QUERY_18S
    min_len = job.min_length or 400
    query   = f'"{species}"[Organism] AND ({gene_q})'
    try:
        ids, count = _ncbi_search(query, email, retmax=50)
        records    = _ncbi_fetch_batch(ids, email)
        candidates = sorted(
            [{'accession': rec.id,
              'length':    len(rec.seq),
              'description': rec.description[:120]}
             for rec in records.values() if len(rec.seq) >= min_len],
            key=lambda x: x['length'], reverse=True
        )
        return jsonify({'species': species, 'candidates': candidates,
                        'total_found': count})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/replace_and_realign',
                methods=['POST'])
@login_required
def replace_and_realign(project_id, job_id):
    """Apply sequence replacements/removals and re-run align → trim → NJ."""
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    if job.status not in ('nj_ready', 'trimmed', 'completed', 'tree_ready'):
        return jsonify({'error': f'Job not in reviewable state (current: {job.status})'}), 400
    data         = request.get_json() or {}
    replacements = data.get('replacements', [])   # [{old_id, new_accession, species}, ...]
    removals     = data.get('removals', [])        # [old_id, ...]
    if not replacements and not removals:
        return jsonify({'error': 'No changes specified.'}), 400
    app = current_app._get_current_object()
    t = threading.Thread(target=_replace_realign_thread,
                         args=(app, job_id, replacements, removals), daemon=True)
    t.start()
    return jsonify({'status': 'aligning', 'message': 'Applying changes and re-aligning…'})


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/approve_for_cipres',
                methods=['POST'])
@login_required
def approve_for_cipres(project_id, job_id):
    """Mark NJ-reviewed job as trimmed and ready for CIPRES."""
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    if job.status not in ('nj_ready', 'trimmed', 'completed', 'tree_ready'):
        return jsonify({'error': f'Job not in reviewable state (current: {job.status})'}), 400
    job.status         = 'trimmed'
    job.status_message = 'Approved after NJ review. Ready for Galaxy submission.'
    db.session.commit()
    return jsonify({'status': 'trimmed', 'message': job.status_message})


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/search_species')
@login_required
def search_species(project_id, job_id):
    """Search NCBI for a species name (any sequence, no gene filter by default)."""
    job      = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    q        = request.args.get('q', '').strip()
    gene_filter = request.args.get('gene_filter', '0') == '1'
    if not q:
        return jsonify({'error': 'q parameter required'}), 400
    email   = job.ncbi_email or 'user@example.com'
    min_len = job.min_length or 400
    if gene_filter and job.gene_query:
        query = f'"{q}"[Organism] AND ({job.gene_query})'
    else:
        query = f'"{q}"[Organism]'
    try:
        ids, count = _ncbi_search(query, email, retmax=50)
        records    = _ncbi_fetch_batch(ids, email)
        candidates = sorted(
            [{'accession': rec.id,
              'length':    len(rec.seq),
              'description': rec.description[:150]}
             for rec in records.values() if len(rec.seq) >= min_len],
            key=lambda x: x['length'], reverse=True
        )
        return jsonify({'candidates': candidates, 'total_found': count, 'query': query})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/add_sequences',
                methods=['POST'])
@login_required
def add_sequences(project_id, job_id):
    """Add arbitrary sequences (by accession) to the job's raw FASTA.

    Accepted in states: fetched, nj_ready, trimmed.
    If nj_ready, automatically re-runs align→trim→NJ after adding.
    """
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    if job.status not in ('fetched', 'nj_ready', 'trimmed'):
        return jsonify({'error': f'Cannot add sequences in state: {job.status}'}), 400
    data       = request.get_json() or {}
    items      = data.get('accessions', [])   # [{accession, species}, ...]
    if not items:
        return jsonify({'error': 'No accessions provided'}), 400

    email = job.ncbi_email or 'user@example.com'
    try:
        from Bio import SeqIO
        from Bio.SeqRecord import SeqRecord

        acc_list = [it['accession'] for it in items]
        fetched  = _ncbi_fetch_batch(acc_list, email)

        current = []
        if job.raw_fasta_path and os.path.exists(job.raw_fasta_path):
            with open(job.raw_fasta_path) as fh:
                current = list(SeqIO.parse(fh, 'fasta'))
        current_ids = {r.id for r in current}

        added = []
        for it in items:
            acc     = it['accession']
            species = it.get('species', '').strip()
            if acc not in fetched:
                continue
            rec = fetched[acc]
            if not species:
                species = _parse_species_name(rec.description)
            sp_norm = species.replace(' ', '_')
            new_id  = f"{rec.id}|{sp_norm}"
            if new_id not in current_ids:
                current.append(SeqRecord(rec.seq, id=new_id, name='', description=''))
                current_ids.add(new_id)
                added.append({'accession': acc, 'species': sp_norm.replace('_', ' '),
                              'length': len(rec.seq)})

        if not added:
            return jsonify({'error': 'No new sequences added (may already be present)'}), 400

        _write_fasta(current, job.raw_fasta_path)
        job.n_sequences_final = len(current)
        job.n_sequences       = len(current)

        if job.status == 'nj_ready':
            # Re-run align→trim→NJ automatically
            app = current_app._get_current_object()
            t = threading.Thread(target=_align_trim_thread, args=(app, job_id), daemon=True)
            t.start()
            db.session.commit()
            return jsonify({'status': 'aligning',
                            'message': f'Added {len(added)} sequence(s). Re-aligning…',
                            'added': added})

        db.session.commit()
        return jsonify({'status': job.status, 'added': added, 'n_sequences': len(current)})

    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/fetch_preview')
@login_required
def fetch_preview(project_id, job_id):
    """Return the list of sequences in the raw FASTA for user review."""
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    if not job.raw_fasta_path or not os.path.exists(job.raw_fasta_path):
        return jsonify({'error': 'Raw FASTA not found on disk.'}), 404
    from Bio import SeqIO
    sequences = []
    with open(job.raw_fasta_path) as fh:
        for rec in SeqIO.parse(fh, 'fasta'):
            species = rec.id.split('|')[1].replace('_', ' ') if '|' in rec.id else rec.id
            sequences.append({
                'id':      rec.id,
                'species': species,
                'length':  len(rec.seq),
            })
    return jsonify({
        'sequences':    sequences,
        'n_raw':        job.n_sequences_raw,
        'n_deduped':    job.n_sequences_deduped,
        'n_final':      job.n_sequences_final,
        'n_sequences':  len(sequences),
    })


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/approve_and_align',
                methods=['POST'])
@login_required
def approve_and_align(project_id, job_id):
    """User approved the fetched sequences — start alignment + trimming."""
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    # Already aligned/trimmed (e.g. server restarted mid-run with old code)
    if job.status in ('aligned', 'trimmed'):
        return jsonify({'status': job.status, 'message': job.status_message or 'Already processed.'})
    if job.status != 'fetched':
        return jsonify({'error': f'Job is not in fetched state (current: {job.status})'}), 400
    app = current_app._get_current_object()
    t = threading.Thread(target=_align_trim_thread, args=(app, job_id), daemon=True)
    t.start()
    return jsonify({'status': 'aligning', 'message': 'Alignment started…'})


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/delete',
                methods=['POST'])
@login_required
def delete_job(project_id, job_id):
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    result_dir = job.result_dir
    db.session.delete(job)
    db.session.commit()
    if result_dir and os.path.isdir(result_dir):
        shutil.rmtree(result_dir, ignore_errors=True)
    return jsonify({'status': 'ok'})


# ── Legacy route: upload existing trimmed FASTA directly (old form) ───────────

@phylo_bp.route('/project/<int:project_id>/tree/upload', methods=['POST'])
@login_required
def upload_tree(project_id):
    """Upload a Newick/NEXUS tree file directly into the project."""
    project = Project.query.get_or_404(project_id)
    tree_file = request.files.get('tree_file')
    if not tree_file:
        return jsonify({'has_tree': False, 'message': 'No file provided.'}), 400
    try:
        content = tree_file.read().decode('utf-8', errors='replace')
        newick  = _extract_newick(content)
        if not newick:
            return jsonify({'has_tree': False, 'message': 'No Newick tree found in file.'})
        project.tree_newick = newick
        added, _ = _sync_specimens_from_newick(newick, project_id)
        db.session.commit()
        return jsonify({'has_tree': True, 'specimens_added': added})
    except Exception as e:
        return jsonify({'has_tree': False, 'message': str(e)}), 500


def _extract_newick(content):
    """Extract Newick string from plain Newick or NEXUS content."""
    content = content.strip()
    if content.upper().startswith('#NEXUS'):
        # Find TREE block
        m = re.search(r'TREE[^=]+=\s*(\[.*?\])?\s*([^;]+;)', content,
                      re.IGNORECASE | re.DOTALL)
        if m:
            newick = m.group(2).strip()
            newick = re.sub(r'\[.*?\]', '', newick)  # strip bracket annotations
            return newick.strip()
        return None
    # Plain Newick
    for line in content.splitlines():
        line = line.strip()
        if line.startswith('(') or (line and not line.startswith('#')):
            return line
    return None
