"""
Script chuyển đổi tự động BAO_CAO_DU_AN_TOT_NGHIEP.md sang BAO_CAO_HOAN_CHINH.docx
Tuân thủ tuyệt đối quy chuẩn Trường Cao đẳng FPT Polytechnic:
1. Khổ giấy A4, lề chuẩn báo cáo: Trái 3.0cm, Phải 2.0cm, Trên 2.0cm, Dưới 2.0cm.
2. Font chữ: Times New Roman, cỡ chữ 13pt (body), chữ màu đen trên nền trắng.
3. Bảng biểu (Tables):
   - autofit = True, ép chiều rộng bảng vừa khít 100% lề trang (16.0 cm).
   - Tự động ngắt dòng (word-wrap) trên tất cả các ô, không bao giờ tràn lề phải.
   - Kẻ viền (Table Grid) rõ nét, lặp lại tiêu đề bảng khi sang trang mới (tblHeader), chống cắt ngang dòng (cantSplit).
"""

import os
import re
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import docx
from docx.shared import Inches, Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn, nsdecls


MD_FILE = "BAO_CAO_DU_AN_TOT_NGHIEP.md"
DOCX_FILE = "BAO_CAO_HOAN_CHINH.docx"

PAGE_WIDTH_CM = 21.0
PAGE_HEIGHT_CM = 29.7
MARGIN_LEFT_CM = 3.0
MARGIN_RIGHT_CM = 2.0
MARGIN_TOP_CM = 2.0
MARGIN_BOTTOM_CM = 2.0
CONTENT_WIDTH_CM = PAGE_WIDTH_CM - MARGIN_LEFT_CM - MARGIN_RIGHT_CM  # 16.0 cm
CONTENT_WIDTH_DXA = int(round(CONTENT_WIDTH_CM * 566.929))  # 9071 dxa


def set_cell_margins(cell, top=100, bottom=100, left=130, right=130):
    """Thiết lập padding cho ô trong bảng (đơn vị dxa)."""
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = OxmlElement('w:tcMar')
    for m, val in [('top', top), ('bottom', bottom), ('left', left), ('right', right)]:
        node = OxmlElement(f'w:{m}')
        node.set(qn('w:w'), str(val))
        node.set(qn('w:type'), 'dxa')
        tcMar.append(node)
    tcPr.append(tcMar)


def add_formatted_text(paragraph, text, base_font_size=13, is_bold_base=False, is_italic_base=False, font_name="Times New Roman", font_color=RGBColor(0, 0, 0)):
    """Phân tích các thẻ Markdown inline (**bold**, *italic*, `code`, <br>) và tạo runs tương ứng."""
    if not text:
        return

    # Tách chuỗi theo thẻ <br> hoặc <br/>
    lines = re.split(r'<br\s*/?>', text, flags=re.IGNORECASE)
    for line_idx, line in enumerate(lines):
        if line_idx > 0:
            paragraph.add_run().add_break()

        # Tokenize markdown: `code`, **bold**, *italic*
        tokens = re.split(r'(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)', line)
        for token in tokens:
            if not token:
                continue
            
            run = paragraph.add_run()
            run.font.name = font_name
            run.font.color.rgb = font_color

            if token.startswith('`') and token.endswith('`') and len(token) >= 2:
                # Inline code
                run.text = token[1:-1]
                run.font.name = "Consolas"
                run.font.size = Pt(base_font_size - 1.5 if base_font_size > 11 else base_font_size - 1)
                run.font.color.rgb = RGBColor(30, 41, 59)
            elif token.startswith('**') and token.endswith('**') and len(token) >= 4:
                # Bold
                run.text = token[2:-2]
                run.font.size = Pt(base_font_size)
                run.bold = True
                run.italic = is_italic_base
            elif token.startswith('*') and token.endswith('*') and len(token) >= 2:
                # Italic
                run.text = token[1:-1]
                run.font.size = Pt(base_font_size)
                run.bold = is_bold_base
                run.italic = True
            else:
                # Plain text
                clean_text = token
                # Bỏ liên kết markdown [Text](url) -> Text
                clean_text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', clean_text)
                run.text = clean_text
                run.font.size = Pt(base_font_size)
                run.bold = is_bold_base
                run.italic = is_italic_base


def calculate_column_widths(headers, num_cols, table_data):
    """Tính toán chiều rộng các cột (cm) sao cho tổng đúng bằng 16.0 cm."""
    header_str = " ".join([h.lower() for h in headers])

    # 1. Bảng 7 cột: Đặc tả Database Schemas (No., Name, Type, Length, Not null, Key, Ghi chú)
    if num_cols == 7 and ("no." in header_str or "stt" in header_str) and ("name" in header_str or "tên" in header_str) and "type" in header_str:
        return [0.9, 2.8, 1.8, 1.4, 1.5, 1.1, 6.5]

    # 2. Bảng 7 cột: Test Cases (Mã TC, Tên chức năng, Các bước, Đầu vào, Kết quả mong đợi, Kết quả thực tế, Trạng thái)
    if num_cols == 7 and ("mã tc" in header_str or "tc" in header_str):
        return [1.2, 2.3, 2.6, 2.4, 3.4, 2.6, 1.5]

    # 3. Bảng 3 cột: Giải thích thuật ngữ (STT, Thuật ngữ, Giải thích)
    if num_cols == 3 and ("thuật ngữ" in header_str or "stt" in header_str):
        return [1.0, 3.5, 11.5]

    # 4. Bảng 5 cột: Thành viên tham gia dự án (STT, Thành viên, Mã SV, Vai trò, Module)
    if num_cols == 5 and "mã sinh viên" in header_str:
        return [1.0, 3.0, 2.2, 3.5, 6.3]

    # 5. Bảng 5 cột: Khảo sát hệ thống tương tự (Tiêu chí, Coolmate, Routine, Shein, AURA STUDIO)
    if num_cols == 5 and ("tiêu chí" in header_str or "coolmate" in header_str):
        return [2.8, 3.2, 3.2, 3.2, 3.6]

    # 6. Bảng 5 cột: Mô tả Use Case (STT, Tên Use Case, Mô tả, Input, Output)
    if num_cols == 5 and ("use case" in header_str or "usecase" in header_str or "input" in header_str):
        return [0.9, 2.6, 3.8, 4.3, 4.4]

    # 7. Bảng 6 cột: Ma trận phân quyền RBAC (STT, Chức năng, Khách, Thành viên, Quản trị, Ghi chú)
    if num_cols == 6 and ("khách" in header_str or "thành viên" in header_str):
        return [0.8, 5.2, 1.2, 1.5, 1.2, 6.1]

    # 8. Bảng 4 cột: Tiến độ Sprints (Giai đoạn, Thời gian, Nội dung, Kết quả)
    if num_cols == 4 and ("giai đoạn" in header_str or "sprint" in header_str):
        return [1.8, 2.2, 6.0, 6.0]

    # 9. Bảng 5 cột: Mức độ hoàn thiện tính năng (STT, Phân hệ, Mức độ, Tỉ lệ %, Đánh giá)
    if num_cols == 5 and ("mức độ hoàn thành" in header_str or "tỉ lệ %" in header_str):
        return [0.9, 5.0, 2.6, 1.5, 6.0]

    # Thuật toán tỷ lệ động thông minh cho các bảng khác
    max_lens = [0] * num_cols
    for row in table_data:
        for c_idx in range(min(num_cols, len(row))):
            clean_text = re.sub(r'[*`_]', '', row[c_idx]).strip()
            max_lens[c_idx] = max(max_lens[c_idx], len(clean_text))

    weights = []
    for c_idx in range(num_cols):
        h = headers[c_idx].lower() if c_idx < len(headers) else ""
        if h in ("stt", "no.", "id"):
            weights.append(10)
        elif h in ("key", "not null", "trạng thái", "role", "loại"):
            weights.append(16)
        else:
            w = max(15, (max_lens[c_idx] + 5) ** 0.6)
            weights.append(w)

    total_weight = sum(weights)
    widths = [round((w / total_weight) * CONTENT_WIDTH_CM, 2) for w in weights]
    
    # Điều chỉnh sai số làm tròn để tổng chính xác 16.0 cm
    diff = round(CONTENT_WIDTH_CM - sum(widths), 2)
    widths[-1] += diff
    return widths


def format_table(table, headers, rows_data, widths_cm):
    """Định dạng bảng chuẩn Word A4, viền Grid, autofit, lặp header và chống tràn lề."""
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True

    # 1. Thuộc tính bảng XML: Ép chiều rộng 100% lề trang (9071 dxa = 16.0 cm)
    tblPr = table._tbl.tblPr
    tblW = tblPr.find(qn('w:tblW'))
    if tblW is None:
        tblW = OxmlElement('w:tblW')
        tblPr.append(tblW)
    tblW.set(qn('w:w'), str(CONTENT_WIDTH_DXA))
    tblW.set(qn('w:type'), 'dxa')

    # Bật Table Grid borders
    tblBorders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        f'  <w:top w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
        f'  <w:bottom w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
        f'  <w:left w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
        f'  <w:right w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
        f'  <w:insideH w:val="single" w:sz="4" w:space="0" w:color="D1D5DB"/>'
        f'  <w:insideV w:val="single" w:sz="4" w:space="0" w:color="D1D5DB"/>'
        f'</w:tblBorders>'
    )
    tblPr.append(tblBorders)

    # Cỡ chữ trong bảng: 10.5pt cho bảng 7 cột để đẹp mắt, 11pt cho các bảng khác
    num_cols = len(widths_cm)
    font_size_pt = 10.5 if num_cols >= 7 else 11.0

    # 2. Xử lý từng dòng
    for r_idx, row in enumerate(table.rows):
        trPr = row._tr.get_or_add_trPr()
        trPr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))

        # Dòng tiêu đề: Lặp lại khi bảng sang trang mới (tblHeader)
        if r_idx == 0:
            trPr.append(parse_xml(f'<w:tblHeader {nsdecls("w")}/>'))

        for c_idx, cell in enumerate(row.cells):
            width_cm = widths_cm[c_idx]
            cell.width = Cm(width_cm)
            width_dxa = int(round(width_cm * 566.929))

            # XML cell width
            tcPr = cell._tc.get_or_add_tcPr()
            tcW = tcPr.find(qn('w:tcW'))
            if tcW is None:
                tcW = OxmlElement('w:tcW')
                tcPr.append(tcW)
            tcW.set(qn('w:w'), str(width_dxa))
            tcW.set(qn('w:type'), 'dxa')

            # Đảm bảo KHÔNG có noWrap (cho phép word-wrap hoàn toàn)
            noWrap = tcPr.find(qn('w:noWrap'))
            if noWrap is not None:
                tcPr.remove(noWrap)

            # Đặt padding cho ô
            set_cell_margins(cell, top=80, bottom=80, left=110, right=110)

            # Căn giữa theo chiều dọc
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

            # Nền màu xám nhạt cho header
            if r_idx == 0:
                shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="EBF0F5"/>')
                tcPr.append(shd)

            # Định dạng đoạn văn trong ô
            for p in cell.paragraphs:
                p.paragraph_format.line_spacing = 1.15
                p.paragraph_format.space_before = Pt(1)
                p.paragraph_format.space_after = Pt(1)
                
                # Căn giữa cho các cột ngắn (STT, Key, Not null, Tỉ lệ...)
                h_name = headers[c_idx].lower() if c_idx < len(headers) else ""
                if r_idx == 0:
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                elif h_name in ("stt", "no.", "id", "key", "not null", "tỉ lệ %", "trạng thái"):
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                else:
                    p.alignment = WD_ALIGN_PARAGRAPH.LEFT


def create_callout_box(doc, text_content, label=""):
    """Tạo hộp mã nguồn / biểu đồ có nền xám viền bo trang nhã."""
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True

    # Chiều rộng 100% lề trang (16.0 cm)
    tblPr = table._tbl.tblPr
    tblW = OxmlElement('w:tblW')
    tblW.set(qn('w:w'), str(CONTENT_WIDTH_DXA))
    tblW.set(qn('w:type'), 'dxa')
    tblPr.append(tblW)

    # Viền xám mỏng
    tblBorders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        f'  <w:top w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        f'  <w:bottom w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        f'  <w:left w:val="single" w:sz="18" w:space="0" w:color="2563EB"/>'  # Viền xanh đậm bên trái 2.25pt
        f'  <w:right w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        f'</w:tblBorders>'
    )
    tblPr.append(tblBorders)

    cell = table.cell(0, 0)
    cell.width = Cm(CONTENT_WIDTH_CM)
    tcPr = cell._tc.get_or_add_tcPr()
    tcPr.append(parse_xml(f'<w:shd {nsdecls("w")} w:fill="F8FAFC"/>'))
    set_cell_margins(cell, top=120, bottom=120, left=160, right=140)

    p = cell.paragraphs[0]
    p.paragraph_format.line_spacing = 1.15
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)

    if label:
        run_lbl = p.add_run(f"[{label}]\n")
        run_lbl.font.name = "Times New Roman"
        run_lbl.font.size = Pt(10)
        run_lbl.bold = True
        run_lbl.font.color.rgb = RGBColor(37, 99, 235)

    run_code = p.add_run(text_content.strip())
    run_code.font.name = "Consolas"
    run_code.font.size = Pt(9.5)
    run_code.font.color.rgb = RGBColor(30, 41, 59)

    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def build_word_document():
    print(f"Bắt đầu chuyển đổi '{MD_FILE}' sang '{DOCX_FILE}'...")
    
    if not os.path.exists(MD_FILE):
        raise FileNotFoundError(f"Không tìm thấy tệp {MD_FILE}")

    with open(MD_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()

    doc = docx.Document()

    # 1. Thiết lập trang giấy A4 và lề chuẩn
    section = doc.sections[0]
    section.page_width = Cm(PAGE_WIDTH_CM)
    section.page_height = Cm(PAGE_HEIGHT_CM)
    section.left_margin = Cm(MARGIN_LEFT_CM)      # 3.0 cm
    section.right_margin = Cm(MARGIN_RIGHT_CM)    # 2.0 cm
    section.top_margin = Cm(MARGIN_TOP_CM)        # 2.0 cm
    section.bottom_margin = Cm(MARGIN_BOTTOM_CM)  # 2.0 cm

    # Thiết lập Header & Footer
    header = section.header
    hp = header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    hrun = hp.add_run("TRƯỜNG CAO ĐẲNG FPT POLYTECHNIC — BÁO CÁO DỰ ÁN TỐT NGHIỆP 2026")
    hrun.font.name = "Times New Roman"
    hrun.font.size = Pt(9)
    hrun.font.italic = True
    hrun.font.color.rgb = RGBColor(120, 120, 120)

    footer = section.footer
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    frun = fp.add_run("Hệ thống E-Commerce Thời trang Tích hợp AI — AURA Studio")
    frun.font.name = "Times New Roman"
    frun.font.size = Pt(9)
    frun.font.color.rgb = RGBColor(120, 120, 120)

    # 2. Thiết lập Style Normal mặc định
    style_normal = doc.styles['Normal']
    style_normal.font.name = 'Times New Roman'
    style_normal.font.size = Pt(13)
    style_normal.font.color.rgb = RGBColor(0, 0, 0)
    style_normal.paragraph_format.line_spacing = 1.25
    style_normal.paragraph_format.space_after = Pt(4)
    style_normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    # Biến trạng thái đọc
    idx = 0
    total_lines = len(lines)
    is_cover_page = True

    while idx < total_lines:
        line = lines[idx]
        stripped = line.strip()

        # ---------- BƯỚC 1: XỬ LÝ TRANG BÌA (Cover Page) ----------
        if is_cover_page:
            if stripped.startswith("---") and idx > 15:
                # Kết thúc trang bìa
                doc.add_page_break()
                is_cover_page = False
                idx += 1
                continue

            # Render các dòng trang bìa
            if stripped.startswith("# TRƯỜNG CAO ĐẲNG FPT POLYTECHNIC"):
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(2)
                run = p.add_run("TRƯỜNG CAO ĐẲNG FPT POLYTECHNIC")
                run.bold = True
                run.font.size = Pt(14)
                run.font.name = "Times New Roman"
            elif stripped.startswith("### KHOA CÔNG NGHỆ THÔNG TIN"):
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(14)
                run = p.add_run("KHOA CÔNG NGHỆ THÔNG TIN")
                run.bold = True
                run.font.size = Pt(13)
                run.font.name = "Times New Roman"
            elif stripped.startswith("### BÁO CÁO DỰ ÁN TỐT NGHIỆP"):
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_before = Pt(40)
                p.paragraph_format.space_after = Pt(16)
                run = p.add_run("BÁO CÁO DỰ ÁN TỐT NGHIỆP")
                run.bold = True
                run.font.size = Pt(18)
                run.font.color.rgb = RGBColor(194, 65, 12)  # FPT Orange tone
                run.font.name = "Times New Roman"
            elif stripped.startswith("# ĐỀ TÀI:"):
                title_text = stripped.replace("# ĐỀ TÀI:", "ĐỀ TÀI:").strip()
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_before = Pt(10)
                p.paragraph_format.space_after = Pt(36)
                run = p.add_run(title_text)
                run.bold = True
                run.font.size = Pt(15)
                run.font.name = "Times New Roman"
            elif stripped.startswith("**Hà Nội"):
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_before = Pt(40)
                p.paragraph_format.space_after = Pt(0)
                run = p.add_run("Hà Nội – 2026")
                run.bold = True
                run.font.size = Pt(13)
                run.font.name = "Times New Roman"
            elif stripped:
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                p.paragraph_format.left_indent = Cm(1.5)
                p.paragraph_format.space_after = Pt(3)
                add_formatted_text(p, stripped, base_font_size=13)

            idx += 1
            continue

        # ---------- BƯỚC 2: XỬ LÝ NỘI DUNG CHÍNH ----------

        # 1. Bỏ qua dòng trống
        if not stripped:
            idx += 1
            continue

        # 2. Xử lý Phân cách ngang --- (Chuyển trang nếu là ranh giới chương)
        if stripped == "---":
            # Bỏ qua hoặc ngắt nhẹ
            idx += 1
            continue

        # 3. Xử lý Tiêu đề Heading
        if stripped.startswith("## "):
            h_text = stripped[3:].strip()
            # Tự động ngắt trang trước các Phần lớn
            if any(k in h_text for k in ["PHẦN ", "LỜI MỞ ĐẦU", "GIẢI THÍCH THUẬT NGỮ", "MỤC LỤC", "LỜI CẢM ƠN", "TÀI LIỆU THAM KHẢO"]):
                doc.add_page_break()

            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.space_before = Pt(14)
            p.paragraph_format.space_after = Pt(6)
            run = p.add_run(h_text)
            run.bold = True
            run.font.size = Pt(15)
            run.font.name = "Times New Roman"
            run.font.color.rgb = RGBColor(0, 51, 102)  # Xanh navy đậm chuẩn văn bản trang trọng
            idx += 1
            continue

        if stripped.startswith("### "):
            h_text = stripped[4:].strip()
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.space_before = Pt(10)
            p.paragraph_format.space_after = Pt(4)
            run = p.add_run(h_text)
            run.bold = True
            run.font.size = Pt(13.5)
            run.font.name = "Times New Roman"
            run.font.color.rgb = RGBColor(0, 0, 0)
            idx += 1
            continue

        if stripped.startswith("#### "):
            h_text = stripped[5:].strip()
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.space_before = Pt(8)
            p.paragraph_format.space_after = Pt(3)
            run = p.add_run(h_text)
            run.bold = True
            run.font.size = Pt(13)
            run.font.name = "Times New Roman"
            idx += 1
            continue

        if stripped.startswith("# "):
            h_text = stripped[2:].strip()
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.space_before = Pt(16)
            p.paragraph_format.space_after = Pt(8)
            run = p.add_run(h_text)
            run.bold = True
            run.font.size = Pt(16)
            run.font.name = "Times New Roman"
            idx += 1
            continue

        # 4. Xử lý Code Blocks (```mermaid / ```python / ```bash ...)
        if stripped.startswith("```"):
            code_lang = stripped[3:].strip()
            code_lines = []
            idx += 1
            while idx < total_lines and not lines[idx].strip().startswith("```"):
                code_lines.append(lines[idx])
                idx += 1
            idx += 1  # Bỏ qua dòng ``` đóng

            code_text = "\n".join(code_lines)
            label = "Mã biểu đồ Mermaid" if "mermaid" in code_lang else f"Mã nguồn ({code_lang})" if code_lang else "Đoạn mã kỹ thuật"
            create_callout_box(doc, code_text, label=label)
            continue

        # 5. Xử lý Bảng biểu (Markdown Tables)
        if stripped.startswith("|") and stripped.endswith("|"):
            table_lines = []
            while idx < total_lines and lines[idx].strip().startswith("|") and lines[idx].strip().endswith("|"):
                table_lines.append(lines[idx].strip())
                idx += 1

            if len(table_lines) >= 2:
                # Tách header và body
                raw_headers = [c.strip() for c in table_lines[0].split("|")[1:-1]]
                
                body_rows = []
                for t_line in table_lines[1:]:
                    # Bỏ qua dòng delimiter (| :---: | :--- |)
                    if re.match(r'^\|[\s:\-]+(?:\|[\s:\-]+)*\|$', t_line):
                        continue
                    cells = [c.strip() for c in t_line.split("|")[1:-1]]
                    body_rows.append(cells)

                num_cols = len(raw_headers)
                widths_cm = calculate_column_widths(raw_headers, num_cols, [raw_headers] + body_rows)

                # Khởi tạo bảng Word
                table = doc.add_table(rows=len(body_rows) + 1, cols=num_cols)
                
                # Điền header
                for c_idx, h_text in enumerate(raw_headers):
                    cell = table.cell(0, c_idx)
                    p = cell.paragraphs[0]
                    add_formatted_text(p, h_text, base_font_size=10.5 if num_cols >= 7 else 11.0, is_bold_base=True)

                # Điền data
                for r_idx, row_data in enumerate(body_rows):
                    for c_idx in range(num_cols):
                        cell = table.cell(r_idx + 1, c_idx)
                        p = cell.paragraphs[0]
                        cell_val = row_data[c_idx] if c_idx < len(row_data) else ""
                        add_formatted_text(p, cell_val, base_font_size=10.5 if num_cols >= 7 else 11.0)

                # Áp dụng viền, độ rộng, lặp header và word-wrap
                format_table(table, raw_headers, body_rows, widths_cm)
                
                # Khoảng đệm sau bảng
                doc.add_paragraph().paragraph_format.space_after = Pt(4)

            continue

        # 6. Xử lý Công thức Toán LaTeX ($$ ... $$)
        if stripped.startswith("$$") and stripped.endswith("$$") and len(stripped) > 4:
            math_text = stripped[2:-2].strip()
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(6)
            run = p.add_run(math_text)
            run.font.name = "Cambria Math"
            run.font.size = Pt(12)
            run.bold = True
            run.font.italic = True
            run.font.color.rgb = RGBColor(15, 23, 42)
            idx += 1
            continue

        # 7. Xử lý Danh sách không thứ tự (* text hoặc - text)
        if stripped.startswith("* ") or stripped.startswith("- "):
            list_text = stripped[2:].strip()
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.63)
            p.paragraph_format.first_line_indent = Cm(-0.4)
            p.paragraph_format.space_after = Pt(3)
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

            bullet_run = p.add_run("• ")
            bullet_run.bold = True
            bullet_run.font.name = "Times New Roman"
            bullet_run.font.size = Pt(13)

            add_formatted_text(p, list_text, base_font_size=13)
            idx += 1
            continue

        # 8. Xử lý Danh sách có thứ tự (1. text, 2. text...)
        num_list_match = re.match(r'^(\d+)\.\s+(.*)$', stripped)
        if num_list_match:
            num_str = num_list_match.group(1)
            list_text = num_list_match.group(2)
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.75)
            p.paragraph_format.first_line_indent = Cm(-0.5)
            p.paragraph_format.space_after = Pt(3)
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

            num_run = p.add_run(f"{num_str}. ")
            num_run.bold = True
            num_run.font.name = "Times New Roman"
            num_run.font.size = Pt(13)

            add_formatted_text(p, list_text, base_font_size=13)
            idx += 1
            continue

        # 9. Xử lý Trích dẫn / Lưu ý (> text)
        if stripped.startswith("> "):
            quote_text = stripped[2:].strip()
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(1.0)
            p.paragraph_format.right_indent = Cm(0.5)
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(4)
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            add_formatted_text(p, quote_text, base_font_size=12, is_italic_base=True)
            idx += 1
            continue

        # 10. Đoạn văn bản thông thường (Paragraph)
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.space_after = Pt(4)
        add_formatted_text(p, stripped, base_font_size=13)
        idx += 1

    # Lưu tài liệu Word hoàn chỉnh
    doc.save(DOCX_FILE)
    print(f"ĐÃ TẠO THÀNH CÔNG TỆP WORD: '{DOCX_FILE}'")


if __name__ == "__main__":
    build_word_document()
