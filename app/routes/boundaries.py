import numpy as np
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from app import db
from app.models import Specimen, Structure, ActivityLog
from app.procrustes import generalized_procrustes, pca
from config import Config

boundaries_bp = Blueprint('boundaries', __name__)


@boundaries_bp.route('/structure/<int:structure_id>/boundaries', methods=['GET'])
@login_required
def boundary_editor(structure_id):
    structure = Structure.query.get_or_404(structure_id)
    specimen = Specimen.query.get_or_404(structure.specimen_id)

    parts = Config.STRUCTURE_PARTS.get(structure.structure_type, [])
    if not parts:
        # No boundary assignment needed for this structure type
        return render_template('boundaries/not_needed.html',
                               structure=structure, specimen=specimen)

    image_url = None
    if structure.image_path:
        image_url = f'/uploads/{structure.image_path}'

    from app.models import Project
    project = Project.query.get(specimen.project_id)

    return render_template('boundaries/editor.html',
                           structure=structure, specimen=specimen,
                           project=project,
                           parts=parts, image_url=image_url)


@boundaries_bp.route('/api/structure/<int:structure_id>/boundaries', methods=['GET'])
@login_required
def get_boundaries(structure_id):
    structure = Structure.query.get_or_404(structure_id)
    return jsonify({
        'boundaries': structure.boundary_json or {},
        'landmarks': structure.landmarks_json or [],
        'confirmed': structure.boundary_confirmed,
        'parts': Config.STRUCTURE_PARTS.get(structure.structure_type, []),
    })


@boundaries_bp.route('/api/structure/<int:structure_id>/boundaries', methods=['PUT'])
@login_required
def save_boundaries(structure_id):
    structure = Structure.query.get_or_404(structure_id)
    data = request.get_json()

    structure.boundary_json = data.get('boundaries', {})
    structure.boundary_confirmed = data.get('confirmed', False)

    if structure.boundary_confirmed:
        specimen = Specimen.query.get(structure.specimen_id)
        _log(specimen.project_id,
             f'Confirmed boundaries for {specimen.species_name} {structure.structure_type}')

        # Trigger character computation with Procrustes alignment
        from app.characters import compute_batch_with_procrustes
        compute_batch_with_procrustes(specimen.project_id,
                                      structure_type=structure.structure_type)

    db.session.commit()
    return jsonify({'status': 'ok'})


@boundaries_bp.route('/api/structure/<int:structure_id>/boundaries/copy_similar', methods=['POST'])
@login_required
def copy_from_similar(structure_id):
    """Find the most similar specimen with confirmed boundaries and copy them."""
    structure = Structure.query.get_or_404(structure_id)
    specimen = Specimen.query.get_or_404(structure.specimen_id)

    if not structure.landmarks_json:
        return jsonify({'error': 'No landmarks for this structure'}), 400

    # Find all structures of same type in same project with confirmed boundaries
    candidates = (Structure.query
                  .join(Specimen)
                  .filter(
                      Specimen.project_id == specimen.project_id,
                      Structure.structure_type == structure.structure_type,
                      Structure.boundary_confirmed == True,
                      Structure.landmarks_json.isnot(None),
                      Structure.id != structure.id,
                  ).all())

    if not candidates:
        return jsonify({'error': 'No confirmed boundaries found for similar specimens'}), 404

    # Find nearest in PCA space
    target_coords = np.array(structure.landmarks_json)
    target_count = len(target_coords)

    # Collect candidates with matching landmark count
    valid = []
    for c in candidates:
        c_coords = np.array(c.landmarks_json)
        if len(c_coords) == target_count:
            valid.append(c)

    if not valid:
        return jsonify({'error': 'No candidates with matching landmark count'}), 404

    # Simple: use Procrustes distance to find nearest
    from app.procrustes import scale_to_unit, optimal_rotation
    target_scaled = scale_to_unit(target_coords)

    best_dist = float('inf')
    best_candidate = None
    for c in valid:
        c_scaled = scale_to_unit(np.array(c.landmarks_json))
        R = optimal_rotation(c_scaled, target_scaled)
        aligned = c_scaled @ R.T
        dist = np.sum((aligned - target_scaled) ** 2)
        if dist < best_dist:
            best_dist = dist
            best_candidate = c

    if best_candidate and best_candidate.boundary_json:
        return jsonify({
            'boundaries': best_candidate.boundary_json,
            'source_specimen': Specimen.query.get(best_candidate.specimen_id).species_name,
            'distance': float(best_dist),
        })

    return jsonify({'error': 'No suitable boundaries found'}), 404


def _log(project_id, action):
    log = ActivityLog(
        project_id=project_id, user_id=current_user.id, action=action
    )
    db.session.add(log)
