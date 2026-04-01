from datetime import datetime, timezone
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models import (
    Project, Specimen, Structure, CharacterDefinition, CharacterValue,
    CorrectionHistory, ActivityLog
)

matrix_bp = Blueprint('matrix', __name__)


@matrix_bp.route('/project/<int:project_id>/matrix')
@login_required
def matrix_view(project_id):
    project = Project.query.get_or_404(project_id)
    structure_filter = request.args.get('structure_type', '')
    dna_only = request.args.get('dna_only') == '1'
    unconfirmed_only = request.args.get('unconfirmed') == '1'

    # Get active characters — only for structure types that have data
    char_query = CharacterDefinition.query.filter_by(
        project_id=project_id, active=True
    ).order_by(CharacterDefinition.code)
    if structure_filter:
        char_query = char_query.filter_by(structure_type=structure_filter)
    characters = char_query.all()

    # Get all species in project
    specimens = Specimen.query.filter_by(project_id=project_id).order_by(Specimen.species_name).all()

    if dna_only:
        from app.models import DNASequence
        ids_with_dna = {s.specimen_id for s in
                        DNASequence.query.filter(
                            DNASequence.specimen_id.in_([sp.id for sp in specimens]),
                            DNASequence.available == True
                        ).all()}
        specimens = [s for s in specimens if s.id in ids_with_dna]

    # Build matrix: species -> {char_code -> value dict}
    matrix_data = []
    for specimen in specimens:
        structures = Structure.query.filter_by(specimen_id=specimen.id).all()
        row = {'specimen': specimen, 'cells': {}}

        for char in characters:
            # Find the structure of matching type
            struct = next((s for s in structures if s.structure_type == char.structure_type), None)
            if struct:
                val = CharacterValue.query.filter_by(
                    structure_id=struct.id, character_id=char.id
                ).first()
                if val:
                    row['cells'][char.code] = {
                        'id': val.id,
                        'state': val.state,
                        'raw_value': val.raw_value,
                        'confidence': val.confidence,
                        'auto_assigned': val.auto_assigned,
                    }
                else:
                    row['cells'][char.code] = None
            else:
                row['cells'][char.code] = None

        if unconfirmed_only:
            has_unconfirmed = any(
                v is not None and v.get('state') == '?'
                for v in row['cells'].values()
            )
            if not has_unconfirmed:
                continue

        matrix_data.append(row)

    return render_template('matrix/matrix_view.html',
                           project=project, characters=characters,
                           matrix_data=matrix_data,
                           structure_filter=structure_filter,
                           dna_only=dna_only, unconfirmed_only=unconfirmed_only)


@matrix_bp.route('/project/<int:project_id>/matrix/gallery/<int:char_id>')
@login_required
def gallery_view(project_id, char_id):
    project = Project.query.get_or_404(project_id)
    char = CharacterDefinition.query.get_or_404(char_id)

    # Get all structures of matching type with their values
    entries = []
    structures = (Structure.query
                  .join(Specimen)
                  .filter(Specimen.project_id == project_id,
                          Structure.structure_type == char.structure_type)
                  .all())

    for struct in structures:
        specimen = Specimen.query.get(struct.specimen_id)
        val = CharacterValue.query.filter_by(
            structure_id=struct.id, character_id=char.id
        ).first()

        entries.append({
            'structure': struct,
            'specimen': specimen,
            'value': val,
            'image_url': f'/uploads/{struct.image_path}' if struct.image_path else None,
            'landmarks': struct.landmarks_json,
            'boundaries': struct.boundary_json,
        })

    # Sort by raw_value for geometric, by state for manual
    if char.computation_type == 'geometric':
        entries.sort(key=lambda e: (e['value'].raw_value if e['value'] and e['value'].raw_value is not None else 0))
    else:
        entries.sort(key=lambda e: (e['value'].state if e['value'] and e['value'].state else '?'))

    from config import Config
    parts = Config.STRUCTURE_PARTS.get(char.structure_type, [])

    return render_template('matrix/gallery.html',
                           project=project, char=char, entries=entries,
                           parts=parts)


@matrix_bp.route('/project/<int:project_id>/matrix/code/<int:structure_id>')
@login_required
def manual_coding(project_id, structure_id):
    """Manual coding interface for bar and MCO characters."""
    project = Project.query.get_or_404(project_id)
    structure = Structure.query.get_or_404(structure_id)
    specimen = Specimen.query.get_or_404(structure.specimen_id)

    # Get manual characters for this structure type
    characters = CharacterDefinition.query.filter_by(
        project_id=project_id,
        structure_type=structure.structure_type,
        computation_type='manual',
        active=True
    ).order_by(CharacterDefinition.code).all()

    # Get existing values
    values = {}
    for char in characters:
        val = CharacterValue.query.filter_by(
            structure_id=structure.id, character_id=char.id
        ).first()
        values[char.code] = val

    image_url = f'/uploads/{structure.image_path}' if structure.image_path else None

    # Count remaining uncoded specimens for progress bar
    total_structures = (Structure.query
                        .join(Specimen)
                        .filter(Specimen.project_id == project_id,
                                Structure.structure_type == structure.structure_type)
                        .count())

    return render_template('matrix/manual_coding.html',
                           project=project, structure=structure,
                           specimen=specimen, characters=characters,
                           values=values, image_url=image_url,
                           total_structures=total_structures)


@matrix_bp.route('/api/project/<int:project_id>/matrix/override', methods=['POST'])
@login_required
def override_value(project_id):
    data = request.get_json()
    value_id = data.get('value_id')
    new_state = data.get('state')
    reason = data.get('reason', '')

    val = CharacterValue.query.get_or_404(value_id)
    old_state = val.state

    # Log correction
    correction = CorrectionHistory(
        project_id=project_id,
        structure_id=val.structure_id,
        character_id=val.character_id,
        old_state=old_state,
        new_state=new_state,
        reason=reason,
        user_id=current_user.id,
    )
    db.session.add(correction)

    val.state = new_state
    val.override_by = current_user.id
    val.override_reason = reason
    val.override_at = datetime.now(timezone.utc)
    val.confidence = 1.0

    db.session.commit()
    return jsonify({'status': 'ok', 'old_state': old_state, 'new_state': new_state})


@matrix_bp.route('/api/project/<int:project_id>/matrix/assign', methods=['POST'])
@login_required
def assign_manual_value(project_id):
    """Assign a manual character value (for bar/MCO coding)."""
    data = request.get_json()
    structure_id = data.get('structure_id')
    character_id = data.get('character_id')
    state = data.get('state')

    # Check dependencies
    char = CharacterDefinition.query.get_or_404(character_id)
    structure = Structure.query.get_or_404(structure_id)

    from app.characters import check_dependencies
    if check_dependencies(char, structure, project_id):
        state = '-'

    val = CharacterValue.query.filter_by(
        structure_id=structure_id, character_id=character_id
    ).first()

    if val:
        val.state = state
        val.confidence = 1.0
        val.auto_assigned = False
        val.reviewer_id = current_user.id
    else:
        val = CharacterValue(
            structure_id=structure_id,
            character_id=character_id,
            state=state,
            confidence=1.0,
            auto_assigned=False,
            reviewer_id=current_user.id,
        )
        db.session.add(val)

    db.session.commit()
    return jsonify({'status': 'ok', 'state': state})


@matrix_bp.route('/api/project/<int:project_id>/matrix/cell_detail', methods=['GET'])
@login_required
def cell_detail(project_id):
    """Get detailed info for a matrix cell popup."""
    value_id = request.args.get('value_id', type=int)
    val = CharacterValue.query.get_or_404(value_id)
    char = CharacterDefinition.query.get(val.character_id)
    structure = Structure.query.get(val.structure_id)
    specimen = Specimen.query.get(structure.specimen_id)

    return jsonify({
        'species': specimen.species_name,
        'character': char.name,
        'code': char.code,
        'state': val.state,
        'raw_value': val.raw_value,
        'confidence': val.confidence,
        'auto_assigned': val.auto_assigned,
        'override_reason': val.override_reason,
        'states': char.states_json,
        'computation_type': char.computation_type,
        'image_url': f'/uploads/{structure.image_path}' if structure.image_path else None,
        'landmarks': structure.landmarks_json,
        'boundaries': structure.boundary_json,
    })


@matrix_bp.route('/project/<int:project_id>/tree/upload', methods=['POST'])
@login_required
def upload_tree(project_id):
    project = Project.query.get_or_404(project_id)

    if 'tree_file' in request.files:
        f = request.files['tree_file']
        project.tree_newick = f.read().decode('utf-8').strip()
        db.session.commit()

    return jsonify({'status': 'ok', 'has_tree': bool(project.tree_newick)})


@matrix_bp.route('/api/project/<int:project_id>/tree', methods=['GET'])
@login_required
def get_tree(project_id):
    project = Project.query.get_or_404(project_id)
    return jsonify({'newick': project.tree_newick or ''})
