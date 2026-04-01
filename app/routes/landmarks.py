import os
import csv
import io
import json
import numpy as np
from flask import Blueprint, render_template, request, jsonify, current_app, flash, redirect, url_for
from flask_login import login_required, current_user
from app import db
from app.models import Specimen, Structure, ActivityLog
from app.geometry import resample_equidistant, suggest_landmark_count, curvature_variance
from config import Config

landmarks_bp = Blueprint('landmarks', __name__)


@landmarks_bp.route('/structure/<int:structure_id>/landmarks', methods=['GET'])
@login_required
def landmark_editor(structure_id):
    structure = Structure.query.get_or_404(structure_id)
    specimen = Specimen.query.get_or_404(structure.specimen_id)

    image_url = None
    if structure.image_path:
        image_url = f'/uploads/{structure.image_path}'

    parts = Config.STRUCTURE_PARTS.get(structure.structure_type, [])
    fixed_count = Config.LANDMARK_COUNTS.get(structure.structure_type)

    return render_template('landmarks/editor.html',
                           structure=structure, specimen=specimen,
                           image_url=image_url, parts=parts,
                           fixed_count=fixed_count)


@landmarks_bp.route('/structure/<int:structure_id>/landmarks/upload', methods=['POST'])
@login_required
def upload_landmarks(structure_id):
    structure = Structure.query.get_or_404(structure_id)
    specimen = Specimen.query.get_or_404(structure.specimen_id)

    if 'csv_file' not in request.files:
        flash('No file uploaded.', 'error')
        return redirect(url_for('landmarks.landmark_editor', structure_id=structure_id))

    f = request.files['csv_file']
    content = f.read().decode('utf-8')
    reader = csv.reader(io.StringIO(content))

    coords = []
    for row in reader:
        # Skip header rows
        try:
            vals = [float(v) for v in row if v.strip()]
            if len(vals) >= 2:
                coords.append([vals[-2], vals[-1]])  # last two columns as X, Y
        except ValueError:
            continue

    if not coords:
        flash('No valid coordinates found in CSV.', 'error')
        return redirect(url_for('landmarks.landmark_editor', structure_id=structure_id))

    # Resample to target count
    coords_arr = np.array(coords)
    target_count = Config.LANDMARK_COUNTS.get(structure.structure_type)

    if target_count is None:
        # Adaptive: suggest based on curvature
        target_count = suggest_landmark_count(
            resample_equidistant(coords_arr, 50),
            structure.structure_type
        )

    coords_resampled = resample_equidistant(coords_arr, target_count)
    structure.landmarks_json = coords_resampled.tolist()
    structure.landmark_count = target_count
    structure.landmarks_confirmed = False

    _log(specimen.project_id, f'Uploaded landmarks for {specimen.species_name} {structure.structure_type}')
    db.session.commit()

    flash(f'Loaded {target_count} landmarks.', 'success')
    return redirect(url_for('landmarks.landmark_editor', structure_id=structure_id))


@landmarks_bp.route('/api/structure/<int:structure_id>/landmarks', methods=['GET'])
@login_required
def get_landmarks(structure_id):
    structure = Structure.query.get_or_404(structure_id)
    return jsonify({
        'landmarks': structure.landmarks_json or [],
        'confirmed': structure.landmarks_confirmed,
        'landmark_count': structure.landmark_count,
        'structure_type': structure.structure_type,
    })


@landmarks_bp.route('/api/structure/<int:structure_id>/landmarks', methods=['PUT'])
@login_required
def save_landmarks(structure_id):
    structure = Structure.query.get_or_404(structure_id)
    data = request.get_json()

    landmarks = data.get('landmarks', [])
    confirmed = data.get('confirmed', False)

    structure.landmarks_json = landmarks
    structure.landmark_count = len(landmarks)
    structure.landmarks_confirmed = confirmed

    if confirmed:
        specimen = Specimen.query.get(structure.specimen_id)
        _log(specimen.project_id,
             f'Confirmed landmarks for {specimen.species_name} {structure.structure_type}')

    db.session.commit()
    return jsonify({'status': 'ok', 'landmark_count': len(landmarks)})


@landmarks_bp.route('/api/structure/<int:structure_id>/landmarks/resample', methods=['POST'])
@login_required
def resample_landmarks(structure_id):
    structure = Structure.query.get_or_404(structure_id)
    data = request.get_json()
    target_count = data.get('count')

    if not structure.landmarks_json:
        return jsonify({'error': 'No landmarks to resample'}), 400

    coords = np.array(structure.landmarks_json)

    if target_count is None:
        target_count = suggest_landmark_count(
            resample_equidistant(coords, 50),
            structure.structure_type
        )

    resampled = resample_equidistant(coords, target_count)
    structure.landmarks_json = resampled.tolist()
    structure.landmark_count = target_count
    structure.landmarks_confirmed = False
    db.session.commit()

    return jsonify({
        'landmarks': resampled.tolist(),
        'landmark_count': target_count,
        'suggested': target_count,
    })


@landmarks_bp.route('/api/structure/<int:structure_id>/landmarks/suggest_count', methods=['GET'])
@login_required
def suggest_count(structure_id):
    structure = Structure.query.get_or_404(structure_id)
    if not structure.landmarks_json:
        return jsonify({'error': 'No landmarks'}), 400

    coords = np.array(structure.landmarks_json)
    low_res = resample_equidistant(coords, 50)
    count = suggest_landmark_count(low_res, structure.structure_type)
    cv = float(curvature_variance(low_res))

    return jsonify({'suggested_count': count, 'curvature_variance': cv})


@landmarks_bp.route('/uploads/<path:filename>')
@login_required
def serve_upload(filename):
    from flask import send_from_directory
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


@landmarks_bp.route('/structure/<int:structure_id>/landmarks/import_json', methods=['POST'])
@login_required
def import_boundaries_json(structure_id):
    """Import landmarks + boundaries from existing AI_morpho JSON format."""
    structure = Structure.query.get_or_404(structure_id)
    specimen = Specimen.query.get_or_404(structure.specimen_id)

    if 'json_file' not in request.files:
        flash('No file uploaded.', 'error')
        return redirect(url_for('landmarks.landmark_editor', structure_id=structure_id))

    f = request.files['json_file']
    data = json.load(f)

    # Find matching specimen key in the JSON
    species_key = None
    for key in data:
        if specimen.species_name.lower() in key.lower():
            species_key = key
            break

    if not species_key and len(data) == 1:
        species_key = list(data.keys())[0]

    if not species_key:
        flash('Specimen not found in JSON file. Tried matching by species name.', 'error')
        return redirect(url_for('landmarks.landmark_editor', structure_id=structure_id))

    entry = data[species_key]

    # Extract coordinates
    if 'coordinates' in entry:
        coords = entry['coordinates']
        structure.landmarks_json = coords
        structure.landmark_count = len(coords)

    # Extract boundaries
    boundary = {}
    for key, val in entry.items():
        if key == 'coordinates':
            continue
        if isinstance(val, list) and all(isinstance(v, (int, float)) for v in val):
            boundary[key] = [int(v) for v in val]

    if boundary:
        structure.boundary_json = boundary
        structure.boundary_confirmed = False

    _log(specimen.project_id,
         f'Imported JSON data for {specimen.species_name} {structure.structure_type}')
    db.session.commit()
    flash('Imported landmarks and boundaries from JSON.', 'success')
    return redirect(url_for('landmarks.landmark_editor', structure_id=structure_id))


def _log(project_id, action):
    log = ActivityLog(
        project_id=project_id, user_id=current_user.id, action=action
    )
    db.session.add(log)
