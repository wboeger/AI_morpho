#!/usr/bin/env python3
"""Generate GyroMorpho v2 User Manual as PDF."""

from fpdf import FPDF


class Manual(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_font('Helvetica', 'I', 8)
            self.set_text_color(120, 120, 120)
            self.cell(0, 8, 'GyroMorpho v2 - User Manual', align='L')
            self.ln(4)
            self.set_draw_color(200, 200, 200)
            self.line(10, self.get_y(), 200, self.get_y())
            self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', align='C')

    def chapter_title(self, num, title):
        self.set_font('Helvetica', 'B', 16)
        self.set_text_color(40, 80, 160)
        self.ln(4)
        self.cell(0, 10, f'{num}. {title}', new_x='LMARGIN', new_y='NEXT')
        self.set_draw_color(40, 80, 160)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def section_title(self, title):
        self.set_font('Helvetica', 'B', 12)
        self.set_text_color(60, 60, 60)
        self.ln(2)
        self.cell(0, 8, title, new_x='LMARGIN', new_y='NEXT')
        self.ln(1)

    def subsection_title(self, title):
        self.set_font('Helvetica', 'BI', 10)
        self.set_text_color(80, 80, 80)
        self.cell(0, 7, title, new_x='LMARGIN', new_y='NEXT')
        self.ln(1)

    def body_text(self, text):
        self.set_font('Helvetica', '', 10)
        self.set_text_color(30, 30, 30)
        self.set_x(10)
        self.multi_cell(0, 5.5, text)
        self.ln(1)

    def bullet(self, text, indent=10):
        self.set_font('Helvetica', '', 10)
        self.set_text_color(30, 30, 30)
        self.set_x(10)
        self.multi_cell(0, 5.5, ' ' * (indent // 2) + '- ' + text)

    def numbered_item(self, num, text, indent=10):
        self.set_font('Helvetica', '', 10)
        self.set_text_color(30, 30, 30)
        self.set_x(10)
        self.multi_cell(0, 5.5, ' ' * (indent // 2) + f'{num}. ' + text)

    def note_box(self, text):
        self.set_fill_color(240, 247, 255)
        self.set_draw_color(40, 80, 160)
        y = self.get_y()
        self.set_font('Helvetica', 'I', 9)
        self.set_text_color(40, 80, 160)
        self.set_xy(12, y + 2)
        self.multi_cell(186, 5, text, border=1, fill=True)
        self.ln(3)

    def code_text(self, text):
        self.set_font('Courier', '', 9)
        self.set_text_color(30, 30, 30)
        self.set_fill_color(245, 245, 245)
        self.set_x(10)
        self.multi_cell(0, 5, text, fill=True)
        self.ln(2)

    def table_row(self, cols, widths, bold=False, fill=False):
        h = 6
        self.set_font('Helvetica', 'B' if bold else '', 9)
        self.set_text_color(30, 30, 30)
        self.set_x(10)
        if fill:
            self.set_fill_color(230, 240, 255)
        for i, col in enumerate(cols):
            self.cell(widths[i], h, col, border=1, fill=fill)
        self.ln(h)


def build_manual():
    pdf = Manual()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)

    # ── Cover page ──
    pdf.add_page()
    pdf.ln(50)
    pdf.set_font('Helvetica', 'B', 32)
    pdf.set_text_color(40, 80, 160)
    pdf.cell(0, 15, 'GyroMorpho v2', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('Helvetica', '', 16)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 10, 'User Manual', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(10)
    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 7, 'A web-based pipeline for morphometric analysis', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 7, 'and automated taxonomic description of Gyrodactylidae', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(20)
    pdf.set_font('Helvetica', '', 10)
    pdf.cell(0, 7, 'Walter A. P. Boeger', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 7, 'https://github.com/wboeger/AI_morpho', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(30)
    pdf.set_font('Helvetica', 'I', 9)
    pdf.cell(0, 7, 'Version 2.0 - April 2026', align='C', new_x='LMARGIN', new_y='NEXT')

    # ── Table of Contents ──
    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 20)
    pdf.set_text_color(40, 80, 160)
    pdf.cell(0, 12, 'Table of Contents', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(5)

    toc = [
        ('1', 'Overview'),
        ('2', 'Installation'),
        ('3', 'Pipeline Workflow'),
        ('4', 'Step 1: Extract Landmarks with ImageJ'),
        ('5', 'Step 2: Import Data into GyroMorpho'),
        ('6', 'Step 3: Assign Part Boundaries'),
        ('7', 'Step 4: Define and Edit Characters'),
        ('8', 'Step 5: Character Matrix and Gallery'),
        ('9', 'Step 6: Species Descriptions and Diagnoses'),
        ('10', 'Step 7: Export'),
        ('11', 'How Character States Are Computed'),
        ('12', 'Data Storage and Backup'),
        ('13', 'Troubleshooting'),
    ]
    for num, title in toc:
        pdf.set_font('Helvetica', '', 11)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(10, 7, num + '.')
        pdf.cell(0, 7, title, new_x='LMARGIN', new_y='NEXT')

    # ── 1. Overview ──
    pdf.add_page()
    pdf.chapter_title('1', 'Overview')
    pdf.body_text(
        'GyroMorpho v2 is a web-based pipeline for morphometric analysis of sclerotized structures '
        'of Gyrodactylidae (Monogenoidea). It uses landmark-based geometric morphometrics with '
        'Generalized Procrustes Analysis (GPA) to:'
    )
    pdf.bullet('Extract 2D pseudolandmarks from specimen images using ImageJ macros')
    pdf.bullet('Align specimens via GPA to remove size, position, and orientation differences')
    pdf.bullet('Compute discrete character states from continuous shape measurements')
    pdf.bullet('Generate species descriptions and taxonomic diagnoses automatically')
    pdf.bullet('Export phylogenetic matrices in Nexus, TNT, CSV, and JSON formats')
    pdf.ln(3)
    pdf.body_text(
        'The pipeline handles five structure types: marginal hooks, anchors, superficial bars, '
        'deep bars, and male copulatory organs (MCO). It ships with 36 pre-defined characters '
        '(C01-C12 for hooks, A01-A09 for anchors, B01-B06 for bars, D01-D03 for deep bars, '
        'M01-M06 for MCO).'
    )

    # ── 2. Installation ──
    pdf.add_page()
    pdf.chapter_title('2', 'Installation')

    pdf.section_title('Requirements')
    pdf.bullet('Python 3.10 or later')
    pdf.bullet('pip (Python package manager)')
    pdf.bullet('ImageJ or Fiji (for landmark extraction macros)')
    pdf.bullet('A modern web browser (Chrome, Firefox, Safari, Edge)')
    pdf.ln(3)

    pdf.section_title('Setup')
    pdf.numbered_item(1, 'Clone the repository:')
    pdf.code_text('  git clone https://github.com/wboeger/AI_morpho.git\n  cd AI_morpho')
    pdf.numbered_item(2, 'Create a virtual environment (recommended):')
    pdf.code_text('  python3 -m venv venv\n  source venv/bin/activate')
    pdf.numbered_item(3, 'Install dependencies:')
    pdf.code_text('  pip install -r requirements.txt')
    pdf.numbered_item(4, 'Initialize the data directory:')
    pdf.code_text('  mkdir -p data/uploads')
    pdf.numbered_item(5, 'Run the application:')
    pdf.code_text('  python run.py')
    pdf.ln(2)
    pdf.body_text('Open http://127.0.0.1:5000 in your browser. Register an account (the first user becomes admin), then create a project.')

    # ── 3. Pipeline Workflow ──
    pdf.add_page()
    pdf.chapter_title('3', 'Pipeline Workflow')
    pdf.body_text(
        'The project dashboard shows a Pipeline bar at the top with the complete workflow. '
        'Follow these steps in order:'
    )
    pdf.ln(2)

    steps = [
        ('ImageJ Macros', 'Download and run the ImageJ landmarking macros to extract 100 equidistant pseudolandmarks from specimen images.'),
        ('Import Landmarks', 'Import the CSV files produced by the macros into the web application. Also import part boundary JSON files and specimen images.'),
        ('Assign Boundaries', 'Use the interactive boundary editor to define which landmarks belong to which anatomical parts (Point, Shaft, Toe, etc.).'),
        ('Define Characters', 'Review and customize character definitions, geometric operations, state thresholds, and dependencies in the Character Workshop.'),
        ('Compute & Review', 'Batch-compute all geometric characters using Procrustes alignment. Review the character matrix, use the gallery to assign or correct states.'),
        ('Descriptions & Diagnoses', 'Generate automated species descriptions and comparative taxonomic diagnoses.'),
        ('Export', 'Export the final character matrix in Nexus, TNT, CSV, or JSON format for phylogenetic analysis.'),
    ]
    for i, (name, desc) in enumerate(steps, 1):
        pdf.set_font('Helvetica', 'B', 10)
        pdf.set_text_color(40, 80, 160)
        pdf.cell(8, 6, f'{i}.')
        pdf.cell(50, 6, name)
        pdf.set_font('Helvetica', '', 10)
        pdf.set_text_color(30, 30, 30)
        pdf.multi_cell(0, 6, desc)
        pdf.ln(2)

    # ── 4. Step 1: ImageJ Macros ──
    pdf.add_page()
    pdf.chapter_title('4', 'Step 1: Extract Landmarks with ImageJ')
    pdf.body_text(
        'Two ImageJ macros are included in the macros/ directory. They are also downloadable '
        'from the Import page with pre-configured directories for your project.'
    )
    pdf.ln(2)

    pdf.section_title('Hook Macro (macrogyrolandmark_v5.5.ijm)')
    pdf.body_text('Landmarks placed interactively:')
    pdf.bullet('L1 = Point tip (tip of the hook)')
    pdf.bullet('L2 = Toe tip (tip of the toe)')
    pdf.bullet('L3 = Junction Point-Shaft (inner face)')
    pdf.ln(2)

    pdf.section_title('Anchor Macro (macrogyrolandmark_v5_anchors.ijm)')
    pdf.body_text('Landmarks placed interactively:')
    pdf.bullet('L1 = Point (tip of the anchor)')
    pdf.bullet('L2 = External tip of the superficial root')
    pdf.bullet('L3 = Distal-most base of the deep root')
    pdf.ln(2)

    pdf.section_title('How to Run')
    pdf.numbered_item(1, 'Download the macro from the Import page (or find it in the macros/ folder).')
    pdf.numbered_item(2, 'Open ImageJ/Fiji.')
    pdf.numbered_item(3, 'Go to Plugins > Macros > Run... and select the .ijm file.')
    pdf.numbered_item(4, 'Configure the session: set input directory (folder with images), output directory (where CSVs will be saved), and processing options.')
    pdf.ln(3)

    pdf.section_title('Macro Workflow (11 stages)')
    macro_steps = [
        'Session setup: configure directories, enhancement settings, landmark count (100), wand tolerance.',
        'Image review: accept, reject (with logged reason), or skip each image.',
        'Crop & upscale: draw bounding rectangle around the structure; 3x upscale for precision.',
        'Enhancement: optional Gaussian blur, CLAHE contrast normalization, Unsharp Mask.',
        'Orientation: verify the structure points to the right; optional horizontal flip.',
        'B&W conversion: optional threshold-based binary mask for cleaner contour extraction.',
        'Wand tool: click on the structure outline to extract the contour; adjustable tolerance.',
        'Contour smoothing: 3-point moving average (configurable number of passes).',
        'Landmark placement: click to place L1, L2, L3 sequentially with visual confirmation.',
        'Equidistant resampling: 100 points starting from L1, evenly spaced by arc length.',
        'Verification & editing: review color-coded overlay (cyan=L1, yellow=L2, magenta=L3, green=semilandmarks); optionally drag points to correct positions.',
    ]
    for i, step in enumerate(macro_steps, 1):
        pdf.numbered_item(i, step)
    pdf.ln(2)

    pdf.body_text('Output: one CSV file per specimen (X,Y columns, 100 rows) plus QC and rejection log files. Full backward navigation is available at every stage.')

    # ── 5. Step 2: Import Data ──
    pdf.add_page()
    pdf.chapter_title('5', 'Step 2: Import Data into GyroMorpho')
    pdf.body_text(
        'From the project page, click "Import from Folders". The import page is organized into four sections.'
    )
    pdf.ln(2)

    pdf.section_title('Import Landmarks (CSV files)')
    pdf.numbered_item(1, 'Click "+ Add Folder".')
    pdf.numbered_item(2, 'Enter the full path to the folder containing CSV files (output from the ImageJ macros).')
    pdf.numbered_item(3, 'Select the structure type (Marginal Hook, Anchor, etc.).')
    pdf.numbered_item(4, 'Click "Scan" to preview files and parsed species names.')
    pdf.numbered_item(5, 'Add more folders as needed (one per structure type).')
    pdf.numbered_item(6, 'Click "Import All".')
    pdf.ln(2)
    pdf.body_text(
        'Species names are automatically parsed from filenames. Supported formats: '
        'Gyrodactylus_salaris.csv (underscores), AB063294Gyrodactylusanguillae.csv '
        '(accession prefix), JF836137.1|Gyrocerviceanseris.csv (pipe-separated).'
    )
    pdf.ln(2)

    pdf.section_title('Import Part Boundaries (JSON files)')
    pdf.body_text(
        'If you have pre-existing boundary definitions, import them as JSON files that map '
        'specimen names to part indices (1-based). Example:'
    )
    pdf.code_text('  { "Gyrodactylus_salaris": {\n      "Point": [1, 2, 3, 4, 5],\n      "Shaft": [6, 7, 8, 9, 10]\n    }\n  }')
    pdf.body_text('After boundary import, character states are automatically computed.')
    pdf.ln(2)

    pdf.section_title('Import Images')
    pdf.body_text(
        'Enter the path to a folder containing specimen images (PNG, JPG, GIF). '
        'Images are matched to existing specimens by species name from the filename. '
        'Use "Scan" to preview matches before importing.'
    )

    # ── 6. Step 3: Boundaries ──
    pdf.add_page()
    pdf.chapter_title('6', 'Step 3: Assign Part Boundaries')
    pdf.body_text(
        'On the project page, each structure has a "boundaries" link (visible when landmarks exist). '
        'The boundary editor assigns landmark points to anatomical parts.'
    )
    pdf.ln(2)

    pdf.section_title('Structure Parts')
    w = [45, 145]
    pdf.table_row(['Structure', 'Parts'], w, bold=True, fill=True)
    pdf.table_row(['Hook', 'Point, Shaft, Toe, Shelf, Base, Heel'], w)
    pdf.table_row(['Anchor', 'Point, Shaft, SuperficialRoot, DeepRoot'], w)
    pdf.table_row(['Superficial Bar', 'BarProper, Shield, ShieldDistalEnd, AnterolateralProcesses'], w)
    pdf.table_row(['Deep Bar', '(single unit)'], w)
    pdf.table_row(['MCO', 'Bulb, PrincipalSpine, Spinelets'], w)
    pdf.ln(3)

    pdf.section_title('Editor Modes')
    pdf.bullet('Click mode: click individual landmarks to assign them to the active part.')
    pdf.bullet('Range mode: click two landmarks to assign the entire range between them.')
    pdf.bullet('Lasso mode: draw a freehand selection around landmarks.')
    pdf.ln(2)

    pdf.section_title('Keyboard Shortcuts')
    pdf.bullet('1-6: select parts; C/R/L: switch modes; Ctrl+Z: undo')
    pdf.bullet('"Copy from similar": automatically copies boundaries from the most morphologically similar specimen that already has confirmed boundaries.')
    pdf.ln(2)
    pdf.body_text('Click "Confirm" to save boundaries and trigger automatic character computation.')

    # ── 7. Step 4: Characters ──
    pdf.add_page()
    pdf.chapter_title('7', 'Step 4: Define and Edit Characters')
    pdf.body_text(
        'Click "Character Workshop" from the project page. Here you manage all character definitions.'
    )
    pdf.ln(2)

    pdf.section_title('Workshop Features')
    pdf.bullet('View all characters grouped by structure type.')
    pdf.bullet('Toggle characters active/inactive (inactive ones are excluded from the matrix and exports).')
    pdf.bullet('Create new characters with custom geometric operations or manual coding.')
    pdf.bullet('View value distributions to check threshold placement.')
    pdf.ln(2)

    pdf.section_title('Edit Character Page')
    pdf.body_text('Clicking a character opens a two-panel editor:')
    pdf.ln(1)
    pdf.subsection_title('Left panel (form):')
    pdf.bullet('Name, description, parts involved, geometric operation, formula.')
    pdf.bullet('States with drag-and-drop reordering, up/down arrows, and delete buttons.')
    pdf.bullet('Each state has: code, name, description, threshold min, threshold max.')
    pdf.bullet('Dependencies: conditions under which the character is inapplicable.')
    pdf.ln(1)
    pdf.subsection_title('Right panel (reference):')
    pdf.bullet('Grid of all specimen thumbnails for this structure type, showing state badges and raw values.')
    pdf.bullet('Structure type dropdown to view other structures for context.')
    pdf.bullet('Shapes/Images toggle button.')
    pdf.bullet('Click any thumbnail to open a lightbox with enlarged view.')
    pdf.ln(2)
    pdf.subsection_title('Measurement explanation:')
    pdf.body_text(
        'A collapsible box at the top of the edit page explains exactly how the system computes '
        'the character: which geometric operation is used, what it measures on the landmark '
        'coordinates, how Procrustes alignment is applied before measurement, and how raw values '
        'are mapped to discrete states via thresholds.'
    )

    # ── 8. Step 5: Matrix & Gallery ──
    pdf.add_page()
    pdf.chapter_title('8', 'Step 5: Character Matrix and Gallery')

    pdf.section_title('Character Matrix')
    pdf.body_text('Click "Character Matrix" from the project page:')
    pdf.bullet('Rows = specimens (species), columns = characters.')
    pdf.bullet('Cells are color-coded by confidence: green (high), yellow (medium), red (low), gray (inapplicable).')
    pdf.bullet('Click any cell to see details (raw value, confidence, computation type) and override the state.')
    pdf.bullet('Filter by structure type (Hook, Anchor, Bar, MCO, All).')
    pdf.bullet('Filter by DNA-only specimens or unconfirmed-only.')
    pdf.ln(2)

    pdf.section_title('Gallery View')
    pdf.body_text('Click a character code in the matrix header to open the gallery:')
    pdf.bullet('Specimens sorted by raw value (geometric characters) or state (manual characters).')
    pdf.bullet('Landmark-derived shape outlines colored by anatomical parts, with a color legend.')
    pdf.bullet('Structure type switcher: view shapes/images for other structure types as context.')
    pdf.bullet('Shapes/Images toggle: switch between landmark outlines and uploaded photographs.')
    pdf.bullet('Lightbox: click any thumbnail to zoom in, with state assignment buttons.')
    pdf.bullet('Inline state buttons: assign states directly from the gallery grid.')
    pdf.ln(2)
    pdf.note_box('Tip: use the gallery to visually compare specimens side by side and assign states for manual characters or correct automatic assignments.')

    # ── 9. Descriptions & Diagnoses ──
    pdf.add_page()
    pdf.chapter_title('9', 'Step 6: Species Descriptions and Diagnoses')

    pdf.section_title('Species Descriptions')
    pdf.body_text(
        'Click "Descriptions" from the project page. The system auto-generates morphological '
        'descriptions for each specimen based on its character states, formatted in standard '
        'taxonomic prose. Click "Regenerate" to update after changing character values.'
    )
    pdf.ln(2)

    pdf.section_title('Taxonomic Diagnoses')
    pdf.body_text(
        'Click "Diagnoses" from the project page. Create taxonomic groups (genus, subfamily) '
        'by selecting which species belong to each group. The system generates comparative '
        'diagnoses highlighting distinguishing features. Diagnoses can be edited manually.'
    )

    # ── 10. Export ──
    pdf.add_page()
    pdf.chapter_title('10', 'Step 7: Export')
    pdf.body_text('Click "Export" from the project page. Available formats:')
    pdf.ln(2)

    w = [35, 70, 85]
    pdf.table_row(['Format', 'Description', 'Use case'], w, bold=True, fill=True)
    pdf.table_row(['CSV', 'Simple matrix (species x characters)', 'Spreadsheet analysis'], w)
    pdf.table_row(['CSV Detail', 'Matrix with raw values and confidence', 'Detailed analysis'], w)
    pdf.table_row(['Nexus', 'Standard phylogenetic format', 'MrBayes, PAUP*, Mesquite'], w)
    pdf.table_row(['TNT', 'TNT format', 'TNT parsimony analysis'], w)
    pdf.table_row(['JSON', 'Complete project data', 'Backup, re-import'], w)
    pdf.table_row(['Descript.', 'Formatted species descriptions', 'Publications'], w)
    pdf.table_row(['Diagnoses', 'Formatted group diagnoses', 'Publications'], w)
    pdf.ln(3)
    pdf.body_text('All matrix exports support optional filters: structure type, DNA-only specimens.')

    # ── 11. How Characters Are Computed ──
    pdf.add_page()
    pdf.chapter_title('11', 'How Character States Are Computed')

    pdf.section_title('Generalized Procrustes Analysis (GPA)')
    pdf.body_text('Before computing character values, all specimens of the same structure type are aligned using GPA:')
    pdf.numbered_item(1, 'Center: each specimen\'s landmarks are translated so the centroid is at the origin.')
    pdf.numbered_item(2, 'Scale: landmarks are scaled to unit centroid size (removes size differences).')
    pdf.numbered_item(3, 'Rotate: specimens are iteratively rotated to minimize the sum of squared distances to the mean shape.')
    pdf.ln(2)
    pdf.body_text('This ensures that character measurements are scale-independent and orientation-independent, capturing pure shape variation.')
    pdf.ln(2)

    pdf.section_title('Geometric Operations')
    w2 = [45, 80, 65]
    pdf.table_row(['Operation', 'Description', 'Example'], w2, bold=True, fill=True)
    pdf.table_row(['ratio_arc_length', 'Ratio of arc lengths of two parts', 'C01: Point/Shaft'], w2)
    pdf.table_row(['sinuosity', 'Arc length / chord length', 'C03: Point waviness'], w2)
    pdf.table_row(['mean_curvature', 'Mean Menger curvature along a part', 'C05: Shaft curvature'], w2)
    pdf.table_row(['max_curvature', 'Maximum local curvature', 'Sharpest bend'], w2)
    pdf.table_row(['junction_angle', 'Angle at junction between parts', 'C02: Point-Shaft angle'], w2)
    pdf.table_row(['direction_angle', 'Angle between direction vectors', 'C06: Shaft-Base angle'], w2)
    pdf.table_row(['relative_position', 'Normalized vertical displacement', 'C04: Point vs Toe'], w2)
    pdf.table_row(['presence_thresh.', 'Part arc as fraction of total', 'C10: Heel presence'], w2)
    pdf.table_row(['sinuosity_w_dir', 'Signed sinuosity (in/outward)', 'C04: Point direction'], w2)
    pdf.table_row(['angle_betw_parts', 'Angle at fork between parts', 'A09: Shaft-root angle'], w2)
    pdf.ln(3)

    pdf.section_title('State Mapping')
    pdf.body_text(
        'Each raw numeric value is mapped to a discrete state using threshold ranges defined in the '
        'character definition. For example, if a junction angle is < 80 degrees it may be state "1" '
        '(recurved), 80-140 degrees state "2" (approximately 90 degrees), and > 140 degrees state "0" '
        '(evenly curved). A confidence score is computed based on distance from the nearest threshold '
        'boundary: farther from boundary = higher confidence.'
    )

    # ── 12. Data Storage ──
    pdf.add_page()
    pdf.chapter_title('12', 'Data Storage and Backup')

    pdf.section_title('Where Data Lives')
    pdf.bullet('data/db.sqlite - SQLite database containing all structured data: specimens, structures, landmark coordinates, part boundaries, character definitions, character values, DNA sequences, descriptions, diagnoses, and activity logs. This is the single source of truth.')
    pdf.ln(1)
    pdf.bullet('data/uploads/ - Specimen images (PNG, JPG, GIF) copied during image import.')
    pdf.ln(3)

    pdf.section_title('Important Notes')
    pdf.bullet('The original CSV landmark files from ImageJ are NOT stored as files in the application. Their coordinates are parsed at import time and saved into the database. The original CSVs remain wherever you pointed the importer at.')
    pdf.ln(1)
    pdf.bullet('Export routes read directly from the database and generate output files on the fly.')
    pdf.ln(1)
    pdf.bullet('To back up a project, copy the entire data/ directory.')
    pdf.ln(3)

    pdf.note_box('Backup tip: periodically copy the data/ folder to a safe location. The db.sqlite file contains everything except the images.')

    # ── 13. Troubleshooting ──
    pdf.add_page()
    pdf.chapter_title('13', 'Troubleshooting')

    problems = [
        ('"Database is locked" error',
         'This occurs when multiple processes access SQLite simultaneously. The app has a 30-second timeout. Ensure only one instance of run.py is running. Delete any stale data/db.sqlite-journal file if the error persists.'),
        ('Characters show all "?"',
         'Click "Compute All Characters" on the project page. Characters require both landmarks and part boundaries to be confirmed before computation can proceed.'),
        ('Species names not matching on import',
         'Use the "Scan" button to preview how filenames are parsed before importing. The parser handles underscores, concatenated names, accession prefixes, and pipe-separated formats.'),
        ('Thresholds seem wrong for my data',
         'After importing new data, check the distribution of raw values via the Character Workshop. Adjust thresholds based on the actual data range for your taxon. The default thresholds are calibrated for Procrustes-normalized Gyrodactylidae data.'),
        ('Gallery shapes have no colors',
         'Part colors appear when a color legend is available. Ensure boundaries are confirmed for the structure type being viewed.'),
        ('ImageJ macro cannot find images',
         'Verify the input directory path in the macro setup dialog. The directory must contain image files (TIF, TIFF, JPG, JPEG, PNG, BMP, or GIF).'),
    ]

    for title, desc in problems:
        pdf.set_font('Helvetica', 'B', 10)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(0, 7, title, new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('Helvetica', '', 10)
        pdf.multi_cell(0, 5.5, desc)
        pdf.ln(3)

    # ── Save ──
    output_path = 'GyroMorpho_v2_Manual.pdf'
    pdf.output(output_path)
    print(f'Manual saved to: {output_path}')
    return output_path


if __name__ == '__main__':
    build_manual()
