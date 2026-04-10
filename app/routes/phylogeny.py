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

from flask import Blueprint, render_template, request, jsonify, current_app
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
                    f'Trimming complete. (NJ failed: {exc}) Ready for CIPRES.')


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
    """Background thread: align → trim → NJ (started after user approves fetch)."""
    with app.app_context():
        job = db.session.get(PhylogenyJob, job_id)
        if not job:
            return
        try:
            _align_step(job)
            _trim_step(job)
            _nj_step(job)
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

            # IDs to drop
            drop_ids = set(removals) | {r['old_id'] for r in replacements}
            kept = [rec for rec in current if rec.id not in drop_ids]

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

        except Exception as exc:
            job.status = 'failed'
            job.status_message = str(exc)
            db.session.commit()


# ── CIPRES helpers (curl-based, matching R script exactly) ───────────────────

def _cipres_base():
    return current_app.config.get('CIPRES_BASE_URL',
                                  'https://cipresrest.sdsc.edu/cipresrest/v1')


def _curl_get(url, user, password, app_key, timeout=60):
    """GET a CIPRES URL with curl, return XML string."""
    result = subprocess.run(
        ['curl', '-s', '--max-time', str(timeout),
         '-u', f'{user}:{password}',
         '-H', f'cipres-appkey:{app_key}',
         url],
        capture_output=True, text=True, timeout=timeout + 10,
    )
    if result.returncode != 0:
        raise RuntimeError(f'curl error: {result.stderr[:300]}')
    raw = result.stdout.strip()
    if not raw:
        raise RuntimeError('Empty response from CIPRES')
    return raw


def _parse_cipres_xml(raw):
    try:
        return ET.fromstring(raw)
    except ET.ParseError as e:
        raise RuntimeError(f'Non-XML CIPRES response: {raw[:400]}') from e


def _submit_to_cipres(fasta_path, user, password, app_key, n_bootstraps=1000):
    base = _cipres_base()
    result = subprocess.run(
        ['curl', '-s', '--max-time', '120',
         '-u', f'{user}:{password}',
         '-H', f'cipres-appkey:{app_key}',
         f'{base}/job/{user}',
         '-F', 'tool=RAXMLNG_XSEDE',
         '-F', f'input.infile_=@{fasta_path}',
         '-F', 'vparam.select_analysis_=all',
         '-F', f'vparam.specify_bootstraps_={n_bootstraps}',
         ],
        capture_output=True, text=True, timeout=130,
    )
    if result.returncode != 0:
        raise RuntimeError(f'curl error: {result.stderr[:300]}')
    raw = result.stdout.strip()
    if not raw:
        raise RuntimeError('Empty response from CIPRES — check credentials and app key.')
    xml = _parse_cipres_xml(raw)
    # Check for CIPRES error element
    err = xml.findtext('.//error') or xml.findtext('.//displayMessage')
    if err:
        raise RuntimeError(f'CIPRES error: {err}')
    job_url    = xml.findtext('.//selfUri/url')
    job_handle = xml.findtext('.//jobHandle') or xml.findtext('jobHandle')
    if not job_url:
        raise RuntimeError(f'No job URL in response: {raw[:400]}')
    return job_url, job_handle


def _check_cipres_status(job_url, user, password, app_key):
    raw = _curl_get(job_url, user, password, app_key)
    xml = _parse_cipres_xml(raw)
    stage       = xml.findtext('.//jobStage') or 'UNKNOWN'
    messages    = [m.text for m in xml.findall('.//messages/message') if m.text]
    results_url = xml.findtext('.//resultsUri/url')
    return stage, results_url, messages


def _download_results(results_url, user, password, app_key, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    raw = _curl_get(results_url, user, password, app_key)
    xml = _parse_cipres_xml(raw)
    downloaded = []
    for node in xml.iter('jobfile'):
        fname = node.findtext('filename') or node.findtext('.//filename')
        url   = node.findtext('.//downloadUri/url')
        if fname and url:
            dest = os.path.join(dest_dir, os.path.basename(fname))
            result = subprocess.run(
                ['curl', '-s', '--max-time', '300',
                 '-u', f'{user}:{password}',
                 '-H', f'cipres-appkey:{app_key}',
                 '-o', dest, url],
                capture_output=True, timeout=310,
            )
            if result.returncode == 0:
                downloaded.append(os.path.basename(dest))
    return downloaded


def _find_best_tree(results_dir):
    """Return path to infile.txt.raxml.support, falling back to bestTree."""
    preferred = os.path.join(results_dir, 'infile.txt.raxml.support')
    if os.path.exists(preferred):
        return preferred
    fallback = os.path.join(results_dir, 'infile.txt.raxml.bestTree')
    if os.path.exists(fallback):
        return fallback
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
        'cipres_user':      current_app.config.get('CIPRES_USER', ''),
        'cipres_app_key':   current_app.config.get('CIPRES_APP_KEY', ''),
    }
    return render_template('phylogeny/phylogeny.html',
                           project=project, jobs=jobs, defaults=defaults)


@phylo_bp.route('/project/<int:project_id>/phylogeny/create', methods=['POST'])
@login_required
def create_job(project_id):
    project = Project.query.get_or_404(project_id)
    mode = request.form.get('mode', 'ncbi')   # 'ncbi' or 'upload'

    marker        = request.form.get('marker', '18S').strip()
    n_bootstraps  = max(100, min(5000, int(request.form.get('n_bootstraps', 1000) or 1000)))
    outgroup_gen  = [g.strip() for g in request.form.get('outgroup_genera', '').splitlines() if g.strip()] \
                    or DEFAULT_OUTGROUP_GENERA[:]
    cipres_user   = request.form.get('cipres_user', '').strip()  or current_app.config.get('CIPRES_USER', '')
    cipres_pw     = request.form.get('cipres_password', '').strip() or current_app.config.get('CIPRES_PASSWORD', '')
    cipres_key    = request.form.get('cipres_app_key', '').strip()  or current_app.config.get('CIPRES_APP_KEY', '')

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
            cipres_user=cipres_user,
            cipres_password_enc=cipres_pw,
            cipres_app_key=cipres_key,
            status='trimmed',
            status_message=f'Uploaded {n_seqs} sequences. Ready for CIPRES.',
        )
        db.session.add(job)
        db.session.commit()
        return jsonify({'job_id': job.id, 'status': 'trimmed',
                        'message': job.status_message})

    else:
        # Full NCBI pipeline
        ncbi_email  = request.form.get('ncbi_email', '').strip()
        target_taxon = request.form.get('target_taxon', 'Gyrodactylidae').strip()
        gene_query  = request.form.get('gene_query', DEFAULT_GENE_QUERY_18S).strip()
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
            cipres_user=cipres_user,
            cipres_password_enc=cipres_pw,
            cipres_app_key=cipres_key,
            status='created',
            status_message='Starting NCBI retrieval…',
        )
        db.session.add(job)
        db.session.commit()

        # Launch background thread
        app = current_app._get_current_object()
        t = threading.Thread(target=_pipeline_thread, args=(app, job.id), daemon=True)
        t.start()

        return jsonify({'job_id': job.id, 'status': 'fetching',
                        'message': 'Pipeline started. Polling for updates…'})


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/status')
@login_required
def job_status(project_id, job_id):
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()

    # For CIPRES stages, also poll CIPRES
    if job.status in ('submitted', 'running') and job.job_url:
        try:
            stage, results_url, messages = _check_cipres_status(
                job.job_url, job.cipres_user, job.cipres_password_enc, job.cipres_app_key
            )
            job.last_checked = datetime.now(timezone.utc)
            job.status_message = '; '.join(messages) if messages else stage
            if stage == 'COMPLETED':
                job.status = 'completed'
                job.results_url = results_url
                job.completed_at = datetime.now(timezone.utc)
            elif stage in ('FAILED', 'TERMINATED', 'SUSPENDED'):
                job.status = 'failed'
            else:
                job.status = 'running'
            db.session.commit()
        except Exception as e:
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
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()

    if job.status not in ('trimmed', 'fetched', 'nj_ready'):
        return jsonify({'error': f'Job not ready (stage: {job.status})'}), 400

    submit_path = job.trimmed_fasta_path or job.raw_fasta_path
    if not submit_path or not os.path.exists(submit_path):
        return jsonify({'error': 'FASTA file not found on disk.'}), 400

    cipres_user = job.cipres_user or current_app.config.get('CIPRES_USER', '')
    cipres_pw   = job.cipres_password_enc or current_app.config.get('CIPRES_PASSWORD', '')
    cipres_key  = job.cipres_app_key or current_app.config.get('CIPRES_APP_KEY', '')
    if not all([cipres_user, cipres_pw, cipres_key]):
        return jsonify({'error': 'CIPRES credentials are required.'}), 400

    try:
        job_url, job_handle = _submit_to_cipres(
            submit_path, cipres_user, cipres_pw, cipres_key, job.n_bootstraps or 1000
        )
        job.job_url    = job_url
        job.job_handle = job_handle
        job.status     = 'submitted'
        job.status_message = f'Submitted to CIPRES (handle: {job_handle})'
        db.session.commit()
        return jsonify({'status': 'submitted', 'message': job.status_message})
    except Exception as e:
        import traceback
        current_app.logger.error('CIPRES submit error: %s', traceback.format_exc())
        return jsonify({'error': str(e)[:600]}), 500


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/download',
                methods=['POST'])
@login_required
def download_and_root(project_id, job_id):
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    if not job.results_url:
        return jsonify({'error': 'No results URL. Check status first.'}), 400
    try:
        results_dir = job.result_dir   # everything goes into the job folder
        downloaded  = _download_results(
            job.results_url, job.cipres_user, job.cipres_password_enc,
            job.cipres_app_key, results_dir
        )
        tree_file = _find_best_tree(results_dir)
        if not tree_file:
            return jsonify({'error': 'No tree file in results. Files: ' +
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


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/import',
                methods=['POST'])
@login_required
def import_tree(project_id, job_id):
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    if not job.tree_newick:
        return jsonify({'error': 'No ML tree available. Download results first.'}), 400
    project = Project.query.get_or_404(project_id)
    project.tree_newick = job.tree_newick
    db.session.commit()
    return jsonify({'status': 'ok', 'message': 'ML tree imported into project.'})


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/import_nj',
                methods=['POST'])
@login_required
def import_nj_tree(project_id, job_id):
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    if not job.nj_newick:
        return jsonify({'error': 'No NJ tree available for this job.'}), 400
    project = Project.query.get_or_404(project_id)
    project.tree_newick = job.nj_newick
    db.session.commit()
    return jsonify({'status': 'ok', 'message': 'NJ tree imported into project.'})


@phylo_bp.route('/api/project/<int:project_id>/phylogeny/<int:job_id>/newick')
@login_required
def get_newick(project_id, job_id):
    job = PhylogenyJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    return jsonify({
        'newick': job.tree_newick,
        'marker': job.marker,
        'taxon':  job.target_taxon or job.fasta_filename or '',
    })


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
    if job.status not in ('nj_ready', 'trimmed'):
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
    if job.status not in ('nj_ready', 'trimmed'):
        return jsonify({'error': f'Job not in reviewable state (current: {job.status})'}), 400
    job.status         = 'trimmed'
    job.status_message = 'Approved after NJ review. Ready for CIPRES submission.'
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
        db.session.commit()
        return jsonify({'has_tree': True})
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
