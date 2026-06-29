#!/usr/bin/env python3
"""Generate the GyroMorpho Quick-Start Guide as a PDF.

A gentle, plain-language guide written for taxonomists and students who are
NOT software people. It assumes the app is already running on a website and
the reader just needs to open it in a browser and start working. No command
line, no installation, no jargon left unexplained.
"""

from fpdf import FPDF


BLUE = (40, 80, 160)
GRAY = (90, 90, 90)
INK = (30, 30, 30)


class QuickStart(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_font('Helvetica', 'I', 8)
            self.set_text_color(140, 140, 140)
            self.cell(0, 8, 'GyroMorpho - Quick-Start Guide', align='L')
            self.ln(4)
            self.set_draw_color(210, 210, 210)
            self.line(10, self.get_y(), 200, self.get_y())
            self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', align='C')

    def chapter_title(self, num, title):
        self.set_font('Helvetica', 'B', 17)
        self.set_text_color(*BLUE)
        self.ln(3)
        self.cell(0, 10, f'{num}. {title}', new_x='LMARGIN', new_y='NEXT')
        self.set_draw_color(*BLUE)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def section_title(self, title):
        self.set_font('Helvetica', 'B', 12)
        self.set_text_color(60, 60, 60)
        self.ln(2)
        self.cell(0, 8, title, new_x='LMARGIN', new_y='NEXT')
        self.ln(1)

    def body(self, text):
        self.set_font('Helvetica', '', 11)
        self.set_text_color(*INK)
        self.set_x(10)
        self.multi_cell(0, 6, text)
        self.ln(1.5)

    def bullet(self, text):
        self.set_font('Helvetica', '', 11)
        self.set_text_color(*INK)
        self.set_x(12)
        self.multi_cell(0, 6, '  -  ' + text)

    def step(self, num, text):
        self.set_x(12)
        self.set_font('Helvetica', 'B', 11)
        self.set_text_color(*BLUE)
        self.cell(7, 6, f'{num}.')
        self.set_font('Helvetica', '', 11)
        self.set_text_color(*INK)
        self.multi_cell(0, 6, text)
        self.ln(0.5)

    def note(self, text, label='Good to know'):
        self.set_fill_color(240, 247, 255)
        self.set_draw_color(*BLUE)
        self.set_font('Helvetica', 'BI', 9.5)
        self.set_text_color(*BLUE)
        y = self.get_y()
        self.set_xy(12, y + 2)
        self.multi_cell(186, 5.5, f'{label}:  {text}', border=1, fill=True)
        self.ln(3)

    def reassure(self, text):
        self.set_fill_color(240, 250, 242)
        self.set_draw_color(60, 150, 90)
        self.set_font('Helvetica', 'I', 9.5)
        self.set_text_color(40, 110, 70)
        y = self.get_y()
        self.set_xy(12, y + 2)
        self.multi_cell(186, 5.5, 'Don\'t worry:  ' + text, border=1, fill=True)
        self.ln(3)

    def glossary_term(self, term, definition):
        self.set_font('Helvetica', 'B', 11)
        self.set_text_color(*BLUE)
        self.set_x(10)
        self.multi_cell(0, 6, term)
        self.set_font('Helvetica', '', 11)
        self.set_text_color(*INK)
        self.set_x(14)
        self.multi_cell(0, 6, definition)
        self.ln(1.5)


def build():
    pdf = QuickStart()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)

    # ── Cover ──
    pdf.add_page()
    pdf.ln(45)
    pdf.set_font('Helvetica', 'B', 30)
    pdf.set_text_color(*BLUE)
    pdf.cell(0, 14, 'GyroMorpho', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('Helvetica', 'B', 18)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 11, 'Quick-Start Guide', align='C', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(6)
    pdf.set_font('Helvetica', 'I', 12)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(0, 8, 'For first-time users - no computer experience needed', align='C',
             new_x='LMARGIN', new_y='NEXT')
    pdf.ln(20)
    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(*INK)
    pdf.set_x(25)
    pdf.multi_cell(160, 6,
        'This short guide walks you through GyroMorpho in plain language. You only need a '
        'web browser and the address of the GyroMorpho website. There is nothing to install '
        'and nothing you can permanently break. Take it one page at a time.',
        align='C')
    pdf.ln(25)
    pdf.set_font('Helvetica', 'I', 9)
    pdf.set_text_color(130, 130, 130)
    pdf.cell(0, 6, 'Version 1.0 - June 2026', align='C', new_x='LMARGIN', new_y='NEXT')

    # ── Contents ──
    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 19)
    pdf.set_text_color(*BLUE)
    pdf.cell(0, 12, 'What\'s Inside', new_x='LMARGIN', new_y='NEXT')
    pdf.ln(4)
    toc = [
        ('1', 'What GyroMorpho Does (in one minute)'),
        ('2', 'What You Need Before You Start'),
        ('3', 'Logging In for the First Time'),
        ('4', 'The Big Picture: How the Work Flows'),
        ('5', 'Finding Your Way Around the Screen'),
        ('6', 'Doing the Work, Step by Step'),
        ('7', 'Working Together: Sharing and Comments'),
        ('8', 'Common Worries, Answered'),
        ('9', 'A Plain-English Word List'),
        ('10', 'Getting Help'),
    ]
    for num, title in toc:
        pdf.set_font('Helvetica', '', 12)
        pdf.set_text_color(*INK)
        pdf.cell(12, 8, num + '.')
        pdf.cell(0, 8, title, new_x='LMARGIN', new_y='NEXT')

    # ── 1. What it does ──
    pdf.add_page()
    pdf.chapter_title('1', 'What GyroMorpho Does (in one minute)')
    pdf.body(
        'GyroMorpho helps you describe and compare the tiny hard structures (hooks, anchors, bars, '
        'and the male copulatory organ) of monogenoidean flatworms - the kind used to tell species apart.'
    )
    pdf.body('In everyday terms, it lets you:')
    pdf.bullet('Store your specimens and their pictures in one tidy place.')
    pdf.bullet('Turn the outline of each structure into a set of points the computer can measure.')
    pdf.bullet('Measure shapes consistently, the same way every time, without a ruler.')
    pdf.bullet('Sort specimens into character "states" (for example: hook point straight vs. curved).')
    pdf.bullet('Write first-draft species descriptions and comparison tables for you.')
    pdf.bullet('Hand you a finished data table ready for family-tree (phylogenetic) software.')
    pdf.ln(2)
    pdf.note('You stay in charge. The computer suggests measurements and states; you review them and '
             'have the final say on everything.')

    # ── 2. What you need ──
    pdf.add_page()
    pdf.chapter_title('2', 'What You Need Before You Start')
    pdf.bullet('A computer (Windows, Mac, or Linux) with a web browser such as Chrome, Firefox, Safari, or Edge.')
    pdf.bullet('The web address (link) of your GyroMorpho site. A colleague or administrator gives you this.')
    pdf.bullet('A username and password. You create these the first time, or someone shares a project with you.')
    pdf.bullet('Your specimen photographs, and ideally the landmark files already prepared (see the big '
               'picture on the next page).')
    pdf.ln(2)
    pdf.reassure('You do NOT need to install anything, use a command line, or understand any code. '
                 'If you can use email in a browser, you can use GyroMorpho.')

    # ── 3. Logging in ──
    pdf.add_page()
    pdf.chapter_title('3', 'Logging In for the First Time')
    pdf.step(1, 'Open your web browser and type (or paste) the GyroMorpho address into the bar at the top, then press Enter.')
    pdf.step(2, 'If you do not have an account yet, click "Register" and choose a username, email, and password. Write your password down somewhere safe.')
    pdf.step(3, 'If you already have an account, click "Log in" and enter your username and password.')
    pdf.step(4, 'You arrive at your Dashboard - the home screen that lists your projects.')
    pdf.ln(2)
    pdf.note('A "project" is one study - usually one genus or one paper\'s worth of specimens. '
             'You can have several projects, and you only see the ones you own or that someone shared with you.')

    # ── 4. Big picture ──
    pdf.add_page()
    pdf.chapter_title('4', 'The Big Picture: How the Work Flows')
    pdf.body(
        'The whole job is a short assembly line. Each step feeds the next. You can always go back and fix '
        'an earlier step. Here is the line, in plain words:'
    )
    flow = [
        ('Add specimens', 'Tell GyroMorpho which species you are studying, and attach their pictures.'),
        ('Bring in landmarks', 'Load the points that trace each structure\'s outline. These usually come '
                               'from a free program called ImageJ, prepared ahead of time.'),
        ('Mark the parts', 'On each outline, colour in which points belong to which part (the point, the '
                           'shaft, the toe, and so on). This is like labelling regions on a map.'),
        ('Set the characters', 'A "character" is one feature you compare across species (for example, how '
                              'curved the shaft is). Each character has a few "states" - the possible answers.'),
        ('Code and review', 'GyroMorpho measures the shapes and proposes a state for each specimen. You '
                           'look through them in the Gallery and confirm or correct.'),
        ('Descriptions', 'It writes draft species descriptions and comparison tables from your states.'),
        ('Export', 'It saves a finished table you can open in a spreadsheet or feed to tree-building software.'),
    ]
    for i, (name, desc) in enumerate(flow, 1):
        pdf.set_x(10)
        pdf.set_font('Helvetica', 'B', 11)
        pdf.set_text_color(*BLUE)
        pdf.cell(7, 6, f'{i}.')
        pdf.cell(42, 6, name)
        pdf.set_font('Helvetica', '', 11)
        pdf.set_text_color(*INK)
        pdf.multi_cell(0, 6, desc)
        pdf.ln(1.5)
    pdf.ln(1)
    pdf.note('At the top of every project page there is a "Pipeline" bar showing these steps. '
             'When you feel lost, look there to see where you are.')

    # ── 5. Finding your way ──
    pdf.add_page()
    pdf.chapter_title('5', 'Finding Your Way Around the Screen')
    pdf.section_title('The Dashboard')
    pdf.body('Your home screen. It lists your projects. Click a project to open it, or click '
             '"New Project" to start a fresh study.')
    pdf.section_title('The Project Page (Specimens)')
    pdf.body('The heart of a project. Here you see every specimen as a row, with its pictures, its '
             'structures, its DNA tags, and buttons to work on each one. The buttons near the top let '
             'you add specimens, import data, and check your progress.')
    pdf.section_title('Buttons and links')
    pdf.bullet('Blue buttons usually start an action (Add, Share, Save).')
    pdf.bullet('Outline (white) buttons are gentler options or tools.')
    pdf.bullet('Red buttons delete things - they always ask you to confirm first.')
    pdf.ln(2)
    pdf.reassure('Clicking around to look is safe. Nothing changes until you press a Save, Add, Post, '
                 'or Confirm button. Closing a pop-up window cancels it.')

    # ── 6. Step by step ──
    pdf.add_page()
    pdf.chapter_title('6', 'Doing the Work, Step by Step')

    pdf.section_title('A. Add a specimen')
    pdf.step(1, 'On the project page, click "Add Specimen".')
    pdf.step(2, 'Type the species name (for example, Gyrodactylus salaris).')
    pdf.step(3, 'Optionally add a specimen ID label and notes, then save.')
    pdf.note('Already have many specimens in a spreadsheet or in folders? Use "Bulk Import CSV" or '
             '"Import from Folders" instead of adding them one by one.')

    pdf.section_title('B. Bring in the landmark points')
    pdf.body('Landmarks are the dots that trace a structure\'s outline. They are normally produced in '
             'ImageJ using the provided macros, then loaded here as small CSV files. If a colleague '
             'prepared them, just import the files; you do not have to run ImageJ yourself.')
    pdf.bullet('Use "Import from Folders" or the "Import Landmarks from ImageJ Macro" box and pick the ZIP or folder.')

    pdf.section_title('C. Mark the parts (boundaries)')
    pdf.body('Open a specimen\'s structure and click "BND" (boundaries). You will see the outline as a '
             'string of dots. Pick a part name, then click or drag across the dots that belong to it. '
             'Repeat for each part, and press "Confirm" when done.')
    pdf.reassure('There is an "Undo" button and a "Copy from similar" helper that borrows the labelling '
                 'from a look-alike specimen you already finished. You can redo a boundary any time.')

    pdf.section_title('D. Look at the characters')
    pdf.body('Click "Character Workshop". A character is one feature you compare; its states are the '
             'possible answers. The project already comes with a full set of standard characters, so '
             'usually you only review them rather than invent new ones.')

    pdf.add_page()
    pdf.section_title('E. Code and review in the Gallery')
    pdf.body('This is where most of your reviewing happens, and it is mostly clicking.')
    pdf.step(1, 'Open the Character Matrix, then click a character code to open its Gallery.')
    pdf.step(2, 'You see every specimen side by side, each with its shape and a suggested state.')
    pdf.step(3, 'Agree? Leave it. Disagree? Click the correct state button under that specimen.')
    pdf.step(4, 'Use the zoom (click a picture) to inspect closely before deciding.')
    pdf.ln(1)
    pdf.body('Need to fix the list of possible states (rename one, add one, remove one)? Click '
             '"Edit states" at the top of the Gallery, make your changes, and Save. The buttons update '
             'right away.')

    pdf.section_title('F. Generate descriptions')
    pdf.body('Click "Descriptions". GyroMorpho writes a first draft for each species from your states. '
             'Click "Regenerate" after you change states so the text stays in step. You can edit the wording.')

    pdf.section_title('G. Export your results')
    pdf.body('Click "Export" and choose a format. Use CSV to open in Excel or Google Sheets; use Nexus '
             'or TNT for tree-building programs. The file downloads to your computer like any other download.')

    # ── 7. Collaboration ──
    pdf.add_page()
    pdf.chapter_title('7', 'Working Together: Sharing and Comments')
    pdf.section_title('Share a project with a colleague')
    pdf.step(1, 'Open the project and find the "Members & Sharing" panel.')
    pdf.step(2, 'Type your colleague\'s username or email. A suggestion list helps you pick the right person.')
    pdf.step(3, 'Choose what they can do - Annotator, Reviewer, or Admin - and click "Share".')
    pdf.body('The project then appears on their dashboard. Anyone on the project can share it; the owner '
             'and Admins can remove people with the "Remove" button. The owner has a small "owner" badge '
             'and cannot be removed.')
    pdf.note('Not sure which role to pick? "Annotator" is a safe default - they can do the coding work. '
             'Give "Admin" only to people you trust to manage who has access.')

    pdf.section_title('Leave comments on a specimen')
    pdf.body('Every specimen row has a small speech-bubble button with a number on it. Click it to open '
             'a comment thread - perfect for questions like "Is this image upside down?" or notes about a '
             'tricky identification.')
    pdf.bullet('Type your message and click "Post" (or hold Ctrl/Cmd and press Enter).')
    pdf.bullet('Each comment shows who wrote it and when.')
    pdf.bullet('You can delete your own comments; Admins can delete any. The number updates automatically.')

    # ── 8. Common worries ──
    pdf.add_page()
    pdf.chapter_title('8', 'Common Worries, Answered')
    worries = [
        ('"Will I break something by clicking?"',
         'No. Looking and clicking around is safe. Real changes only happen when you press Save, Add, '
         'Post, Confirm, or a red Delete button (which asks you to confirm first).'),
        ('"I made a mistake. Can I undo it?"',
         'Almost always. States can be re-coded, boundaries redrawn, descriptions regenerated, comments '
         'deleted. Deleting a whole specimen is the main thing that is permanent - and it warns you clearly.'),
        ('"The pictures or shapes are blank."',
         'The structure probably has no image yet, or its boundaries are not confirmed. Add the image, '
         'or confirm the parts, and it will appear.'),
        ('"Everything shows a question mark (?)."',
         'That means the computer has not measured those characters yet. Click "Compute All Characters" '
         'on the project page. Characters need landmarks AND confirmed parts first.'),
        ('"I lost my place / jumped to the top of the page."',
         'Scroll back down - your work is saved. The Pipeline bar at the top always shows which step you are on.'),
        ('"Someone else is editing the same project."',
         'That is fine - sharing is meant for teamwork. Use specimen comments to coordinate who does what.'),
    ]
    for q, a in worries:
        pdf.set_font('Helvetica', 'B', 11)
        pdf.set_text_color(*INK)
        pdf.set_x(10)
        pdf.multi_cell(0, 6, q)
        pdf.set_font('Helvetica', '', 11)
        pdf.set_x(12)
        pdf.multi_cell(0, 6, a)
        pdf.ln(2)

    # ── 9. Word list ──
    pdf.add_page()
    pdf.chapter_title('9', 'A Plain-English Word List')
    terms = [
        ('Specimen', 'One individual you are studying, usually identified to a species.'),
        ('Landmark', 'A single point placed on the outline of a structure. Many landmarks together '
                     'trace the whole shape so the computer can measure it.'),
        ('Structure', 'One hard part you analyse: a hook, an anchor, a bar, or the male copulatory organ (MCO).'),
        ('Part / Boundary', 'A named region of a structure (point, shaft, toe...). Marking boundaries means '
                            'telling the computer which landmarks belong to which region.'),
        ('Character', 'One feature you compare across species - for example, "shaft curvature".'),
        ('State', 'One of the possible answers for a character - for example, "straight", "curved". '
                  'Each specimen gets one state per character.'),
        ('Matrix', 'The big table with species down the side and characters across the top, filled with states.'),
        ('GPA (Procrustes)', 'A fair way of comparing shapes: the computer removes differences in size, '
                             'position, and rotation so only the true shape is compared. You do not have to do anything for this.'),
        ('Taxon', 'A named group of organisms, such as a genus or a family.'),
        ('Export', 'Saving your results as a file on your computer to use elsewhere.'),
    ]
    for term, definition in terms:
        pdf.glossary_term(term, definition)

    # ── 10. Help ──
    pdf.add_page()
    pdf.chapter_title('10', 'Getting Help')
    pdf.body('If you are stuck:')
    pdf.bullet('Look at the Pipeline bar at the top of the project page to see which step you are on.')
    pdf.bullet('Re-read the matching step in Section 6 of this guide.')
    pdf.bullet('Leave a comment on the specimen so a colleague can see exactly what you mean.')
    pdf.bullet('Ask the person who runs your GyroMorpho site - they can check accounts and settings.')
    pdf.bullet('For deeper technical detail (installation, how measurements are computed, exports), '
               'see the full "GyroMorpho v2 - User Manual".')
    pdf.ln(3)
    pdf.note('Take it slowly, one specimen and one character at a time. The work adds up faster than '
             'you expect, and nothing you do in a hurry is hard to fix later.', label='Final tip')

    out = 'GyroMorpho_QuickStart_Guide.pdf'
    pdf.output(out)
    print(f'Quick-start guide saved to: {out}')
    return out


if __name__ == '__main__':
    build()
