"""归档服务 - 归档三闸门校验、归档目录构建与两件套写入（纯函数，无 Flask 路由）

唯一路径规则（所有归档入口统一）：
    {archive_root}/{单位类型}/{代号}-{单位名}/{complaint_date}_{姓名}_{身份证}_{代号}/
        投诉登记表.docx
        投诉回复函.docx
        {姓名}_合同_*.pdf
        {date}_{姓名}_撤诉说明.txt

- archive_root：可由用户在界面配置（持久化到 data/config.json 的 archive_root），
  默认 BASE_DIR / "案件归档"。允许指向任意可写目录（如 ~/Documents/投诉档案/）。
- 单位类型：organization_unit_type（分校/分店），未知时「未归属」。
- 代号：school_short，单字代号（如「常」），未知时「未归属」。
- 单位名：organization_unit_name（如「常平横江厦分校」），未知时「未归属」。
- 学员段：complaint_date / student_name / id_card / code，任意缺失用「未归属」兜底。
"""
import os
import shutil
import subprocess
import sys
import warnings

from config import BASE_DIR, load_config


REGISTER_FILENAME = "投诉登记表.docx"
REPLY_FILENAME = "投诉回复函.docx"
FALLBACK_UNIT_TYPE = "未归属"
FALLBACK_UNIT_NAME = "未归属"
FALLBACK_CODE = "未归属"
FALLBACK_DATE = "1970-01-01"
FALLBACK_NAME = "未命名学员"
FALLBACK_ID_CARD = "无证件"
DEFAULT_ARCHIVE_ROOT = str(BASE_DIR / "案件归档")


def archive_gate_errors(ticket: dict) -> list[str]:
    """归档三闸门：处理情况已填 + 配合度已评 + 费用明细已确认。

    返回缺失项中文描述列表，全部通过时返回空列表。
    """
    errors = []
    if not str(ticket.get("handling_notes") or "").strip():
        errors.append("处理情况未填写")
    if not str(ticket.get("branch_cooperation") or "").strip():
        errors.append("配合度未评定")
    if ticket.get("fee_plan_status") != "confirmed":
        errors.append("费用明细未确认")
    return errors


def _normalize_archive_root(root: str | None) -> str:
    """把用户输入或 config 里的 root 规范成「绝对路径、可写」。

    - 空值/None → DEFAULT_ARCHIVE_ROOT（BASE_DIR/案件归档）
    - 展开 ~ 与相对路径 → 绝对路径
    - 目录不存在则自动创建（创建失败抛 ValueError）
    - 不可写抛 ValueError
    """
    raw = (root or "").strip()
    if not raw:
        raw = DEFAULT_ARCHIVE_ROOT
    expanded = os.path.expanduser(raw)
    # 历史配置里可能存有相对路径（如「案件归档」）：统一锚定到项目根目录，
    # 不随服务进程 CWD 漂移
    if not os.path.isabs(expanded):
        expanded = os.path.join(str(BASE_DIR), expanded)
    expanded = os.path.abspath(expanded)
    if not os.path.isdir(expanded):
        try:
            os.makedirs(expanded, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"无法创建归档根目录: {expanded} ({exc})") from exc
    if not os.access(expanded, os.W_OK):
        raise ValueError(f"归档根目录不可写: {expanded}")
    return expanded


def _assert_within(child: str, parent: str) -> None:
    """断言 child 是 parent 的子路径（含自身），否则抛 ValueError。

    用 realpath 防止符号链接绕路。"""
    real_child = os.path.realpath(child)
    real_parent = os.path.realpath(parent)
    if real_child != real_parent and not real_child.startswith(real_parent + os.sep):
        raise ValueError(f"路径越界: {real_child} 不在 {real_parent} 内")


def _safe_segment(value: str, fallback: str) -> str:
    """把任意字符串清洗成可作为路径段的形式。"""
    import re

    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(value or "").strip(), flags=re.UNICODE)
    cleaned = cleaned.strip("_-")
    return cleaned[:80] or fallback


def _unit_segment(code: str, unit_name: str) -> str:
    """{代号}-{单位名} 路径段；任一为空时退回 FALLBACK_UNIT_NAME。"""
    safe_name = _safe_segment(unit_name, FALLBACK_UNIT_NAME)
    if not code.strip():
        return f"{FALLBACK_CODE}-{safe_name}"
    return f"{code}-{safe_name}"


def _case_segment(complaint_date: str, name: str, id_card: str, code: str) -> str:
    """{date}_{name}_{id_card}_{code} 路径段；空值用兜底。"""
    parts = [
        _safe_segment(complaint_date, FALLBACK_DATE),
        _safe_segment(name, FALLBACK_NAME),
        _safe_segment(id_card, FALLBACK_ID_CARD),
        _safe_segment(code, FALLBACK_CODE),
    ]
    return "_".join(parts)


def build_archive_dir(ticket: dict, root: str | None = None) -> tuple[str, str, str]:
    """按统一规则构建归档目录与两件套目标路径（只拼路径，不建目录）。

    Returns:
        (case_dir, register_target, reply_target) — 全部为绝对路径。
    Raises:
        ValueError: root 不可用或计算结果越界。
    """
    if root is None:
        root = load_config().get("archive_root") or DEFAULT_ARCHIVE_ROOT
    normalized_root = _normalize_archive_root(root)

    code = str(ticket.get("school_short") or "").strip()
    unit_type = str(ticket.get("organization_unit_type") or "").strip() or FALLBACK_UNIT_TYPE
    unit_name = str(ticket.get("organization_unit_name") or "").strip() or FALLBACK_UNIT_NAME
    complaint_date = str(ticket.get("complaint_date") or "").strip()
    name = str(ticket.get("student_name") or "").strip()
    id_card = str(ticket.get("id_card") or "").strip()

    case_dir = os.path.join(
        normalized_root,
        unit_type,
        _unit_segment(code, unit_name),
        _case_segment(complaint_date, name, id_card, code),
    )
    register_target = os.path.join(case_dir, REGISTER_FILENAME)
    reply_target = os.path.join(case_dir, REPLY_FILENAME)

    _assert_within(case_dir, normalized_root)
    return case_dir, register_target, reply_target


def archive_case(ticket: dict, files: dict, open_folder: bool = False) -> dict:
    """一键归档：闸门校验 → 建夹 → 复制两件套（存在者）→ 可选打开文件夹。

    files 形如 {"register_form": 源路径或None, "reply": 源路径或None}，至少一个真实存在。
    成功返回 {"success":True,"dir":...,"files":[...],"opened":bool}。
    """
    errors = archive_gate_errors(ticket)
    if errors:
        return {"success": False, "errors": errors}

    case_dir, register_target, reply_target = build_archive_dir(ticket)
    targets = {"register_form": register_target, "reply": reply_target}
    present = {k: v for k, v in files.items() if v and os.path.isfile(v)}
    if not present:
        return {"success": False, "errors": ["无可归档文件：登记表与回复函均不存在"]}

    os.makedirs(case_dir, exist_ok=True)
    copied = []
    for key in ("register_form", "reply"):
        src = present.get(key)
        if src:
            # 登记表生成时已直接写入归档目录，源与目标相同时跳过复制避免自覆盖损坏
            if os.path.abspath(src) == os.path.abspath(targets[key]):
                copied.append(targets[key])
                continue
            shutil.copyfile(src, targets[key])
            copied.append(targets[key])

    opened = False
    if open_folder:
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", case_dir], check=True)
            elif sys.platform == "win32":
                os.startfile(case_dir)
            else:
                subprocess.run(["xdg-open", case_dir], check=True)
            opened = True
        except Exception as exc:
            warnings.warn(f"打开归档文件夹失败: {exc}")

    return {"success": True, "dir": case_dir, "files": copied, "opened": opened}


def validate_archive_root(root: str | None) -> str:
    """公开入口：校验并规范归档根目录，返回绝对路径；不可用抛 ValueError。"""
    return _normalize_archive_root(root)


def contract_target_name(student_name: str, src_filename: str) -> str:
    """合同归档统一命名：{姓名}_合同_{原始文件名}{扩展名}。

    - 已符合「{姓名}_合同_」前缀的文件保持原名（避免重复拼接）；
    - 原始文件名经 _safe_segment 清洗，姓名缺失用「未命名学员」兜底。
    """
    stem, ext = os.path.splitext(os.path.basename(src_filename))
    safe_name = _safe_segment(student_name, FALLBACK_NAME)
    if stem.startswith(f"{safe_name}_合同_"):
        return f"{stem}{ext}"
    return f"{safe_name}_合同_{_safe_segment(stem, '合同')}{ext}"


def archive_contract(ticket: dict, sources: list, case_dir: str | None = None) -> dict:
    """把合同源文件统一归入案件归档夹（命名 {姓名}_合同_*）。

    - sources：合同源路径列表（contract_path + manifest 里的文件），自动去重、忽略不存在项；
    - case_dir：调用方已构建好的案件夹（终归档流程传入），缺省时按 ticket 自建；
    - 源在案件夹外 → 复制（保留原件供分析链路引用）；源已在案件夹内但命名不规范 → 原地重命名；
    - 返回 {"copied": [目标路径...], "errors": [失败描述...], "mapping": {源路径: 目标路径}}，失败不抛出。
    """
    copied: list[str] = []
    errors: list[str] = []
    mapping: dict[str, str] = {}
    seen: set[str] = set()
    srcs: list[str] = []
    for src in sources or []:
        s = str(src or "").strip()
        if not s or not os.path.isfile(s):
            continue
        real = os.path.realpath(s)
        if real in seen:
            continue
        seen.add(real)
        srcs.append(s)
    if not srcs:
        return {"copied": [], "errors": [], "mapping": {}}

    try:
        if not case_dir:
            case_dir, _, _ = build_archive_dir(ticket)
        os.makedirs(case_dir, exist_ok=True)
    except (ValueError, OSError) as exc:
        return {"copied": [], "errors": [f"合同归档目录构建失败: {exc}"], "mapping": {}}

    real_case_dir = os.path.realpath(case_dir)
    name = str(ticket.get("student_name") or "").strip()
    for src in srcs:
        try:
            target = os.path.join(case_dir, contract_target_name(name, os.path.basename(src)))
            real_src = os.path.realpath(src)
            if real_src == os.path.realpath(target):
                copied.append(target)
                mapping[src] = target
                continue
            if real_src.startswith(real_case_dir + os.sep):
                # 已在案件夹内（如旧命名的历史文件）→ 原地改名归位
                if os.path.exists(target):
                    os.remove(target)
                os.replace(src, target)
            else:
                shutil.copyfile(src, target)
            copied.append(target)
            mapping[src] = target
        except OSError as exc:
            errors.append(f"合同归档失败 {os.path.basename(src)}: {exc}")
    return {"copied": copied, "errors": errors, "mapping": mapping}
