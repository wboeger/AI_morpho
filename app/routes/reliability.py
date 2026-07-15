"""Blind MCO-illustration reliability evaluation.

Scientists (logged-in users) independently score each species' MCO illustration
against a set of user-editable criteria. A composite reliability index (CRI, in
[0,1]) is computed per rating and averaged across raters into a per-species
index, with inter-observer agreement (average pairwise weighted Cohen's kappa on
the confidence band) and an automatic count-concordance (K) flag.
"""
import re
import base64
import itertools

from flask import (Blueprint, render_template, request, jsonify, abort)
from flask_login import login_required, current_user

from app import db
from app.models import (Project, ProjectMembership, Specimen, Structure,
                        CharacterDefinition, CharacterValue,
                        ReliabilityCriterion, MCOReliabilityRating)

reliability_bp = Blueprint('reliability', __name__)


# ── access / helpers ──────────────────────────────────────────────────────────

def _project_or_403(project_id):
    project = Project.query.get_or_404(project_id)
    is_member = (project.created_by == current_user.id or
                 ProjectMembership.query.filter_by(
                     user_id=current_user.id, project_id=project_id).first())
    if not is_member:
        abort(403)
    return project


def _norm(name):
    """Normalize a species name to a matching key (lowercase, genus+epithet)."""
    s = (name or '').strip().lower().replace('_', ' ')
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    toks = s.split()
    return ' '.join(toks[:2])


def _tok(species_norm):
    return base64.urlsafe_b64encode(species_norm.encode()).decode()


def _untok(token):
    try:
        return base64.urlsafe_b64decode((token or '').encode()).decode()
    except Exception:
        return ''


# Bands for the composite index (fixed; documented in the protocol).
def _band(cri):
    if cri is None:
        return None
    if cri >= 0.80:
        return 'High'
    if cri >= 0.50:
        return 'Moderate'
    return 'Low'


DEFAULT_CRITERIA = [
    ('S', 'Source fidelity', 2, 1.0,
     '2 = original photomicrograph or phase-contrast/DIC image\n'
     '1 = published line drawing traced from a specimen\n'
     '0 = redrawn/schematic from a secondary source'),
    ('O', 'Orientation / aspect', 2, 1.5,
     '2 = MCO in clear en-face / standard aspect, symmetry axis visible\n'
     '1 = oblique but interpretable\n'
     '0 = lateral, folded, or ambiguous orientation'),
    ('C', 'Structural completeness', 2, 1.0,
     '2 = bulb, principal spine, and full spinelet crown all visible\n'
     '1 = one region obscured or truncated\n'
     '0 = MCO partially everted/collapsed or damaged'),
    ('R', 'Optical/rendering resolution', 2, 1.5,
     '2 = individual spinelets and their bases resolvable\n'
     '1 = spinelets visible but bases/size differences uncertain\n'
     '0 = spinelet zone blurred or stippled generically'),
]


def _ensure_default_criteria(project_id):
    """Seed the protocol's default criteria the first time a project is used."""
    if ReliabilityCriterion.query.filter_by(project_id=project_id).first():
        return
    for i, (code, name, mx, w, rubric) in enumerate(DEFAULT_CRITERIA):
        db.session.add(ReliabilityCriterion(
            project_id=project_id, code=code, name=name, max_score=mx,
            weight=w, rubric=rubric, display_order=i, active=True))
    db.session.commit()


def _criteria(project_id, active_only=False):
    q = ReliabilityCriterion.query.filter_by(project_id=project_id)
    if active_only:
        q = q.filter_by(active=True)
    return q.order_by(ReliabilityCriterion.display_order,
                      ReliabilityCriterion.id).all()


def _crit_dict(c):
    return {'id': c.id, 'code': c.code, 'name': c.name, 'rubric': c.rubric or '',
            'max_score': c.max_score, 'weight': c.weight,
            'display_order': c.display_order, 'active': bool(c.active)}


def _compute_cri(scores, crit_by_id):
    """CRI = Σ(w·score) / Σ(w·max) over the criteria a rating actually scored,
    using each criterion's current weight/max. Returns None if nothing scorable."""
    num = den = 0.0
    for cid, sc in (scores or {}).items():
        c = crit_by_id.get(int(cid))
        if not c or c.max_score <= 0:
            continue
        try:
            v = float(sc)
        except (TypeError, ValueError):
            continue
        num += c.weight * v
        den += c.weight * c.max_score
    return (num / den) if den > 0 else None


# ── species ↔ MCO image ───────────────────────────────────────────────────────

def _species_mco(project_id):
    """{species_norm: {display, structure_id, image_url}} for species that have an
    MCO structure with an image. Picks the most complete MCO per species."""
    out = {}
    for sp in Specimen.query.filter_by(project_id=project_id).all():
        n = _norm(sp.species_name)
        if not n:
            continue
        for st in sp.structures:
            if st.structure_type != 'mco' or not st.image_path:
                continue
            score = bool(st.landmarks_json) + bool(st.boundary_json) + 1
            e = out.get(n)
            if not e or score > e['_score']:
                out[n] = {'display': sp.species_name, 'structure_id': st.id,
                          'image_url': f'/uploads/{st.image_path}', '_score': score}
    for e in out.values():
        e.pop('_score', None)
    return out


# ── aggregation / agreement ───────────────────────────────────────────────────

def species_cri_map(project_id):
    """{species_norm: {cri, band, n}} — mean of per-rating CRI (recomputed live
    from current criteria) across raters. Shared by Specimens/Matrix/Export."""
    crit_by_id = {c.id: c for c in _criteria(project_id)}
    by_sp = {}
    for r in MCOReliabilityRating.query.filter_by(project_id=project_id).all():
        cri = _compute_cri(r.scores or {}, crit_by_id)
        if cri is None:
            continue
        by_sp.setdefault(r.species_norm, []).append(cri)
    out = {}
    for n, vals in by_sp.items():
        m = sum(vals) / len(vals)
        out[n] = {'cri': round(m, 3), 'band': _band(m), 'n': len(vals)}
    return out


def _weighted_kappa(a, b, k):
    """Linearly-weighted Cohen's kappa for two ordinal rating vectors over the
    same items, categories 0..k. Returns None if undefined (no variance)."""
    n = len(a)
    if n == 0 or k <= 0:
        return None
    cats = list(range(k + 1))
    # observed and expected disagreement (linear weights)
    obs = 0.0
    for x, y in zip(a, b):
        obs += abs(x - y) / k
    obs /= n
    ma = [a.count(c) / n for c in cats]
    mb = [b.count(c) / n for c in cats]
    exp = 0.0
    for i in cats:
        for j in cats:
            exp += ma[i] * mb[j] * (abs(i - j) / k)
    if exp == 0:
        return 1.0 if obs == 0 else None
    return 1.0 - obs / exp


def _band_kappa(project_id):
    """Average pairwise weighted kappa across raters on the 3-level confidence
    band (Low=0, Moderate=1, High=2) of each shared species. None if <2 raters
    with overlap."""
    crit_by_id = {c.id: c for c in _criteria(project_id)}
    lvl = {'Low': 0, 'Moderate': 1, 'High': 2}
    # rater -> {species_norm: band_level}
    by_rater = {}
    for r in MCOReliabilityRating.query.filter_by(project_id=project_id).all():
        cri = _compute_cri(r.scores or {}, crit_by_id)
        b = _band(cri)
        if b is None:
            continue
        by_rater.setdefault(r.rater_id, {})[r.species_norm] = lvl[b]
    raters = list(by_rater)
    kappas = []
    for x, y in itertools.combinations(raters, 2):
        common = set(by_rater[x]) & set(by_rater[y])
        if len(common) < 2:
            continue
        a = [by_rater[x][s] for s in common]
        b = [by_rater[y][s] for s in common]
        kp = _weighted_kappa(a, b, 2)
        if kp is not None:
            kappas.append(kp)
    if not kappas:
        return None
    return round(sum(kappas) / len(kappas), 3)


def _concordance_flags(project_id):
    """Automatic K flag: per species, mismatch between a raw MCO count character
    and a binned count character (M05). Best-effort — skipped if the characters
    are absent. Returns {species_norm: True} for mismatches."""
    chars = CharacterDefinition.query.filter_by(project_id=project_id).all()
    def find(pred):
        return next((c for c in chars if pred((c.code or '').lower(),
                                               (c.name or '').lower())), None)
    raw = find(lambda code, name: 'count' in name and 'mco' in (code + name))
    binned = find(lambda code, name: code == 'm05')
    if not raw or not binned:
        return {}
    # structure_id -> species_norm
    sid_sp = {}
    for sp in Specimen.query.filter_by(project_id=project_id).all():
        n = _norm(sp.species_name)
        for st in sp.structures:
            sid_sp[st.id] = n
    def states(char):
        out = {}
        for v in CharacterValue.query.filter_by(character_id=char.id).all():
            n = sid_sp.get(v.structure_id)
            if n and v.state and v.state != '?':
                out.setdefault(n, set()).add(v.state)
        return out
    raw_s, bin_s = states(raw), states(binned)
    flags = {}
    for n in set(raw_s) & set(bin_s):
        # a mismatch is any species where either character resolves to >1 state
        # (internal inconsistency) — a lightweight re-examination trigger.
        if len(raw_s[n]) > 1 or len(bin_s[n]) > 1:
            flags[n] = True
    return flags


# ── page ──────────────────────────────────────────────────────────────────────

@reliability_bp.route('/project/<int:project_id>/reliability')
@login_required
def reliability_view(project_id):
    project = _project_or_403(project_id)
    _ensure_default_criteria(project_id)
    mco = _species_mco(project_id)
    return render_template('reliability/evaluate.html',
                           project=project,
                           n_species=len(mco))


# ── criteria CRUD ─────────────────────────────────────────────────────────────

@reliability_bp.route('/api/project/<int:project_id>/reliability/criteria')
@login_required
def list_criteria(project_id):
    _project_or_403(project_id)
    _ensure_default_criteria(project_id)
    return jsonify({'criteria': [_crit_dict(c) for c in _criteria(project_id)]})


@reliability_bp.route('/api/project/<int:project_id>/reliability/criteria',
                      methods=['POST'])
@login_required
def add_criterion(project_id):
    _project_or_403(project_id)
    d = request.get_json(silent=True) or {}
    code = (d.get('code') or '').strip()
    name = (d.get('name') or '').strip()
    if not code or not name:
        return jsonify({'error': 'code and name are required.'}), 400
    try:
        mx = max(1, int(d.get('max_score', 2)))
        w = max(0.0, float(d.get('weight', 1.0)))
    except (TypeError, ValueError):
        return jsonify({'error': 'max_score/weight must be numbers.'}), 400
    order = (db.session.query(db.func.coalesce(
        db.func.max(ReliabilityCriterion.display_order), -1))
        .filter_by(project_id=project_id).scalar()) + 1
    c = ReliabilityCriterion(
        project_id=project_id, code=code, name=name,
        rubric=(d.get('rubric') or '').strip(), max_score=mx, weight=w,
        display_order=order, active=True)
    db.session.add(c)
    db.session.commit()
    return jsonify({'status': 'ok', 'criterion': _crit_dict(c)})


@reliability_bp.route(
    '/api/project/<int:project_id>/reliability/criteria/<int:cid>',
    methods=['PATCH'])
@login_required
def edit_criterion(project_id, cid):
    _project_or_403(project_id)
    c = ReliabilityCriterion.query.filter_by(id=cid, project_id=project_id).first_or_404()
    d = request.get_json(silent=True) or {}
    if 'code' in d:
        c.code = (d['code'] or '').strip() or c.code
    if 'name' in d:
        c.name = (d['name'] or '').strip() or c.name
    if 'rubric' in d:
        c.rubric = (d['rubric'] or '').strip()
    if 'max_score' in d:
        try:
            c.max_score = max(1, int(d['max_score']))
        except (TypeError, ValueError):
            return jsonify({'error': 'max_score must be an integer.'}), 400
    if 'weight' in d:
        try:
            c.weight = max(0.0, float(d['weight']))
        except (TypeError, ValueError):
            return jsonify({'error': 'weight must be a number.'}), 400
    if 'active' in d:
        c.active = bool(d['active'])
    db.session.commit()
    return jsonify({'status': 'ok', 'criterion': _crit_dict(c)})


@reliability_bp.route(
    '/api/project/<int:project_id>/reliability/criteria/<int:cid>',
    methods=['DELETE'])
@login_required
def delete_criterion(project_id, cid):
    _project_or_403(project_id)
    c = ReliabilityCriterion.query.filter_by(id=cid, project_id=project_id).first_or_404()
    db.session.delete(c)
    db.session.commit()
    # Existing ratings keep their scores; CRI recomputes from remaining criteria.
    return jsonify({'status': 'ok'})


@reliability_bp.route(
    '/api/project/<int:project_id>/reliability/criteria/reorder',
    methods=['POST'])
@login_required
def reorder_criteria(project_id):
    _project_or_403(project_id)
    ids = (request.get_json(silent=True) or {}).get('order') or []
    by_id = {c.id: c for c in _criteria(project_id)}
    for i, cid in enumerate(ids):
        c = by_id.get(int(cid))
        if c:
            c.display_order = i
    db.session.commit()
    return jsonify({'status': 'ok'})


# ── blind evaluation ──────────────────────────────────────────────────────────

@reliability_bp.route('/api/project/<int:project_id>/reliability/queue')
@login_required
def queue(project_id):
    """Blind queue for the current rater: species with an MCO image not yet
    scored by this user. Species identity is not returned — only an opaque token
    and the image. Deterministic-random order per user."""
    _project_or_403(project_id)
    mco = _species_mco(project_id)
    done = {r.species_norm for r in MCOReliabilityRating.query.filter_by(
        project_id=project_id, rater_id=current_user.id).all()}
    pending = [n for n in mco if n not in done]
    # stable shuffle keyed by user so the order is fixed for this rater
    pending.sort(key=lambda n: (hash((current_user.id, n)) & 0xffffffff))
    items = [{'token': _tok(n), 'image_url': mco[n]['image_url']} for n in pending]
    return jsonify({
        'items': items,
        'total': len(mco),
        'done': len(done),
        'criteria': [_crit_dict(c) for c in _criteria(project_id, active_only=True)],
    })


@reliability_bp.route('/api/project/<int:project_id>/reliability/rate',
                      methods=['POST'])
@login_required
def rate(project_id):
    _project_or_403(project_id)
    d = request.get_json(silent=True) or {}
    species_norm = _untok(d.get('token'))
    if not species_norm:
        return jsonify({'error': 'Invalid item token.'}), 400
    mco = _species_mco(project_id)
    if species_norm not in mco:
        return jsonify({'error': 'Species has no MCO image to score.'}), 404

    crit_by_id = {c.id: c for c in _criteria(project_id, active_only=True)}
    raw = d.get('scores') or {}
    scores = {}
    for cid, sc in raw.items():
        c = crit_by_id.get(int(cid))
        if not c:
            continue
        try:
            v = int(sc)
        except (TypeError, ValueError):
            continue
        scores[str(c.id)] = max(0, min(c.max_score, v))
    if not scores:
        return jsonify({'error': 'Score at least one criterion.'}), 400

    cri = _compute_cri(scores, {c.id: c for c in crit_by_id.values()})
    row = MCOReliabilityRating.query.filter_by(
        project_id=project_id, rater_id=current_user.id,
        species_norm=species_norm).first()
    if not row:
        row = MCOReliabilityRating(project_id=project_id, rater_id=current_user.id,
                                   species_norm=species_norm)
        db.session.add(row)
    row.species_display = mco[species_norm]['display']
    row.structure_id = mco[species_norm]['structure_id']
    row.scores = scores
    row.cri = cri
    row.notes = (d.get('notes') or '').strip() or None
    db.session.commit()
    return jsonify({'status': 'ok', 'cri': round(cri, 3) if cri is not None else None,
                    'band': _band(cri)})


# ── results (revealed) ────────────────────────────────────────────────────────

@reliability_bp.route('/api/project/<int:project_id>/reliability/results')
@login_required
def results(project_id):
    _project_or_403(project_id)
    crit_by_id = {c.id: c for c in _criteria(project_id)}
    mco = _species_mco(project_id)
    flags = _concordance_flags(project_id)

    per_sp = {}   # species_norm -> list of cri
    raters_by_sp = {}
    for r in MCOReliabilityRating.query.filter_by(project_id=project_id).all():
        cri = _compute_cri(r.scores or {}, crit_by_id)
        if cri is None:
            continue
        per_sp.setdefault(r.species_norm, []).append(cri)
        raters_by_sp.setdefault(r.species_norm, set()).add(r.rater_id)

    rows = []
    for n, e in mco.items():
        vals = per_sp.get(n, [])
        if vals:
            m = sum(vals) / len(vals)
            sd = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5
            rows.append({'species': e['display'], 'image_url': e['image_url'],
                         'cri': round(m, 3), 'band': _band(m),
                         'n': len(vals), 'sd': round(sd, 3),
                         'k_flag': bool(flags.get(n))})
        else:
            rows.append({'species': e['display'], 'image_url': e['image_url'],
                         'cri': None, 'band': None, 'n': 0, 'sd': None,
                         'k_flag': bool(flags.get(n))})
    rows.sort(key=lambda x: (x['cri'] is None, -(x['cri'] or 0)))
    return jsonify({
        'rows': rows,
        'kappa': _band_kappa(project_id),
        'n_rated': sum(1 for r in rows if r['n'] > 0),
        'n_total': len(mco),
    })
