import json
import statistics
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
from flask_login import login_required, current_user
from app import db
from app.models import Project, Specimen, Structure, CharacterDefinition, CharacterValue

ai_advisor_bp = Blueprint('ai_advisor', __name__)


def _build_project_context(project_id: int) -> dict:
    """Collect all project data needed for the AI prompt."""
    project = Project.query.get_or_404(project_id)
    specimens = Specimen.query.filter_by(project_id=project_id).all()
    characters = CharacterDefinition.query.filter_by(
        project_id=project_id, active=True
    ).order_by(CharacterDefinition.code).all()

    # Structure type summary
    structure_counts = {}
    for sp in specimens:
        for st in sp.structures:
            structure_counts[st.structure_type] = structure_counts.get(st.structure_type, 0) + 1

    # Character summaries with value stats
    char_summaries = []
    for c in characters:
        vals = [v.raw_value for v in c.values if v.raw_value is not None]
        state_dist = {}
        for v in c.values:
            if v.state:
                state_dist[v.state] = state_dist.get(v.state, 0) + 1

        summary = {
            'code': c.code,
            'name': c.name,
            'description': c.description or '',
            'structure_type': c.structure_type,
            'computation_type': c.computation_type,
            'geometric_operation': c.geometric_operation or '',
            'formula': c.formula or '',
            'parts_involved': c.parts_involved or [],
            'states': c.states_json or [],
            'n_values': len(vals),
            'state_distribution': state_dist,
        }
        if vals:
            summary['value_min'] = round(min(vals), 4)
            summary['value_max'] = round(max(vals), 4)
            summary['value_mean'] = round(statistics.mean(vals), 4)
            if len(vals) > 1:
                summary['value_stdev'] = round(statistics.stdev(vals), 4)
        char_summaries.append(summary)

    return {
        'project_name': project.name,
        'n_specimens': len(specimens),
        'structure_types': structure_counts,
        'n_characters': len(characters),
        'characters': char_summaries,
    }


def _build_prompt(context: dict) -> str:
    ctx_json = json.dumps(context, indent=2)
    return f"""You are an expert in helminth morphology and geometric morphometrics, specializing in monogenean parasites (Gyrodactylidae and related groups). You are acting as a scientific advisor reviewing a morphometric dataset.

Here is the current state of the project:

{ctx_json}

Based on this, please suggest:
1. **New characters** that could be measured from the existing structures but are not yet defined — focus on biologically meaningful and taxonomically informative measurements (distances, ratios, angles between landmark regions).
2. **New states** or improved state boundaries for existing characters where the value range or distribution suggests the current discretization could be improved.
3. **Redundant or problematic characters** — any that appear redundant, have zero variance, or are likely correlated with others already defined.
4. **General observations** about the quality or completeness of the morphometric scheme.

Respond ONLY with a valid JSON object in this exact structure (no markdown, no extra text):
{{
  "new_characters": [
    {{
      "name": "Character name",
      "description": "What it measures and why it is informative",
      "structure_type": "hook|anchor|superficial_bar|deep_bar|mco",
      "suggested_formula": "e.g. dist(P3,P7) / dist(P1,P10)",
      "suggested_states": [
        {{"code": "0", "name": "small", "description": "< 0.3"}},
        {{"code": "1", "name": "large", "description": ">= 0.3"}}
      ]
    }}
  ],
  "state_improvements": [
    {{
      "character_code": "C01",
      "character_name": "existing name",
      "current_states": [],
      "suggestion": "Explanation of the improvement",
      "proposed_states": []
    }}
  ],
  "redundant_characters": [
    {{
      "character_code": "C02",
      "character_name": "existing name",
      "reason": "Why it is redundant or problematic"
    }}
  ],
  "observations": [
    "General observation 1",
    "General observation 2"
  ]
}}"""


def _call_claude(api_key: str, prompt: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model='claude-opus-4-6',
        max_tokens=4096,
        messages=[{'role': 'user', 'content': prompt}]
    )
    text = message.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith('```'):
        text = text.split('\n', 1)[1]
        if text.endswith('```'):
            text = text.rsplit('```', 1)[0]
    return json.loads(text)


def _call_openai(api_key: str, prompt: str) -> dict:
    import urllib.request
    payload = json.dumps({
        'model': 'gpt-4o',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 4096,
    }).encode()
    req = urllib.request.Request(
        'https://api.openai.com/v1/chat/completions',
        data=payload,
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    text = data['choices'][0]['message']['content'].strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1]
        if text.endswith('```'):
            text = text.rsplit('```', 1)[0]
    return json.loads(text)


def _call_gemini(api_key: str, prompt: str) -> dict:
    import urllib.request
    url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={api_key}'
    payload = json.dumps({
        'contents': [{'parts': [{'text': prompt}]}],
        'generationConfig': {'maxOutputTokens': 4096},
    }).encode()
    req = urllib.request.Request(
        url, data=payload, headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    text = data['candidates'][0]['content']['parts'][0]['text'].strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1]
        if text.endswith('```'):
            text = text.rsplit('```', 1)[0]
    return json.loads(text)


@ai_advisor_bp.route('/project/<int:project_id>/ai_advisor')
@login_required
def advisor_page(project_id):
    project = Project.query.get_or_404(project_id)
    saved_provider = session.get('ai_provider', 'claude')
    return render_template('ai_advisor/advisor.html',
                           project=project,
                           saved_provider=saved_provider)


@ai_advisor_bp.route('/project/<int:project_id>/ai_advisor/analyze', methods=['POST'])
@login_required
def analyze(project_id):
    project = Project.query.get_or_404(project_id)
    provider = request.form.get('provider', 'claude')
    api_key = request.form.get('api_key', '').strip()

    if not api_key:
        return jsonify({'error': 'No API key provided.'}), 400

    session['ai_provider'] = provider

    try:
        context = _build_project_context(project_id)
        prompt = _build_prompt(context)

        if provider == 'claude':
            result = _call_claude(api_key, prompt)
        elif provider == 'openai':
            result = _call_openai(api_key, prompt)
        elif provider == 'gemini':
            result = _call_gemini(api_key, prompt)
        else:
            return jsonify({'error': f'Unknown provider: {provider}'}), 400

        return jsonify({'status': 'ok', 'result': result, 'context_summary': {
            'n_specimens': context['n_specimens'],
            'n_characters': context['n_characters'],
            'structure_types': context['structure_types'],
        }})

    except json.JSONDecodeError as e:
        return jsonify({'error': f'AI returned invalid JSON: {e}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@ai_advisor_bp.route('/api/project/<int:project_id>/ai_advisor/create_character', methods=['POST'])
@login_required
def create_suggested_character(project_id):
    """Create a new CharacterDefinition from an AI suggestion."""
    from app.models import CharacterDefinition
    from sqlalchemy import func

    data = request.get_json()
    structure_type = data.get('structure_type', 'hook')
    name = data.get('name', 'AI Suggested Character')
    description = data.get('description', '')
    states = data.get('suggested_states', [])
    formula = data.get('suggested_formula', '')

    # Generate next available code
    existing_codes = [
        c.code for c in CharacterDefinition.query.filter_by(project_id=project_id).all()
    ]
    prefix = 'AI'
    n = 1
    while f'{prefix}{n:02d}' in existing_codes:
        n += 1
    code = f'{prefix}{n:02d}'

    char = CharacterDefinition(
        project_id=project_id,
        code=code,
        name=name,
        description=description,
        structure_type=structure_type,
        computation_type='manual',
        formula=formula,
        states_json=states,
        active=True,
        created_by=current_user.id,
    )
    db.session.add(char)
    db.session.commit()
    return jsonify({'status': 'ok', 'code': code, 'id': char.id})
