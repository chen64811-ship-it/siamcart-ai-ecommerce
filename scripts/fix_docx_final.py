"""
Fix DOCX: Add tblGrid to new Table 4.1, remove old table, insert 4 figures.
"""
import shutil, os, zipfile, csv
from lxml import etree
from PIL import Image as PILImage

DOCX_PATH = r'T:/thai-ecommerce-agent/thesis/Xingyu_Chen_IS_Ch4_Ch5_Final_Updated.docx'
BACKUP_PATH = DOCX_PATH.replace('.docx', '_bak.docx')
shutil.copy2(DOCX_PATH, BACKUP_PATH)
print(f'Backup saved to {BACKUP_PATH}')

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
PIC = 'http://schemas.openxmlformats.org/drawingml/2006/picture'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'

z = zipfile.ZipFile(DOCX_PATH, 'r')
data = {name: z.read(name) for name in z.namelist()}
z.close()

doc_xml = etree.fromstring(data['word/document.xml'])
rels_xml = etree.fromstring(data['word/_rels/document.xml.rels'])
ct_xml = etree.fromstring(data['[Content_Types].xml'])
body = doc_xml.find(f'{{{W}}}body')

# STEP 1: Fix tblGrid on new Table 4.1
print('STEP 1: Fixing Table 4.1 tblGrid...')
for tbl in body.iter(f'{{{W}}}tbl'):
    tblGrid = tbl.find(f'{{{W}}}tblGrid')
    if tblGrid is None:
        first_tr = tbl.find(f'{{{W}}}tr')
        if first_tr is not None:
            tcs = first_tr.findall(f'{{{W}}}tc')
            grid = etree.Element(f'{{{W}}}tblGrid')
            for tc in tcs:
                tcW = tc.find(f'{{{W}}}tcPr/{{{W}}}tcW')
                gc = etree.SubElement(grid, f'{{{W}}}gridCol')
                w_val = tcW.get(f'{{{W}}}w') if tcW is not None else '1000'
                gc.set(f'{{{W}}}w', w_val)
            tblPr = tbl.find(f'{{{W}}}tblPr')
            if tblPr is not None:
                tblPr.addnext(grid)
            else:
                tbl.insert(0, grid)
            print(f'  Added tblGrid with {len(tcs)} cols')

# STEP 2: Remove old metrics definition table (3-column table with "Metric" header)
print('STEP 2: Removing old metrics table...')
removed = 0
for tbl in list(body.iter(f'{{{W}}}tbl')):
    first_tr = tbl.find(f'{{{W}}}tr')
    if first_tr is not None:
        tcs = first_tr.findall(f'{{{W}}}tc')
        if len(tcs) == 3:
            for tc in tcs:
                for t_elem in tc.iter(f'{{{W}}}t'):
                    if t_elem.text and 'Metric' in t_elem.text:
                        body.remove(tbl)
                        removed += 1
                        break
                break
print(f'  Removed {removed} old table(s)')

# Remove old Figure 4.1 caption
for p in list(body.iter(f'{{{W}}}p')):
    for t_elem in p.iter(f'{{{W}}}t'):
        txt = t_elem.text or ''
        if 'Accuracy and Correctness Comparison' in txt:
            p.getparent().remove(p)
            print('  Removed old Figure 4.1 caption')
            break

# STEP 3: Insert 4 figure images
print('STEP 3: Inserting figure images...')

FIG_DATA = [
    ('T:/thai-ecommerce-agent/data/evaluation/figures/figure_4_1_performance_metrics.png', 15.5,
     '4.2 Overall Performance',
     'Figure 4.1. Performance comparison between the Multi-Agent Framework and the monolithic LLM baseline.'),
    ('T:/thai-ecommerce-agent/data/evaluation/figures/figure_4_2_response_time.png', 13.5,
     '4.6 Response Time',
     'Figure 4.2. Average response time of the two evaluated systems.'),
    ('T:/thai-ecommerce-agent/data/evaluation/figures/figure_4_3_reliability_comparison.png', 15.5,
     '4.8 Reliability and Error Analysis',
     'Figure 4.3. Successful completion and structured-output failure rates.'),
    ('T:/thai-ecommerce-agent/data/evaluation/figures/figure_4_4_routing_by_category.png', 15.5,
     '4.3 Routing Accuracy',
     'Figure 4.4. Routing intent accuracy by evaluation category.'),
]

existing_media = [n for n in data if n.startswith('word/media/image') and n.endswith('.png')]
next_img_num = len(existing_media) + 1
next_rid = 25

for img_path, width_cm, heading_marker, caption_text in FIG_DATA:
    if not os.path.exists(img_path):
        print(f'  WARNING: {img_path} not found!')
        continue
    
    rid = f'rId{next_rid}'
    next_rid += 1
    img_num = next_img_num
    next_img_num += 1
    media_name = f'word/media/image{img_num}.png'
    
    with open(img_path, 'rb') as f:
        data[media_name] = f.read()
    
    rel_elem = etree.SubElement(rels_xml, f'{{{R}}}Relationship')
    rel_elem.set('Id', rid)
    rel_elem.set('Type', 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/image')
    rel_elem.set('Target', f'media/image{img_num}.png')
    
    ct_override = etree.SubElement(ct_xml, f'{{{CT}}}Override')
    ct_override.set('PartName', f'/{media_name}')
    ct_override.set('ContentType', 'image/png')
    
    with PILImage.open(img_path) as img:
        w, h = img.size
    width_emu = int(width_cm * 360000)
    height_emu = int(width_emu * h / w)
    
    heading_p = None
    for elem in body.iter(f'{{{W}}}t'):
        if elem.text and heading_marker in elem.text:
            heading_p = elem.getparent().getparent()
            break
    
    if heading_p is None:
        print(f'  WARNING: Could not find heading "{heading_marker}"')
        continue
    
    # Build figure paragraph
    fig_p = etree.Element(f'{{{W}}}p')
    fig_pPr = etree.SubElement(fig_p, f'{{{W}}}pPr')
    fig_jc = etree.SubElement(fig_pPr, f'{{{W}}}jc')
    fig_jc.set(f'{{{W}}}val', 'center')
    fig_sp = etree.SubElement(fig_pPr, f'{{{W}}}spacing')
    fig_sp.set(f'{{{W}}}line', '240')
    fig_sp.set(f'{{{W}}}lineRule', 'auto')
    
    run = etree.SubElement(fig_p, f'{{{W}}}r')
    drawing = etree.SubElement(run, f'{{{W}}}drawing')
    
    inline = etree.SubElement(drawing, f'{{{WP}}}inline')
    inline.set('distT', '0'); inline.set('distB', '0')
    inline.set('distL', '0'); inline.set('distR', '0')
    
    ext_el = etree.SubElement(inline, f'{{{WP}}}extent')
    cx_el = etree.SubElement(ext_el, f'{{{WP}}}cx'); cx_el.text = str(width_emu)
    cy_el = etree.SubElement(ext_el, f'{{{WP}}}cy'); cy_el.text = str(height_emu)
    
    ee = etree.SubElement(inline, f'{{{WP}}}effectExtent')
    for s in ['l','t','r','b']:
        el = etree.SubElement(ee, f'{{{WP}}}{s}'); el.text = '0'
    
    docPr = etree.SubElement(inline, f'{{{WP}}}docPr')
    docPr.set('id', str(100 + img_num))
    docPr.set('name', f'Figure_{img_num-3}')
    docPr.set('descr', f'Figure_{img_num-3}')
    
    cNv = etree.SubElement(inline, f'{{{WP}}}cNvGraphicFramePr')
    noGrp = etree.SubElement(cNv, f'{{{A}}}noGrp'); noGrp.set('val', '1')
    
    graphic = etree.SubElement(inline, f'{{{A}}}graphic')
    gd = etree.SubElement(graphic, f'{{{A}}}graphicData')
    gd.set('uri', 'http://schemas.openxmlformats.org/drawingml/2006/picture')
    
    pic = etree.SubElement(gd, f'{{{PIC}}}pic')
    nvPicPr = etree.SubElement(pic, f'{{{PIC}}}nvPicPr')
    cNvPr = etree.SubElement(nvPicPr, f'{{{PIC}}}cNvPr')
    cNvPr.set('id', '0'); cNvPr.set('name', f'Pic_{img_num}')
    etree.SubElement(nvPicPr, f'{{{PIC}}}cNvPicPr')
    
    blipFill = etree.SubElement(pic, f'{{{PIC}}}blipFill')
    blip = etree.SubElement(blipFill, f'{{{A}}}blip')
    blip.set(f'{{{R}}}embed', rid)
    stretch = etree.SubElement(blipFill, f'{{{A}}}stretch')
    etree.SubElement(stretch, f'{{{A}}}fillRect')
    
    spPr = etree.SubElement(pic, f'{{{PIC}}}spPr')
    xfrm = etree.SubElement(spPr, f'{{{A}}}xfrm')
    off = etree.SubElement(xfrm, f'{{{A}}}off')
    off.set('x', '0'); off.set('y', '0')
    ext = etree.SubElement(xfrm, f'{{{A}}}ext')
    ext.set('cx', str(width_emu)); ext.set('cy', str(height_emu))
    etree.SubElement(spPr, f'{{{A}}}prstGeom').set('prst', 'rect')
    
    # Build caption paragraph
    cap_p = etree.Element(f'{{{W}}}p')
    cap_pPr = etree.SubElement(cap_p, f'{{{W}}}pPr')
    cap_jc = etree.SubElement(cap_pPr, f'{{{W}}}jc')
    cap_jc.set(f'{{{W}}}val', 'center')
    cap_run_elem = etree.SubElement(cap_p, f'{{{W}}}r')
    cap_rPr = etree.SubElement(cap_run_elem, f'{{{W}}}rPr')
    cap_rFonts = etree.SubElement(cap_rPr, f'{{{W}}}rFonts')
    cap_rFonts.set(f'{{{W}}}ascii', 'Times New Roman')
    cap_rFonts.set(f'{{{W}}}hAnsi', 'Times New Roman')
    cap_sz = etree.SubElement(cap_rPr, f'{{{W}}}sz')
    cap_sz.set(f'{{{W}}}val', '20')
    cap_szCs = etree.SubElement(cap_rPr, f'{{{W}}}szCs')
    cap_szCs.set(f'{{{W}}}val', '20')
    etree.SubElement(cap_rPr, f'{{{W}}}b')
    etree.SubElement(cap_rPr, f'{{{W}}}i')
    cap_t = etree.SubElement(cap_run_elem, f'{{{W}}}t')
    cap_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    cap_t.text = caption_text
    
    # Insert after heading: figure then caption
    heading_idx = list(body).index(heading_p)
    body.insert(heading_idx + 1, fig_p)
    body.insert(heading_idx + 2, cap_p)
    
    print(f'  Inserted {media_name} after "{heading_marker}"')

# Write back to zip
data['word/document.xml'] = etree.tostring(doc_xml, xml_declaration=True, encoding='UTF-8', standalone=True)
data['word/_rels/document.xml.rels'] = etree.tostring(rels_xml, xml_declaration=True, encoding='UTF-8', standalone=True)
data['[Content_Types].xml'] = etree.tostring(ct_xml, xml_declaration=True, encoding='UTF-8', standalone=True)

with zipfile.ZipFile(DOCX_PATH, 'w', zipfile.ZIP_DEFLATED) as zout:
    for name, content in data.items():
        zout.writestr(name, content)

print()
print('All fixes applied successfully!')
