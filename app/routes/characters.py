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

    characters = query.order_by(CharacterDefinition.code).all()

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

    return render_template('characters/edit_character.html',
                           project=project, char=char,
                           operations=GEOMETRIC_OPERATIONS,
                           structure_parts=Config.STRUCTURE_PARTS,
                           entries=entries, parts=parts,
                           available_types=available_types)


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
            val = compute_geometric_value(
                char.geometric_operation, parts_coords,
                np.array(struct.landmarks_json), char.formula
            )
            raw_values.append(val)
            labels.append(specimen.species_name)

    return jsonify({
        'type': 'geometric',
        'values': raw_values,
        'labels': labels,
        'states': char.states_json or [],
        'stats': {
            'min': min(raw_values) if raw_values else 0,
            'max': max(raw_values) if raw_values else 0,
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


def _log(project_id, action):
    log = ActivityLog(
        project_id=project_id, user_id=current_user.id, action=action
    )
    db.session.add(log)
