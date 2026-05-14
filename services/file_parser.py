"""多格式文件文本提取器 - 支持 PDF/图片/DOCX/XLSX/TXT"""
import os


def extract_text(filepath: str) -> str:
    """根据文件扩展名自动选择提取方式"""
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".pdf":
        return _extract_pdf(filepath)
    elif ext in (".png", ".jpg", ".jpeg", ".bmp", ".tiff"):
        return _extract_image(filepath)
    elif ext == ".docx":
        return _extract_docx(filepath)
    elif ext == ".xlsx":
        return _extract_xlsx(filepath)
    elif ext == ".txt":
        return _extract_txt(filepath)
    else:
        return ""


def _extract_pdf(filepath: str) -> str:
    """从 PDF 提取文本"""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n".join(text_parts)
    except Exception:
        pass

    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(filepath)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""


def _extract_image(filepath: str) -> str:
    """从图片 OCR 提取文本（使用 easyocr + 图像预处理）"""
    try:
        # 修复 macOS Python 3.14 的 SSL 证书问题
        import ssl, urllib.request
        ssl_ctx = ssl.create_default_context(cafile=__import__("certifi").where())
        urllib.request.install_opener(
            urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_ctx))
        )
        
        # 图像预处理：提升 OCR 识别率
        from PIL import Image, ImageEnhance, ImageFilter
        img = Image.open(filepath)
        # 转为灰度图并增强对比度
        img = img.convert('L')
        img = ImageEnhance.Contrast(img).enhance(2.0)
        img = img.filter(ImageFilter.SHARPEN)
        
        # 临时保存处理后图片
        temp_path = filepath + "_temp_processed.png"
        img.save(temp_path)
        
        import easyocr
        reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
        result = reader.readtext(temp_path)
        
        # 清理临时文件
        if os.path.exists(temp_path): os.remove(temp_path)
        
        # 过滤低置信度结果并拼接
        lines = [item[1] for item in result if item[2] > 0.2]
        return "\n".join(lines)
    except Exception as e:
        print(f"OCR Error: {e}")
        return ""


def _extract_docx(filepath: str) -> str:
    """从 Word 文档提取文本"""
    try:
        from docx import Document
        doc = Document(filepath)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception:
        return ""


def _extract_xlsx(filepath: str) -> str:
    """从 Excel 提取所有单元格文本"""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
        lines = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            lines.append(f"--- 工作表: {sheet_name} ---")
            for row in ws.iter_rows(values_only=True):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    lines.append(" | ".join(cells))
        return "\n".join(lines)
    except Exception:
        return ""


def _extract_txt(filepath: str) -> str:
    """从文本文件提取"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(filepath, "r", encoding="gbk") as f:
                return f.read()
        except Exception:
            return ""
