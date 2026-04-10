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
            '<img src="/static/diagrams/point_curvature_diagram.svg" alt="Point curvature angle diagram" '
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
        method = char_explanation.format(parts=part_str)
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


def _log(project_id, action):
    log = ActivityLog(
        project_id=project_id, user_id=current_user.id, action=action
    )
    db.session.add(log)
