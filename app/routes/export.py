from flask import Blueprint, render_template, request, Response
from flask_login import login_required
from app.models import Project
from app.export import (
    export_csv, export_csv_detailed, export_nexus, export_tnt,
    export_json_full, export_descriptions_text, export_diagnoses_text
)

export_bp = Blueprint('export', __name__)


@export_bp.route('/project/<int:project_id>/export')
@login_required
def export_page(project_id):
    project = Project.query.get_or_404(project_id)
    return render_template('export/export.html', project=project)


@export_bp.route('/project/<int:project_id>/export/csv')
@login_required
def download_csv(project_id):
    structure_type = request.args.get('structure_type')
    dna_only = request.args.get('dna_only') == '1'
    content = export_csv(project_id, structure_type=structure_type, dna_only=dna_only)
    return Response(content, mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename=matrix_{project_id}.csv'})


@export_bp.route('/project/<int:project_id>/export/csv_detailed')
@login_required
def download_csv_detailed(project_id):
    structure_type = request.args.get('structure_type')
    dna_only = request.args.get('dna_only') == '1'
    content = export_csv_detailed(project_id, structure_type=structure_type, dna_only=dna_only)
    return Response(content, mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename=matrix_detailed_{project_id}.csv'})


@export_bp.route('/project/<int:project_id>/export/nexus')
@login_required
def download_nexus(project_id):
    structure_type = request.args.get('structure_type')
    dna_only = request.args.get('dna_only') == '1'
    content = export_nexus(project_id, structure_type=structure_type, dna_only=dna_only)
    return Response(content, mimetype='text/plain',
                    headers={'Content-Disposition': f'attachment; filename=matrix_{project_id}.nex'})


@export_bp.route('/project/<int:project_id>/export/tnt')
@login_required
def download_tnt(project_id):
    structure_type = request.args.get('structure_type')
    dna_only = request.args.get('dna_only') == '1'
    content = export_tnt(project_id, structure_type=structure_type, dna_only=dna_only)
    return Response(content, mimetype='text/plain',
                    headers={'Content-Disposition': f'attachment; filename=matrix_{project_id}.tnt'})


@export_bp.route('/project/<int:project_id>/export/json')
@login_required
def download_json(project_id):
    content = export_json_full(project_id)
    return Response(content, mimetype='application/json',
                    headers={'Content-Disposition': f'attachment; filename=project_{project_id}_full.json'})


@export_bp.route('/project/<int:project_id>/export/descriptions')
@login_required
def download_descriptions(project_id):
    content = export_descriptions_text(project_id)
    return Response(content, mimetype='text/plain',
                    headers={'Content-Disposition': f'attachment; filename=descriptions_{project_id}.txt'})


@export_bp.route('/project/<int:project_id>/export/diagnoses')
@login_required
def download_diagnoses(project_id):
    content = export_diagnoses_text(project_id)
    return Response(content, mimetype='text/plain',
                    headers={'Content-Disposition': f'attachment; filename=diagnoses_{project_id}.txt'})
