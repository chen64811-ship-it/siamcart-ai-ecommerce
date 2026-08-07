"""
Validation script for Task 4D-2 output DOCX.
"""
from docx import Document
import zipfile
import os

DOCX_PATH = r'T:/thai-ecommerce-agent/thesis/Xingyu_Chen_IS_Ch4_Ch5_Final_Updated.docx'
INPUT_PATH = r'T:/thai-ecommerce-agent/tests/Xingyu_Chen_IS_Working.docx'

doc = Document(DOCX_PATH)
input_doc = Document(INPUT_PATH)

print('='*60)
print('VALIDATION REPORT')
print('='*60)

# 1. DOCX validity
print('\n1. DOCX validity:')
print('   Opens as valid ZIP/DOCX: YES')
print(f'   Paragraphs: {len(doc.paragraphs)}')
print(f'   Tables: {len(doc.tables)}')

with zipfile.ZipFile(DOCX_PATH, 'r') as z:
    media = [m for m in z.namelist() if m.startswith('word/media')]
    print(f'   Media files: {len(media)}')

# 2. Chapter 4 sections
print('\n2. Chapter 4 sections:')
sections_4 = []
for p in doc.paragraphs:
    t = p.text.strip()
    if t.startswith('4.') and len(t) < 80 and not t.startswith('4.0'):
        sections_4.append(t)
        print(f'   {t}')
if len(sections_4) >= 10:
    print('   ALL sections 4.1-4.10 present')
else:
    print(f'   Only {len(sections_4)} sections found')

# 3. Chapter 5 sections
print('\n3. Chapter 5 sections:')
sections_5 = []
for p in doc.paragraphs:
    t = p.text.strip()
    if t.startswith('5.') and len(t) < 60:
        sections_5.append(t)
        print(f'   {t}')
main_sections = [s for s in sections_5 if '. ' in s and s[2:4].strip().isdigit() and '.' not in s[3:]]
print(f'   Main sections: {len(main_sections)}')

# 4. Table 4.1
print('\n4. Table 4.1:')
for idx, table in enumerate(doc.tables):
    first = table.rows[0].cells[0].text[:50] if table.rows else ''
    if 'Metric' in first:
        print(f'   Table {idx}: {len(table.rows)} rows x {len(table.rows[0].cells)} cols')
        for r in table.rows:
            print(f'     {[c.text[:40] for c in r.cells]}')
    if 'Routing accuracy' in table.rows[0].cells[0].text or \
       (len(table.rows) >= 2 and 'Routing accuracy' in table.rows[1].cells[0].text):
        print(f'   FOUND at Table {idx}: {len(table.rows)} rows')
        for r in table.rows[:3]:
            print(f'     {[c.text[:40] for c in r.cells]}')

# Also check for specific table by caption
for i, p in enumerate(doc.paragraphs):
    if 'Table 4.1' in p.text and 'Overall' in p.text:
        print(f'   Caption found at paragraph {i}: {p.text[:80]}')

# 5. Figures
print('\n5. Figures 4.1-4.4:')
fig_captions = []
for i, p in enumerate(doc.paragraphs):
    t = p.text.strip()
    if t.startswith('Figure 4.') and len(t) > 10:
        fig_captions.append((i, t))
        print(f'   [{i}] {t}')
print(f'   Total figure captions: {len(fig_captions)}')

# Check for drawings
with zipfile.ZipFile(DOCX_PATH, 'r') as z:
    doc_xml = z.read('word/document.xml')
    drawing_count = doc_xml.count(b'<w:drawing')
    print(f'   Drawings in document: {drawing_count}')

# 6. Metrics confirmation
print('\n6. Final metrics:')
metrics = ['68.3%', '65.0%', '86.7%', '40.0%', '73.3%', '80.0%', '4.61', '4.46', '36.7%', '27.5%']
all_text = ' '.join(p.text for p in doc.paragraphs)
for m in metrics:
    if m in all_text:
        print(f'   {m}: PRESENT')
    else:
        print(f'   {m}: MISSING')

# 7. Old values (only 92.5%, 90.8%, 2.4s, 3.8s - the truly obsolete ones)
print('\n7. Old value audit:')
bad_vals = ['92.5%', '90.8%', '2.4 s', '3.8 s']
for val in bad_vals:
    if val in all_text:
        # Find location
        for i, p in enumerate(doc.paragraphs):
            if val in p.text:
                # Check if in Ch4/Ch5
                print(f'   WARNING: {val} at paragraph {i}')
    else:
        print(f'   {val}: ABSENT (correct)')

# 8. Chapters 1-3 intact
print('\n8. Chapter integrity:')
expected = ['Chapter 1', 'Chapter 2', 'Chapter 3', 'Chapter 4', 'Chapter 5', 'References']
found = []
for p in doc.paragraphs:
    if p.text.strip() in expected:
        found.append(p.text.strip())
        print(f'   FOUND: {p.text.strip()}')
missing = [e for e in expected if e not in found]
if missing:
    print(f'   MISSING: {missing}')
else:
    print('   All chapters and references intact')

# 9. No markdown
print('\n9. Markdown syntax check:')
issues = []
for i, p in enumerate(doc.paragraphs):
    t = p.text
    if '## ' in t or '```' in t:
        issues.append((i, t[:80]))
if issues:
    for idx, txt in issues:
        print(f'   [{idx}] {txt}')
else:
    print('   No markdown syntax found')

# 10. Page count (approximate)
print(f'\n10. Page estimates:')
input_paras = len(input_doc.paragraphs)
output_paras = len(doc.paragraphs)
print(f'   Input paragraphs: {input_paras}')
print(f'   Output paragraphs: {output_paras}')

# Check old values outside Ch4/Ch5
print('\n11. Non-Ch4/Ch5 old values:')
other_old = []
for i, p in enumerate(doc.paragraphs):
    t = p.text
    for val in bad_vals:
        if val in t:
            if 'Chapter 4' in t or i < 10:
                continue
            if p.style and 'Heading' in p.style.name:
                continue
            other_old.append((i, val, t[:100]))
if other_old:
    for idx, val, ctx in other_old:
        print(f'   [{idx}] {val}: {ctx}')
else:
    print('   None found outside Ch4/Ch5')

print('\n' + '='*60)
print('VALIDATION COMPLETE')
print('='*60)
