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
from app.models import Project, PhylogenyJob, Specimen

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
    for attempt in range(5):
        try:
            h = Entrez.esearch(db='nuccore', term=term, retmax=retmax)
            result = Entrez.read(h)
            h.close()
            time.sleep(0.4)   # respect NCBI rate limit (3 req/s without API key)
            return result['IdList'], int(result['Count'])
        except Exception as exc:
            is_429 = '429' in str(exc) or 'Too Many Requests' in str(exc)
            if attempt < 4 and (is_429 or attempt < 2):
                time.sleep(5 * (attempt + 1) if is_429 else 3)
            else:
                raise


def _ncbi_fetch_batch(ids, email, batch_size=200):
    """Download FASTA records in batches. Returns dict {rec.id: SeqRecord}."""
    from Bio import Entrez, SeqIO
    Entrez.email = email
    records = {}
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i + batch_size]
        for attempt in range(5):
            try:
                h = Entrez.efetch(db='nuccore', id=','.join(batch),
                                  rettype='fasta', retmode='text')
                for rec in SeqIO.parse(h, 'fasta'):
                    records[rec.id] = rec
                h.close()
                break
            except Exception as exc:
                is_429 = '429' in str(exc) or 'Too Many Requests' in str(exc)
                if attempt < 4:
                    time.sleep(5 * (attempt + 1) if is_429 else 3)
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


def _norm_species(name):
    """Normalize a species name for matching: lowercase, spaces/underscores unified,
    collapse to 'genus species' (first two tokens), strip punctuation."""
    if not name:
        return ''
    s = str(name).replace('_', ' ').lower().strip()
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    toks = s.split()
    return ' '.join(toks[:2])


def _restrict_ingroup(ingroup, restrict_species):
    """Keep only ingroup records whose species matches the allowed set.

    restrict_species is a list of species names (from the project Specimens page).
    Matching is normalized (case/underscore/whitespace insensitive, genus+epithet).
    Returns (kept_records, matched_norms, missing_species).
    """
    allowed = {_norm_species(s) for s in (restrict_species or []) if _norm_species(s)}
    if not allowed:
        return ingroup, set(), []
    kept, matched = [], set()
    for rec in ingroup:
        sp = rec.id.split('|')[1] if '|' in rec.id else rec.id
        n = _norm_species(sp)
        if n in allowed:
            kept.append(rec)
            matched.add(n)
    missing = sorted(a for a in allowed if a not in matched)
    return kept, matched, missing


def _fetch_missing_specimens(missing_norm, restrict_species, email, gene_q, min_length, bad_accessions=None):
    """Per-species targeted NCBI retry for specimens the bulk taxon-level search
    missed. Returns (recovered_records, still_missing_norm).

    missing_norm: normalized ('genus species') names from _restrict_ingroup.
    restrict_species: original species-name strings from the Specimens page,
    used to recover display casing/underscores and as the search term.
    """
    from Bio.SeqRecord import SeqRecord

    orig_by_norm = {}
    for s in restrict_species:
        n = _norm_species(s)
        if n and n not in orig_by_norm:
            orig_by_norm[n] = s

    recovered = []
    still_missing = []
    for n in missing_norm:
        orig = orig_by_norm.get(n, n)
        species_query = orig.replace('_', ' ').strip()
        try:
            query = f'"{species_query}"[Organism] AND ({gene_q})'
            ids, _ = _ncbi_search(query, email, retmax=50)
            if not ids:
                still_missing.append(n)
                continue
            recs = _ncbi_fetch_batch(ids, email)
            if bad_accessions:
                recs = {k: v for k, v in recs.items()
                        if not any(b.strip() in v.description
                                   for b in bad_accessions if b.strip())}
            candidates = [r for r in recs.values() if len(r.seq) >= min_length]
            if not candidates:
                still_missing.append(n)
                continue
            best = max(candidates, key=lambda r: len(r.seq))
            sp_label = species_query.replace(' ', '_')
            new_id = f"{best.id}|{sp_label}"
            recovered.append(SeqRecord(best.seq, id=new_id, name='', description=''))
        except Exception:
            still_missing.append(n)
    return recovered, still_missing


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


def _find_r_flipped_ids(fasta_path):
    """Return ids of records MAFFT reverse-complemented (`_R_` prefix), with the
    prefix stripped so they match the original raw-FASTA ids."""
    ids = []
    for line in open(fasta_path):
        if line.startswith('>_R_'):
            ids.append(line[1:].split()[0][3:])
    return ids


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

    # Optional: restrict ingroup to species selected from the project Specimens page
    if job.restrict_species:
        kept, matched, missing = _restrict_ingroup(ingroup, job.restrict_species)
        msg = (f'Restricted to project specimens: {len(kept)} of {len(ingroup)} '
               f'sequences kept ({len(matched)} species matched).')

        # The one broad taxon-level search can miss a specimen (retmax cutoff,
        # gene-query wording mismatch, name variant) even though NCBI has a
        # usable record for it. Retry each missing specimen with its own
        # targeted per-species search before giving up on it.
        if missing:
            _set_status(job, 'fetching',
                        msg + f' Retrying {len(missing)} missing specimen(s) individually…')
            recovered, still_missing = _fetch_missing_specimens(
                missing, job.restrict_species, email, gene_q, min_len, bad_acc)
            kept.extend(recovered)
            if recovered:
                msg = (f'Restricted to project specimens: {len(kept)} of '
                       f'{len(ingroup) + len(recovered)} sequences kept '
                       f'({len(matched) + len(recovered)} species matched, '
                       f'{len(recovered)} recovered via per-species retry).')
            missing = still_missing

        if missing:
            shown = ', '.join(m.replace(' ', '_') for m in missing[:8])
            more = '' if len(missing) <= 8 else f' (+{len(missing) - 8} more)'
            msg += f' No NCBI {job.marker} sequence for: {shown}{more}.'
        ingroup = kept
        job.missing_specimens = [m.replace(' ', '_') for m in missing]
        _set_status(job, 'fetching', msg)

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


def _orient_fasta_by_reference(in_path, k=8):
    """Reverse-complement sequences that match a reference better in RC orientation.

    MAFFT `--adjustdirection` does this locally, but the Galaxy MAFFT wrapper
    exposes no such option, so orient the FASTA before uploading. Uses the same
    idea as MAFFT: k-mer overlap against a reference (the longest sequence).
    For each sequence the shared-k-mer count is compared forward vs reverse
    complement and the higher-scoring (lower alignment cost) orientation is kept.
    Rewrites `in_path` in place only if something flipped. Best-effort: on any
    error the file is left untouched. Returns list of ids that were flipped
    (empty list on error or if nothing flipped).
    """
    flipped_ids = []
    try:
        from Bio import SeqIO
        from Bio.Seq import Seq

        def kmers(s):
            return {s[i:i + k] for i in range(len(s) - k + 1)}

        records = list(SeqIO.parse(in_path, 'fasta'))
        if len(records) < 2:
            return flipped_ids
        ref = max(records, key=lambda r: len(r.seq))
        ref_k = kmers(str(ref.seq).upper())
        if not ref_k:
            return flipped_ids
        for rec in records:
            if rec is ref:
                continue
            s = str(rec.seq).upper()
            if len(s) < k:
                continue
            fwd = sum(1 for km in kmers(s) if km in ref_k)
            rc  = sum(1 for km in kmers(str(Seq(s).reverse_complement())) if km in ref_k)
            if rc > fwd:
                rec.seq = rec.seq.reverse_complement()
                rec.description = ''
                flipped_ids.append(rec.id)
        if flipped_ids:
            SeqIO.write(records, in_path, 'fasta')
    except Exception:
        return []
    return flipped_ids


def _align_step(job):
    """MAFFT --auto --adjustdirection (local binary, or Galaxy when unavailable).

    When running on Galaxy, MAFFT + trimAl are done together in one history;
    both output files are produced here and _trim_step becomes a no-op.
    """
    aligned_path = os.path.join(job.result_dir, f'{job.marker}_aligned.fa')
    trimmed_path = os.path.join(job.result_dir, f'{job.marker}_trimmed.fa')

    if _use_galaxy_for_align():
        _set_status(job, 'aligning', 'Aligning (MAFFT) and trimming (trimAl) on Galaxy…')
        _galaxy_align_trim(job, job.raw_fasta_path, aligned_path, trimmed_path)
        job.aligned_fasta_path = aligned_path
        job.trimmed_fasta_path = trimmed_path        # signals _trim_step to skip
        n = _count_fasta(aligned_path)
        note = ' (trimAl skipped — using untrimmed alignment)' \
            if getattr(job, '_trim_skipped', False) else ''
        _set_status(job, 'aligned', f'Galaxy alignment done ({n} sequences).{note}')
        return

    _set_status(job, 'aligning', 'Running MAFFT alignment…')
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
    flipped = _find_r_flipped_ids(aligned_path)
    if flipped:
        job.flipped_sequences = sorted(set((job.flipped_sequences or []) + flipped))
    note = f' {len(flipped)} sequence(s) auto-reversed by MAFFT (direction mismatch).' if flipped else ''
    _set_status(job, 'aligned', f'Alignment done ({n} sequences).{note} Trimming…')


def _trimal_no_seq_loss(aligned_path, trimmed_path, timeout=600):
    """Run trimAl without letting it silently drop whole sequences.

    `-gappyout` trims poorly-conserved columns; if that leaves any sequence
    all-gap, trimAl drops it from the output with no warning. Adding new
    sequences to an alignment shifts which columns look "gappy" across the
    combined set, so a previously-safe run can suddenly lose specimens.
    Retries with the gentler `-automated1` heuristic, then falls back to the
    untrimmed alignment (copied byte-for-byte) so no sequence is ever lost.
    Returns (n_sequences, mode_used) where mode_used is 'gappyout',
    'automated1', or 'untrimmed'.
    """
    n_in = _count_fasta(aligned_path)
    for mode in ('-gappyout', '-automated1'):
        result = subprocess.run(
            ['trimal', '-in', aligned_path, '-out', trimmed_path, mode],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0 and os.path.exists(trimmed_path) \
                and os.path.getsize(trimmed_path):
            n_out = _count_fasta(trimmed_path)
            if n_out >= n_in:
                return n_out, mode.lstrip('-')
    # Both trimAl modes lost sequences (or failed) — use the untrimmed alignment.
    import shutil as _sh
    _sh.copyfile(aligned_path, trimmed_path)
    return n_in, 'untrimmed'


def _trim_step(job):
    """trimAl -gappyout (local). On Galaxy the trim already ran in _align_step."""
    trimmed_path = os.path.join(job.result_dir, f'{job.marker}_trimmed.fa')

    # Galaxy path: alignment step already produced the trimmed file.
    if job.trimmed_fasta_path and os.path.exists(job.trimmed_fasta_path) \
            and os.path.getsize(job.trimmed_fasta_path):
        n = _count_fasta(job.trimmed_fasta_path)
        job.fasta_filename = os.path.basename(job.trimmed_fasta_path)
        job.n_sequences    = n
        _set_status(job, 'trimmed',
                    f'Trimming complete ({n} sequences). Ready to submit to Galaxy.')
        return

    _set_status(job, 'trimming', 'Running trimAl (-gappyout)…')
    n, mode = _trimal_no_seq_loss(job.aligned_fasta_path, trimmed_path)
    note = '' if mode == 'gappyout' else \
        (f' (fell back to -{mode} trimming — gappyout would have dropped '
         f'sequence(s))' if mode == 'automated1' else
         ' (trimAl would have dropped sequence(s) — using untrimmed alignment)')
    job.trimmed_fasta_path = trimmed_path
    job.fasta_filename      = os.path.basename(trimmed_path)
    job.n_sequences         = n
    _set_status(job, 'trimmed',
                f'Trimming complete ({n} sequences).{note} '
                f'Ready to submit to CIPRES.')


def _verify_specimen_coverage(job):
    """Compare job.restrict_species (Specimens page selection) against the species
    actually present in the final trimmed alignment. Sets job.missing_specimens
    and appends a warning to status_message if any specimen has no sequence in
    the final alignment. Non-fatal — never raises."""
    if not job.restrict_species or not job.trimmed_fasta_path \
            or not os.path.exists(job.trimmed_fasta_path):
        return
    try:
        from Bio import SeqIO
        present = set()
        for rec in SeqIO.parse(job.trimmed_fasta_path, 'fasta'):
            rid = rec.id[3:] if rec.id.startswith('_R_') else rec.id
            sp = rid.split('|')[1] if '|' in rid else rid
            present.add(_norm_species(sp))
        allowed = {_norm_species(s): s for s in job.restrict_species if _norm_species(s)}
        missing = sorted(allowed[n] for n in allowed if n not in present)
        job.missing_specimens = missing
        if missing:
            shown = ', '.join(m.replace(' ', '_') for m in missing[:8])
            more = '' if len(missing) <= 8 else f' (+{len(missing) - 8} more)'
            warn = f' ⚠ {len(missing)} specimen(s) missing from final alignment: {shown}{more}.'
            job.status_message = (job.status_message or '') + warn
        db.session.commit()
    except Exception:
        pass


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


def _presence_key(sp):
    """Normalize a species label to match tip labels in the rendered tree."""
    s = re.sub(r'^_R_', '', str(sp or ''), flags=re.IGNORECASE)
    return s.lower().replace('_', ' ').strip()


def _concatenate_alignments(path1, marker1, path2, marker2, out_path):
    """Concatenate two trimmed alignments. Taxa missing from one marker get all-gap columns.

    Returns (n_taxa, len1, len2, presence) where len1/len2 are per-marker
    alignment widths and presence maps normalized species -> which markers it
    contributed ('18S+ITS', '18S', or 'ITS'). Species are matched by the label
    after '|' in the FASTA header.
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
    presence = {}
    for sp in all_sp:
        r1 = by_sp1.get(sp)
        r2 = by_sp2.get(sp)
        seq = (str(r1.seq) if r1 else gap1) + (str(r2.seq) if r2 else gap2)
        rec_id = (r1 or r2).id
        records.append(SR(Seq(seq), id=rec_id, name='', description=''))
        marks = [m for m, r in ((marker1, r1), (marker2, r2)) if r is not None]
        presence[_presence_key(sp)] = '+'.join(marks)

    _write_fasta(records, out_path)
    return len(records), w1, w2, presence


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

    # Optional: restrict ingroup to project specimen species
    if job.restrict_species:
        kept, matched, missing = _restrict_ingroup(ingroup, job.restrict_species)
        recovered = []
        if missing:
            recovered, missing = _fetch_missing_specimens(
                missing, job.restrict_species, email, gene_query, min_len, bad_acc)
            kept.extend(recovered)
        _set_status(job, 'fetching',
                    f'[{marker}] Restricted to project specimens: {len(kept)} of '
                    f'{len(ingroup) + len(recovered)} kept '
                    f'({len(matched) + len(recovered)} species matched'
                    f'{", " + str(len(missing)) + " missing" if missing else ""}).')
        ingroup = kept

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
    """MAFFT align one marker file (local binary). Returns aligned_path."""
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
    """trimAl -gappyout on one marker (local binary). Returns trimmed_path."""
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


def _align_trim_marker(job, raw_path, suffix):
    """Align + trim one marker, on Galaxy when local binaries are unavailable.
    Returns (aligned_path, trimmed_path)."""
    if _use_galaxy_for_align():
        aligned_path = os.path.join(job.result_dir, f'{suffix}_aligned.fa')
        trimmed_path = os.path.join(job.result_dir, f'{suffix}_trimmed.fa')
        _galaxy_align_trim(job, raw_path, aligned_path, trimmed_path)
        return aligned_path, trimmed_path
    aligned_path = _align_marker(raw_path, suffix, job.result_dir)
    flipped = _find_r_flipped_ids(aligned_path)
    if flipped:
        job.flipped_sequences = sorted(set((job.flipped_sequences or []) + flipped))
        db.session.commit()
    trimmed_path = _trim_marker(aligned_path, suffix, job.result_dir)
    return aligned_path, trimmed_path


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

            # Align + trim both markers (on Galaxy when local binaries absent)
            _where = 'Galaxy' if _use_galaxy_for_align() else 'local'
            _set_status(job, 'aligning', f'Aligning + trimming 18S ({_where})…')
            aln18s, trm18s = _align_trim_marker(job, raw18s, '18S')
            _set_status(job, 'aligning', f'Aligning + trimming ITS ({_where})…')
            alnITS, trmITS = _align_trim_marker(job, rawITS, 'ITS')
            job.aligned_fasta_path = aln18s

            # Concatenate
            _set_status(job, 'trimming', 'Concatenating alignments…')
            cat_path = os.path.join(job.result_dir, 'concatenated.fa')
            n_taxa, w18s, wITS, presence = _concatenate_alignments(
                trm18s, '18S', trmITS, 'ITS', cat_path)
            job.trimmed_fasta_path = cat_path
            job.fasta_filename     = 'concatenated.fa'
            job.n_sequences        = n_taxa
            # Column ranges per fragment — drives per-partition model selection
            job.partition_spec = [
                {'name': '18S', 'start': 1, 'end': w18s},
                {'name': 'ITS', 'start': w18s + 1, 'end': w18s + wITS},
            ]
            # Which markers each taxon contributed — for coloring tree tips
            job.partition_presence = presence
            _set_status(job, 'trimmed',
                        f'Concatenation done: {n_taxa} taxa, {w18s}bp 18S + {wITS}bp ITS = '
                        f'{w18s + wITS}bp total. Ready for Galaxy.')
            _verify_specimen_coverage(job)

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
            _verify_specimen_coverage(job)
            _nj_step(job)
            _modeltest_step(job)   # non-fatal; updates model fields if installed
        except Exception as exc:
            job.status = 'failed'
            job.status_message = str(exc)
            db.session.commit()


def _replace_realign_thread(app, job_id, replacements, removals, revcomps=None):
    """Fetch replacement sequences, rewrite raw FASTA, re-align → trim → NJ."""
    revcomps = revcomps or []
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
            revcomp_ids = {_strip_r(r) for r in revcomps}
            kept = [rec for rec in current if _strip_r(rec.id) not in drop_ids]

            # Reverse-complement selected sequences in place
            for rec in kept:
                if _strip_r(rec.id) in revcomp_ids:
                    rec.seq = rec.seq.reverse_complement()

            # Fetch and insert replacements
            failed_accs = []
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
                    else:
                        # NCBI did not return this accession — the specimen would
                        # otherwise vanish silently (it was already dropped above).
                        failed_accs.append(acc)

            _write_fasta(kept, job.raw_fasta_path)
            job.n_sequences_final = len(kept)
            job.n_sequences       = len(kept)
            db.session.commit()

            _align_step(job)
            _trim_step(job)
            _verify_specimen_coverage(job)
            if failed_accs:
                warn = (f' ⚠ Failed to fetch replacement accession(s) from NCBI: '
                        f'{", ".join(failed_accs)} — these specimens are missing '
                        f'from the alignment.')
                job.status_message = (job.status_message or '') + warn
                db.session.commit()
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
    _galaxy_raise_for_status(r, f'upload ({file_type})')
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


def _galaxy_raise_for_status(r, what):
    """raise_for_status that includes Galaxy's JSON error body (err_msg/err_code).

    Galaxy answers a 400 on /api/tools with a JSON body explaining the cause
    (unknown tool_id, bad parameter, etc.); the default requests exception
    hides it, so surface it here.
    """
    if r.status_code < 400:
        return
    detail = ''
    try:
        body = r.json()
        detail = body.get('err_msg') or body.get('message') or json.dumps(body)
    except ValueError:
        detail = (r.text or '')[:500]
    raise RuntimeError(f'Galaxy {what} failed ({r.status_code}): {detail}')


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
    _galaxy_raise_for_status(r, f'tool run ({tool_id})')
    data = r.json()
    if data.get('err_msg'):
        raise RuntimeError(f'Galaxy tool error: {data["err_msg"]}')
    jobs = data.get('jobs') or []
    if not jobs:
        raise RuntimeError(f'Galaxy returned no jobs: {str(data)[:400]}')
    return jobs[0]['id']


def _galaxy_download_dataset(api_key, ds_id, dest_path):
    """Download a single Galaxy dataset to a local path."""
    import requests as _req
    base = _galaxy_base()
    dl = _req.get(f'{base}/api/datasets/{ds_id}/display',
                  headers=_galaxy_headers(api_key), stream=True, timeout=600)
    dl.raise_for_status()
    with open(dest_path, 'wb') as fh:
        for chunk in dl.iter_content(65536):
            fh.write(chunk)
    return dest_path


def _galaxy_run_chain(api_key, history_id, tool_id, input_key, dataset_id,
                      extra_params_json, label, max_wait=3600):
    """Run a single Galaxy tool on an existing dataset, wait, return the job id."""
    inputs = {input_key: {'src': 'hda', 'id': dataset_id}}
    try:
        extra = json.loads(extra_params_json or '{}')
        if isinstance(extra, dict):
            inputs.update(extra)
    except Exception:
        pass
    gjob = _galaxy_run_tool(api_key, history_id, tool_id, inputs)
    state = _galaxy_wait_for_job(api_key, gjob, max_wait=max_wait)
    if state != 'ok':
        raise RuntimeError(f'Galaxy {label} job ended in state "{state}".')
    return gjob


def _galaxy_pick_fasta_output(api_key, job_id, dest_path):
    """Download a finished job's outputs and keep the first that is real FASTA.

    Returns (dataset_id, dest_path) for the FASTA output, or (None, None) if no
    output parses as FASTA. Content-based — robust to tool output naming/format.
    """
    import requests as _req
    base = _galaxy_base()
    r = _req.get(f'{base}/api/jobs/{job_id}/outputs',
                 headers=_galaxy_headers(api_key), timeout=30)
    r.raise_for_status()
    cands = []
    for out in r.json():
        ds = out.get('dataset') or {}
        ds_id = ds.get('id') or out.get('id')
        if ds_id:
            cands.append((ds_id, (out.get('name') or '').lower()))
    # Prefer non-report-looking outputs first
    cands.sort(key=lambda c: any(k in c[1] for k in ('html', 'report', 'log', 'summary')))
    tmp = dest_path + '.cand'
    for ds_id, _name in cands:
        try:
            _galaxy_download_dataset(api_key, ds_id, tmp)
        except Exception:
            continue
        if os.path.exists(tmp) and _count_fasta(tmp) > 0:
            os.replace(tmp, dest_path)
            return ds_id, dest_path
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    return None, None


def _galaxy_align_trim(job, in_path, aligned_out, trimmed_out):
    """Align (MAFFT) then trim (trimAl) a FASTA on Galaxy.

    Trimming is best-effort: if trimAl yields no FASTA output, the untrimmed
    MAFFT alignment is used so the pipeline can still proceed to tree building.
    Writes aligned_out and trimmed_out locally. Returns (aligned_out, trimmed_out).
    """
    import shutil as _sh
    cfg = current_app.config
    api_key = (job.galaxy_api_key or cfg.get('GALAXY_API_KEY', ''))
    if not api_key:
        raise RuntimeError('A Galaxy API key is required to align/trim on Galaxy '
                           '(set it on the job or GALAXY_API_KEY).')
    hist = _galaxy_create_history(api_key, 'GyroMorpho_AlignTrim')
    # Galaxy MAFFT has no --adjustdirection; orient sequences before upload.
    flipped = _orient_fasta_by_reference(in_path)
    if flipped:
        job.flipped_sequences = sorted(set((job.flipped_sequences or []) + flipped))
        db.session.commit()
    ds_id, up_job = _galaxy_upload_file(api_key, hist, in_path, 'fasta')
    if up_job:
        st = _galaxy_wait_for_job(api_key, up_job, max_wait=600)
        if st != 'ok':
            raise RuntimeError(f'Galaxy upload failed (state: {st}).')

    # MAFFT (required — its output must be FASTA)
    mafft_job = _galaxy_run_chain(
        api_key, hist, cfg['GALAXY_MAFFT_TOOL_ID'], cfg['GALAXY_MAFFT_INPUT_KEY'],
        ds_id, cfg['GALAXY_MAFFT_PARAMS'], 'MAFFT')
    aln_ds, _ = _galaxy_pick_fasta_output(api_key, mafft_job, aligned_out)
    if not aln_ds:
        raise RuntimeError('Galaxy MAFFT produced no FASTA output (check '
                           'GALAXY_MAFFT_TOOL_ID / params).')

    # trimAl (best-effort — fall back to the alignment if it is not FASTA, or if
    # it silently drops whole sequences that end up all-gap after trimming).
    n_aligned = _count_fasta(aligned_out)
    try:
        trim_job = _galaxy_run_chain(
            api_key, hist, cfg['GALAXY_TRIMAL_TOOL_ID'], cfg['GALAXY_TRIMAL_INPUT_KEY'],
            aln_ds, cfg['GALAXY_TRIMAL_PARAMS'], 'trimAl')
        trm_ds, _ = _galaxy_pick_fasta_output(api_key, trim_job, trimmed_out)
    except Exception:
        trm_ds = None
    if trm_ds and _count_fasta(trimmed_out) < n_aligned:
        trm_ds = None
    if not trm_ds:
        # Use the untrimmed alignment so no sequence is ever lost.
        _sh.copyfile(aligned_out, trimmed_out)
        job._trim_skipped = True
    return aligned_out, trimmed_out


def _use_galaxy_for_align():
    """True when alignment/trimming should run on Galaxy instead of local binaries."""
    import shutil as _sh
    if current_app.config.get('PHYLO_FORCE_GALAXY'):
        return True
    # Fall back to Galaxy whenever the local binaries are missing (e.g. Railway)
    return not (_sh.which('mafft') and _sh.which('trimal'))


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


# Galaxy dataset names emitted by the RAxML / RAxML-NG tools, ranked by how
# useful the tree is for display. We want bootstrap support drawn on the tree
# AS NODE LABELS — "bipartitions" (e.g. `)95:`), which the client parser reads.
# "bipartitionsBranchLabels" stores support as `[95]` branch comments that the
# parser strips, so it is only a last resort. Files are saved by
# _galaxy_download_results as "<Galaxy dataset name>.dat".
# Galaxy names them like "RAxML on dataset 1: Bipartitions" -> the normalized
# stem is a long string ENDING in the meaningful part, so match with endswith.
# Order 'bipartitions' before 'bipartitionsbranchlabels' so the plain
# node-label tree wins (endswith('bipartitions') is false for branchlabels).
_TREE_NAME_PRIORITY = (
    'bipartitions',              # ML best tree, support as node labels (preferred)
    'support',                   # RAxML-NG .raxml.support (support as node labels)
    'bipartitionsbranchlabels',  # support as [..] branch labels (parser strips these)
    'bestscoringmltree',         # RAxML 8 "Best-scoring ML Tree" — no support
    'besttree',                  # RAxML-NG best tree (.raxml.bestTree) — no support
    'mltree',
    'result',                    # RAxML 8 ML best tree (RAxML_result.*) — no support
    'bestmodel',
)


def _norm_ds_name(name):
    """Normalize a downloaded dataset filename to its comparable stem."""
    stem = os.path.splitext(name)[0]           # drop the ".dat" suffix
    return re.sub(r'[^a-z0-9]', '', stem.lower())


def _looks_like_newick(path):
    """Cheap sniff: first non-space char is '(' and a ')' appears in the head."""
    try:
        with open(path, 'r', errors='ignore') as fh:
            head = fh.read(8192)
    except OSError:
        return False
    head = head.lstrip()
    return head.startswith('(') and ')' in head


def _find_best_tree(results_dir):
    """Return the best RAxML tree file in results_dir, preferring the tree that
    carries bootstrap support as node labels (bipartitions). Returns path or None."""
    try:
        names = os.listdir(results_dir)
    except OSError:
        return None
    norm = {name: _norm_ds_name(name) for name in names}

    def pick(match):
        for key in _TREE_NAME_PRIORITY:
            for name in names:
                if match(norm[name], key):
                    path = os.path.join(results_dir, name)
                    if os.path.isfile(path) and _looks_like_newick(path):
                        return path
        return None

    # endswith first (Galaxy prefixes names, e.g. "...bipartitions"); this keeps
    # 'bipartitions' from matching the 'bipartitionsbranchlabels' file. Fall back
    # to a looser substring match only if nothing ended cleanly.
    return pick(lambda n, k: n.endswith(k)) or pick(lambda n, k: k in n)


def _find_newick_in_dir(results_dir):
    """Fallback: first file in results_dir whose content parses as newick."""
    try:
        names = sorted(os.listdir(results_dir))
    except OSError:
        return None
    for name in names:
        path = os.path.join(results_dir, name)
        if os.path.isfile(path) and _looks_like_newick(path):
            return path
    return None


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
    # Rapid bootstrap analysis (-f a): best ML tree + N bootstrap replicates,
    # with support values drawn onto the best tree (RAxML_bipartitions).
    # Conditional/section parameters MUST be passed as flattened '|'-delimited
    # keys — nested dicts are silently ignored by the tool API, which then runs
    # a plain ML search (-f d) with no bootstrap and emits no Bipartitions tree.
    inputs = {
        'infile': {'src': 'hda', 'id': ds_id},
        'search_model_selector|model_type': 'nucleotide',
        'search_model_selector|base_model': 'GTRGAMMA',
        'random_seed': 1234567890,
        'selExtraOpts|extraOptions': 'full',
        'selExtraOpts|search_algorithm': 'a',   # -f a: rapid bootstrap + ML + bipartitions
        'selExtraOpts|rapid_bootstrap_random_seed': 12345,
        'selExtraOpts|number_of_runs_conditional|number_of_runs_selector': 'by_number_of_runs',
        'selExtraOpts|number_of_runs_conditional|number_of_runs': int(n_bootstraps),
    }
    job_id = _galaxy_run_tool(api_key, history_id, tool_id, inputs)
    return history_id, job_id


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
            _set_status(job, job.status,
                        f'Best-fit model (BIC): {best}. Ready for Galaxy.')
        else:
            _set_status(job, job.status,
                        prev_msg + ' | ModelTest-NG ran but no BIC model found.')
    except Exception as exc:
        _set_status(job, job.status,
                    prev_msg + f' | ModelTest-NG error: {exc}')


def _root_tree_python(tree_file, outgroup_genera, output_file):
    """Root a tree with Biopython when Rscript/ape is unavailable (e.g. Railway).

    Matches outgroup tips by genus (case-insensitive substring) and roots at
    their MRCA. Returns (success, message). Non-fatal.
    """
    try:
        from Bio import Phylo
        tree = Phylo.read(tree_file, 'newick')
        pats = [g.lower() for g in (outgroup_genera or []) if g]
        og_tips = [t for t in tree.get_terminals()
                   if any(p in (t.name or '').lower() for p in pats)]
        if not og_tips:
            Phylo.write(tree, output_file, 'newick')
            return os.path.exists(output_file), 'No outgroup tips found (Python); unrooted.'
        if len(og_tips) == 1:
            tree.root_with_outgroup(og_tips[0])
        else:
            mrca = tree.common_ancestor(og_tips)
            tree.root_with_outgroup(mrca)
        Phylo.write(tree, output_file, 'newick')
        return os.path.exists(output_file), f'Rooted with Biopython ({len(og_tips)} outgroup tips).'
    except Exception as exc:
        return False, f'Python rooting failed: {exc}'


def _root_tree(tree_file, outgroup_genera, output_file):
    import shutil as _sh
    # No R on this host → root with Biopython instead.
    if not _sh.which('Rscript'):
        return _root_tree_python(tree_file, outgroup_genera, output_file)
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
    # edgelabel=TRUE keeps internal node labels (bootstrap / posterior support)
    # attached to the correct branches when the tree is re-rooted.
    rooted <- root(tree, outgroup=outgroup, resolve.root=TRUE, edgelabel=TRUE)
    write.tree(rooted, file='{of}')
    cat('SUCCESS\\n')
  }}, error=function(e) {{
    rooted <- root(tree, outgroup=outgroup[1], resolve.root=TRUE, edgelabel=TRUE)
    write.tree(rooted, file='{of}')
    cat('FALLBACK\\n')
  }})
}} else {{
  write.tree(tree, file='{of}')
  cat('NO_OUTGROUP\\n')
}}
"""
    try:
        result = subprocess.run(
            ['Rscript', '--vanilla', '-e', r_code],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError:
        return _root_tree_python(tree_file, outgroup_genera, output_file)
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
    specimen_species = sorted({
        (s.species_name or '').strip()
        for s in Specimen.query.filter_by(project_id=project_id).all()
        if (s.species_name or '').strip()
    })
    defaults = {
        'target_taxon':     'Gyrodactylidae',
        'gene_query':       DEFAULT_GENE_QUERY_18S,
        'min_length':       400,
        'outgroup_defs':    DEFAULT_OUTGROUP_DEFS,
        'outgroup_genera':  '\n'.join(DEFAULT_OUTGROUP_GENERA),
        'galaxy_api_key':   current_app.config.get('GALAXY_API_KEY', ''),
    }
    return render_template('phylogeny/phylogeny.html',
                           project=project, jobs=jobs, defaults=defaults,
                           specimen_species=specimen_species)


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

        # Optional: restrict ingroup to selected project specimens.
        # Sent as JSON list in 'restrict_species' when the toggle is on.
        restrict_species = None
        rs_raw = request.form.get('restrict_species', '').strip()
        if rs_raw:
            try:
                parsed = _json.loads(rs_raw)
                if isinstance(parsed, list):
                    restrict_species = [str(s).strip() for s in parsed if str(s).strip()]
            except Exception:
                restrict_species = [s.strip() for s in rs_raw.splitlines() if s.strip()]
            if not restrict_species:
                restrict_species = None

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
            # Safety net: if marker=='ITS' but the query is still the 18S default
            # (JS should swap it, but guard against a bypassed/stale client),
            # the 18S query's "NOT ITS" clause would return zero ITS records.
            if marker == 'ITS' and gene_query == DEFAULT_GENE_QUERY_18S:
                gene_query = DEFAULT_GENE_QUERY_ITS

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
            restrict_species=restrict_species,
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
    """Submit a trimmed alignment to Galaxy (usegalaxy.eu) for RAxML-NG inference."""
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()

    if job.status not in ('trimmed', 'fetched', 'nj_ready'):
        return jsonify({'error': f'Job not ready (stage: {job.status})'}), 400

    submit_path = job.trimmed_fasta_path or job.raw_fasta_path
    if not submit_path or not os.path.exists(submit_path):
        return jsonify({'error': 'FASTA file not found on disk.'}), 400

    api_key = (job.galaxy_api_key or current_app.config.get('GALAXY_API_KEY', ''))
    if not api_key:
        return jsonify({'error': 'Galaxy API key is required. Set it in the job form or GALAXY_API_KEY env var.'}), 400

    try:
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
    project.tree_fragments = job.partition_presence or None
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
    project.tree_fragments = job.partition_presence or None
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
        'presence': job.partition_presence or {},
    })


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/model')
@login_required
def get_model_info(project_id, job_id):
    """Return model selection info for the Galaxy submission modal."""
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    return jsonify({
        'best_fit_model': job.best_fit_model,
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
            project.tree_fragments = None   # uploaded tree — no fragment info
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
            return jsonify({'error': 'ML tree not available.'}), 404
        from io import BytesIO
        buf = BytesIO(job.tree_newick.encode())
        return send_file(buf, as_attachment=True,
                         download_name='raxml_tree.nwk', mimetype='text/plain')

    return jsonify({'error': f'Unknown file type: {filetype}'}), 400


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/nj_preview')
@login_required
def nj_preview(project_id, job_id):
    """Return NJ newick + sequence list for the NJ review modal."""
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    if not job.nj_newick:
        return jsonify({'error': 'NJ tree not available.'}), 404
    sequences = []
    flipped_set = set(job.flipped_sequences or [])
    if job.trimmed_fasta_path and os.path.exists(job.trimmed_fasta_path):
        from Bio import SeqIO
        with open(job.trimmed_fasta_path) as fh:
            for rec in SeqIO.parse(fh, 'fasta'):
                sp = rec.id.split('|')[1].replace('_', ' ') if '|' in rec.id else rec.id
                raw_id = rec.id[3:] if rec.id.startswith('_R_') else rec.id
                sequences.append({'id': rec.id, 'species': sp, 'length': len(rec.seq),
                                  'flipped': raw_id in flipped_set or rec.id in flipped_set})
    return jsonify({
        'newick':     job.nj_newick,
        'sequences':  sequences,
        'n_sequences': len(sequences),
        'status_message': job.status_message or '',
        'flipped_sequences': sorted(flipped_set),
        'missing_specimens': job.missing_specimens or [],
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
    revcomps     = data.get('revcomps', [])         # [old_id, ...]
    if not replacements and not removals and not revcomps:
        return jsonify({'error': 'No changes specified.'}), 400
    app = current_app._get_current_object()
    t = threading.Thread(target=_replace_realign_thread,
                         args=(app, job_id, replacements, removals, revcomps), daemon=True)
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

        added  = []
        failed = []
        for it in items:
            acc     = it['accession']
            species = it.get('species', '').strip()
            if acc not in fetched:
                failed.append(acc)
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
            return jsonify({'error': 'No new sequences added (may already be present)',
                            'failed': failed}), 400

        _write_fasta(current, job.raw_fasta_path)
        job.n_sequences_final = len(current)
        job.n_sequences       = len(current)

        fail_note = f' {len(failed)} accession(s) failed: {", ".join(failed)}.' if failed else ''

        if job.status == 'nj_ready':
            # Re-run align→trim→NJ automatically
            app = current_app._get_current_object()
            t = threading.Thread(target=_align_trim_thread, args=(app, job_id), daemon=True)
            t.start()
            db.session.commit()
            return jsonify({'status': 'aligning',
                            'message': f'Added {len(added)} sequence(s).{fail_note} Re-aligning…',
                            'added': added, 'failed': failed})

        db.session.commit()
        return jsonify({'status': job.status, 'added': added, 'failed': failed,
                        'n_sequences': len(current)})

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
        project.tree_fragments = None   # uploaded tree — no fragment info
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
