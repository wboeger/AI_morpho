import numpy as np
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from app import db
from app.models import (
    Project, Specimen, Structure, CharacterDefinition, CharacterValue, ActivityLog
)
from app.characters import (
    GEOMETRIC_OPERATIONS, compute_geometric_value, extract_part_coords,
    compute_all_characters,
)
from config import Config

characters_bp = Blueprint('characters', __name__)


@characters_bp.route('/project/<int:project_id>/characters')
@login_required
def workshop(project_id):
    project = Project.query.get_or_404(project_id)
    structure_filter = request.args.get('structure_type', '')

    query = CharacterDefinition.query.filter_by(project_id=project_id)
    if structure_filter:
        query = query.filter_by(structure_type=structure_filter)

    from sqlalchemy import func
    characters = query.order_by(
        func.coalesce(CharacterDefinition.display_order, 999999),
        CharacterDefinition.code
    ).all()

    return render_template('characters/workshop.html',
                           project=project, characters=characters,
                           structure_filter=structure_filter,
                           structure_types=['hook', 'anchor', 'superficial_bar', 'deep_bar', 'mco'])


@characters_bp.route('/project/<int:project_id>/characters/new', methods=['GET', 'POST'])
@login_required
def new_character(project_id):
    project = Project.query.get_or_404(project_id)

    if request.method == 'POST':
        data = request.form
        computation_type = data.get('computation_type', 'manual')
        structure_type = data.get('structure_type')
        code = data.get('code', '').strip()
        name = data.get('name', '').strip()

        if not code or not name or not structure_type:
            flash('Code, name, and structure type are required.', 'error')
            return redirect(url_for('characters.new_character', project_id=project_id))

        # Check code uniqueness
        if CharacterDefinition.query.filter_by(project_id=project_id, code=code).first():
            flash(f'Character code "{code}" already exists in this project.', 'error')
            return redirect(url_for('characters.new_character', project_id=project_id))

        # Parse states from form
        states = _parse_states_from_form(data)
        deps = _parse_deps_from_form(data)

        parts = []
        if data.get('parts_involved'):
            parts = [p.strip() for p in data.get('parts_involved').split(',') if p.strip()]

        char = CharacterDefinition(
            project_id=project_id,
            code=code,
            name=name,
            description=data.get('description', ''),
            structure_type=structure_type,
            computation_type=computation_type,
            parts_involved=parts,
            geometric_operation=data.get('geometric_operation') if computation_type == 'geometric' else None,
            formula=data.get('formula', '') if computation_type == 'geometric' else None,
            states_json=states,
            dependencies_json=deps,
            active=True,
            created_by=current_user.id,
            history_json=[{
                'user': current_user.id,
                'action': 'created',
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'details': f'Created by {current_user.username}',
            }],
        )
        db.session.add(char)
        db.session.flush()

        # Compute for all existing structures of this type
        _recompute_character(char, project_id)

        _log(project_id, f'Created character {code}: {name}')
        db.session.commit()
        flash(f'Character {code} created. Matrix updated.', 'success')
        return redirect(url_for('characters.workshop', project_id=project_id))

    operations = GEOMETRIC_OPERATIONS
    return render_template('characters/new_character.html',
                           project=project, operations=operations,
                           structure_parts=Config.STRUCTURE_PARTS)


@characters_bp.route('/project/<int:project_id>/characters/<int:char_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_character(project_id, char_id):
    project = Project.query.get_or_404(project_id)
    char = CharacterDefinition.query.get_or_404(char_id)

    if request.method == 'POST':
        data = request.form
        char.name = data.get('name', char.name).strip()
        char.description = data.get('description', char.description)

        new_states = _parse_states_from_form(data)
        if new_states:
            char.states_json = new_states

        new_deps = _parse_deps_from_form(data)
        char.dependencies_json = new_deps

        if data.get('parts_involved'):
            char.parts_involved = [p.strip() for p in data.get('parts_involved').split(',') if p.strip()]
        if data.get('geometric_operation'):
            char.geometric_operation = data.get('geometric_operation')
        if data.get('formula'):
            char.formula = data.get('formula')

        # Log modification
        history = char.history_json or []
        history.append({
            'user': current_user.id,
            'action': 'modified',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'details': f'Modified by {current_user.username}',
        })
        char.history_json = history

        # Recompute geometric characters with new thresholds
        if char.computation_type == 'geometric':
            _recompute_character(char, project_id)

        _log(project_id, f'Modified character {char.code}')
        db.session.commit()
        flash(f'Character {char.code} updated.', 'success')
        return redirect(url_for('characters.workshop', project_id=project_id))

    # Gather specimen structures for the reference panel
    structures = (Structure.query
                  .join(Specimen)
                  .filter(Specimen.project_id == project_id,
                          Structure.structure_type == char.structure_type)
                  .all())
    entries = []
    for struct in structures:
        specimen = Specimen.query.get(struct.specimen_id)
        val = CharacterValue.query.filter_by(
            structure_id=struct.id, character_id=char.id
        ).first()
        # Collect all structure types for this specimen
        alt = {}
        for st in Structure.query.filter_by(specimen_id=specimen.id).all():
            alt[st.structure_type] = {
                'image_url': f'/uploads/{st.image_path}' if st.image_path else None,
                'landmarks': st.landmarks_json,
                'boundaries': st.boundary_json,
            }
        entries.append({
            'structure_id': struct.id,
            'species': specimen.species_name,
            'state': val.state if val else '?',
            'raw_value': val.raw_value if val else None,
            'alt': alt,
        })
    # Sort by raw_value for geometric, state for manual
    if char.computation_type == 'geometric':
        entries.sort(key=lambda e: e['raw_value'] if e['raw_value'] is not None else 0)
    else:
        entries.sort(key=lambda e: e['state'] or '?')

    parts = Config.STRUCTURE_PARTS.get(char.structure_type, [])
    available_types = sorted({st.structure_type for st in
        Structure.query.join(Specimen).filter(Specimen.project_id == project_id).all()})

    explanation = _build_measurement_explanation(char)

    return render_template('characters/edit_character.html',
                           project=project, char=char,
                           operations=GEOMETRIC_OPERATIONS,
                           structure_parts=Config.STRUCTURE_PARTS,
                           entries=entries, parts=parts,
                           available_types=available_types,
                           explanation=explanation)


@characters_bp.route('/project/<int:project_id>/characters/print')
@login_required
def print_characters(project_id):
    project = Project.query.get_or_404(project_id)
    from sqlalchemy import func
    characters = CharacterDefinition.query.filter_by(
        project_id=project_id, active=True
    ).order_by(
        func.coalesce(CharacterDefinition.display_order, 999999),
        CharacterDefinition.code
    ).all()
    # Build explanation for each character
    char_data = []
    for c in characters:
        char_data.append({'char': c, 'explanation': _build_measurement_explanation(c)})
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    return render_template('characters/print_characters.html',
                           project=project, char_data=char_data, now=now)


@characters_bp.route('/api/project/<int:project_id>/characters/reorder', methods=['POST'])
@login_required
def reorder_characters(project_id):
    """Persist a new display order for a list of character IDs."""
    Project.query.get_or_404(project_id)
    data  = request.get_json() or {}
    order = data.get('order', [])
    if data.get('reset'):
        CharacterDefinition.query.filter_by(project_id=project_id).update({'display_order': None})
        db.session.commit()
        return jsonify({'status': 'ok', 'reset': True})
    if not order:
        return jsonify({'error': 'order list required'}), 400
    for i, char_id in enumerate(order):
        char = CharacterDefinition.query.filter_by(id=char_id, project_id=project_id).first()
        if char:
            char.display_order = i
    db.session.commit()
    return jsonify({'status': 'ok', 'saved': len(order)})


@characters_bp.route('/api/project/<int:project_id>/characters/<int:char_id>/toggle', methods=['POST'])
@login_required
def toggle_character(project_id, char_id):
    char = CharacterDefinition.query.get_or_404(char_id)
    char.active = not char.active
    db.session.commit()
    return jsonify({'active': char.active})


@characters_bp.route('/api/project/<int:project_id>/characters/<int:char_id>/delete', methods=['DELETE'])
@login_required
def delete_character(project_id, char_id):
    char = CharacterDefinition.query.get_or_404(char_id)
    _log(project_id, f'Deleted character {char.code}: {char.name}')
    db.session.delete(char)
    db.session.commit()
    return jsonify({'status': 'deleted'})


@characters_bp.route('/api/project/<int:project_id>/characters/<int:char_id>/distribution', methods=['GET'])
@login_required
def character_distribution(project_id, char_id):
    """Compute and return the distribution of raw values for a geometric character."""
    char = CharacterDefinition.query.get_or_404(char_id)

    if char.computation_type != 'geometric':
        # For manual characters, return state counts
        values = CharacterValue.query.filter_by(character_id=char_id).all()
        state_counts = {}
        for v in values:
            s = v.state or '?'
            state_counts[s] = state_counts.get(s, 0) + 1
        return jsonify({'type': 'manual', 'state_counts': state_counts})

    # Geometric: compute raw values for all relevant structures
    structures = (Structure.query
                  .join(Specimen)
                  .filter(
                      Specimen.project_id == project_id,
                      Structure.structure_type == char.structure_type,
                      Structure.landmarks_confirmed == True,
                      Structure.boundary_confirmed == True,
                  ).all())

    raw_values = []
    labels = []
    for struct in structures:
        specimen = Specimen.query.get(struct.specimen_id)
        parts_coords = {}
        for part_name in (char.parts_involved or []):
            coords = extract_part_coords(struct.landmarks_json, struct.boundary_json, part_name)
            if len(coords) > 0:
                parts_coords[part_name] = coords

        if parts_coords:
            try:
                val = compute_geometric_value(
                    char.geometric_operation, parts_coords,
                    np.array(struct.landmarks_json), char.formula,
                    boundary=struct.boundary_json
                )
                if val is not None and not np.isnan(val) and not np.isinf(val):
                    raw_values.append(float(val))
                    labels.append(specimen.species_name)
            except Exception:
                pass

    return jsonify({
        'type': 'geometric',
        'char_name': char.name,
        'values': raw_values,
        'labels': labels,
        'states': char.states_json or [],
        'stats': {
            'min': float(np.min(raw_values)) if raw_values else 0,
            'max': float(np.max(raw_values)) if raw_values else 0,
            'mean': float(np.mean(raw_values)) if raw_values else 0,
            'median': float(np.median(raw_values)) if raw_values else 0,
        }
    })


def _jenks_breaks(values: list, k: int):
    """Fisher-Jenks optimal 1-D classification (pure numpy, O(n²k)).

    Returns (breaks, gvf) where breaks is a list of k-1 boundary values
    and gvf is the Goodness of Variance Fit (0–1; higher = better separation).
    """
    v = np.sort(np.array(values, dtype=float))
    n = len(v)
    if k >= n:
        return [float((v[i] + v[i + 1]) / 2) for i in range(n - 1)], 1.0

    # DP tables: (n+1) × (k+1), 1-indexed on data
    INF = float('inf')
    # wcss[i][j] = min within-class SS for first i points into j classes
    # lower[i][j] = starting index (1-based) of last class
    wcss  = [[INF] * (k + 1) for _ in range(n + 1)]
    lower = [[0]   * (k + 1) for _ in range(n + 1)]

    # Base: 1 class
    cumsum  = np.cumsum(v)
    cumsum2 = np.cumsum(v ** 2)
    for i in range(1, n + 1):
        s  = cumsum[i - 1]
        s2 = cumsum2[i - 1]
        wcss[i][1]  = s2 - s * s / i
        lower[i][1] = 1

    for j in range(2, k + 1):
        for i in range(j, n + 1):
            for l in range(j - 1, i):
                seg_n  = i - l
                seg_s  = cumsum[i - 1] - (cumsum[l - 1] if l > 0 else 0)
                seg_s2 = cumsum2[i - 1] - (cumsum2[l - 1] if l > 0 else 0)
                seg_v  = seg_s2 - seg_s * seg_s / seg_n
                cand   = wcss[l][j - 1] + seg_v
                if cand < wcss[i][j]:
                    wcss[i][j]  = cand
                    lower[i][j] = l + 1  # first 1-based index of this last class

    # Traceback class boundaries
    klass = [0] * (k + 1)
    klass[k] = n
    for j in range(k, 1, -1):
        klass[j - 1] = lower[klass[j]][j] - 1

    # Breaks = midpoints between last element of one class and first of next
    breaks = []
    for j in range(1, k):
        idx = klass[j]        # last 0-based index of class j
        breaks.append(float((v[idx - 1] + v[idx]) / 2.0))

    total_var = float(np.var(v) * n)
    gvf = (total_var - wcss[n][k]) / total_var if total_var > 1e-12 else 1.0
    return breaks, float(gvf)


@characters_bp.route('/api/project/<int:project_id>/characters/<int:char_id>/suggest_thresholds')
@login_required
def suggest_thresholds(project_id, char_id):
    """Return Jenks natural-break suggestions for 2, 3, and 4 classes."""
    char = CharacterDefinition.query.get_or_404(char_id)
    values = [cv.raw_value for cv in
              CharacterValue.query.filter_by(character_id=char_id).all()
              if cv.raw_value is not None]
    if len(values) < 4:
        return jsonify({'error': 'Not enough data (need ≥ 4 measured values).'}), 400

    v = np.array(values, dtype=float)
    n_bins = max(8, min(25, len(values) // 2))
    counts, edges = np.histogram(v, bins=n_bins)

    suggestions = {}
    for k in range(2, 5):
        if k <= len(values):
            breaks, gvf = _jenks_breaks(values, k)
            suggestions[str(k)] = {
                'breaks': [round(b, 4) for b in breaks],
                'gvf': round(gvf, 4),
            }

    return jsonify({
        'n': len(values),
        'min':  round(float(v.min()), 4),
        'max':  round(float(v.max()), 4),
        'mean': round(float(v.mean()), 4),
        'std':  round(float(v.std()),  4),
        'histogram': {
            'counts': counts.tolist(),
            'edges':  [round(float(e), 4) for e in edges],
        },
        'suggestions': suggestions,
    })


def _recompute_character(char_def, project_id):
    """Recompute a single character for all relevant structures."""
    structures = (Structure.query
                  .join(Specimen)
                  .filter(
                      Specimen.project_id == project_id,
                      Structure.structure_type == char_def.structure_type,
                  ).all())

    for struct in structures:
        if char_def.computation_type == 'geometric':
            from app.characters import assign_character
            result = assign_character(struct, char_def, project_id)

            existing = CharacterValue.query.filter_by(
                structure_id=struct.id, character_id=char_def.id
            ).first()

            # Preserve manual overrides
            if existing and existing.override_by:
                continue

            if existing:
                existing.raw_value = result['raw_value']
                existing.state = result['state']
                existing.confidence = result['confidence']
                existing.auto_assigned = result['auto_assigned']
            else:
                cv = CharacterValue(
                    structure_id=struct.id, character_id=char_def.id, **result
                )
                db.session.add(cv)
        else:
            # Manual — just ensure placeholder exists
            existing = CharacterValue.query.filter_by(
                structure_id=struct.id, character_id=char_def.id
            ).first()
            if not existing:
                cv = CharacterValue(
                    structure_id=struct.id, character_id=char_def.id,
                    state='?', confidence=0.0, auto_assigned=False
                )
                db.session.add(cv)


def _parse_states_from_form(data):
    """Parse states from form data."""
    states = []
    i = 0
    while f'state_code_{i}' in data:
        code = data.get(f'state_code_{i}', '').strip()
        name = data.get(f'state_name_{i}', '').strip()
        if code and name:
            state = {'code': code, 'name': name}
            desc = data.get(f'state_desc_{i}', '').strip()
            if desc:
                state['description'] = desc
            t_min = data.get(f'state_threshold_min_{i}', '').strip()
            t_max = data.get(f'state_threshold_max_{i}', '').strip()
            state['threshold_min'] = float(t_min) if t_min else None
            state['threshold_max'] = float(t_max) if t_max else None
            states.append(state)
        i += 1
    return states


def _parse_deps_from_form(data):
    """Parse dependencies from form data."""
    deps = []
    i = 0
    while f'dep_char_{i}' in data:
        dep_char = data.get(f'dep_char_{i}', '').strip()
        dep_state = data.get(f'dep_state_{i}', '').strip()
        if dep_char and dep_state:
            deps.append({
                'if_character': dep_char,
                'if_state': dep_state,
                'then': 'inapplicable',
            })
        i += 1
    return deps


def _build_measurement_explanation(char):
    """Build a human-readable explanation of how this character is measured."""
    parts = char.parts_involved or []
    op = char.geometric_operation
    states = char.states_json or []

    if char.computation_type == 'manual':
        lines = [
            'This is a <strong>manual character</strong> — states are assigned by the taxonomist through visual inspection.',
        ]
        if states:
            lines.append('States are defined as:')
            for s in states:
                desc = f': {s["description"]}' if s.get('description') else ''
                lines.append(f'&nbsp;&nbsp;&bull; <strong>{s["code"]}</strong> = {s["name"]}{desc}')
        return '<br>'.join(lines)

    # Geometric character
    part_str = ' and '.join(f'<strong>{p}</strong>' for p in parts)

    _OP_EXPLANATIONS = {
        'ratio_arc_length': (
            'Computes the <em>ratio of arc lengths</em> between {parts}. '
            'The arc length of each part is measured along its pseudolandmark coordinates '
            '(sum of Euclidean distances between consecutive points). The ratio indicates '
            'relative size — values near 1.0 mean equal length; values &gt;1 mean the first part is longer.'
        ),
        'sinuosity': (
            'Measures the <em>sinuosity</em> of {parts} — the ratio of arc length (path along the curve) '
            'to chord length (straight-line distance between endpoints). Values near 1.0 indicate a nearly '
            'straight part; higher values indicate more curvature or waviness.'
        ),
        'mean_curvature': (
            'Computes the <em>mean curvature</em> of {parts}. At each interior landmark point, local curvature '
            'is estimated from the angle formed by the vectors to its neighbors divided by the arc-length step. '
            'The mean of absolute curvature values captures overall bending — higher values mean more curved.'
        ),
        'max_curvature': (
            'Computes the <em>maximum curvature</em> of {parts}. Local curvature is estimated at each interior '
            'point (angle between adjacent vectors / arc-length step). The maximum value captures the sharpest '
            'bend — useful for detecting abrupt turns or hooks.'
        ),
        'junction_angle': (
            'Measures the <em>angle at the junction</em> between {parts} — '
            'the change in direction where one part ends and the next begins, '
            'using direction vectors averaged over the terminal landmarks of each part.'
        ),
        'direction_angle': (
            'Measures the <em>direction angle</em> between {parts} — '
            'the angle between the average direction vectors at the ends of the two parts (0°–180°).'
        ),
        'relative_position': (
            'Measures the <em>relative vertical position</em> between {parts}. The vertical displacement between '
            'the tips (first points) of the two parts is normalized by total extent. Positive values mean the '
            'first part tip is lower; negative means higher.'
        ),
        'presence_threshold': (
            'Measures <em>presence/absence</em> of {parts} by computing the fraction of the total outline '
            'arc length occupied by this part. If the fraction exceeds the threshold, the part is considered present.'
        ),
        'sinuosity_with_direction': (
            'Measures <em>signed sinuosity</em> of {parts}. Like sinuosity (arc/chord ratio), but also '
            'determines whether the curve bows outward (positive) or inward (negative) using the cross product '
            'of the chord with the midpoint displacement.'
        ),
        'angle_between_parts': (
            'Measures the <em>angle between {parts}</em> using their direction vectors at the start of each part (0°–180°).'
        ),
        'fork_angle': (
            'Measures the <em>deviation angle</em> between {parts} — how much the two parts depart '
            'from a straight continuation of each other (0° = perfectly aligned, larger values = wider fork). '
            'The proximal half of each part\'s central axis is used to fit a midline; '
            'the midlines are extended to their intersection, and the deviation is measured there.'
        ),
    }

    # Character-specific overrides (replaces the generic operation description)
    _CHAR_EXPLANATIONS = {
        'A02': (
            'Measures the <em>curvature of the point</em> — how sharply the point departs from the shaft axis. '
            '<br><br>'
            'A best-fit midline is computed for the <strong>middle portion</strong> of the shaft central axis '
            '(skipping the curved ends near the root and point junction) and for the proximal half of the '
            'point central axis (near the junction). The angle between these two midlines gives the deviation: '
            '<strong>0°</strong> = point continues the shaft in a straight line; '
            '<strong>90°</strong> = point is perpendicular to the shaft (classic hook); '
            '<strong>&gt;90°</strong> = point recurves back past perpendicular.'
            '<br><br>'
            '<img src="/api/project/{project_id}/character/A02/diagram.svg" alt="Point curvature angle diagram" '
            'style="max-width:600px; width:100%; display:block; margin:0.5rem auto; border:1px solid #ddd; border-radius:4px; padding:8px;">'
        ),
        'A09': (
            'Measures the <em>angle between the Shaft and SuperficialRoot</em> — specifically, how much the '
            'superficial root departs from a straight continuation of the shaft axis. '
            '<br><br>'
            'A best-fit midline is computed for the proximal half of each part\'s central axis. '
            'The two midlines are extended to their intersection point, and the deviation angle between them '
            'is measured there (0° = the root continues the shaft in a straight line; '
            'larger values = the root departs more sharply from the shaft).'
            '<br><br>'
            '<img src="/static/diagrams/fork_angle_diagram.svg" alt="Shaft–superficial root deviation angle diagram" '
            'style="max-width:480px; display:block; margin:0.5rem auto; border:1px solid #ddd; border-radius:4px; padding:8px;">'
        ),
    }

    char_explanation = _CHAR_EXPLANATIONS.get(char.code)
    if char_explanation:
        method = char_explanation.format(parts=part_str, project_id=char.project_id)
    else:
        method = _OP_EXPLANATIONS.get(op, 'Custom geometric computation on {parts}.').format(parts=part_str)

    lines = [f'<strong>Measurement method:</strong> {method}']

    # Procrustes note
    lines.append(
        '<strong>Alignment:</strong> All specimens are aligned via Generalized Procrustes Analysis (GPA) — '
        'centered, scaled to unit centroid size, and iteratively rotated — before measurement. '
        'This removes differences in position, size, and orientation, isolating shape variation.'
    )

    # State mapping
    if states:
        lines.append('<strong>State assignment:</strong> The raw value is mapped to discrete states using thresholds:')
        for s in states:
            t_min = s.get('threshold_min')
            t_max = s.get('threshold_max')
            if t_min is not None and t_max is not None:
                rng = f'{t_min} – {t_max}'
            elif t_min is not None:
                rng = f'&ge; {t_min}'
            elif t_max is not None:
                rng = f'&lt; {t_max}'
            else:
                rng = 'no thresholds'
            desc = f' — {s["description"]}' if s.get('description') else ''
            lines.append(f'&nbsp;&nbsp;&bull; <strong>{s["code"]}</strong> ({s["name"]}): {rng}{desc}')
        lines.append('Values between thresholds are assigned with proportional confidence; the best-matching state is chosen.')

    return '<br>'.join(lines)


@characters_bp.route('/api/project/<int:project_id>/character/A02/diagram.svg')
@login_required
def a02_diagram_svg(project_id):
    """Generate the A02 (point curvature) diagram SVG from actual specimen data.

    Queries one structure per state (0, 1, 2), renders their real landmark
    outlines with the computed shaft/point midlines and deviation angle arc.
    Falls back to a placeholder panel when no example exists for a state.
    """
    from flask import Response
    from app.geometry import _central_axis, _midline_vector, angle_between_vectors

    char = CharacterDefinition.query.filter_by(project_id=project_id, code='A02').first()

    # Pick best example per state: prefer auto-assigned, highest confidence
    examples = {}
    if char:
        for state_code in ('0', '1', '2'):
            cv = (CharacterValue.query
                  .filter_by(character_id=char.id, state=state_code)
                  .join(Structure, CharacterValue.structure_id == Structure.id)
                  .filter(
                      Structure.landmarks_json.isnot(None),
                      Structure.boundary_json.isnot(None),
                  )
                  .order_by(CharacterValue.confidence.desc())
                  .first())
            if cv:
                examples[state_code] = cv

    # Layout
    PANEL_W, PANEL_H = 182, 290
    MARGIN, GAP = 8, 8
    CAPTION_H, LEGEND_H = 52, 32
    W = 3 * PANEL_W + 2 * GAP + 2 * MARGIN
    H = MARGIN + PANEL_H + CAPTION_H + LEGEND_H

    state_labels = {
        '0': ('State 0 — slightly curved', 'acute ext. angle &lt; 30°'),
        '1': ('State 1 — moderately curved', '30° – 60°'),
        '2': ('State 2 — strongly curved', 'acute ext. angle &gt; 60°'),
    }

    out = []
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
    out.append('<rect width="100%" height="100%" fill="white"/>')
    out.append('''<defs><style>
.ol{fill:#dde8f4;stroke:#3a6ea8;stroke-width:1.2;}
.ph{fill:rgba(26,110,42,0.22);stroke:#1a6e2a;stroke-width:1.5;fill:none;}
.sh{fill:rgba(160,48,32,0.18);stroke:#b03020;stroke-width:1.5;fill:none;}
.sm{stroke:#b03020;stroke-width:1.8;stroke-dasharray:6,3;fill:none;}
.pm{stroke:#1a6e2a;stroke-width:1.8;stroke-dasharray:6,3;fill:none;}
.arc{stroke:#6020a0;stroke-width:2.2;fill:none;}
.jd{fill:#6020a0;}
.bk{stroke:#cc6600;stroke-width:1.4;fill:none;}
.alb{font-family:Arial,sans-serif;font-size:12px;font-weight:bold;fill:#6020a0;text-anchor:middle;}
.slb{font-family:Arial,sans-serif;font-size:11px;fill:#222;text-anchor:middle;}
.tlb{font-family:Arial,sans-serif;font-size:9px;fill:#555;text-anchor:middle;}
.sp{font-family:Arial,sans-serif;font-size:9px;font-style:italic;fill:#333;text-anchor:middle;}
.leg{font-family:Arial,sans-serif;font-size:9px;fill:#333;}
</style></defs>''')

    for i, state_code in enumerate(('0', '1', '2')):
        px = MARGIN + i * (PANEL_W + GAP)
        py = MARGIN
        pcx = px + PANEL_W / 2
        lbl, thresh = state_labels[state_code]

        cv = examples.get(state_code)
        if cv is None:
            # Placeholder
            out.append(f'<rect x="{px}" y="{py}" width="{PANEL_W}" height="{PANEL_H}" '
                       f'fill="#f6f6f6" stroke="#ccc" stroke-width="1" rx="4"/>')
            out.append(f'<text x="{pcx:.1f}" y="{py + PANEL_H/2:.1f}" '
                       f'text-anchor="middle" font-family="Arial" font-size="10" fill="#aaa">'
                       f'No specimen in this state</text>')
        else:
            structure = Structure.query.get(cv.structure_id)
            specimen  = Specimen.query.get(structure.specimen_id)
            lm        = np.array(structure.landmarks_json)
            boundary  = structure.boundary_json or {}
            raw_val   = cv.raw_value if cv.raw_value is not None else 0.0

            point_idx = boundary.get('Point', [])
            shaft_idx = boundary.get('Shaft', [])

            # Compute junction, axes (mirrors point_curvature_angle logic)
            fork_point = v1 = v2 = None
            shaft_mid_pts = None
            axis_a_full = axis_b_full = None
            if point_idx and shaft_idx:
                a_ends = [lm[point_idx[0]], lm[point_idx[-1]]]
                b_ends = [lm[shaft_idx[0]], lm[shaft_idx[-1]]]
                min_d = float('inf')
                fp = (a_ends[0] + b_ends[0]) / 2.0
                for ae in a_ends:
                    for be in b_ends:
                        d = np.linalg.norm(ae - be)
                        if d < min_d:
                            min_d = d
                            fp = (ae + be) / 2.0
                fork_point = fp

                axis_a = _central_axis(lm, point_idx, ref_point=fork_point)
                axis_a_full = axis_a
                if len(axis_a) >= 2:
                    if np.linalg.norm(axis_a[0] - fork_point) > np.linalg.norm(axis_a[-1] - fork_point):
                        axis_a = axis_a[::-1]
                        axis_a_full = axis_a
                    # Direct line from mid-base (fork end) to tip
                    v1_raw = axis_a[-1] - axis_a[0]
                    nrm1 = np.linalg.norm(v1_raw)
                    if nrm1 > 1e-10:
                        v1 = v1_raw / nrm1

                axis_b = _central_axis(lm, shaft_idx, ref_point=fork_point)
                axis_b_full = axis_b
                if len(axis_b) >= 4:
                    if np.linalg.norm(axis_b[0] - fork_point) > np.linalg.norm(axis_b[-1] - fork_point):
                        axis_b = axis_b[::-1]
                        axis_b_full = axis_b
                    n = len(axis_b)
                    s, e = max(1, n // 3), min(n - 1, 2 * n // 3)
                    shaft_mid_pts = axis_b[s:e]
                    v2 = _midline_vector(shaft_mid_pts)

                    # Ensure v2 points FROM junction TOWARD root (away from point).
                    shaft_centroid = lm[shaft_idx].mean(axis=0)
                    if np.dot(v2, shaft_centroid - fork_point) < 0:
                        v2 = -v2

            # Coordinate normalisation: fit landmarks into panel with padding
            pad = 14
            mn, mx = lm.min(axis=0), lm.max(axis=0)
            span = mx - mn
            scale = min((PANEL_W - 2 * pad) / max(span[0], 1),
                        (PANEL_H - 2 * pad) / max(span[1], 1))
            orig_ctr = (mn + mx) / 2.0

            def svg_pt(pt):
                pt = np.array(pt)
                x = (pt[0] - orig_ctr[0]) * scale + pcx
                y = (pt[1] - orig_ctr[1]) * scale + py + PANEL_H / 2
                return float(x), float(y)

            # Full outline
            pts_str = ' '.join(f'{svg_pt(p)[0]:.1f},{svg_pt(p)[1]:.1f}' for p in lm)
            out.append(f'<polygon class="ol" points="{pts_str}"/>')

            # Highlight parts
            if point_idx:
                s = ' '.join(f'{svg_pt(lm[j])[0]:.1f},{svg_pt(lm[j])[1]:.1f}' for j in point_idx)
                out.append(f'<polyline class="ph" points="{s}"/>')
            if shaft_idx:
                s = ' '.join(f'{svg_pt(lm[j])[0]:.1f},{svg_pt(lm[j])[1]:.1f}' for j in shaft_idx)
                out.append(f'<polyline class="sh" points="{s}"/>')

            if v1 is not None and v2 is not None and fork_point is not None:
                jx, jy = svg_pt(fork_point)

                # ── Shaft midline: span the full shaft axis extent ─────────────
                if axis_b_full is not None and len(axis_b_full) >= 2:
                    projs = [np.dot(p - fork_point, v2) for p in axis_b_full]
                    sf_min, sf_max = min(projs), max(projs)
                    sf_ext = (sf_max - sf_min) * 0.08 + 10 / scale
                    s0 = svg_pt(fork_point + v2 * (sf_min - sf_ext))
                    s1 = svg_pt(fork_point + v2 * (sf_max + sf_ext))
                else:
                    reach = PANEL_H * 0.5
                    s0 = (jx - v2[0]*reach*0.3, jy - v2[1]*reach*0.3)
                    s1 = (jx + v2[0]*reach*0.7, jy + v2[1]*reach*0.7)
                out.append(f'<line class="sm" x1="{s0[0]:.1f}" y1="{s0[1]:.1f}" '
                           f'x2="{s1[0]:.1f}" y2="{s1[1]:.1f}"/>')

                # ── Point midline: span the full point axis, cross past junction ─
                if axis_a_full is not None and len(axis_a_full) >= 2:
                    projs = [np.dot(p - fork_point, v1) for p in axis_a_full]
                    pf_max = max(projs)
                    p_ext = pf_max * 0.20 + 10 / scale   # extend past junction
                    p0 = svg_pt(fork_point - v1 * p_ext)
                    p1 = svg_pt(fork_point + v1 * (pf_max + 10 / scale))
                else:
                    reach = PANEL_H * 0.5
                    p0 = (jx - v1[0]*reach*0.25, jy - v1[1]*reach*0.25)
                    p1 = (jx + v1[0]*reach*0.75, jy + v1[1]*reach*0.75)
                out.append(f'<line class="pm" x1="{p0[0]:.1f}" y1="{p0[1]:.1f}" '
                           f'x2="{p1[0]:.1f}" y2="{p1[1]:.1f}"/>')

                # Junction dot
                out.append(f'<circle class="jd" cx="{jx:.1f}" cy="{jy:.1f}" r="3"/>')

                # Middle-shaft bracket (orange)
                if shaft_mid_pts is not None and len(shaft_mid_pts) >= 2:
                    ms0x, ms0y = svg_pt(shaft_mid_pts[0])
                    ms1x, ms1y = svg_pt(shaft_mid_pts[-1])
                    perp = np.array([-v2[1], v2[0]])
                    off = 9
                    bx0, by0 = ms0x + perp[0]*off, ms0y + perp[1]*off
                    bx1, by1 = ms1x + perp[0]*off, ms1y + perp[1]*off
                    out.append(f'<line class="bk" x1="{bx0:.1f}" y1="{by0:.1f}" x2="{bx1:.1f}" y2="{by1:.1f}"/>')
                    for bx, by in ((bx0, by0), (bx1, by1)):
                        out.append(f'<line class="bk" x1="{bx:.1f}" y1="{by:.1f}" '
                                   f'x2="{bx - perp[0]*4:.1f}" y2="{by - perp[1]*4:.1f}"/>')

                # ── Angle arc (Acute Exterior Angle) ─────────────────────────────
                # Always draw the ACUTE arc at the external junction.
                # bend < 90°: arc from shaft-continuation (-v2) to tip (v1).
                # bend ≥ 90°: the acute angle is between +v2 and +v1 (both
                #   point away from junction on the same side for recurved hooks).
                r = 24
                bend_live = 180.0 - angle_between_vectors(v1, v2)
                if bend_live > 90.0:
                    # Acute angle = 180 - bend, arc from +v2 to +v1
                    asx, asy = jx + v2[0]*r, jy + v2[1]*r
                    aex, aey = jx + v1[0]*r, jy + v1[1]*r
                    cross_z  = v2[0]*v1[1] - v2[1]*v1[0]
                    bv = v1 + v2   # bisector between +v2 and +v1
                    display_angle = 180.0 - bend_live
                else:
                    # Acute angle = bend, arc from -v2 to +v1
                    asx, asy = jx - v2[0]*r, jy - v2[1]*r
                    aex, aey = jx + v1[0]*r, jy + v1[1]*r
                    cross_z  = (-v2[0])*v1[1] - (-v2[1])*v1[0]
                    bv = v1 - v2   # bisector between -v2 and +v1
                    display_angle = bend_live
                sweep = 1 if cross_z > 0 else 0
                large = 0
                out.append(f'<path class="arc" d="M {asx:.1f},{asy:.1f} '
                           f'A {r},{r} 0 {large},{sweep} {aex:.1f},{aey:.1f}"/>')

                bn = np.linalg.norm(bv)
                bv = bv / bn if bn > 1e-6 else np.array([1.0, 0.0])
                lx, ly = jx + bv[0]*(r + 13), jy + bv[1]*(r + 13)
                out.append(f'<text class="alb" x="{lx:.1f}" y="{ly + 4:.1f}">{display_angle:.0f}°</text>')

            # Species name
            sp_name = specimen.species_name if specimen else '?'
            out.append(f'<text class="sp" x="{pcx:.1f}" y="{py + PANEL_H + 13:.1f}">{sp_name}</text>')

        # State caption
        cap_y = py + PANEL_H + 27
        out.append(f'<text class="slb" x="{pcx:.1f}" y="{cap_y:.1f}">{lbl}</text>')
        out.append(f'<text class="tlb" x="{pcx:.1f}" y="{cap_y + 13:.1f}">{thresh}</text>')

    # Legend
    ly = MARGIN + PANEL_H + CAPTION_H + 10
    out.append(f'<line x1="10" y1="{ly}" x2="32" y2="{ly}" stroke="#1a6e2a" stroke-width="1.5" stroke-dasharray="5,2"/>')
    out.append(f'<text class="leg" x="36" y="{ly+4}">point midline</text>')
    out.append(f'<line x1="130" y1="{ly}" x2="152" y2="{ly}" stroke="#b03020" stroke-width="1.5" stroke-dasharray="5,2"/>')
    out.append(f'<text class="leg" x="156" y="{ly+4}">shaft midline (middle portion)</text>')
    out.append(f'<circle cx="348" cy="{ly}" r="4" fill="#6020a0"/>')
    out.append(f'<text class="leg" x="355" y="{ly+4}">junction &amp; angle</text>')
    out.append(f'<line x1="445" y1="{ly}" x2="460" y2="{ly}" stroke="#cc6600" stroke-width="2"/>')
    out.append(f'<text class="leg" x="463" y="{ly+4}">middle shaft region</text>')

    out.append('</svg>')
    return Response('\n'.join(out), mimetype='image/svg+xml',
                   headers={'Cache-Control': 'no-cache'})


def _log(project_id, action):
    log = ActivityLog(
        project_id=project_id, user_id=current_user.id, action=action
    )
    db.session.add(log)
