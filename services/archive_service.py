"""归档服务 - 归档三闸门校验、归档目录构建与两件套写入（纯函数，无 Flask 路由）"""
import os
import shutil
import subprocess
import sys
import warnings

from config import load_config


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


def build_archive_dir(ticket: dict, root: str = None) -> tuple[str, str, str]:
    """按规则构建归档目录与两件套目标路径（只拼路径，不建目录）。

    规则：{root}/{organization_unit_type}/{代号}-{单位名}/{complaint_date}_{student_name}_{id_card}_{代号}/
    代号取 school_short；单位名取 organization_unit_name；未知时用「未归属」。
    返回 (夹路径, 登记表目标路径, 回复函目标路径)。
    """
    if root is None:
        root = load_config().get("archive_root", "案件归档")
    code = str(ticket.get("school_short") or "").strip() or "未归属"
    unit_type = str(ticket.get("organization_unit_type") or "").strip() or "未归属"
    unit_name = str(ticket.get("organization_unit_name") or "").strip() or "未归属"
    date = str(ticket.get("complaint_date") or "").strip()
    name = str(ticket.get("student_name") or "").strip()
    id_card = str(ticket.get("id_card") or "").strip()
    case_dir = os.path.join(root, unit_type, f"{code}-{unit_name}", f"{date}_{name}_{id_card}_{code}")
    register_target = os.path.join(case_dir, f"{date}_{name}_投诉登记表.docx")
    reply_target = os.path.join(case_dir, f"{date}_{name}_投诉回复函.docx")
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
