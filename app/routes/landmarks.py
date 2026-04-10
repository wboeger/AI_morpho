import os
import csv
import io
import json
import zipfile
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
    content = f.read().decode('utf-8', errors='replace')
    coords = _parse_imagej_csv(content)

    if not coords:
        flash('No valid coordinates found in CSV. Expected ImageJ Results table with X and Y columns.', 'error')
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


def _parse_imagej_csv(text: str) -> list:
    """Parse an ImageJ Results-table CSV (or plain X,Y CSV) into [[x,y], ...].

    Handles:
      - ImageJ saveAs("Results") format:  ` ,X,Y\\n0,100.5,200.3\\n...`
      - Plain two-column CSV:             `100.5,200.3\\n...`
      - Tab-separated variants
      - Semicolon-separated variants
    Returns coordinates in image-pixel space (no scaling applied).
    """
    # Auto-detect delimiter
    sniff = text[:2000]
    try:
        dialect = csv.Sniffer().sniff(sniff, delimiters=',\t;')
    except csv.Error:
        dialect = csv.excel  # default comma

    reader = csv.reader(io.StringIO(text), dialect)
    coords = []
    x_col = None
    y_col = None

    for row_num, row in enumerate(reader):
        stripped = [v.strip() for v in row]
        if not any(stripped):
            continue

        # Header detection: look for 'X' and 'Y' column labels (case-insensitive)
        if x_col is None:
            upper = [v.upper() for v in stripped]
            if 'X' in upper and 'Y' in upper:
                x_col = upper.index('X')
                y_col = upper.index('Y')
                continue  # skip header row

        # Data row
        try:
            if x_col is not None:
                x = float(stripped[x_col])
                y = float(stripped[y_col])
            else:
                # Fallback: last two parseable numbers in the row
                nums = [float(v) for v in stripped if v]
                if len(nums) < 2:
                    continue
                x, y = nums[-2], nums[-1]
            coords.append([x, y])
        except (ValueError, IndexError):
            continue

    return coords


def _specimen_name_from_stem(stem: str) -> str:
    """Convert an ImageJ CSV filename stem to a normalized species name.

    The macro saves files as  FirstPart_SecondPart.csv  (first two
    underscore-separated tokens of the image filename).  We convert
    underscores to spaces to match Specimen.species_name.
    """
    return stem.replace('_', ' ').strip()


@landmarks_bp.route('/project/<int:project_id>/landmarks/batch_import', methods=['POST'])
@login_required
def batch_import_landmarks(project_id):
    """Batch-import landmark CSVs from an ImageJ macro ZIP export.

    Expects a ZIP file where each entry is a CSV produced by the
    Gyro-Landmark macro (one file per specimen, named
    SpeciesName_SpecimenID.csv).  The user selects the target
    structure type (hook / anchor / etc.) in the form.
    """
    from app.models import Project, Specimen, Structure

    project = Project.query.get_or_404(project_id)
    structure_type = request.form.get('structure_type', 'hook')

    if 'zip_file' not in request.files or request.files['zip_file'].filename == '':
        flash('No ZIP file selected.', 'error')
        return redirect(url_for('project.view_project', project_id=project_id))

    raw = request.files['zip_file'].read()
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        flash('Uploaded file is not a valid ZIP archive.', 'error')
        return redirect(url_for('project.view_project', project_id=project_id))

    # Build a fast specimen lookup: normalized_name → Specimen
    specimens = Specimen.query.filter_by(project_id=project_id).all()
    sp_index = {}
    for sp in specimens:
        norm = sp.species_name.strip().lower()
        sp_index[norm] = sp

    imported, skipped, errors = [], [], []
    target_count = Config.LANDMARK_COUNTS.get(structure_type)

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        csv_names = [
            n for n in zf.namelist()
            if n.lower().endswith('.csv')
            and not os.path.basename(n).startswith('.')
            and os.path.basename(n) not in ('rejected_log.csv', 'qc_log.csv', 'error_log.csv')
        ]

        for zname in csv_names:
            base = os.path.basename(zname)
            stem = base[:-4]  # strip .csv
            species_guess = _specimen_name_from_stem(stem).lower()

            # Fuzzy match: exact, then starts-with, then substring
            specimen = sp_index.get(species_guess)
            if specimen is None:
                for norm, sp in sp_index.items():
                    if norm.startswith(species_guess) or species_guess.startswith(norm):
                        specimen = sp
                        break

            if specimen is None:
                skipped.append(f'{base} — no specimen matching "{stem.replace("_", " ")}"')
                continue

            structure = Structure.query.filter_by(
                specimen_id=specimen.id, structure_type=structure_type
            ).first()
            if structure is None:
                skipped.append(f'{base} — {specimen.species_name}: no {structure_type} structure')
                continue

            try:
                text = zf.read(zname).decode('utf-8', errors='replace')
                coords = _parse_imagej_csv(text)
                if not coords:
                    errors.append(f'{base} — no valid coordinates found')
                    continue

                coords_arr = np.array(coords)
                n = target_count
                if n is None:
                    n = suggest_landmark_count(
                        resample_equidistant(coords_arr, 50), structure_type
                    )
                resampled = resample_equidistant(coords_arr, n)

                structure.landmarks_json = resampled.tolist()
                structure.landmark_count = n
                structure.landmarks_confirmed = False
                imported.append(f'{specimen.species_name} ({n} landmarks)')

            except Exception as exc:
                errors.append(f'{base} — {exc}')

    db.session.commit()

    _log(project_id,
         f'Batch import ({structure_type}): {len(imported)} imported, '
         f'{len(skipped)} skipped, {len(errors)} errors')

    return render_template('landmarks/batch_import_result.html',
                           project=project,
                           structure_type=structure_type,
                           imported=imported,
                           skipped=skipped,
                           errors=errors)


def _log(project_id, action):
    log = ActivityLog(
        project_id=project_id, user_id=current_user.id, action=action
    )
    db.session.add(log)
