"""文件管理服务"""
import os
import shutil
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


def is_path_within(filepath: str, allowed_dirs: list[str]) -> bool:
    """Return whether an existing or prospective path is inside an allowed directory."""
    if not filepath:
        return False
    real_path = os.path.realpath(filepath)
    for directory in allowed_dirs:
        try:
            if os.path.commonpath((real_path, os.path.realpath(directory))) == os.path.realpath(directory):
                return True
        except ValueError:
            continue
    return False


def create_derived_copy(filepath: str, suffix: str = ".derived") -> str:
    """Copy an immutable source file into its per-directory derived-file area."""
    source = os.path.realpath(filepath)
    base_dir = os.path.dirname(source)
    derived_dir = os.path.join(base_dir, ".derived")
    os.makedirs(derived_dir, exist_ok=True)
    filename = os.path.basename(source)
    stem, ext = os.path.splitext(filename)
    derived = unique_path(os.path.join(derived_dir, f"{stem}{suffix}{ext}"))
    shutil.copy2(source, derived)
    return derived
