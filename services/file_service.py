"""文件管理服务"""
import os
from datetime import datetime
from config import load_config, BASE_DIR

def get_contract_dir():
    cfg = load_config()
    d = cfg["paths"]["contract_dir"]
    os.makedirs(d, exist_ok=True)
    return d

def get_reply_dir():
    cfg = load_config()
    d = cfg["paths"]["reply_dir"]
    os.makedirs(d, exist_ok=True)
    return d

def contract_filename(name, id_card, suffix="合同", ext=".pdf"):
    return f"{datetime.now().strftime('%Y%m%d')}+{name}+{id_card}+{suffix}{ext}"

def reply_filename(name, id_card, school_short):
    return f"{datetime.now().strftime('%Y%m%d')}{name}{id_card}投诉回复函{school_short}.docx"

def unique_path(filepath):
    if not os.path.exists(filepath):
        return filepath
    base, ext = os.path.splitext(filepath)
    n = 1
    while os.path.exists(f"{base}({n}){ext}"):
        n += 1
    return f"{base}({n}){ext}"
