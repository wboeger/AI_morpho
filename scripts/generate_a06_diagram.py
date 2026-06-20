#!/usr/bin/env python3
"""Generate the static A06 diagram SVG using actual specimen shapes."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.models import CharacterDefinition

app = create_app()
with app.app_context():
    from app.routes.characters import _a06_generate_svg

    char = CharacterDefinition.query.filter_by(code='A06').first()
    if not char:
        print("ERROR: No A06 CharacterDefinition found in database")
        sys.exit(1)

    project_id = char.project_id
    print(f"Generating A06 diagram for project_id={project_id} ...")
    svg_content = _a06_generate_svg(project_id)

    out_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'app', 'static', 'diagrams', 'a06_superficial_root_profile.svg'
    )
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(svg_content)

    print(f"Written {len(svg_content):,} bytes → {out_path}")
