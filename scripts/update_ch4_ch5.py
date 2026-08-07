"""
Task 4D-2: Insert finalized Chapter 4 results and update Chapter 5.

Replaces Chapter 4, updates Chapter 5, inserts Table 4.1 and Figures 4.1-4.4,
updates Abstract with final values.
"""

import shutil
import os
import re
import csv
from copy import deepcopy
from lxml import etree
from docx import Document
from docx.shared import Pt, Emu, Cm, Inches
from docx.oxml.ns import qn
from docx.oxml import OxmlElement, parse_xml
from docx.enum.text import WD_ALIGN_PARAGRAPH
from PIL import Image as PILImage

# ============================================================
# PATHS
# ============================================================
INPUT_PATH = r'T:/thai-ecommerce-agent/tests/Xingyu_Chen_IS_Working.docx'
OUTPUT_PATH = r'T:/thai-ecommerce-agent/thesis/Xingyu_Chen_IS_Ch4_Ch5_Final_Updated.docx'
THESIS_DIR = r'T:/thai-ecommerce-agent/thesis'
REPORT_PATH = os.path.join(THESIS_DIR, 'chapter4_5_update_report.md')

DRAFT_PATH = r'T:/thai-ecommerce-agent/data/evaluation/results/chapter4_results_draft.md'
CSV_TABLE41 = r'T:/thai-ecommerce-agent/data/evaluation/results/chapter4_table_4_1_formatted.csv'
NEW_FIG41 = r'T:/thai-ecommerce-agent/data/evaluation/figures/figure_4_1_performance_metrics.png'
FIG42 = r'T:/thai-ecommerce-agent/data/evaluation/figures/figure_4_2_response_time.png'
FIG43 = r'T:/thai-ecommerce-agent/data/evaluation/figures/figure_4_3_reliability_comparison.png'
FIG44 = r'T:/thai-ecommerce-agent/data/evaluation/figures/figure_4_4_routing_by_category.png'

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
WP_NS = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
A_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main'
PIC_NS = 'http://schemas.openxmlformats.org/drawingml/2006/picture'


def make_normal_element(text):
    """Create a Normal paragraph element."""
    p = OxmlElement('w:p')
    pPr = OxmlElement('w:pPr')
    p.append(pPr)
    
    pStyle = OxmlElement('w:pStyle')
    pStyle.set(qn('w:val'), 'Normal')
    pPr.append(pStyle)
    
    jc = OxmlElement('w:jc')
    jc.set(qn('w:val'), 'both')
    pPr.append(jc)
    
    spacing = OxmlElement('w:spacing')
    spacing.set(qn('w:line'), '360')
    spacing.set(qn('w:lineRule'), 'auto')
    pPr.append(spacing)
    
    r = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')
    r.append(rPr)
    rFonts = OxmlElement('w:rFonts')
    rFonts.set(qn('w:ascii'), 'Times New Roman')
    rFonts.set(qn('w:hAnsi'), 'Times New Roman')
    rPr.append(rFonts)
    sz = OxmlElement('w:sz'); sz.set(qn('w:val'), '24'); rPr.append(sz)
    szCs = OxmlElement('w:szCs'); szCs.set(qn('w:val'), '24'); rPr.append(szCs)
    t = OxmlElement('w:t')
    t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    t.text = text
    r.append(t)
    p.append(r)
    return p


def make_heading2_element(text):
    """Create Heading 2 paragraph."""
    p = OxmlElement('w:p')
    pPr = OxmlElement('w:pPr')
    p.append(pPr)
    pStyle = OxmlElement('w:pStyle'); pStyle.set(qn('w:val'), 'Heading 2'); pPr.append(pStyle)
    spacing = OxmlElement('w:spacing'); spacing.set(qn('w:line'), '360'); spacing.set(qn('w:lineRule'), 'auto'); pPr.append(spacing)
    r = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')
    r.append(rPr)
    rFonts = OxmlElement('w:rFonts'); rFonts.set(qn('w:ascii'), 'Times New Roman'); rFonts.set(qn('w:hAnsi'), 'Times New Roman'); rPr.append(rFonts)
    sz = OxmlElement('w:sz'); sz.set(qn('w:val'), '24'); rPr.append(sz)
    szCs = OxmlElement('w:szCs'); szCs.set(qn('w:val'), '24'); rPr.append(szCs)
    b = OxmlElement('w:b'); rPr.append(b); bCs = OxmlElement('w:bCs'); rPr.append(bCs)
    t = OxmlElement('w:t'); t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve'); t.text = text; r.append(t)
    p.append(r)
    return p


def make_heading3_element(text):
    """Create Heading 3 paragraph."""
    p = OxmlElement('w:p')
    pPr = OxmlElement('w:pPr')
    p.append(pPr)
    pStyle = OxmlElement('w:pStyle'); pStyle.set(qn('w:val'), 'Heading 3'); pPr.append(pStyle)
    spacing = OxmlElement('w:spacing'); spacing.set(qn('w:line'), '360'); spacing.set(qn('w:lineRule'), 'auto'); pPr.append(spacing)
    r = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')
    r.append(rPr)
    rFonts = OxmlElement('w:rFonts'); rFonts.set(qn('w:ascii'), 'Times New Roman'); rFonts.set(qn('w:hAnsi'), 'Times New Roman'); rPr.append(rFonts)
    sz = OxmlElement('w:sz'); sz.set(qn('w:val'), '24'); rPr.append(sz)
    szCs = OxmlElement('w:szCs'); szCs.set(qn('w:val'), '24'); rPr.append(szCs)
    b = OxmlElement('w:b'); rPr.append(b); bCs = OxmlElement('w:bCs'); rPr.append(bCs)
    t = OxmlElement('w:t'); t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve'); t.text = text; r.append(t)
    p.append(r)
    return p


def make_subtitle_element(text):
    """Create Chapter Subtitle paragraph."""
    p = OxmlElement('w:p')
    pPr = OxmlElement('w:pPr')
    p.append(pPr)
    pStyle = OxmlElement('w:pStyle'); pStyle.set(qn('w:val'), 'Chapter Subtitle'); pPr.append(pStyle)
    jc = OxmlElement('w:jc'); jc.set(qn('w:val'), 'center'); pPr.append(jc)
    spacing = OxmlElement('w:spacing'); spacing.set(qn('w:after'), '120'); pPr.append(spacing)
    r = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')
    r.append(rPr)
    rFonts = OxmlElement('w:rFonts'); rFonts.set(qn('w:ascii'), 'Times New Roman'); rFonts.set(qn('w:hAnsi'), 'Times New Roman'); rPr.append(rFonts)
    sz = OxmlElement('w:sz'); sz.set(qn('w:val'), '28'); rPr.append(sz)
    szCs = OxmlElement('w:szCs'); szCs.set(qn('w:val'), '28'); rPr.append(szCs)
    b = OxmlElement('w:b'); rPr.append(b); bCs = OxmlElement('w:bCs'); rPr.append(bCs)
    t = OxmlElement('w:t'); t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve'); t.text = text; r.append(t)
    p.append(r)
    return p


def make_caption_element(text):
    """Create a centered Caption paragraph."""
    p = OxmlElement('w:p')
    pPr = OxmlElement('w:pPr')
    p.append(pPr)
    pStyle = OxmlElement('w:pStyle'); pStyle.set(qn('w:val'), 'Caption'); pPr.append(pStyle)
    jc = OxmlElement('w:jc'); jc.set(qn('w:val'), 'center'); pPr.append(jc)
    spacing = OxmlElement('w:spacing'); spacing.set(qn('w:line'), '240'); spacing.set(qn('w:lineRule'), 'auto'); pPr.append(spacing)
    r = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')
    r.append(rPr)
    rFonts = OxmlElement('w:rFonts'); rFonts.set(qn('w:ascii'), 'Times New Roman'); rFonts.set(qn('w:hAnsi'), 'Times New Roman'); rPr.append(rFonts)
    sz = OxmlElement('w:sz'); sz.set(qn('w:val'), '20'); rPr.append(sz)
    szCs = OxmlElement('w:szCs'); szCs.set(qn('w:val'), '20'); rPr.append(szCs)
    b = OxmlElement('w:b'); rPr.append(b); bCs = OxmlElement('w:bCs'); rPr.append(bCs)
    i = OxmlElement('w:i'); rPr.append(i); iCs = OxmlElement('w:iCs'); rPr.append(iCs)
    t = OxmlElement('w:t'); t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve'); t.text = text; r.append(t)
    p.append(r)
    return p


def insert_after_element(parent, ref_element, new_element):
    """Insert new_element after ref_element."""
    next_sibling = ref_element.getnext()
    if next_sibling is not None:
        parent.insert(list(parent).index(next_sibling), new_element)
    else:
        parent.append(new_element)


def remove_elements_between(body, start_el, end_el):
    """Remove all elements between start_el (exclusive) and end_el (exclusive)."""
    to_remove = []
    in_range = False
    for child in list(body):
        if child is start_el:
            in_range = True
            continue
        if child is end_el:
            break
        if in_range:
            to_remove.append(child)
    for elem in to_remove:
        body.remove(elem)
    return len(to_remove)


def parse_ch4_draft(filepath):
    """Parse chapter 4 markdown into structured sections.
    Returns list of (type, text) where type is 'h2', 'p'.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    result = []
    current_type = None
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        
        # Skip the top-level # headings (CHAPTER 4, RESULTS AND DISCUSSION)
        if stripped.startswith('# ') or stripped == '# CHAPTER 4' or stripped == '# RESULTS AND DISCUSSION':
            continue
        
        if stripped.startswith('## '):
            # Section heading - remove ## prefix
            text = stripped[3:].strip()
            # Clean markdown bold markers
            text = text.replace('**', '')
            result.append(('h2', text))
        else:
            # Normal paragraph - clean markdown
            clean = stripped.replace('**', '')
            # Handle italic/bold markers
            clean = clean.replace('*', '')
            result.append(('p', clean))
    
    return result


def add_image_to_xml(parent, image_path, width_cm, image_id, rel_id):
    """Create a <w:p> containing an inline image and insert it into parent after ref_element."""
    from lxml import etree as E
    
    width_emu = int(width_cm * 360000)
    with PILImage.open(image_path) as img:
        w, h = img.size
    height_emu = int(width_emu * h / w)
    
    # Create w:p wrapper
    p = OxmlElement('w:p')
    pPr = OxmlElement('w:pPr')
    p.append(pPr)
    jc = OxmlElement('w:jc'); jc.set(qn('w:val'), 'center'); pPr.append(jc)
    spacing = OxmlElement('w:spacing'); spacing.set(qn('w:line'), '240'); spacing.set(qn('w:lineRule'), 'auto'); pPr.append(spacing)
    
    r = OxmlElement('w:r')
    p.append(r)
    drawing = OxmlElement('w:drawing')
    r.append(drawing)
    
    # Build the full inline image XML as string and parse it
    img_xml = f'''<wp:inline xmlns:wp="{WP_NS}" xmlns:a="{A_NS}" xmlns:pic="{PIC_NS}" xmlns:r="{R}" distT="0" distB="0" distL="0" distR="0">
      <wp:extent><wp:cx>{width_emu}</wp:cx><wp:cy>{height_emu}</wp:cy></wp:extent>
      <wp:effectExtent><wp:l>0</wp:l><wp:t>0</wp:t><wp:r>0</wp:r><wp:b>0</wp:b></wp:effectExtent>
      <wp:docPr id="{image_id}" name="Figure_{image_id-3}" descr="Figure_{image_id-3}"/>
      <wp:cNvGraphicFramePr><a:noGrp val="1"/></wp:cNvGraphicFramePr>
      <a:graphic>
        <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
          <pic:pic>
            <pic:nvPicPr><pic:cNvPr id="0" name="Picture_{image_id}"/><pic:cNvPicPr/></pic:nvPicPr>
            <pic:blipFill><a:blip r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
            <pic:spPr>
              <a:xfrm><a:off x="0" y="0"/><a:ext cx="{width_emu}" cy="{height_emu}"/></a:xfrm>
              <a:prstGeom prst="rect"/>
            </pic:spPr>
          </pic:pic>
        </a:graphicData>
      </a:graphic>
    </wp:inline>'''
    
    inline_elem = E.fromstring(img_xml)
    drawing.append(inline_elem)
    return p


def add_image_to_docx_package(doc, image_path):
    """Add image to docx package and return (rel_id, bytes_written)."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    image_part = doc.part.get_or_add_image(image_path)
    rId = doc.part.relate_to(image_part, RT.IMAGE)
    return rId


def build_table_41():
    """Build Table 4.1 as OxmlElement."""
    with open(CSV_TABLE41, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        rows = list(reader)
    
    header = rows[0]
    data = rows[1:6]
    
    tbl = OxmlElement('w:tbl')
    tblPr = OxmlElement('w:tblPr')
    tbl.append(tblPr)
    
    tblStyle = OxmlElement('w:tblStyle'); tblStyle.set(qn('w:val'), 'Table'); tblPr.append(tblStyle)
    tblW = OxmlElement('w:tblW'); tblW.set(qn('w:w'), '5000'); tblW.set(qn('w:type'), 'pct'); tblPr.append(tblW)
    
    borders = OxmlElement('w:tblBorders')
    for bname in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
        b = OxmlElement(f'w:{bname}')
        b.set(qn('w:val'), 'single'); b.set(qn('w:sz'), '4')
        b.set(qn('w:space'), '0'); b.set(qn('w:color'), '000000')
        borders.append(b)
    tblPr.append(borders)
    
    tblLook = OxmlElement('w:tblLook'); tblLook.set(qn('w:val'), '04A0'); tblPr.append(tblLook)
    
    col_widths = [1600, 1000, 1000, 700, 2200]
    
    def make_cell(text, is_header=False, col_idx=0):
        tc = OxmlElement('w:tc')
        tcPr = OxmlElement('w:tcPr')
        tc.append(tcPr)
        tcW = OxmlElement('w:tcW'); tcW.set(qn('w:w'), str(col_widths[col_idx])); tcW.set(qn('w:type'), 'dxa'); tcPr.append(tcW)
        
        if is_header:
            shd = OxmlElement('w:shd'); shd.set(qn('w:val'), 'clear'); shd.set(qn('w:color'), 'auto'); shd.set(qn('w:fill'), 'D9E2F3'); tcPr.append(shd)
        
        # Cell margins
        tcMar = OxmlElement('w:tcMar')
        for side in ['top', 'left', 'bottom', 'right']:
            m = OxmlElement(f'w:{side}'); m.set(qn('w:w'), '40'); m.set(qn('w:type'), 'dxa'); tcMar.append(m)
        tcPr.append(tcMar)
        
        p = OxmlElement('w:p')
        pPr = OxmlElement('w:pPr')
        p.append(pPr)
        spacing = OxmlElement('w:spacing'); spacing.set(qn('w:line'), '240'); spacing.set(qn('w:lineRule'), 'auto'); pPr.append(spacing)
        jc = OxmlElement('w:jc'); jc.set(qn('w:val'), 'center' if col_idx not in [0, 4] else 'left'); pPr.append(jc)
        
        r = OxmlElement('w:r')
        rPr = OxmlElement('w:rPr')
        r.append(rPr)
        rFonts = OxmlElement('w:rFonts'); rFonts.set(qn('w:ascii'), 'Times New Roman'); rFonts.set(qn('w:hAnsi'), 'Times New Roman'); rPr.append(rFonts)
        sz = OxmlElement('w:sz'); sz.set(qn('w:val'), '20'); rPr.append(sz)
        szCs = OxmlElement('w:szCs'); szCs.set(qn('w:val'), '20'); rPr.append(szCs)
        if is_header:
            b = OxmlElement('w:b'); rPr.append(b); bCs = OxmlElement('w:bCs'); rPr.append(bCs)
        
        t = OxmlElement('w:t'); t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve'); t.text = text
        r.append(t)
        p.append(r)
        tc.append(p)
        return tc
    
    # Header row
    hdr_row = OxmlElement('w:tr')
    hdr_trPr = OxmlElement('w:trPr')
    hdr_row.append(hdr_trPr)
    tblHdr = OxmlElement('w:tblHeader'); tblHdr.set(qn('w:val'), 'true'); hdr_trPr.append(tblHdr)
    
    for ci, cell_text in enumerate(header):
        hdr_row.append(make_cell(cell_text, is_header=True, col_idx=ci))
    tbl.append(hdr_row)
    
    # Data rows
    for row_data in data:
        tr = OxmlElement('w:tr')
        for ci, cell_text in enumerate(row_data):
            tr.append(make_cell(cell_text, is_header=False, col_idx=ci))
        tbl.append(tr)
    
    return tbl


def get_paragraphs_by_text(doc, search_texts):
    """Find paragraph indices containing text. Returns dict of {text: index}."""
    result = {}
    for i, p in enumerate(doc.paragraphs):
        t = p.text.strip()
        for st in search_texts:
            if st in t and st not in result:
                result[st] = i
    return result


def main():
    os.makedirs(THESIS_DIR, exist_ok=True)
    
    # Step 1: Copy input to output
    print('[INFO] Copying input docx...')
    shutil.copy2(INPUT_PATH, OUTPUT_PATH)
    
    # Step 2: Open and edit
    print('[INFO] Opening document...')
    doc = Document(OUTPUT_PATH)
    body = doc.element.body
    
    # Find key paragraph indices
    ch4_start = None
    ch5_start = None
    refs_start = None
    abstract_para = None
    
    for i, p in enumerate(doc.paragraphs):
        t = p.text.strip()
        if t == 'Chapter 4':
            ch4_start = i
        elif t == 'Chapter 5':
            ch5_start = i
        elif t == 'References':
            refs_start = i
        elif i == 87:
            abstract_para = p
    
    print(f'[INFO] Ch4={ch4_start}, Ch5={ch5_start}, Refs={refs_start}')
    
    # Step 3: Update Ch4 subtitle
    if ch4_start is not None:
        ch4_heading_el = doc.paragraphs[ch4_start]._element
        ch4_subtitle_el = ch4_heading_el.getnext()
        if ch4_subtitle_el is not None and ch4_subtitle_el.tag == qn('w:p'):
            for t_el in ch4_subtitle_el.iter(qn('w:t')):
                if t_el.text:
                    t_el.text = 'RESULTS AND DISCUSSION'
                    break
    
    # Step 4: Find Ch5 heading element
    ch5_heading_el = None
    for i, p in enumerate(doc.paragraphs):
        if p.text.strip() == 'Chapter 5':
            ch5_heading_el = p._element
            break
    
    refs_el = None
    for i, p in enumerate(doc.paragraphs):
        if p.text.strip() == 'References':
            refs_el = p._element
            break
    
    # Step 5: Remove old Chapter 4 content
    # Find Ch4 subtitle element and Ch5 heading element
    if ch4_start is not None and ch5_heading_el is not None:
        ch4_el = doc.paragraphs[ch4_start]._element
        # The Ch4 subtitle is the next sibling
        ch4_sub = ch4_el.getnext()
        if ch4_sub is not None:
            count = remove_elements_between(body, ch4_sub, ch5_heading_el)
            print(f'[INFO] Removed {count} old Chapter 4 elements')
            
            # Update subtitle text (subtitle is kept)
            subtitle_updated = False
            for t_el in ch4_sub.iter(qn('w:t')):
                if t_el.text and ('Results' in t_el.text or 'Evaluation' in t_el.text):
                    t_el.text = 'RESULTS AND DISCUSSION'
                    subtitle_updated = True
                    break
            if not subtitle_updated:
                for t_el in ch4_sub.iter(qn('w:t')):
                    t_el.text = 'RESULTS AND DISCUSSION'
                    break
            
            # Step 6: Insert new Chapter 4 content
            print('[INFO] Inserting new Chapter 4 content...')
            ch4_sections = parse_ch4_draft(DRAFT_PATH)
            
            insert_point = ch4_sub
            for elem_type, text in ch4_sections:
                if elem_type == 'h2':
                    new_p = make_heading2_element(text)
                else:
                    new_p = make_normal_element(text)
                insert_after_element(body, insert_point, new_p)
                insert_point = new_p
    
    # Step 7: Insert Table 4.1 after Section 4.2 heading
    print('[INFO] Building and inserting Table 4.1...')
    
    # Find the 4.2 heading
    sec42_el = None
    for child in body:
        if child.tag == qn('w:p'):
            for t_el in child.iter(qn('w:t')):
                if t_el.text and '4.2 Overall Performance' in t_el.text:
                    sec42_el = child
                    break
        if sec42_el is not None:
            break
    
    if sec42_el is not None:
        table_el = build_table_41()
        cap1 = make_caption_element('Table 4.1')
        cap2 = make_caption_element('Overall Performance Comparison of the Multi-Agent Framework and Monolithic LLM Baseline')
        
        # Insert after 4.2 heading: first the table caption, then table
        insert_after_element(body, sec42_el, cap1)
        insert_after_element(body, cap1, cap2)
        insert_after_element(body, cap2, table_el)
        print('[INFO] Table 4.1 inserted')
    
    # Step 8: Update Chapter 5
    print('[INFO] Updating Chapter 5...')
    
    if ch5_heading_el is not None and refs_el is not None:
        # Remove old Ch5 content between Ch5 heading and References
        count = remove_elements_between(body, ch5_heading_el, refs_el)
        print(f'[INFO] Removed {count} old Chapter 5 elements')
        
        insert_point = ch5_heading_el
        
        # Ch5 subtitle
        subtitle = make_subtitle_element('Conclusions and Recommendations')
        insert_after_element(body, insert_point, subtitle)
        insert_point = subtitle
        
        # 5.1 Overview
        h = make_heading2_element('5.1 Overview')
        insert_after_element(body, insert_point, h)
        insert_point = h
        p = make_normal_element(
            'This chapter consolidates the conclusions of the study and relates them to the three research questions. '
            'The developed Multi-Agent Framework \u2014 comprising the Intelligent Router, Transaction Tracker, Store Policy Evaluator, '
            'and Clarification Handler \u2014 was evaluated across 120 controlled simulation scenarios representing routing, transaction, '
            'policy, and clarification categories in the Thai e-commerce post-purchase domain. '
            'The framework was compared with a monolithic LLM baseline under identical conditions using the same DeepSeek provider model '
            '(deepseek-v4-flash) at the same temperature setting. Five primary metrics were used for the comparison: '
            'routing accuracy, transaction-answer accuracy, policy-answer correctness, average response time, and combined escalation rate.'
        )
        insert_after_element(body, insert_point, p)
        insert_point = p
        
        # 5.2 Conclusions
        h = make_heading2_element('5.2 Conclusions')
        insert_after_element(body, insert_point, h)
        insert_point = h
        p = make_normal_element(
            'Research Question 1 asked how a Design Science Research process could be applied to design and implement an '
            'LLM-powered multi-agent customer support framework for Thai e-commerce. The study demonstrated a complete DSR pathway '
            'from problem identification \u2014 operational challenges faced by Thai e-commerce SMEs in post-purchase support \u2014 through '
            'design, development, and evaluation of a functional web-based prototype. The six DSR activities were operationalised as follows: '
            'problem identification through analysis of Thai SME e-commerce support requirements; solution design by decomposing the '
            'support workflow into task-specialised components; iterative artefact development of the Intelligent Router, Transaction '
            'Tracker, Store Policy Evaluator, and Clarification Handler; controlled evaluation against a monolithic LLM baseline; '
            'and communication of the evaluated design knowledge. The DSR process provided a structured methodology that connected '
            'operational requirements to measurable architectural decisions.'
        )
        insert_after_element(body, insert_point, p)
        insert_point = p
        
        p = make_normal_element(
            'Research Question 2 asked how accurately the proposed framework could route inquiries, retrieve transaction information, '
            'and generate policy-grounded answers across 120 controlled scenarios. The framework achieved 68.3% routing accuracy (82/120), '
            'with strongest performance in the routing (86.7%) and transaction (86.7%) categories where deterministic keyword patterns '
            'aligned with clear intent boundaries. Transaction-answer accuracy reached 86.7% end-to-end (26/30), with 100.0% independent '
            'fact accuracy for all scenarios that reached the Transaction Tracker. Policy-answer correctness, determined through structured '
            'rubric-assisted adjudication against five source policy documents, was 73.3% (22/30). The average response time was 4.61 seconds. '
            'The combined escalation rate \u2014 scenarios that triggered clarification or simulated-human-review \u2014 was 36.7%. '
            'These results show that the framework can route requests, retrieve structured transaction data, and generate policy-grounded '
            'responses, with the strongest performance in structured transaction handling.'
        )
        insert_after_element(body, insert_point, p)
        insert_point = p
        
        p = make_normal_element(
            'Research Question 3 asked how the proposed framework compared with the monolithic LLM baseline. '
            'The comparison reveals a nuanced picture rather than universal superiority of either approach. '
            'The Multi-Agent Framework demonstrated a major advantage in transaction factual accuracy (86.7% versus 40.0%, a difference '
            'of +46.7 percentage points), attributable to the structured SQLite-based retrieval that eliminated hallucination risk '
            'for known data points. The framework demonstrated stronger operational reliability, completing 120 of 120 scenarios '
            'compared with 103 of 120 (85.8%) for the monolithic baseline, which experienced 17 execution failures including 9 JSON parse '
            'failures, 6 schema-validation failures, and 2 empty provider responses. The framework achieved slightly higher routing accuracy '
            '(68.3% versus 65.0%, +3.3 percentage points). However, the monolithic baseline achieved higher policy-answer correctness '
            'among successfully executed scenarios (80.0% versus 73.3%, -6.7 percentage points). The framework had a slightly higher average '
            'latency (4.61 seconds versus 4.46 seconds, +0.15 seconds) but a lower p95 tail latency (6.9 seconds versus 9.1 seconds). '
            'The framework escalated more conservatively (36.7% combined escalation rate versus 27.5%), indicating greater caution in '
            'ambiguous or missing-information scenarios. The findings do not show universal superiority across every task type: '
            'the multi-agent architecture excelled at structured data tasks and reliability, while monolithic generation remained competitive '
            'for open-ended policy questions when structured output was reliably formatted.'
        )
        insert_after_element(body, insert_point, p)
        insert_point = p
        
        # 5.3 Contributions
        h = make_heading2_element('5.3 Contributions of the Study')
        insert_after_element(body, insert_point, h)
        insert_point = h
        
        th = make_heading3_element('5.3.1 Theoretical Contribution')
        insert_after_element(body, insert_point, th)
        insert_point = th
        
        p = make_normal_element(
            'The study contributes theoretical evidence concerning task-specialised decomposition for Thai e-commerce customer support. '
            'The experimental results demonstrate that separating structured transaction querying from generative policy answering yields '
            'measurable benefits in factual accuracy and operational reliability, while also revealing architecture-specific trade-offs. '
            'The distinction between structured transaction handling \u2014 where deterministic SQL queries guarantee exact fact reproduction \u2014 '
            'and generative policy support \u2014 where retrieval-augmented generation reduces but does not eliminate semantic omissions \u2014 '
            'provides empirical evidence for a design principle: task types with well-defined evidence boundaries benefit from '
            'task-specialised architectures, while open-ended response generation may remain competitive under monolithic approaches '
            'when structured output is reliably formatted. These findings contribute empirical evidence of architecture trade-offs '
            'in the multi-agent versus monolithic comparison for domain-constrained customer support.'
        )
        insert_after_element(body, insert_point, p)
        insert_point = p
        
        ph = make_heading3_element('5.3.2 Practical Contribution')
        insert_after_element(body, insert_point, ph)
        insert_point = ph
        
        p = make_normal_element(
            'The practical contribution is a functional web-based prototype demonstrating the viability of the proposed architecture. '
            'The prototype includes the Intelligent Router for deterministic intent classification with keyword-based routing patterns, '
            'the SQLite-based Transaction Tracker for parameterised read-only order and shipment queries, and the ChromaDB-based Store '
            'Policy Evaluator using SentenceTransformer embeddings for policy retrieval and grounding validation against source documents. '
            'The design incorporates deterministic fallback mechanisms \u2014 when the DeepSeek-generated response fails grounding validation, '
            'the system substitutes a rule-based response rather than returning an unreliable answer. The evaluation pipeline, designed '
            'as a reproducible JSONL record and CSV aggregation workflow, enables consistent comparison across experimental conditions. '
            'The framework is not deployed in production and has not been tested with real customer data.'
        )
        insert_after_element(body, insert_point, p)
        insert_point = p
        
        # 5.4 Limitations
        h = make_heading2_element('5.4 Limitations')
        insert_after_element(body, insert_point, h)
        insert_point = h
        
        limitations = [
            ('5.4.1 Simulated Rather Than Real-User Data',
             'The first limitation is the use of simulated orders and scenarios rather than conversations collected from real Thai e-commerce customers. '
             'While simulation made expected routes and answers controllable and avoided exposure of personally identifiable information, it cannot capture '
             'the full variability, ambiguity, and pragmatic complexity of authentic customer interactions. Real customer conversations may introduce '
             'unexpected linguistic patterns, emotional tone, and domain-specific expressions that the simulated scenarios did not represent.'),
            ('5.4.2 Limited Policy Document Coverage',
             'The second limitation is the use of only five source policy documents (refund, return, exchange, shipping, and payment policies). '
             'Real e-commerce operations typically involve a larger and more interconnected set of policy documents, terms of service, and exception rules. '
             'The limited number of source documents may constrain the observed retrieval difficulty and grounding-validation challenges.'),
            ('5.4.3 Limited Number of Synthetic Orders',
             'The third limitation concerns the synthetic order database. The evaluation used a limited number of synthetic orders within the SQLite '
             'Transaction Tracker. A larger and more diverse order dataset would test the system against a wider range of transaction states, '
             'edge cases, and data inconsistencies.'),
            ('5.4.4 Deterministic Keyword-Based Router',
             'The fourth limitation is the deterministic keyword-based Intelligent Router. The router achieved 68.3% accuracy overall but only 26.7% '
             'on policy scenarios, revealing that semantic overlap between policy subcategories exceeds the coverage of deterministic keyword patterns. '
             'A trainable or hybrid intent classifier would likely improve routing performance, particularly for policy-related queries.'),
            ('5.4.5 Use of a Single Generation Model',
             'The fifth limitation is model coverage. DeepSeek (deepseek-v4-flash) was the only LLM provider evaluated. Using the same model for '
             'both the multi-agent and monolithic conditions ensured a controlled comparison, but the results may not generalise to other LLMs '
             'with different instruction-following, structured-output, or Thai-language capabilities.'),
            ('5.4.6 Single-Turn or Limited-Session Interactions',
             'The sixth limitation is the narrow conversational scope. The evaluation focused on primarily single-turn or limited-session interactions '
             'concerning orders, payments, shipments, returns, refunds, and store policies. Multi-turn conversations that require context retention '
             'across multiple exchanges were not systematically evaluated.'),
            ('5.4.7 Rubric-Assisted Rather Than Independent Blind Review',
             'The seventh limitation concerns the policy-answer evaluation method. Policy correctness was assessed through structured rubric-assisted '
             'adjudication by the experimenter rather than independent blind human review. The adjudicator had access to the expected answer facts '
             'while evaluating each response, which may introduce confirmation bias. No inter-rater reliability metric is available.'),
            ('5.4.8 No Statistical Significance Testing',
             'The eighth limitation is the absence of formal statistical significance testing. The reported differences between conditions are '
             'descriptive only. Without repeated experimental runs or inferential statistics, the reliability of the observed differences '
             'cannot be assessed statistically.'),
            ('5.4.9 No Real Customer Deployment',
             'The ninth limitation is that the framework was not deployed with real customers. Production deployment would expose the system to '
             'unexpected inputs, concurrent user sessions, latency variability under load, and security considerations that the controlled '
             'evaluation did not address.'),
            ('5.4.10 Sensitivity to Thai Phrasing and Code-Switching',
             'The tenth limitation concerns Thai language handling. Thai-English code-switching remained a cross-cutting source of difficulty. '
             'Some routing errors and retrieval issues may reflect sensitivity to specific Thai phrasing patterns, regional expressions, '
             'and mixed-language input that the preprocessing pipeline did not fully normalise.'),
            ('5.4.11 Cold-Start and External Provider Latency',
             'The eleventh limitation concerns the latency characteristics of the evaluation. The first policy scenario exhibited a cold-start '
             'latency of 26.8 seconds due to SentenceTransformer model loading. External provider latency from the DeepSeek API was beyond '
             'experimental control and may vary under different network conditions or API load levels.')
        ]
        
        for lim_title, lim_text in limitations:
            th = make_heading3_element(lim_title)
            insert_after_element(body, insert_point, th)
            insert_point = th
            p = make_normal_element(lim_text)
            insert_after_element(body, insert_point, p)
            insert_point = p
        
        # 5.5 Recommendations
        h = make_heading2_element('5.5 Recommendations for Future Research')
        insert_after_element(body, insert_point, h)
        insert_point = h
        
        future_work = [
            ('5.5.1 Evaluation with Real Thai E-Commerce Data',
             'Future research should address the simulation limitation by evaluating the framework with de-identified real Thai e-commerce conversations '
             'and controlled access to authentic order, payment, shipment, and policy data. Real data would test the framework against genuine '
             'customer language patterns and operational edge cases that simulated scenarios cannot fully replicate.'),
            ('5.5.2 Larger and More Diverse Evaluation Datasets',
             'Future studies should expand the evaluation with larger transaction datasets encompassing more merchants, product categories, '
             'logistics conditions, payment states, and return-refund exceptions, as well as a broader set of policy documents covering '
             'additional domains such as warranty terms, promotional conditions, and membership programmes.'),
            ('5.5.3 Trainable or Hybrid Intent Classifier',
             'The deterministic keyword-based Router should be extended to a trainable or hybrid intent classifier that combines rule-based '
             'patterns with lightweight semantic classification. Such an approach would likely improve routing accuracy for policy-related queries, '
             'which was the weakest category for the current Router.'),
            ('5.5.4 Stronger Thai Semantic Routing',
             'Research should investigate stronger Thai language semantic routing approaches, potentially including Thai-specific embedding models, '
             'fine-tuned classifiers for code-switched input, and normalisation strategies that better handle the Thai-English mixed-language '
             'patterns common in Thai e-commerce communication.'),
            ('5.5.5 Multi-Turn Conversation Evaluation',
             'Future work should extend the evaluation from primarily single-turn scenarios to complex multi-turn post-purchase dialogue. '
             'A durable state model should store verified customer and order identifiers, unresolved clarification questions, and conversation '
             'history across multiple exchanges to assess the framework\'s ability to maintain context.'),
            ('5.5.6 Independent Bilingual Human Evaluators',
             'The policy-answer evaluation should be repeated with independent bilingual human evaluators who are fluent in Thai and English '
             'and blinded to the system condition. This would provide a more rigorous assessment of answer quality and reduce the risk of '
             'confirmation bias inherent in rubric-assisted adjudication.'),
            ('5.5.7 Inter-Rater Agreement Metrics',
             'Related to the above, future studies should report inter-rater agreement metrics (such as Cohen\'s Kappa or Fleiss\' Kappa) '
             'when multiple evaluators assess system outputs, establishing the reliability of the evaluation methodology itself.'),
            ('5.5.8 Comparison Across Multiple LLMs',
             'Future studies should compare the multi-agent architecture across multiple commercial and open-weight LLMs under the same '
             'orchestration, prompts, data, retrieval index, and scenario conditions to assess the generalisability of the findings '
             'to different generation models.'),
            ('5.5.9 Production Monitoring and Operational Evaluation',
             'Research should evaluate the framework under production-like conditions including concurrent user sessions, request queues, '
             'latency monitoring, and error rates. Production deployment would reveal operational challenges not visible in the controlled '
             'simulation environment.'),
            ('5.5.10 Security and Privacy Evaluation',
             'Security aspects should be formally evaluated, including prompt injection resistance, parameterised query isolation guarantees, '
             'and the effectiveness of in-context policy access controls against adversarial customer inputs.'),
            ('5.5.11 Integration with Real E-Commerce APIs',
             'The framework should be tested with real Thai e-commerce platform APIs (for order, payment, shipment, and product data) '
             'after appropriate authorisation and data protection agreements. API integration would reveal practical integration challenges '
             'related to authentication, rate limiting, data format differences, and error handling.'),
            ('5.5.12 Statistical Testing with Repeated Runs',
             'Future evaluations should incorporate repeated experimental runs to enable formal statistical testing of observed differences. '
             'Repeating the evaluation multiple times would allow confidence intervals and significance tests to be calculated, '
             'providing stronger evidence for the reported performance differences.')
        ]
        
        for ft_title, ft_text in future_work:
            th = make_heading3_element(ft_title)
            insert_after_element(body, insert_point, th)
            insert_point = th
            p = make_normal_element(ft_text)
            insert_after_element(body, insert_point, p)
            insert_point = p
        
        print('[INFO] Chapter 5 updated')
    
    # Step 9: Update Abstract
    if abstract_para is not None:
        # Replace runs with clean final text
        # Clear existing runs
        for r in list(abstract_para._element.iter(qn('w:r'))):
            r.getparent().remove(r)
        
        new_r = OxmlElement('w:r')
        rPr = OxmlElement('w:rPr')
        new_r.append(rPr)
        rFonts = OxmlElement('w:rFonts'); rFonts.set(qn('w:ascii'), 'Times New Roman'); rFonts.set(qn('w:hAnsi'), 'Times New Roman'); rPr.append(rFonts)
        sz = OxmlElement('w:sz'); sz.set(qn('w:val'), '24'); rPr.append(sz)
        szCs = OxmlElement('w:szCs'); szCs.set(qn('w:val'), '24'); rPr.append(szCs)
        t = OxmlElement('w:t')
        t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        t.text = (
            'The evaluation compared the proposed framework with a monolithic LLM baseline across 120 controlled scenarios. '
            'The proposed framework achieved 68.3% routing accuracy, 86.7% transaction-answer accuracy, and 73.3% policy-answer correctness, '
            'compared with 65.0%, 40.0%, and 80.0% for the baseline. '
            'Its average response time was 4.61 seconds compared with 4.46 seconds, '
            'while its combined escalation rate was 36.7% compared with 27.5%. '
            'The multi-agent framework completed 120 of 120 scenarios without execution error, '
            'compared with 103 of 120 for the monolithic baseline. '
            'The findings indicate that explicit task specialisation and separation of structured and semantic evidence improve '
            'transaction factual accuracy and operational reliability, although the architecture introduces trade-offs in policy '
            'answering capability and latency.'
        )
        new_r.append(t)
        abstract_para._element.append(new_r)
        print('[INFO] Abstract updated')
    
    # Step 10: Save without images first
    print('[INFO] Saving text-only document...')
    doc.save(OUTPUT_PATH)
    print('[INFO] Saved text-only version successfully')
    
    # Step 11: Reopen and add images
    print('[INFO] Reopening to add images...')
    doc = Document(OUTPUT_PATH)
    body = doc.element.body
    
    # Add all images to the document package
    fig_info = [
        (NEW_FIG41, 15.5, 4, 'Figure 4.1', 'Figure 4.1. Performance comparison between the Multi-Agent Framework and the monolithic LLM baseline.', 
         '4.2 Overall Performance'),
        (FIG42, 13.5, 5, 'Figure 4.2', 'Figure 4.2. Average response time of the two evaluated systems.',
         '4.6 Response Time'),
        (FIG43, 15.5, 6, 'Figure 4.3', 'Figure 4.3. Successful completion and structured-output failure rates.',
         '4.8 Reliability and Error Analysis'),
        (FIG44, 15.5, 7, 'Figure 4.4', 'Figure 4.4. Routing intent accuracy by evaluation category.',
         '4.3 Routing Accuracy'),
    ]
    
    for img_path, width_cm, img_id, fig_name, caption, heading_text in fig_info:
        if not os.path.exists(img_path):
            print(f'[WARN] {fig_name} image not found at {img_path}')
            continue
        
        print(f'[INFO] Adding {fig_name}...')
        
        # Add image to package
        rId = add_image_to_docx_package(doc, img_path)
        
        # Create image paragraph
        fig_p = add_image_to_xml(body, img_path, width_cm, img_id, rId)
        
        # Find the target heading to insert after
        target_heading = None
        for child in body:
            if child.tag == qn('w:p'):
                for t_el in child.iter(qn('w:t')):
                    if t_el.text and heading_text in t_el.text:
                        target_heading = child
                        break
            if target_heading is not None:
                break
        
        if target_heading is not None:
            fig_cap = make_caption_element(caption)
            insert_after_element(body, target_heading, fig_p)
            insert_after_element(body, fig_p, fig_cap)
            print(f'[INFO] {fig_name} inserted')
        else:
            print(f'[WARN] Could not find heading "{heading_text}" for {fig_name}')
    
    # Step 12: Final save with images
    print('[INFO] Final save with images...')
    doc.save(OUTPUT_PATH)
    print('[INFO] Document saved successfully!')


if __name__ == '__main__':
    main()
