"""驾校投诉处理系统 - Flask 后端"""
import asyncio
import os
import sys
import json
import re
import traceback
import mimetypes
import certifi
import uuid
import hashlib
import shutil
import tempfile
import atexit
from datetime import datetime, date
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event, Lock, Thread
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge, NotFound

# 修复 macOS Python 3.14 SSL 证书问题，确保 OCR 库能下载模型
os.environ['SSL_CERT_FILE'] = certifi.where()

# 确保项目根目录在路径中
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config import load_config, save_config, normalize_llm_api_url, BASE_DIR as PROJECT_DIR
from database import (
    save_ticket, get_ticket, get_ticket_by_idcard, get_latest_ticket_by_idcard,
    list_tickets, update_ticket, delete_ticket, get_ticket_statistics, get_processing_duration_stats,
    save_complaint, get_complaint_by_idcard,
    list_complaints, get_statistics,
    add_log, find_same_day_ticket,
    save_template, list_templates, get_default_template, delete_template,
    get_distinct_school_short,
    migrate_communications_to_notes,
    get_org_vehicle_count_items, get_org_vehicle_counts, save_org_vehicle_counts,
    create_contract_analysis_job, get_contract_analysis_job,
    find_active_contract_analysis_job, update_contract_analysis_job,
)
from core.query_engine import query_engine, query_all_systems_sync
from core.auth_manager import auth_manager, refresh_session_if_due, SystemType
from services.contract_service import (
    analyze_contract_from_file, apply_authoritative_total_fee,
    build_contract_clauses,
    _llm_config, _format_llm_error,
)
from services.reply_docx import generate_reply_docx
from services.archive_service import archive_gate_errors, build_archive_dir, archive_case
from services.intake_service import parse_complaint_file, parse_complaint_text, ensure_upload_dir, ai_summarize_complaint
from services.org_unit_service import ORGANIZATION_UNITS, resolve_org_unit
from services.file_service import is_path_within, create_derived_copy, unique_path
from services.file_parser import warmup_ocr
from utils.logger import system_logger

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB
app.config["TEMPLATES_AUTO_RELOAD"] = True  # 禁用模板缓存


@app.errorhandler(RequestEntityTooLarge)
def _handle_request_entity_too_large(_e):
    return _err("上传文件超过大小限制（50MB）", 413)


@app.errorhandler(NotFound)
def _handle_not_found(e):
    """API 未知路由返回 JSON 404，页面路由保持原 HTML 404"""
    if request.path.startswith("/api/"):
        return _err("接口不存在", 404)
    return e

UPLOAD_DIR = os.path.join(PROJECT_DIR, "uploads")
ARCHIVE_DIR = os.path.join(PROJECT_DIR, "案件归档")
REPLY_DIR = os.path.join(PROJECT_DIR, "回复函")

for d in (UPLOAD_DIR, ARCHIVE_DIR, REPLY_DIR):
    os.makedirs(d, exist_ok=True)


CONTRACT_ANALYSIS_EXECUTOR = ThreadPoolExecutor(max_workers=2)
CONTRACT_ANALYSIS_JOBS = {}
CONTRACT_ANALYSIS_LOCK = Lock()
CONTRACT_ANALYSIS_MAX_JOBS = 100
REPLY_GENERATE_LOCKS = {}
REPLY_GENERATE_LOCKS_GUARD = Lock()
QUERY_EXECUTOR = ThreadPoolExecutor(max_workers=2)
QUERY_JOBS = {}
QUERY_JOBS_LOCK = Lock()
QUERY_JOBS_MAX = 100
INTERNAL_SESSION_REFRESH_AFTER_SECONDS = 12 * 60
# 第三/东莞驾培会话同样需后台保活：两者未定义 SESSION_MAX_AGE_SECONDS，
# 会话在服务端过期后，首个业务查询才会触发验证码重登录（driving 最多 6 次 OCR），
# 提前刷新可避免冷查询延迟。
THIRD_SESSION_REFRESH_AFTER_SECONDS = 12 * 60
DRIVING_SESSION_REFRESH_AFTER_SECONDS = 12 * 60
INTERNAL_SESSION_CHECK_INTERVAL_SECONDS = 60
INTERNAL_SESSION_MAINTENANCE_STOP = Event()
FEE_PLAN_FIELDS = {
    "total_fee",
    "actual_paid",
    "deduction_fee",
    "refund_fee",
    "deduction_detail",
    "contract_set",
    "fee_plan_status",
    "fee_plan_version",
    "fee_plan_history",
    "fee_plan_snapshot",
}
CONTRACT_UPLOAD_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
CONTRACT_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def _safe_path_component(value: str, fallback: str) -> str:
    """Keep a human-readable label while removing every path/control character."""
    import re

    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(value or "").strip(), flags=re.UNICODE)
    cleaned = cleaned.strip("_-")
    return cleaned[:80] or fallback


def _prune_contract_analysis_jobs(max_jobs: int = CONTRACT_ANALYSIS_MAX_JOBS):
    """限制内存任务表大小，只移除已结束的旧任务。"""
    with CONTRACT_ANALYSIS_LOCK:
        if len(CONTRACT_ANALYSIS_JOBS) <= max_jobs:
            return
        finished = [
            (job_id, job)
            for job_id, job in CONTRACT_ANALYSIS_JOBS.items()
            if job.get("status") in {"done", "failed"}
        ]
        finished.sort(key=lambda item: item[1].get("finished_at") or item[1].get("created_at") or "")
        remove_count = len(CONTRACT_ANALYSIS_JOBS) - max_jobs
        for job_id, _job in finished[:remove_count]:
            CONTRACT_ANALYSIS_JOBS.pop(job_id, None)


def _prune_query_jobs(max_jobs: int = QUERY_JOBS_MAX):
    with QUERY_JOBS_LOCK:
        if len(QUERY_JOBS) <= max_jobs:
            return
        finished = [
            (job_id, job)
            for job_id, job in QUERY_JOBS.items()
            if job.get("status") in {"done", "failed"}
        ]
        finished.sort(key=lambda item: item[1].get("finished_at") or item[1].get("created_at") or "")
        for job_id, _job in finished[:len(QUERY_JOBS) - max_jobs]:
            QUERY_JOBS.pop(job_id, None)


def get_archive_folder(name: str, id_card: str, school_short: str) -> str:
    """获取或创建学员的归档文件夹（优先复用已有文件夹）"""
    safe_name = _safe_path_component(name, "未知学员")
    safe_id_card = _safe_path_component(id_card, "未知证件")
    safe_school = _safe_path_component(school_short, "未知校区")
    # 先查找是否已存在该学员的文件夹（按身份证号匹配）
    if os.path.exists(ARCHIVE_DIR):
        exact_identity_suffix = f"_{safe_name}_{safe_id_card}_{safe_school}"
        for folder_name in os.listdir(ARCHIVE_DIR):
            candidate = os.path.join(ARCHIVE_DIR, folder_name)
            if (
                folder_name.endswith(exact_identity_suffix)
                and os.path.isdir(candidate)
                and is_path_within(candidate, [ARCHIVE_DIR])
            ):
                return candidate
    
    # 不存在则创建新文件夹
    date_str = datetime.now().strftime("%Y%m%d")
    folder_name = f"{date_str}_{safe_name}_{safe_id_card}_{safe_school}"
    folder_path = os.path.join(ARCHIVE_DIR, folder_name)
    if not is_path_within(folder_path, [ARCHIVE_DIR]):
        raise ValueError("归档目录不安全")
    os.makedirs(folder_path, exist_ok=True)
    return folder_path


# ═══════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════

def _ok(data=None, msg="成功"):
    """统一成功返回"""
    resp = {"success": True}
    if data is not None:
        resp["data"] = data
    if msg:
        resp["message"] = msg
    return jsonify(resp)


def _err(msg, code=400):
    """统一错误返回"""
    return jsonify({"success": False, "error": msg}), code


def parse_deductions(raw) -> list:
    """兼容解析扣费明细：list 直返；str 反序列化；dict（save_analysis 整包存储）提取其明细数组。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if isinstance(raw, dict):
        inner = raw.get("deductions", raw.get("deduction_detail"))
        return inner if isinstance(inner, list) else []
    return raw if isinstance(raw, list) else []


def _excel_safe_value(value):
    """防公式注入：以 = + - @ 开头的字符串写入单元格前缀 ' 转义"""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _fee_plan_ticket_or_error(ticket_id: str, material_name: str):
    """对外材料必须基于人工确认过的费用明细。返回 (ticket, err, http_status)。"""
    if not ticket_id:
        return None, f"缺少案件ID，不能生成{material_name}", 400
    ticket = get_ticket(ticket_id)
    if not ticket:
        return None, "工单不存在", 404
    if ticket.get("fee_plan_status") != "confirmed":
        return None, "请先人工确认费用方案，再生成材料", 400
    return ticket, "", 200


def _confirmed_ticket_or_error(ticket_id: str):
    """正式文档/归档输出必须基于已人工确认的费用明细。"""
    return _fee_plan_ticket_or_error(ticket_id, "正式材料")


def _official_case_payload(ticket: dict, incoming: dict | None = None) -> dict:
    """以数据库中已确认的费用明细为准，忽略前端传入的金额/扣费草稿。"""
    incoming = incoming or {}
    deductions = parse_deductions(ticket.get("deduction_detail"))

    return {
        **incoming,
        "ticket_id": ticket.get("id", incoming.get("ticket_id", "")),
        "name": ticket.get("student_name") or incoming.get("name", ""),
        "id_card": ticket.get("id_card") or incoming.get("id_card", ""),
        "phone": ticket.get("phone") or incoming.get("phone", ""),
        "school_short": ticket.get("school_short") or incoming.get("school_short", ""),
        "school_name": ticket.get("school_name") or incoming.get("school_name", ""),
        "registration_date": ticket.get("registration_date") or incoming.get("registration_date", ""),
        "license_type": ticket.get("license_type") or incoming.get("license_type", ""),
        "exam_stage": ticket.get("exam_stage") or incoming.get("exam_stage", ""),
        "complaint_date": ticket.get("complaint_date") or incoming.get("complaint_date", ""),
        "complaint_channel": ticket.get("source_channel") or incoming.get("complaint_channel", ""),
        "complaint_type": ticket.get("complaint_type") or incoming.get("complaint_type", ""),
        "total_fee": float(ticket.get("total_fee", 0) or 0),
        "registration_fee": float(ticket.get("total_fee", 0) or 0),
        "actual_paid": float(ticket.get("actual_paid", 0) or 0),
        "deductions": deductions,
        "total_deduction": float(ticket.get("deduction_fee", 0) or 0),
        "deduction": float(ticket.get("deduction_fee", 0) or 0),
        "deduction_fee": float(ticket.get("deduction_fee", 0) or 0),
        "refund": float(ticket.get("refund_fee", 0) or 0),
        "contract_code": ticket.get("contract_code") or incoming.get("contract_code", ""),
        "training_hours": ticket.get("training_hours") or incoming.get("training_hours", {}),
        "final_outcome": ticket.get("final_outcome") or incoming.get("final_outcome", ""),
        "branch_cooperation": ticket.get("branch_cooperation") or incoming.get("branch_cooperation", ""),
    }


def _completion_gate_error(ticket: dict, incoming: dict) -> str:
    """案件完结/归档三闸门：处理情况已填 + 配合度已评 + 费用明细已确认。"""
    wants_complete = incoming.get("handle_status") == "已完结" or incoming.get("archive_status") == "已归档"
    if not wants_complete:
        return ""

    handling_notes = str(incoming.get("handling_notes") or ticket.get("handling_notes") or "").strip()
    if not handling_notes:
        return "请先填写处理情况，再完结归档案件"

    cooperation = str(incoming.get("branch_cooperation") or ticket.get("branch_cooperation") or "").strip()
    if not cooperation:
        return "请先评价网点配合度，再完结归档案件"

    fee_status = incoming.get("fee_plan_status") or ticket.get("fee_plan_status", "")
    if fee_status != "confirmed":
        return "请先人工确认费用明细，再完结归档案件"

    return ""


def _reopen_case_fields() -> dict:
    """归档后的费用更正必须重新打开案件，而不是保留旧归档结论。"""
    return {
        "handle_status": "处理中",
        "archive_status": "未归档",
        "completed_at": "",
    }


def _deduction_row_amount(row) -> float:
    if not isinstance(row, dict):
        return 0.0
    try:
        return float(row.get("amt", row.get("amount")) or 0)
    except (TypeError, ValueError):
        return 0.0


def _reply_generate_lock(ticket_id: str) -> Lock:
    with REPLY_GENERATE_LOCKS_GUARD:
        lock = REPLY_GENERATE_LOCKS.get(ticket_id)
        if lock is None:
            lock = Lock()
            REPLY_GENERATE_LOCKS[ticket_id] = lock
        return lock


def _cleanup_withdraw_archive(ticket: dict) -> list:
    """已归档工单取消撤诉后，移除归档夹内残留的撤诉说明。

    返回失败描述列表（空列表=成功），任何失败不抛出。
    """
    errors = []
    try:
        case_dir, _reg_target, _reply_target = build_archive_dir(ticket)
        name = str(ticket.get("student_name") or "").strip() or "未知"
        date = str(ticket.get("complaint_date") or "").strip()
        note_path = os.path.join(case_dir, f"{date}_{name}_撤诉说明.txt")
        if os.path.isfile(note_path):
            os.remove(note_path)
    except Exception as e:
        errors.append(f"撤诉说明清理失败: {e}")
    return errors


def _mask_id_card(id_card) -> str:
    value = str(id_card or "").strip()
    if len(value) <= 10:
        return value[:3] + "*" * max(0, len(value) - 3)
    return value[:6] + "*" * (len(value) - 10) + value[-4:]


def _polish_registration_ticket(ticket: dict) -> tuple:
    """登记表预览/归档前，AI 补齐缺失的 投诉内容/投诉诉求/处理经过 三段。

    返回 (工单副本, ai_sections)。AI 失败返回空 sections，由登记表数据层按类型话术兜底；
    AI 生成的 内容/诉求 在原字段为空时回写工单持久化，避免重复调用与多次生成内容漂移。
    """
    from services.intake_service import ai_polish_registration
    from services.visit_service import _looks_like_idcard
    ticket = dict(ticket)
    # 防御：投诉内容/诉求被误填为身份证号（或与本工单 id_card 相同）时视为缺失，
    # 交由 AI 重新整理，避免身份证号写入登记表文档
    cc = str(ticket.get("complaint_content") or "").strip()
    if _looks_like_idcard(cc) or cc == str(ticket.get("id_card") or "").strip():
        ticket["complaint_content"] = ""
    dm = str(ticket.get("complaint_demands") or "").strip()
    if _looks_like_idcard(dm) or dm == str(ticket.get("id_card") or "").strip():
        ticket["complaint_demands"] = ""
    needs = (
        not str(ticket.get("complaint_content") or "").strip()
        or not str(ticket.get("complaint_demands") or "").strip()
        or not str(ticket.get("handling_notes") or "").strip()
    )
    if not needs:
        return ticket, {}
    polished = ai_polish_registration(ticket)
    if not polished:
        return ticket, {}
    updates = {}
    if not str(ticket.get("complaint_content") or "").strip() and polished["complaint_content"]:
        ticket["complaint_content"] = polished["complaint_content"]
        updates["complaint_content"] = polished["complaint_content"]
    if not str(ticket.get("complaint_demands") or "").strip() and polished["complaint_demands"]:
        ticket["complaint_demands"] = polished["complaint_demands"]
        updates["complaint_demands"] = polished["complaint_demands"]
    if updates and ticket.get("id"):
        try:
            update_ticket(str(ticket["id"]), updates)
        except Exception:
            pass
    return ticket, polished


def _regen_registration_form(ticket: dict, output_dir: str = "") -> dict:
    from services.visit_service import generate_registration_form
    special_warnings = ticket.get("special_warnings") or []
    if isinstance(special_warnings, str):
        try:
            special_warnings = json.loads(special_warnings) if special_warnings.strip() else []
        except json.JSONDecodeError:
            special_warnings = []
    ticket, ai_sections = _polish_registration_ticket(ticket)
    return generate_registration_form(
        ticket,
        handling_notes=str(ticket.get("handling_notes") or ""),
        final_outcome=str(ticket.get("final_outcome") or ""),
        negotiation_outcome=str(ticket.get("negotiation_outcome") or ""),
        withdraw_status=str(ticket.get("withdraw_status") or ""),
        branch_cooperation=str(ticket.get("branch_cooperation") or ""),
        output_dir=output_dir,
        total_fee=float(ticket.get("total_fee", 0) or 0),
        refund=float(ticket.get("refund_fee", 0) or 0),
        deductions=parse_deductions(ticket.get("deduction_detail")),
        special_warnings=special_warnings,
        exam_stage=str(ticket.get("exam_stage") or ""),
        ai_sections=ai_sections,
    )


def _sync_withdraw_archive(ticket: dict) -> list:
    """已归档工单撤诉后同步归档夹：重生成登记表覆盖副本 + 写撤诉说明。

    返回失败描述列表（空列表=全部成功），任何失败不抛出。
    """
    errors = []
    try:
        case_dir, reg_target, _reply_target = build_archive_dir(ticket)
        name = str(ticket.get("student_name") or "").strip() or "未知"
        date = str(ticket.get("complaint_date") or "").strip()
        os.makedirs(case_dir, exist_ok=True)
        if str(ticket.get("registration_form_path") or "").strip():
            try:
                with tempfile.TemporaryDirectory() as tmp_dir:
                    gen = _regen_registration_form(ticket, output_dir=tmp_dir)
                    if gen.get("success"):
                        shutil.copyfile(gen["filepath"], reg_target)
                    else:
                        errors.append(f"登记表重新生成失败: {gen.get('error')}")
            except Exception as e:
                errors.append(f"登记表重新生成失败: {e}")
        note_path = os.path.join(case_dir, f"{date}_{name}_撤诉说明.txt")
        content = (
            f"学员姓名：{name}\n"
            f"身份证号：{_mask_id_card(ticket.get('id_card'))}\n"
            f"投诉日期：{date}\n"
            f"撤诉时间：{ticket.get('withdrawn_at') or ''}\n"
            f"撤诉原因：{ticket.get('withdraw_reason') or ''}\n"
            f"经办说明：本撤诉申请由学员本人提出，经办人已核实学员身份与撤诉意愿，案件按撤诉终结处理。\n"
        )
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        errors.append(f"撤诉档案同步失败: {e}")
    return errors


# ═══════════════════════════════════════════════════════════════
#  后台预登录
# ═══════════════════════════════════════════════════════════════

def background_login():
    """后台预登录三系统，提升首次查询速度"""
    import threading

    def _login_system(system_type):
        try:
            type_map = {
                "internal": SystemType.INTERNAL,
                "third": SystemType.THIRD,
                "driving": SystemType.DRIVING,
            }
            st = type_map.get(system_type)
            if not st:
                return

            crawler = query_engine._crawlers.get(st)
            if not crawler:
                system_logger.info("[预登录] %s: 爬虫未初始化，跳过", system_type)
                return

            # 检查配置完整性（避免空配置浪费线程）
            cfg = load_config().get(f"{system_type}_system", {})
            if not cfg.get("username") or not cfg.get("password") or cfg.get("password") == "":
                system_logger.info("[预登录] %s: 配置不完整，跳过", system_type)
                return

            result = crawler.login()
            system_logger.info("[预登录] %s: %s - %s", system_type, "成功" if result.success else "失败", result.message)
        except Exception as e:
            system_logger.error("[预登录] %s: 异常 - %s", system_type, e)

    systems = ["internal", "third", "driving"]
    for system in systems:
        t = threading.Thread(target=_login_system, args=(system,), daemon=True)
        t.start()
    system_logger.info("[预登录] 已启动后台登录线程")


def _maintain_sessions():
    """按系统后台刷新登录态，避免首个业务查询承担验证码登录（含第三/东莞驾培）。"""
    refresh_plan = [
        (SystemType.INTERNAL, INTERNAL_SESSION_REFRESH_AFTER_SECONDS),
        (SystemType.THIRD, THIRD_SESSION_REFRESH_AFTER_SECONDS),
        (SystemType.DRIVING, DRIVING_SESSION_REFRESH_AFTER_SECONDS),
    ]
    while not INTERNAL_SESSION_MAINTENANCE_STOP.wait(
        INTERNAL_SESSION_CHECK_INTERVAL_SECONDS
    ):
        for system_type, refresh_after in refresh_plan:
            crawler = query_engine._crawlers.get(system_type)
            if not crawler:
                continue
            try:
                refresh_session_if_due(crawler, refresh_after_seconds=refresh_after)
            except Exception as exc:
                system_logger.warning("[会话维护] %s: %s", system_type.value, exc)


def _start_background_services():
    """应用真正对外服务前才启动后台线程与一次性迁移（模块导入保持无副作用）。"""
    try:
        migrate_communications_to_notes()
    except Exception as e:
        system_logger.error("[初始化] 沟通记录迁移失败: %s", e)

    background_login()
    Thread(target=warmup_ocr, name="ocr-warmup", daemon=True).start()
    thread = Thread(target=_maintain_sessions, name="session-maintenance", daemon=True)
    thread.start()
    atexit.register(INTERNAL_SESSION_MAINTENANCE_STOP.set)


# ═══════════════════════════════════════════════════════════════
#  页面路由
# ═══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


# ═══════════════════════════════════════════════════════════════
#  API: 智能投诉工单受理
# ═══════════════════════════════════════════════════════════════

@app.route("/api/intake/parse", methods=["POST"])
def api_intake_parse():
    """提取投诉关键信息：支持 JSON {"text": "..."} 直接解析，或上传投诉工单文件"""
    try:
        if request.is_json:
            data = request.get_json(force=True, silent=True) or {}
            text = str(data.get("text") or "")
            if len(text.strip()) < 10:
                return _err("投诉内容过短，请粘贴完整的投诉文本")

            result = parse_complaint_text(text)
            if result.get("error"):
                return _err(result["error"])

            add_log("intake_parse", f"受理解析(粘贴文本): 姓名={result.get('student_name') or '?'}")
            return _ok(result)

        if "file" not in request.files:
            return _err("未选择文件")

        file = request.files["file"]
        if not file.filename:
            return _err("文件名为空")

        # 保存上传文件
        upload_dir = ensure_upload_dir()
        ext = os.path.splitext(file.filename)[1].lower()
        allowed_ext = (".pdf", ".png", ".jpg", ".jpeg", ".docx", ".xlsx", ".txt")
        if ext not in allowed_ext:
            return _err(f"不支持的文件格式: {ext}，支持: {', '.join(allowed_ext)}")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = f"intake_{timestamp}_{secure_filename(file.filename)}"
        filepath = os.path.join(upload_dir, safe_name)
        file.save(filepath)

        # LLM 提取
        result = parse_complaint_file(filepath)

        if result.get("error"):
            return _err(result["error"])

        result["_filepath"] = filepath
        result["_filename"] = file.filename

        add_log("intake_parse", f"受理解析: {file.filename} -> {result.get('student_name', '?')}")
        return _ok(result)

    except RequestEntityTooLarge:
        return _err("上传文件超过大小限制（50MB）", 413)
    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


# ═══════════════════════════════════════════════════════════════
#  API: 三系统查询
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════════

def validate_id_card(id_card: str) -> tuple[bool, str]:
    """
    只验证18位大陆身份证（GB 11643-1999 校验码算法）
    其他证件仅放行已知格式（居留证如 F1249468(8)、外国人永久居留证 3字母+12数字、纯数字证件号），
    明显乱码直接拦截，避免无效请求直打生产三系统
    返回: (是否有效, 错误信息)
    """
    id_card = id_card.strip().upper()
    if len(id_card) == 18 and id_card[:17].isdigit() and id_card[17] in '0123456789X':
        weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
        check_codes = ['1', '0', 'X', '9', '8', '7', '6', '5', '4', '3', '2']
        s = sum(int(id_card[i]) * weights[i] for i in range(17))
        if id_card[17] != check_codes[s % 11]:
            return False, "身份证号校验失败，请确认是否正确"
        return True, ""
    # 非18位：格式白名单（前缀最多3位大写字母 + 6~17位数字 + 可选括号尾注，兼容居留证 F1249468(8)）
    if re.fullmatch(r"[A-Z]{0,3}\d{6,17}(?:[（(]\d{1,4}[)）])?", id_card):
        return True, ""
    return False, "证件号格式不正确"


def _persist_query_result(data: dict, result: dict, id_card: str, phone: str) -> dict:
    school_name = result.get("school_name", "")
    school_short = result.get("school_short", "")
    if not school_short and school_name:
        import re
        match = re.search(r'\(([^)]+)\)', school_name)
        if match:
            school_short = match.group(1)
            result["school_short"] = school_short

    ticket_data = {
        "id_card": id_card or result.get("id_card", ""),
        "student_name": result.get("name", ""),
        "phone": phone or result.get("phone", ""),
        "license_type": result.get("license_type", ""),
        "school_name": school_name,
        "school_short": school_short,
        "registration_date": result.get("registration_date", ""),
        "exam_stage": result.get("exam_stage", ""),
        "student_status": result.get("student_status", ""),
        "training_hours": result.get("training_hours", {}),
        "query_result": result,
        "source_channel": data.get("source_channel", "") or "交通部门",
        "complaint_type": data.get("complaint_type", "") or "A",
        "complaint_date": data.get("complaint_date", datetime.now().strftime("%Y-%m-%d")),
        "handler_name": data.get("handler_name", ""),
        "complaint_content": str(data.get("complaint_desc", "") or ""),
        "complaint_summary": str(data.get("complaint_summary", "") or ""),
        "complaint_demands": str(data.get("complaint_demands", "") or ""),
        "attachments": data.get("attachments", []),
    }
    # 受理端选择「另建新工单」(force_new=true) 时跳过同日同人去重；
    # 否则按与 save_ticket 相同口径做预检：命中即显式并入既有工单，
    # 并向受理端回传 merged_into_existing 事实，避免静默更新对用户不可见
    force_new = bool(data.get("force_new"))
    if data.get("ticket_id"):
        ticket_data["id"] = data["ticket_id"]
    elif not force_new:
        same_day = find_same_day_ticket(
            str(ticket_data.get("id_card") or ""),
            str(ticket_data.get("complaint_date") or ""),
        )
        if same_day:
            ticket_data["id"] = same_day["id"]
            result["merged_into_existing"] = True
    saved_ticket_id = save_ticket(ticket_data, force_new=force_new)
    result["ticket_id"] = saved_ticket_id
    add_log(
        "query",
        f"查询学员: {result.get('name') or '未命中'} ({id_card or phone})",
        ticket_id=saved_ticket_id,
    )
    return result


@app.route("/api/query", methods=["POST"])
def api_query():
    """查询三系统学员信息（支持身份证/居留证优先，手机号备选）"""
    try:
        data = request.get_json(force=True, silent=True)
        if data is None:
            return _err("请求体不是合法JSON", 400)
        id_card = data.get("id_card", "").strip()
        phone = data.get("phone", "").strip()

        # 证件号校验（18位身份证验真伪，其他证件不验）
        if id_card:
            ok, err = validate_id_card(id_card)
            if not ok:
                return _err(err)

        # 手机号格式校验（1开头的11位数字）
        if phone and not re.fullmatch(r"1[3-9]\d{9}", phone):
            return _err("手机号格式不正确，应为1开头的11位数字")

        # 执行查询
        if id_card and len(id_card) >= 7:
            result = query_all_systems_sync(id_card)
        elif phone and len(phone) == 11:
            result = query_all_systems_by_phone(phone)
        else:
            return _err("请输入有效的证件号（至少7位）或手机号(11位)")

        return jsonify(_persist_query_result(data, result, id_card, phone))

    except Exception as e:
        traceback.print_exc()
        add_log("query", f"查询失败: {e}", success=False)
        return _err(f"查询异常: {str(e)}", 500)


# ═══════════════════════════════════════════════════════════════
#  API: 手机号查询三系统
# ═══════════════════════════════════════════════════════════════

def query_all_systems_by_phone(phone: str, timeout: float = 60.0) -> dict:
    """
    同步方式通过手机号查询所有系统（先查内部系统拿证件号，再用证件号查全系统）
    """
    from core.query_engine import query_engine
    from core.auth_manager import SystemType
    import time

    try:
        started_at = time.monotonic()
        # 第一步：通过手机号在内部系统找到身份证号
        crawler = query_engine._crawlers.get(SystemType.INTERNAL)
        id_card = crawler.lookup_id_card_by_phone(phone) if crawler else ""

        if not id_card:
            return {
                "name": "", "id_card": "", "phone": phone,
                "license_type": "", "registration_date": "",
                "school_name": "", "school_short": "",
                "student_status": "", "exam_stage": "",
                "exam_counts": {}, "training_hours": {},
                "training_details": [], "fees": [], "timeline_display": [],
                "sources": {"internal": "not_found", "third": "not_found", "driving": "not_found"},
                "error": "手机号在内部系统未查到学员",
            }

        # 第二步：用证件号查三系统（支持特殊格式如 F1249468(8)）
        # 处理特殊证件号前缀补全：如 1249468(8) → F1249468(8)
        id_cards_to_try = [id_card]
        if id_card and len(id_card) >= 7 and not id_card[0].isalpha() and '(' in id_card:
            # 可能是居留证缺了首字母前缀，尝试加 F 前缀
            id_cards_to_try.append("F" + id_card)

        # 先尝试完整证件号查询，失败则逐个尝试
        merged = None
        for try_id in id_cards_to_try:
            remaining = timeout - (time.monotonic() - started_at)
            if remaining <= 0:
                break
            r = query_all_systems_sync(try_id, timeout=remaining)
            merged = r
            if r.get("name"):
                break

        return merged or {
            "name": "", "id_card": id_card, "phone": phone,
            "license_type": "", "registration_date": "",
            "school_name": "", "school_short": "",
            "student_status": "", "exam_stage": "",
            "exam_counts": {}, "training_hours": {},
            "training_details": [], "fees": [], "timeline_display": [],
            "sources": {"internal": "timeout", "third": "timeout", "driving": "timeout"},
            "error": "手机号查询超过总时限",
        }

    except Exception as e:
        return {
            "name": "", "id_card": "", "phone": phone,
            "license_type": "", "registration_date": "",
            "school_name": "", "school_short": "",
            "student_status": "", "exam_stage": "",
            "exam_counts": {}, "training_hours": {},
            "training_details": [], "fees": [], "timeline_display": [],
            "sources": {"internal": "error", "third": "error", "driving": "error"},
            "error": str(e),
        }


def _query_job_worker(job_id: str, data: dict):
    id_card = data.get("id_card", "").strip()
    phone = data.get("phone", "").strip()

    # 投诉摘要与爬虫并行生成：摘要仅供工单存档，与三系统查询无关联，
    # 提前起线程跑，爬虫完成后再汇合（思考关闭后约 2~4s，远短于爬虫耗时）
    summary_holder = {}
    summary_thread = None
    if not str(data.get("complaint_summary") or "").strip() and str(data.get("complaint_desc") or "").strip():
        def _gen_summary():
            out = ai_summarize_complaint(data.get("complaint_desc") or "")
            summary_holder["summary"] = str(out.get("complaint_summary") or "").strip()
            summary_holder["demands"] = str(out.get("complaint_demands") or "").strip()
        summary_thread = Thread(target=_gen_summary, name="intake-summary", daemon=True)
        summary_thread.start()

    def publish(partial):
        partial_result = dict(partial.__dict__)
        with QUERY_JOBS_LOCK:
            QUERY_JOBS[job_id].update({
                "status": "running",
                "result": partial_result,
                "sources": partial_result.get("sources", {}),
                "query_durations_ms": partial_result.get("query_durations_ms", {}),
            })

    with QUERY_JOBS_LOCK:
        QUERY_JOBS[job_id].update({
            "status": "running",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    try:
        started = time.monotonic()
        if id_card:
            merged = asyncio.run(
                query_engine.query_all(id_card, timeout=60, on_update=publish)
            )
            result = dict(merged.__dict__)
        else:
            result = query_all_systems_by_phone(phone)

        # 空壳拦截：姓名与证件号同时为空说明未命中学员档案，不建工单，
        # job 置失败并把文案透传给前端轮询（前端 status.error 直接展示给操作员）。
        # 同时附 no_match/manual_intake_eligible：三系统均「明确查无」（not_found/no_contract）
        # 时前端展示「转人工建案」入口；存在超时/异常时不放行，避免把已录入学员误判为漏录。
        sources = result.get("sources", {}) or {}
        manual_eligible = bool(sources) and all(
            s in {"not_found", "no_contract"} for s in sources.values()
        )
        if not str(result.get("name") or "").strip() and not str(result.get("id_card") or "").strip():
            with QUERY_JOBS_LOCK:
                QUERY_JOBS[job_id].update({
                    "status": "failed",
                    "error": "未匹配到学员档案，请核对手机号或改用身份证号查询",
                    "no_match": True,
                    "manual_intake_eligible": manual_eligible,
                    "sources": sources,
                    "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
            return

        # 等待并行摘要完成（超时则放弃，摘要留空不阻塞建案）
        if summary_thread is not None:
            summary_thread.join(timeout=45)
            s = str(summary_holder.get("summary") or "").strip()
            d = str(summary_holder.get("demands") or "").strip()
            if s:
                data = {**data, "complaint_summary": s}
                result["complaint_summary"] = s
            if d:
                data = {**data, "complaint_demands": d}
                result["complaint_demands"] = d

        result = _persist_query_result(data, result, id_card, phone)
        total_ms = int((time.monotonic() - started) * 1000)
        durations = result.get("query_durations_ms", {}) or {}
        key = phone or id_card
        system_logger.info(
            "[查询耗时] %s -> 总 %dms, 各系统 %s, 阶段 %s, sources %s",
            key, total_ms, durations,
            result.get("system_phase_durations_ms", {}),
            result.get("sources", {}),
        )
        if total_ms > 20000:
            slowest = max(durations.items(), key=lambda kv: kv[1] or 0, default=("", 0))
            add_log(
                "query",
                f"慢查询告警: {key} 总耗时 {total_ms}ms，最慢系统 {slowest[0] or '无'}={slowest[1]}ms",
                ticket_id=result.get("ticket_id"),
            )
        with QUERY_JOBS_LOCK:
            QUERY_JOBS[job_id].update({
                "status": "done",
                "result": result,
                "sources": result.get("sources", {}),
                "query_durations_ms": result.get("query_durations_ms", {}),
                "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
    except Exception as e:
        traceback.print_exc()
        with QUERY_JOBS_LOCK:
            QUERY_JOBS[job_id].update({
                "status": "failed",
                "error": str(e),
                "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })


@app.route("/api/query/start", methods=["POST"])
def api_query_start():
    data = request.get_json(force=True, silent=True)
    if data is None:
        return _err("请求体不是合法JSON", 400)
    id_card = data.get("id_card", "").strip()
    phone = data.get("phone", "").strip()

    if id_card:
        ok, err = validate_id_card(id_card)
        if not ok:
            return _err(err)
    elif not re.fullmatch(r"1[3-9]\d{9}", phone):
        return _err("手机号格式不正确，应为1开头的11位数字")

    _prune_query_jobs()
    job_id = uuid.uuid4().hex
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with QUERY_JOBS_LOCK:
        QUERY_JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "created_at": now,
            "result": None,
            "sources": {
                "internal": "pending",
                "third": "pending",
                "driving": "pending",
            },
            "query_durations_ms": {},
            "error": "",
        }
    future = QUERY_EXECUTOR.submit(_query_job_worker, job_id, data)
    with QUERY_JOBS_LOCK:
        QUERY_JOBS[job_id]["future"] = future
    return jsonify({"success": True, "job_id": job_id, "status": "queued"})


@app.route("/api/query/status/<job_id>", methods=["GET"])
def api_query_status(job_id):
    with QUERY_JOBS_LOCK:
        job = QUERY_JOBS.get(job_id)
        if not job:
            return _err("查询任务不存在", 404)
        public_job = {key: value for key, value in job.items() if key != "future"}
    return jsonify({"success": True, **public_job})


@app.route("/api/students/search", methods=["POST"])
def api_students_search():
    """统一智能查询：按姓名(模糊) + 报名点 + 报名时间范围到内部系统检索候选学员。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        name = (data.get("name") or "").strip()
        school = (data.get("school_short") or "").strip()
        start = (data.get("start_date") or "").strip()
        end = (data.get("end_date") or "").strip()
        try:
            page = max(1, int(data.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        if not name:
            return _err("请提供学员姓名")
        crawler = query_engine._crawlers.get(SystemType.INTERNAL)
        if crawler is None:
            return _err("内部系统爬虫未初始化", 500)
        t0 = time.monotonic()
        result = crawler.search_students(name, school, start, end, page=page, limit=10)
        elapsed = int((time.monotonic() - t0) * 1000)
        students = []
        for info in result["students"]:
            students.append({
                "id_card": info.id_card,
                "student_name": info.name,
                "phone": info.phone,
                "school_short": info.school_short,
                "school_name": info.school_name,
                "license_type": info.license_type,
                "registration_date": info.registration_date,
                "exam_stage": info.exam_stage,
                "student_status": info.student_status,
                "source": "内部系统",
            })
        return _ok({
            "students": students,
            "total": result["total"],
            "org_fallback": result["org_fallback"],
            "elapsed_ms": elapsed,
        })
    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


# ═══════════════════════════════════════════════════════════════
#  API: 投诉工单 CRUD
# ═══════════════════════════════════════════════════════════════

@app.route("/api/school-codes", methods=["GET"])
def api_school_codes():
    """获取所有代号（校区简称）列表，附带标准字典中的名称与类型"""
    try:
        codes = get_distinct_school_short()
        items = []
        for code in codes:
            unit = resolve_org_unit(code, "")
            items.append({
                "code": code,
                "name": (unit or {}).get("name") or code,
                "type": (unit or {}).get("type") or "",
            })
        return _ok(items)
    except Exception as e:
        return _err(str(e))


@app.route("/api/organization-units", methods=["GET"])
def api_organization_units():
    """获取分校/分店标准字典，供未匹配时人工选择。"""
    return _ok(ORGANIZATION_UNITS)

@app.route("/api/tickets/same-day-check", methods=["POST"])
def api_tickets_same_day_check():
    """受理预检：同日同身份证是否已有工单（与 save_ticket 去重口径一致）。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        ticket = find_same_day_ticket(
            str(data.get("id_card") or ""),
            str(data.get("complaint_date") or ""),
        )
        if not ticket:
            return _ok(None)
        return _ok({
            "id": ticket.get("id", ""),
            "student_name": ticket.get("student_name", ""),
            "handle_status": ticket.get("handle_status", ""),
            "source_channel": ticket.get("source_channel", ""),
            "complaint_date": ticket.get("complaint_date", ""),
            "created_at": ticket.get("created_at", ""),
        })
    except Exception as e:
        return _err(str(e))


MANUAL_INTAKE_TYPE = "三系统无信息"


@app.route("/api/tickets/manual-create", methods=["POST"])
def api_tickets_manual_create():
    """三系统无信息学员 · 人工建案。

    学员已报名缴费但未录入三系统：受理时三系统均明确查无记录，由操作员
    手工补齐身份与归属信息后直接建案；后续流程与普通工单一致
    （登记表/回复函照常生成，费用走手工确认或零口径确认）。
    """
    try:
        data = request.get_json(force=True, silent=True)
        if data is None:
            return _err("请求体不是合法JSON", 400)
        data = data or {}

        student_name = str(data.get("student_name") or "").strip()
        id_card = str(data.get("id_card") or "").strip()
        registration_date = str(data.get("registration_date") or "").strip()
        org_id = str(data.get("organization_unit_id") or "").strip()
        phone = str(data.get("phone") or "").strip()

        missing = []
        if not student_name:
            missing.append("学员姓名")
        if not id_card:
            missing.append("身份证号")
        if not org_id:
            missing.append("所属分校/分店")
        if not registration_date:
            missing.append("报名时间")
        if missing:
            return _err("缺少必填项：" + "、".join(missing), 400)

        ok, err = validate_id_card(id_card)
        if not ok:
            return _err(err, 400)

        unit = next((u for u in ORGANIZATION_UNITS if u.get("id") == org_id and u.get("active", True)), None)
        if unit is None:
            return _err("所属分校/分店不在机构字典中，请重新选择", 400)

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", registration_date):
            return _err("报名时间格式应为 YYYY-MM-DD", 400)
        if phone and not re.fullmatch(r"1[3-9]\d{9}", phone):
            return _err("手机号格式不正确，应为1开头的11位数字", 400)

        complaint_date = str(data.get("complaint_date") or "").strip() or datetime.now().strftime("%Y-%m-%d")

        # 同日同人预检（与 save_ticket 去重同口径）：命中即并入既有工单并回传事实
        existing = find_same_day_ticket(id_card, complaint_date)

        ticket_data = {
            "student_name": student_name,
            "id_card": id_card,
            "phone": phone,
            "license_type": str(data.get("license_type") or "").strip(),
            "school_short": unit["code"],
            "organization_unit_id": unit["id"],
            "organization_unit_type": unit["type"],
            "organization_unit_name": unit["name"],
            "organization_unit_code": unit["code"],
            "registration_date": registration_date,
            "intake_type": MANUAL_INTAKE_TYPE,
            "source_channel": str(data.get("source_channel") or "").strip() or "电话来访",
            "complaint_type": str(data.get("complaint_type") or "").strip() or "A",
            "complaint_date": complaint_date,
            "handler_name": str(data.get("handler_name") or "").strip(),
            "complaint_content": str(data.get("complaint_content") or "").strip(),
            "complaint_summary": str(data.get("complaint_summary") or "").strip(),
            "complaint_demands": str(data.get("complaint_demands") or "").strip(),
        }
        ticket_id = save_ticket(ticket_data)

        # 受理建夹 + 投诉内容留档（口径与 PUT /api/tickets/<id> 一致）
        complaint_content = ticket_data["complaint_content"]
        if complaint_content:
            try:
                folder_path = get_archive_folder(student_name, id_card, unit["code"])
                txt_path = os.path.join(folder_path, "投诉内容.txt")
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(f"投诉时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"投诉渠道: {ticket_data['source_channel']}\n")
                    f.write(f"投诉类型: {ticket_data['complaint_type']}\n")
                    f.write(f"数据来源: {MANUAL_INTAKE_TYPE}（人工录入）\n\n")
                    f.write(complaint_content)
            except OSError:
                pass

        add_log(
            "manual_create",
            f"人工建案（{MANUAL_INTAKE_TYPE}）: {student_name} ({id_card}) @ {unit['name']}",
            ticket_id=ticket_id,
        )
        ticket = get_ticket(ticket_id) or {}
        return _ok({
            "ticket_id": ticket_id,
            "ticket_no": ticket.get("ticket_no", ""),
            "intake_type": MANUAL_INTAKE_TYPE,
            "merged_into_existing": bool(existing),
        })
    except Exception as e:
        traceback.print_exc()
        add_log("manual_create", f"人工建案失败: {e}", success=False)
        return _err(str(e), 500)


@app.route("/api/tickets", methods=["GET"])
def api_tickets_list():
    """获取投诉工单列表"""
    try:
        status = request.args.get("status", "")
        source = request.args.get("source", "")
        school = request.args.get("school", "")
        search = request.args.get("search", "")
        start_date = request.args.get("start_date", "")
        end_date = request.args.get("end_date", "")
        repeat_only = request.args.get("repeat_only", "") in ("1", "true", "True")
        try:
            limit = int(request.args.get("limit", 10))
            offset = int(request.args.get("offset", 0))
        except ValueError:
            limit, offset = 10, 0
        # 深分页保护：limit 上限 200，offset 上限 100000，防恶意深翻页拖垮查询
        limit = min(max(limit, 1), 200)
        offset = min(max(offset, 0), 100000)

        records, total = list_tickets(
            status=status, source=source, school=school, search=search,
            start_date=start_date, end_date=end_date, repeat_only=repeat_only,
            limit=limit, offset=offset
        )
        return _ok({"records": records, "total": total})
    except Exception as e:
        return _err(str(e))


@app.route("/api/tickets/<ticket_id>", methods=["GET"])
def api_tickets_get(ticket_id):
    """获取单个工单详情"""
    try:
        ticket = get_ticket(ticket_id)
        if not ticket:
            return _err("工单不存在", 404)
        return _ok(ticket)
    except Exception as e:
        return _err(str(e))


@app.route("/api/tickets/<ticket_id>", methods=["PUT"])
def api_tickets_update(ticket_id):
    """更新工单（支持文件归档）"""
    try:
        data = request.get_json(force=True, silent=True)
        if data is None:
            return _err("请求体不是合法JSON", 400)
        ticket = get_ticket(ticket_id)
        if not ticket:
            return _err("工单不存在", 404)
        if ticket.get("fee_plan_status") == "confirmed" and FEE_PLAN_FIELDS.intersection(data):
            return _err("费用明细已确认锁定；请先调用费用解锁（fee-unlock）再调整")

        if data.get("handle_status") is not None and data["handle_status"] not in {"待处理", "处理中", "已完结"}:
            return _err("处理状态非法", 400)

        if ticket.get("archive_status") == "已归档":
            if data.get("handle_status") is not None:
                return _err("已归档案件请先通过费用解锁（fee-unlock）恢复处理", 400)
            if data.get("archive_status") == "":
                return _err("已归档案件不允许清空归档状态", 400)

        gate_error = _completion_gate_error(ticket, data)
        if gate_error:
            add_log("ticket_update", gate_error, success=False, ticket_id=ticket_id)
            return _err(gate_error)
        
        # 如果有投诉描述，自动生成 txt 文件归档
        complaint_desc = data.get("complaint_desc", "")
        if complaint_desc:
            folder_path = get_archive_folder(
                ticket.get("student_name", ""),
                ticket.get("id_card", ""),
                ticket.get("school_short", "")
            )
            txt_path = os.path.join(folder_path, "投诉内容.txt")
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(f"投诉时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"投诉渠道: {data.get('source_channel', '未知')}\n")
                f.write(f"投诉类型: {data.get('complaint_type', '未知')}\n\n")
                f.write(complaint_desc)
        
        if data.get("archive_status") == "已归档":
            data["handle_status"] = "已完结"
            data["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        elif data.get("handle_status") == "处理中":
            data["processing_started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        elif data.get("handle_status") == "已完结":
            data["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        update_ticket(ticket_id, data)
        add_log("ticket_update", f"更新工单: {ticket_id}", ticket_id=ticket_id)
        return _ok({"id": ticket_id})
    except Exception as e:
        return _err(str(e), 500)


@app.route("/api/tickets/<ticket_id>", methods=["DELETE"])
def api_tickets_delete(ticket_id):
    """删除工单（硬删除数据库记录，不清理磁盘归档文件）"""
    try:
        ticket = get_ticket(ticket_id)
        if not ticket:
            return _err("工单不存在", 404)
        delete_ticket(ticket_id)
        add_log("ticket_delete", f"删除工单: {ticket_id}（{ticket.get('student_name', '')}）", ticket_id=ticket_id)
        return _ok({"id": ticket_id}, "已删除")
    except Exception as e:
        return _err(str(e), 500)


_GRADUATED_STATUSES = {"科四通过", "已结业", "已领证"}
_GRADUATION_WARNING = {
    "type": "no_refund_target_completed",
    "message": "该学员已完成全部培训考试，已无退费标的，请核实投诉诉求后再确认费用方案。",
    "reply_text": "该学员已完成全部培训考试，根据合同约定，此种情况下没有费用退还。",
}


def _merge_graduation_warning(existing, student_status) -> list:
    if str(student_status or "").strip() not in _GRADUATED_STATUSES:
        return existing
    warnings = list(existing or [])
    if any(w.get("type") == _GRADUATION_WARNING["type"] for w in warnings):
        return warnings
    warnings.append(dict(_GRADUATION_WARNING))
    return warnings


@app.route("/api/tickets/<ticket_id>/fee-confirm", methods=["POST"])
def api_ticket_fee_confirm(ticket_id):
    """确认费用方案（v2 简化）：直接保存扣费行集与实缴金额并标记已确认。"""
    try:
        ticket = get_ticket(ticket_id)
        if not ticket:
            return _err("工单不存在", 404)

        data = request.get_json(force=True, silent=True)
        if data is None:
            return _err("请求体不是合法JSON", 400)
        data = data or {}
        no_fee_basis = bool(data.get("no_fee_basis"))
        if no_fee_basis:
            # 三系统查无记录案件的闸门豁免：零口径确认，留痕于 fee_confirm_note
            deductions = []
            total_fee = actual_paid = total_deduction = refund = 0
        else:
            deductions = parse_deductions(data.get("deductions")) or parse_deductions(ticket.get("deduction_detail"))
            if not deductions:
                return _err("请提供扣费明细")

            total_fee = float(data.get("total_fee", ticket.get("total_fee", 0)) or 0)
            actual_paid = float(
                data.get("actual_paid", data.get("paid_amount", ticket.get("actual_paid", 0))) or 0
            )
            if actual_paid <= 0:
                return _err("请填写学员实际已交金额")

            for _d in deductions:
                if not isinstance(_d, dict):
                    return _err("扣费金额必须为不小于0的数值", 400)
                try:
                    _amt = float(_d.get("amount", 0))
                except (TypeError, ValueError):
                    return _err("扣费金额必须为不小于0的数值", 400)
                if _amt < 0:
                    return _err("扣费金额必须为不小于0的数值", 400)
            raw_deduction = round(sum(float(d.get("amount", 0) or 0) for d in deductions), 2)
            total_deduction = min(raw_deduction, total_fee) if total_fee > 0 else raw_deduction
            refund = max(0, round(actual_paid - total_deduction, 2))

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        case_fields = {}
        if ticket.get("archive_status") == "已归档" or ticket.get("handle_status") == "已完结":
            case_fields = _reopen_case_fields()
        elif ticket.get("handle_status") == "待处理":
            case_fields = {"handle_status": "处理中"}

        special_warnings = ticket.get("special_warnings") or []
        if isinstance(special_warnings, str):
            try:
                special_warnings = json.loads(special_warnings) if special_warnings else []
            except json.JSONDecodeError:
                special_warnings = []
        special_warnings = _merge_graduation_warning(special_warnings, ticket.get("student_status"))

        default_note = "三系统查无记录，无费用明细" if no_fee_basis else ""
        update_ticket(ticket_id, {
            "total_fee": total_fee,
            "actual_paid": actual_paid,
            "deduction_fee": total_deduction,
            "refund_fee": refund,
            "deduction_detail": deductions,
            "fee_plan_status": "confirmed",
            "fee_confirmed_by": (str(ticket.get("fee_confirmed_by") or "")
                                 if not data.get("confirmed_by") else str(data.get("confirmed_by")).strip()),
            "fee_confirmed_at": now,
            "fee_confirm_note": str(data.get("confirm_note") or default_note).strip(),
            "special_warnings": special_warnings,
            **case_fields,
        })
        if no_fee_basis:
            add_log("fee_confirm", "查无记录确认：无费用明细（0元口径）", ticket_id=ticket_id)
        else:
            add_log("fee_confirm", f"确认费用明细: 扣费{total_deduction}元，应退{refund}元", ticket_id=ticket_id)
        return _ok({
            "ticket_id": ticket_id,
            "total_fee": total_fee,
            "actual_paid": actual_paid,
            "total_deduction": total_deduction,
            "refund": refund,
            "fee_plan_status": "confirmed",
        })
    except ValueError as e:
        add_log("fee_confirm", f"校验失败: {str(e)}", success=False, ticket_id=ticket_id)
        return _err(str(e), 400)
    except Exception as e:
        traceback.print_exc()
        add_log("fee_confirm", f"异常: {str(e)}", success=False, ticket_id=ticket_id)
        return _err(str(e), 500)


@app.route("/api/tickets/<ticket_id>/fee-unlock", methods=["POST"])
def api_ticket_fee_unlock(ticket_id):
    """解锁已确认的费用明细：confirmed 置回未确认；若案件已归档/已完结则同步重新打开。"""
    ticket = get_ticket(ticket_id)
    if not ticket:
        return _err("工单不存在", 404)
    if ticket.get("fee_plan_status") != "confirmed":
        return _err("只有已确认的费用明细可以解锁")
    reopened = ticket.get("archive_status") == "已归档" or ticket.get("handle_status") == "已完结"
    payload = {
        "fee_plan_status": "draft",
        "reply_outdated": bool(ticket.get("reply_path")),
    }
    if reopened:
        payload.update(_reopen_case_fields())
    update_ticket(ticket_id, payload)
    add_log(
        "fee_unlock",
        "解锁费用明细，允许重新编辑确认" + ("，已同步重新打开案件" if reopened else ""),
        ticket_id=ticket_id,
    )
    return _ok({
        "ticket_id": ticket_id,
        "fee_plan_status": "draft",
        "case_reopened": bool(reopened),
    })


@app.route("/api/tickets/<ticket_id>/withdraw", methods=["PUT"])
def api_ticket_withdraw(ticket_id):
    """撤诉标记（v2）：任意状态可用（含已归档），写入撤诉时间与原因。"""
    try:
        ticket = get_ticket(ticket_id)
        if not ticket:
            return _err("工单不存在", 404)
        data = request.get_json(silent=True) or {}
        reason = str(data.get("reason") or "").strip()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        update_ticket(ticket_id, {
            "withdraw_status": "已撤诉",
            "withdraw_updated_at": now,
            "withdrawn_at": now,
            "withdraw_reason": reason,
        })
        add_log("ticket_withdraw", f"学员撤诉: {reason[:40]}" if reason else "学员撤诉", ticket_id=ticket_id)
        warnings = []
        fresh = get_ticket(ticket_id) or {}
        if str(fresh.get("archive_status") or "") == "已归档":
            warnings = _sync_withdraw_archive(fresh)
        return _ok({
            "ticket_id": ticket_id,
            "withdraw_status": "已撤诉",
            "withdrawn_at": now,
            "withdraw_reason": reason,
            "warnings": warnings,
        })
    except Exception as e:
        return _err(str(e), 500)


@app.route("/api/tickets/<ticket_id>/withdraw-status", methods=["PUT"])
def api_ticket_withdraw_status(ticket_id):
    """更新撤诉状态（归档后仍可单独修改，不影响费用方案与协商结论）。

    撤诉是动态过程：学员今天没撤、明天撤了，不应强制重新打开整个案件。
    withdrawn_at 与 withdraw_status 同步维护，保证统计口径一致。
    """
    try:
        ticket = get_ticket(ticket_id)
        if not ticket:
            return _err("工单不存在", 404)
        data = request.get_json(force=True, silent=True) or {}
        status = str(data.get("withdraw_status") or "").strip()
        if status not in {"已撤诉", "未撤诉"}:
            return _err("撤诉状态只能是 已撤诉 或 未撤诉")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prev_status = str(ticket.get("withdraw_status") or "").strip()
        update_ticket(ticket_id, {
            "withdraw_status": status,
            "withdraw_updated_at": now,
            "withdrawn_at": now if status == "已撤诉" else "",
        })
        warnings = []
        if (
            prev_status == "已撤诉"
            and status == "未撤诉"
            and str(ticket.get("archive_status") or "").strip() == "已归档"
        ):
            fresh = get_ticket(ticket_id)
            warnings = _cleanup_withdraw_archive(fresh or ticket)
        add_log("withdraw_status_update", f"撤诉状态更新为: {status}", ticket_id=ticket_id)
        return _ok({"withdraw_status": status, "withdraw_updated_at": now, "warnings": warnings})
    except Exception as e:
        return _err(str(e), 500)


@app.route("/api/tickets/export", methods=["GET"])
def api_tickets_export():
    """导出工单数据为 Excel"""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from io import BytesIO
        
        # 获取筛选参数
        status = request.args.get("status", "")
        school = request.args.get("school", "")
        date_start = request.args.get("date_start", "")
        date_end = request.args.get("date_end", "")
        # 网点类型范围（与 /api/ticket-statistics 同语义）：branch=仅分校 / store=仅分店
        scope = request.args.get("scope", "")
        if scope and scope not in {"all", "branch", "store"}:
            return _err("scope 只能是 all/branch/store")
        # 批量导出：可选 ids 参数（逗号分隔工单 id），指定时按 id 直查，不受 limit 与其他筛选影响
        ids_raw = request.args.get("ids", "").strip()
        selected_ids = [s.strip() for s in ids_raw.split(",") if s.strip()] if ids_raw else None

        if selected_ids is not None:
            tickets = [get_ticket(tid) for tid in selected_ids]
            tickets = [t for t in tickets if t]
        else:
            # 查询工单列表（不分页，获取全部）
            tickets, _ = list_tickets(
                limit=10000,  # 最多导出1万条
                offset=0,
                status=status,
                school=school,
                start_date=date_start,
                end_date=date_end,
            )
            if scope == "branch":
                tickets = [t for t in tickets if t.get("organization_unit_type") == "分校"]
            elif scope == "store":
                tickets = [t for t in tickets if t.get("organization_unit_type") == "分店"]
        
        # 创建工作簿
        wb = Workbook()
        ws = wb.active
        ws.title = "工单数据"
        
        # 设置表头
        headers = [
            "工单编号", "创建日期", "学员姓名", "身份证", "手机号",
            "驾校", "驾照类型", "当前阶段", "合同金额", "实际已交",
            "违约金比例", "总扣费", "应退金额", "处理状态", "投诉渠道",
            "投诉类型", "扣费明细", "撤诉状态", "归档状态"
        ]
        
        # 写入表头
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = Font(bold=True, size=11)
            cell.fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
            cell.font = Font(bold=True, size=11, color="FFFFFF")
            cell.alignment = Alignment(horizontal="center", vertical="center")
        
        # 写入数据
        for row_idx, ticket in enumerate(tickets, 2):
            # 解析扣费明细
            deductions = parse_deductions(ticket.get("deduction_detail", "[]"))
            deductions_str = "; ".join([f"{d.get('item', '')}:{d.get('amount', 0)}元" for d in deductions]) if deductions else ""

            penalty_rate = 0.2
            for deduction in deductions if isinstance(deductions, list) else []:
                if deduction.get("item") == "违约金" and deduction.get("penalty_rate") is not None:
                    penalty_rate = float(deduction["penalty_rate"])
                    break
            
            row_data = [
                ticket.get("id", ""),
                ticket.get("created_at", ""),
                ticket.get("student_name", ""),
                ticket.get("id_card", ""),
                ticket.get("phone", ""),
                ticket.get("school_short", ""),
                ticket.get("license_type", ""),
                ticket.get("exam_stage", ""),
                ticket.get("total_fee", 0),
                ticket.get("actual_paid", 0),
                f"{penalty_rate * 100}%",
                ticket.get("deduction_fee", 0),
                ticket.get("refund_fee", 0),
                ticket.get("handle_status", "待处理"),
                ticket.get("source_channel", ""),
                ticket.get("complaint_type", ""),
                deductions_str,
                ticket.get("withdraw_status", "") or "",
                ticket.get("archive_status", "") or "",
            ]
            
            for col, value in enumerate(row_data, 1):
                ws.cell(row=row_idx, column=col, value=_excel_safe_value(value))
        
        # 调整列宽
        column_widths = [15, 12, 10, 20, 12, 10, 8, 10, 10, 10, 10, 10, 10, 10, 10, 10, 30, 10, 10]
        for i, width in enumerate(column_widths, 1):
            ws.column_dimensions[chr(64 + i) if i <= 26 else 'A' + chr(64 + i - 26)].width = width
        
        # 保存到内存
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        
        # 生成文件名
        filename = f"工单导出_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        return send_file(
            output,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        traceback.print_exc()
        return _err(f"导出失败: {str(e)}", 500)


# ═══════════════════════════════════════════════════════════════
#  API: 认证管理
# ═══════════════════════════════════════════════════════════════

@app.route("/api/auth/status")
def api_auth_status():
    try:
        status = query_engine.get_auth_status()
        return _ok(status)
    except Exception as e:
        return _err(str(e), 500)


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    try:
        data = request.get_json(force=True, silent=True)
        if data is None:
            return _err("请求体不是合法JSON", 400)
        background = data.get("background", True)

        results = query_engine.login_all(background=background)

        serializable_results = {}
        for system_type, result in results.items():
            serializable_results[system_type.value] = {
                "success": result.success if hasattr(result, 'success') else False,
                "message": result.message if hasattr(result, 'message') else str(result),
            }

        return _ok(serializable_results)
    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


@app.route("/api/auth/clear", methods=["POST"])
def api_auth_clear():
    try:
        query_engine.clear_all_cache()
        return _ok(None, "缓存已清除")
    except Exception as e:
        return _err(str(e), 500)


# ═══════════════════════════════════════════════════════════════
#  API: 合同操作
# ═══════════════════════════════════════════════════════════════

@app.route("/api/contract/download", methods=["POST"])
def api_contract_download():
    try:
        data = request.get_json(force=True, silent=True)
        if data is None:
            return _err("请求体不是合法JSON", 400)
        id_card = data.get("id_card", "").strip()
        student_name = data.get("name", "")
        school_short = data.get("school_short", "未知")
        force = data.get("force", False)  # 是否强制重新下载
        ticket_id = data.get("ticket_id", "").strip()

        if not id_card:
            return _err("缺少身份证号")

        # 2024年3月15日前报名：东莞驾培无电子合同，无需登录查询
        from core.query_engine import _parse_date_str, DRIVING_ECONTRACT_START_DATE
        reg_date = data.get("registration_date", "").strip()
        if not reg_date and ticket_id:
            from database import get_ticket
            ticket = get_ticket(ticket_id)
            if ticket:
                reg_date = ticket.get("registration_date", "") or ""
        parsed_reg = _parse_date_str(reg_date)
        if parsed_reg and parsed_reg < DRIVING_ECONTRACT_START_DATE:
            return _err("该学员2024年3月15日前报名，东莞驾培无电子合同，请上传合同文件或手动填写费用信息")

        # 确定存储路径（归档到学员独立文件夹）
        save_dir = get_archive_folder(student_name, id_card, school_short)

        # 检查缓存（除非强制重新下载）
        if not force:
            from database import contract_cache_get
            cached = contract_cache_get(id_card)
            if cached and os.path.exists(cached["file_path"]):
                add_log("contract_cache", f"使用缓存合同: {cached['student_name']} ({id_card})")
                if ticket_id:
                    _save_contract_to_ticket(ticket_id, cached["file_path"], os.path.basename(cached["file_path"]))
                return _ok({
                    "filepath": cached["file_path"],
                    "filename": os.path.basename(cached["file_path"]),
                    "cached": True,
                    "downloaded_at": cached["downloaded_at"],
                })

        from crawlers.driving import DrivingCrawler
        crawler = DrivingCrawler()
        try:
            filepath = crawler.download_contract(id_card, save_dir, student_name)
        except RuntimeError as e:
            err_msg = str(e)
            if err_msg == "API_QUOTA_EXCEEDED":
                return _err("⚠️ 东莞驾培系统API调用次数已用完，请联系管理员充值后再试")
            elif err_msg == "CONTRACT_PAGE_ERROR":
                return _err("⚠️ 该学员在东莞驾培系统中的电子合同页面异常，请联系东莞驾培人员处理")
            raise

        if filepath:
            filename = os.path.basename(filepath)
            file_size = os.path.getsize(filepath)
            from database import contract_cache_save
            contract_cache_save(id_card, student_name or "未知", filepath, file_size)
            add_log("contract_download", f"下载合同: {filename}")
            if ticket_id:
                _save_contract_to_ticket(ticket_id, filepath, filename)
            return _ok({"filepath": filepath, "filename": filename, "cached": False})
        else:
            return _err("东莞驾培未找到该学员的电子合同。请改用「上传合同分析」导入纸质合同，或点「手动录入费用」录入")

    except Exception as e:
        traceback.print_exc()
        add_log("contract_download", f"异常: {str(e)}", success=False)
        return _err(str(e), 500)


@app.route("/api/contract/upload", methods=["POST"])
def api_contract_upload():
    try:
        # 尝试多种方式获取文件
        files = request.files.getlist("file")
        
        # 如果 getlist 为空，尝试 get 单个文件
        if not files:
            single_file = request.files.get("file")
            if single_file:
                files = [single_file]
        
        # 如果还是为空，尝试遍历所有文件
        if not files:
            all_files = []
            for key in request.files.keys():
                file_list = request.files.getlist(key)
                all_files.extend(file_list)
            files = all_files
        

        
        if not files or len(files) == 0:
            return _err("未选择文件")

        id_card = request.form.get("id_card", "").strip()
        name = request.form.get("name", "").strip()
        school_short = request.form.get("school_short", "").strip()
        ticket_id = request.form.get("ticket_id", "").strip()
        ticket = get_ticket(ticket_id) if ticket_id else None
        if ticket_id and not ticket:
            return _err("工单不存在", 404)
        if ticket:
            id_card = str(ticket.get("id_card") or "").strip()
            name = str(ticket.get("student_name") or "").strip()
            school_short = str(ticket.get("school_short") or "").strip()

        candidates = []
        for file in files:
            if not file.filename:
                continue
            ext = os.path.splitext(file.filename)[1].lower()
            if ext not in CONTRACT_UPLOAD_EXTENSIONS:
                return _err(f"不支持的合同文件格式: {ext or '无扩展名'}")
            candidates.append((file, ext))
        if not candidates:
            return _err("没有可保存的文件")
        if len(candidates) > 1 and not all(ext in CONTRACT_IMAGE_EXTENSIONS for _file, ext in candidates):
            return _err("仅支持一份文档或同一份合同的多页图片；多合同请分别上传")

        saved_files = []
        source_files = []
        image_paths = []  # 有序、按内容去重后的分析副本
        seen_hashes = {}

        for page_index, (file, ext) in enumerate(candidates):
            original_stem = os.path.splitext(os.path.basename(file.filename.replace("\\", "/")))[0]
            safe_orig = _safe_path_component(original_stem, "contract")
            if id_card and name:
                save_dir = get_archive_folder(name, id_card, school_short)
                # 文件名标准化：姓名_合同_序号
                safe_name = _safe_path_component(name, "未知学员")
                filename = f"{safe_name}_合同_{safe_orig}{ext}"
            else:
                filename = f"{safe_orig}{ext}"
                save_dir = UPLOAD_DIR

            os.makedirs(save_dir, exist_ok=True)
            if not is_path_within(save_dir, [ARCHIVE_DIR, UPLOAD_DIR]):
                return _err("合同保存目录不安全", 403)
            filepath = unique_path(os.path.join(save_dir, filename))
            filename = os.path.basename(filepath)
            if not is_path_within(filepath, [ARCHIVE_DIR, UPLOAD_DIR]):
                return _err("合同文件路径不安全", 403)
            file.save(filepath)

            digest = hashlib.sha256()
            with open(filepath, "rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            content_hash = digest.hexdigest()
            duplicate_of = seen_hashes.get(content_hash)

            analysis_path = filepath
            if duplicate_of is not None:
                analysis_path = source_files[duplicate_of]["analysis_path"]
            elif ext in [".jpg", ".jpeg", ".png"]:
                try:
                    from services.image_compressor import compress_image
                    analysis_path = create_derived_copy(filepath, suffix=".analysis")
                    compress_image(analysis_path, analysis_path)
                except Exception as compress_err:
                    system_logger.warning("[COMPRESS WARNING] %s: %s", filename, compress_err)
                image_paths.append(analysis_path)

            source_entry = {
                "filepath": filepath,
                "filename": filename,
                "analysis_path": analysis_path,
                "page_index": page_index,
                "sha256": content_hash,
            }
            if duplicate_of is not None:
                source_entry["duplicate_of"] = duplicate_of
            else:
                seen_hashes[content_hash] = len(source_files)
            source_files.append(source_entry)
            saved_files.append({"filepath": filepath, "filename": filename})

        if not saved_files:
            return _err("没有可保存的文件（格式可能不支持）")

        # ── 核心逻辑：如果有多张图片，自动合并成 PDF ──
        merged_pdf_path = None
        if len(image_paths) > 1:
            try:
                import img2pdf
                safe_name = _safe_path_component(name, "上传")
                pdf_filename = f"{safe_name}_合同_合并版.pdf"
                merged_pdf_path = unique_path(os.path.join(save_dir, pdf_filename))

                # 先压缩图片再合并，减小 PDF 体积
                from services.image_compressor import compress_for_vision_api
                try:
                    compressed_for_pdf = compress_for_vision_api(
                        image_paths, 
                        max_size=(1920, 1920),
                        jpeg_quality=75,  # PDF 用稍低质量，体积更小
                        target_max_mb=1.5
                    )
                except Exception:
                    compressed_for_pdf = image_paths
                
                with open(merged_pdf_path, "wb") as f:
                    # img2pdf.convert 接收文件路径列表
                    f.write(img2pdf.convert(compressed_for_pdf))
                
                # 清理临时压缩文件
                for temp_path in compressed_for_pdf:
                    if ".compressed" in temp_path and os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except:
                            pass
                
                add_log("contract_merge", f"已将 {len(image_paths)} 张图片合并为 PDF")
            except Exception as merge_err:
                if merged_pdf_path and os.path.exists(merged_pdf_path):
                    try:
                        os.remove(merged_pdf_path)
                    except OSError:
                        pass
                merged_pdf_path = None
                traceback.print_exc()
                add_log("contract_merge_err", f"PDF 合并失败: {str(merge_err)}", success=False)
                # 合并失败不影响原始图片上传，继续执行

        upload_count = len(source_files)
        preview_path = merged_pdf_path or source_files[0]["filepath"]
        preview_filename = os.path.basename(preview_path)
        manifest = {
            "source_files": source_files,
            "merged_pdf_path": merged_pdf_path or "",
            "analysis_image_paths": image_paths,
            "upload_count": upload_count,
        }

        if ticket_id and get_ticket(ticket_id):
            update_ticket(ticket_id, {
                "contract_path": preview_path,
                "contract_manifest": manifest,
            })

        add_log("contract_upload", f"上传 {upload_count} 个文件", ticket_id=ticket_id)

        return _ok({
            "filepath": preview_path,
            "filename": preview_filename,
            "image_paths": image_paths,
            "all_files": saved_files,
            "count": upload_count,
            "manifest": manifest,
        })

    except RequestEntityTooLarge:
        return _err("上传文件超过大小限制（50MB）", 413)
    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


@app.route("/api/contract/analyze", methods=["POST"])
def api_contract_analyze():
    try:
        data = request.get_json(force=True, silent=True)
        if data is None:
            return _err("请求体不是合法JSON", 400)
        _filepath, validation_error = _validate_contract_analysis_request(data)
        if validation_error:
            return _err(validation_error, _contract_validation_status(validation_error))
        result = _run_contract_analysis(data)
        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        add_log("contract_analyze", f"异常: {str(e)}", success=False)
        return _err(f"分析异常: {str(e)}", 500)


def _contract_validation_status(error: str) -> int:
    if error == "工单不存在":
        return 404
    if "无权" in error or "上传记录" in error:
        return 403
    return 400


def _ticket_contract_paths(ticket: dict) -> set[str]:
    manifest = ticket.get("contract_manifest") or {}
    if isinstance(manifest, str):
        try:
            manifest = json.loads(manifest)
        except json.JSONDecodeError:
            manifest = {}
    paths = {ticket.get("contract_path", ""), manifest.get("merged_pdf_path", "")}
    paths.update(manifest.get("analysis_image_paths") or [])
    for item in manifest.get("source_files") or []:
        if isinstance(item, dict):
            paths.add(item.get("filepath", ""))
            paths.add(item.get("analysis_path", ""))
    return {os.path.realpath(path) for path in paths if path}


def _associate_contract_to_ticket(ticket_id: str, filepath: str, image_paths: list[str]) -> None:
    """将尚未关联的合同文件惰性写入工单记录（下载流程 / 先于工单上传场景）。

    顶层校验已确认 filepath 与 image_paths 均存在、在允许目录内且格式合法，
    此处仅做合并写入，使后续分析、归档可追溯到该文件。
    """
    try:
        ticket = get_ticket(ticket_id)
        if not ticket:
            return
        if os.path.realpath(filepath) in _ticket_contract_paths(ticket):
            return  # 已关联，避免重复写
        manifest = ticket.get("contract_manifest") or {}
        if isinstance(manifest, str):
            try:
                manifest = json.loads(manifest)
            except json.JSONDecodeError:
                manifest = {}
        if not isinstance(manifest, dict):
            manifest = {}

        source_files = list(manifest.get("source_files") or [])
        source_files.append({
            "filepath": filepath,
            "filename": os.path.basename(filepath),
            "analysis_path": filepath,
            "page_index": len(source_files),
        })
        merged = manifest.get("merged_pdf_path") or ""
        if not merged and os.path.splitext(filepath)[1].lower() == ".pdf":
            merged = filepath
        analysis_images = list(manifest.get("analysis_image_paths") or [])
        for p in image_paths:
            if p and p not in analysis_images:
                analysis_images.append(p)

        update_ticket(ticket_id, {
            "contract_path": filepath,
            "contract_manifest": {
                "source_files": source_files,
                "merged_pdf_path": merged,
                "analysis_image_paths": analysis_images,
            },
        })
    except Exception as e:
        add_log("contract_associate", f"惰性关联工单失败: {e}", success=False)


def _save_contract_to_ticket(ticket_id: str, filepath: str, filename: str) -> None:
    """把下载/缓存命中的合同写入工单记录（下载流程专用，覆盖式写入单一来源）。"""
    try:
        if not ticket_id:
            return
        ticket = get_ticket(ticket_id)
        if not ticket:
            return
        if os.path.realpath(filepath) in _ticket_contract_paths(ticket):
            return
        manifest = {
            "source_files": [{
                "filepath": filepath,
                "filename": filename,
                "analysis_path": filepath,
                "page_index": 0,
            }],
            "merged_pdf_path": filepath if os.path.splitext(filepath)[1].lower() == ".pdf" else "",
            "analysis_image_paths": [],
        }
        update_ticket(ticket_id, {"contract_path": filepath, "contract_manifest": manifest})
    except Exception as e:
        add_log("contract_save", f"写入工单失败: {e}", success=False)


def _validate_contract_analysis_request(data: dict) -> tuple[str, str]:
    filepath = str(data.get("filepath") or "").strip()
    allowed_dirs = [
        os.path.abspath(ARCHIVE_DIR),
        os.path.abspath(UPLOAD_DIR),
        os.path.abspath(REPLY_DIR),
    ]
    if not is_path_within(filepath, allowed_dirs):
        return "", "无权访问该文件"
    if not filepath or not os.path.exists(filepath):
        return "", "合同文件不存在"
    if not os.path.isfile(filepath):
        return "", "合同路径不是文件"
    if os.path.splitext(filepath)[1].lower() not in CONTRACT_UPLOAD_EXTENSIONS:
        return "", "不支持的合同文件格式"

    image_paths = data.get("image_paths") or []
    if not isinstance(image_paths, list):
        return "", "图片路径格式错误"
    for image_path in image_paths:
        if not isinstance(image_path, str) or not image_path.strip():
            return "", "图片路径格式错误"
        if not is_path_within(image_path, allowed_dirs):
            return "", "无权访问该图片"
        if not os.path.exists(image_path) or not os.path.isfile(image_path):
            return "", "合同图片不存在"
        if os.path.splitext(image_path)[1].lower() not in CONTRACT_IMAGE_EXTENSIONS:
            return "", "不支持的合同图片格式"

    ticket_id = str(data.get("ticket_id") or "").strip()
    if ticket_id:
        ticket = get_ticket(ticket_id)
        if not ticket:
            return "", "工单不存在"
        persisted_paths = _ticket_contract_paths(ticket)
        requested_paths = [filepath, *image_paths]
        if not all(os.path.realpath(p) in persisted_paths for p in requested_paths):
            # 合同合法但未关联到工单（如下载流程、或先于工单上传）。
            # 顶层校验已确认所有路径存在且在允许目录内，这里做惰性关联后放行，
            # 避免「分析文件与该工单的合同上传记录不一致」误报。
            _associate_contract_to_ticket(ticket_id, filepath, image_paths)
    return filepath, ""


def _run_contract_analysis(data: dict) -> dict:
    filepath, err = _validate_contract_analysis_request(data)
    ticket_id = data.get("ticket_id", "")
    if err:
        add_log("contract_analyze", err, success=False, ticket_id=ticket_id)
        return {"error": err}

    exam_stage = data.get("exam_stage", "")
    training_hours = data.get("training_hours", {})
    total_fee = float(data.get("total_fee", 0) or 0)
    exam_counts = data.get("exam_counts", {})

    registration_date = ""
    skill_cert_date = ""
    contract_fee = 0
    if ticket_id:
        ticket = get_ticket(ticket_id)
        if ticket:
            registration_date = ticket.get("registration_date", "")
            try:
                qr = ticket.get("query_result", {})
                if isinstance(qr, str):
                    qr = json.loads(qr)
                skill_cert_date = qr.get("skill_cert_date", "")
                driving_fee = qr.get("driving_fee", {}) if isinstance(qr, dict) else {}
                if isinstance(driving_fee, dict):
                    contract_fee = float(driving_fee.get("contract_fee", 0) or 0)
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

    result = analyze_contract_from_file(
        filepath=filepath,
        exam_stage=exam_stage,
        training_hours=training_hours,
        total_fee=total_fee,
        image_paths=data.get("image_paths", []),
        exam_counts=exam_counts,
        registration_date=registration_date,
        skill_cert_date=skill_cert_date,
    )

    if result.get("error"):
        add_log("contract_analyze", f"分析失败: {result['error']}", success=False, ticket_id=ticket_id)
        return result

    # 东莞驾培 contract_fee 为权威合同金额，覆盖 AI/规则结果（AI 仅作校验对比）
    if contract_fee > 0:
        result = apply_authoritative_total_fee(result, contract_fee)

    add_log("contract_analyze", f"分析完成: 应退{result.get('refund', 0)}元", ticket_id=ticket_id)
    if ticket_id:
        current_ticket = get_ticket(ticket_id)
        if current_ticket and current_ticket.get("fee_plan_status") == "confirmed":
            update_ticket(ticket_id, {"contract_path": filepath})
        else:
            update_ticket(ticket_id, {
                "total_fee": result.get("total_fee", 0),
                "actual_paid": result.get("actual_paid", 0),
                "deduction_fee": result.get("total_deduction", 0),
                "refund_fee": result.get("refund", 0),
                "deduction_detail": result.get("deductions", []),
                "contract_set": result.get("contract_set", {}),
                "contract_code": result.get("contract_code", ""),
                "contract_path": filepath,
                "special_warnings": result.get("special_warnings", []),
                "fee_plan_status": result.get("fee_plan_status", "draft"),
            })
    else:
        id_card = data.get("id_card", "")
        if id_card:
            existing = get_complaint_by_idcard(id_card)
            if existing:
                save_complaint({
                    "id": existing["id"],
                    "registration_fee": result.get("total_fee", 0),
                    "actual_paid": result.get("actual_paid", 0),
                    "deduction_fee": result.get("total_deduction", 0),
                    "refund_fee": result.get("refund", 0),
                    "deduction_detail": result.get("deductions", []),
                    "contract_code": result.get("contract_code", ""),
                    "contract_path": filepath,
                    "special_warnings": result.get("special_warnings", []),
                })
    return result


def _contract_analysis_worker(job_id: str, data: dict):
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with CONTRACT_ANALYSIS_LOCK:
        CONTRACT_ANALYSIS_JOBS[job_id]["status"] = "running"
        CONTRACT_ANALYSIS_JOBS[job_id]["started_at"] = started_at
    update_contract_analysis_job(job_id, status="running", started_at=started_at)
    try:
        result = _run_contract_analysis(data)
        status = "failed" if result.get("error") else "done"
        finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with CONTRACT_ANALYSIS_LOCK:
            CONTRACT_ANALYSIS_JOBS[job_id].update({
                "status": status,
                "result": result,
                "error": result.get("error", ""),
                "finished_at": finished_at,
            })
        update_contract_analysis_job(
            job_id,
            status=status,
            result=result,
            error=result.get("error", ""),
            finished_at=finished_at,
        )
    except Exception as e:
        traceback.print_exc()
        finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with CONTRACT_ANALYSIS_LOCK:
            CONTRACT_ANALYSIS_JOBS[job_id].update({
                "status": "failed",
                "error": str(e),
                "finished_at": finished_at,
            })
        update_contract_analysis_job(
            job_id,
            status="failed",
            error=str(e),
            finished_at=finished_at,
        )


def _contract_analysis_fingerprint(data: dict, filepath: str) -> str:
    paths = [filepath]
    paths.extend(data.get("image_paths") or [])
    file_facts = []
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        stat = os.stat(path)
        file_facts.append((os.path.realpath(path), stat.st_size, stat.st_mtime_ns))
    raw = json.dumps({
        "ticket_id": data.get("ticket_id", ""),
        "files": sorted(file_facts),
    }, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@app.route("/api/contract/analyze/start", methods=["POST"])
def api_contract_analyze_start():
    data = request.get_json(force=True, silent=True)
    if data is None:
        return _err("请求体不是合法JSON", 400)
    filepath, err = _validate_contract_analysis_request(data)
    if err:
        return _err(err, _contract_validation_status(err))

    fingerprint = _contract_analysis_fingerprint(data, filepath)
    existing = find_active_contract_analysis_job(fingerprint)
    if existing:
        return jsonify({
            "success": True,
            "job_id": existing["id"],
            "status": existing["status"],
            "deduplicated": True,
        })

    _prune_contract_analysis_jobs()
    job_id = uuid.uuid4().hex
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    create_contract_analysis_job({
        "id": job_id,
        "ticket_id": data.get("ticket_id", ""),
        "fingerprint": fingerprint,
        "filepath": filepath,
        "status": "queued",
        "created_at": now,
    })
    with CONTRACT_ANALYSIS_LOCK:
        CONTRACT_ANALYSIS_JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "filepath": filepath,
            "ticket_id": data.get("ticket_id", ""),
            "created_at": now,
            "result": None,
            "error": "",
        }
    future = CONTRACT_ANALYSIS_EXECUTOR.submit(_contract_analysis_worker, job_id, data)
    with CONTRACT_ANALYSIS_LOCK:
        CONTRACT_ANALYSIS_JOBS[job_id]["future"] = future
    return jsonify({"success": True, "job_id": job_id, "status": "queued", "deduplicated": False})


@app.route("/api/contract/analyze/status/<job_id>", methods=["GET"])
def api_contract_analyze_status(job_id):
    with CONTRACT_ANALYSIS_LOCK:
        job = CONTRACT_ANALYSIS_JOBS.get(job_id)
        future = job.get("future") if job else None

    if not job:
        persisted_job = get_contract_analysis_job(job_id)
        if not persisted_job:
            return _err("分析任务不存在", 404)
        return jsonify({"success": True, **persisted_job})

    if future and not future.done():
        try:
            future.result(timeout=0.02)
        except TimeoutError:
            pass

    with CONTRACT_ANALYSIS_LOCK:
        public_job = {k: v for k, v in CONTRACT_ANALYSIS_JOBS[job_id].items() if k != "future"}
        return jsonify({"success": True, **public_job})


# ═══════════════════════════════════════════════════════════════
#  API: 回复函生成 + 模板管理
# ═══════════════════════════════════════════════════════════════

@app.route("/api/reply/generate", methods=["POST"])
def api_reply_generate():
    try:
        data = request.get_json(force=True, silent=True)
        if data is None:
            return _err("请求体不是合法JSON", 400)
        ticket_id = data.get("ticket_id", "")
        ticket, err, status = _confirmed_ticket_or_error(ticket_id)
        if err:
            add_log("reply_generate", err, success=False, ticket_id=ticket_id)
            return _err(err, status)
        official = _official_case_payload(ticket, data)

        with _reply_generate_lock(str(ticket_id)):
            return _do_generate_reply(ticket, official, data)
    except Exception as e:
        traceback.print_exc()
        add_log("reply_generate", f"异常: {str(e)}", success=False)
        return _err(str(e), 500)


def _do_generate_reply(ticket: dict, official: dict, data: dict):
    try:
        ticket_id = str(official.get("ticket_id") or "")

        # 确定回复函保存路径（文件命名规则保持旧版不变）
        reply_name = official.get("name", "")
        reply_id_card = official.get("id_card", "")
        reply_school = official.get("school_short", "")
        if reply_name and reply_id_card:
            reply_dir = get_archive_folder(reply_name, reply_id_card, reply_school)
        else:
            reply_dir = REPLY_DIR

        # 金额一律取数据库已确认快照，不信任请求体
        deductions = official.get("deductions", []) or []

        mismatch = False
        req_rows = data.get("deductions")
        if isinstance(req_rows, list) and req_rows:
            req_sum = round(sum(_deduction_row_amount(d) for d in req_rows), 2)
            snap_sum = round(sum(_deduction_row_amount(d) for d in deductions), 2)
            if abs(req_sum - snap_sum) > 0.009:
                mismatch = True
        for req_key, snap_key in (("refund", "refund"), ("total", "total_fee"), ("total_fee", "total_fee")):
            if data.get(req_key) is not None:
                try:
                    if abs(float(data[req_key]) - float(official.get(snap_key, 0) or 0)) > 0.009:
                        mismatch = True
                except (TypeError, ValueError):
                    pass

        today_str = datetime.now().strftime("%Y%m%d")
        base_filename = f"{today_str}{reply_name}{reply_id_card}投诉回复函{reply_school}"
        output_path = unique_path(os.path.join(reply_dir, base_filename + ".docx"))

        result = generate_reply_docx(ticket=ticket, deductions=deductions, output_path=output_path)
        if result.get("success"):
            filepath = result["path"]
            add_log("reply_generate", f"生成回复函(v2): {filepath}", ticket_id=ticket_id)
            if ticket_id:
                update_ticket(ticket_id, {
                    "reply_path": filepath,
                    "reply_outdated": False,
                })
            elif data.get("id_card"):
                existing = get_complaint_by_idcard(data["id_card"])
                if existing:
                    save_complaint({
                        "id": existing["id"],
                        "reply_path": filepath,
                    })
            resp = {
                "success": True,
                "filename": os.path.basename(filepath),
                "filepath": filepath,
            }
            if mismatch:
                resp["warning"] = "以系统确认的费用明细为准"
            return jsonify(resp)
        else:
            add_log("reply_generate", f"生成失败: {result.get('error')}", success=False, ticket_id=ticket_id)
            return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        add_log("reply_generate", f"异常: {str(e)}", success=False)
        return _err(str(e), 500)


@app.route("/api/reply/download")
def api_reply_download():
    try:
        filepath = request.args.get("path", "")
        if not filepath or not os.path.exists(filepath):
            return _err("文件不存在", 404)

        allowed_reply = [
            os.path.abspath(REPLY_DIR),
            os.path.abspath(ARCHIVE_DIR),
            os.path.abspath(UPLOAD_DIR),
        ]
        if not is_path_within(filepath, allowed_reply):
            return _err("无权访问", 403)

        return send_file(filepath, as_attachment=True, download_name=os.path.basename(filepath))
    except Exception as e:
        return _err(str(e))


@app.route("/api/fs/browse")
def api_fs_browse():
    """目录浏览（归档路径选择弹框用）：列出指定目录下的子文件夹。

    path 为空时返回用户主目录；仅返回文件夹，跳过 . 开头的隐藏目录。
    本系统为本机内部工具，归档本身即允许用户指定任意根路径，此处仅提供同等的只读浏览能力。
    """
    try:
        raw = (request.args.get("path") or "").strip()
        path = os.path.abspath(os.path.expanduser(raw)) if raw else os.path.expanduser("~")
        if not os.path.exists(path):
            return _err("目录不存在", 404)
        if not os.path.isdir(path):
            return _err("该路径不是文件夹", 400)
        dirs = []
        warning = ""
        try:
            entries = sorted(os.listdir(path), key=str.lower)
        except PermissionError:
            entries = []
            warning = "无权限读取该目录内容"
        for name in entries:
            if name.startswith("."):
                continue
            if os.path.isdir(os.path.join(path, name)):
                dirs.append(name)
        parent = os.path.dirname(path)
        return jsonify({
            "success": True,
            "data": {"path": path, "parent": parent if parent != path else "", "dirs": dirs, "warning": warning},
        })
    except Exception as e:
        return _err(str(e))


@app.route("/api/tickets/<ticket_id>/archive", methods=["POST"])
def api_tickets_archive(ticket_id):
    """D10 最终归档（权威实现）：三闸门校验 → 建夹 → 复制两件套 → 更新状态。

    与 get_archive_folder（受理建夹，沿用 {日期}_{姓名}_{身份证}_{校区} 规则）分工不同：
    本路由按 archive_service.build_archive_dir 的
    {root}/{单位类型}/{代号-单位名}/{日期}_{姓名}_{身份证}_{代号}/ 规则拼路径，
    仅作为结案归档入口，不负责受理阶段建夹。两者路径规则差异保留，不顺手统一。
    """
    try:
        ticket = get_ticket(ticket_id)
        if not ticket:
            return _err("工单不存在", 404)

        # 归档三闸门：处理情况 + 配合度 + 费用明细确认
        errors = archive_gate_errors(ticket)
        if errors:
            return jsonify({"success": False, "errors": errors}), 400

        # 归档根目录：优先取请求体（用户在界面填写/修改），并持久化为上次使用路径
        body = request.get_json(force=True, silent=True) or {}
        cfg = load_config()
        root = str(body.get("archive_root") or "").strip()
        if root:
            if root != cfg.get("archive_root", ""):
                cfg["archive_root"] = root
                save_config(cfg)
        else:
            root = cfg.get("archive_root", "案件归档")
        case_dir, reg_target, reply_target = build_archive_dir(ticket, root=root)

        warnings = []
        reg_src = ticket.get("registration_form_path") or ""
        updated_at_raw = str(ticket.get("updated_at") or "").strip()
        try:
            updated_ts = datetime.strptime(updated_at_raw, "%Y-%m-%d %H:%M:%S").timestamp() if updated_at_raw else 0
        except ValueError:
            updated_ts = 0
        src_missing = not reg_src or not os.path.isfile(reg_src)
        stale = src_missing or (updated_ts > 0 and os.path.getmtime(reg_src) < updated_ts)
        if stale and str(ticket.get("handling_notes") or "").strip():
            gen = _regen_registration_form(ticket)
            if gen.get("success"):
                reg_src = gen["filepath"]
                update_ticket(ticket_id, {"registration_form_path": reg_src})
            else:
                warnings.append(f"登记表重新生成失败，沿用旧文件: {gen.get('error')}")

        # 收集已生成的源文件：登记表（受理流程写盘 registration_form_path）、
        # 回复函（/api/reply/generate 写盘 reply_path），存在才复制
        files = {}
        if reg_src and os.path.isfile(reg_src):
            files["register_form"] = reg_src
        reply_src = ticket.get("reply_path") or ""
        if reply_src and os.path.isfile(reply_src):
            files["reply"] = reply_src

        if not files:
            return jsonify({"success": False, "errors": ["无可归档文件：请先生成登记表或回复函"]}), 400

        result = archive_case(ticket, files, open_folder=False)
        if not result.get("success"):
            return jsonify(result), 400

        update_ticket(ticket_id, {
            "archive_status": "已归档",
            "handle_status": "已完结",
            "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "archived_dir": result["dir"],
        })

        add_log("archive", f"归档案件: {case_dir}", ticket_id=ticket_id)
        resp = {
            "success": True,
            "dir": result["dir"],
            "files": result["files"],
            "opened": bool(result.get("opened")),
        }
        if warnings:
            resp["warning"] = "；".join(warnings)
        return jsonify(resp)
    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


@app.route("/api/templates", methods=["GET"])
def api_templates_list():
    """列出回复模板"""
    try:
        return _ok(list_templates())
    except Exception as e:
        return _err(str(e))


@app.route("/api/templates/upload", methods=["POST"])
def api_templates_upload():
    """上传回复模板"""
    try:
        if "file" not in request.files:
            return _err("未选择文件")

        file = request.files["file"]
        if not file.filename:
            return _err("文件名为空")

        from services.template_service import upload_template
        name = request.form.get("name", file.filename)
        description = request.form.get("description", "")

        ext = os.path.splitext(file.filename)[1].lower()
        if ext != ".docx":
            return _err("仅支持 .docx 模板文件")

        template_dir = os.path.join(PROJECT_DIR, "回复模板")
        os.makedirs(template_dir, exist_ok=True)
        # 使用稳定的 uuid 文件名保存上传流，避免 secure_filename 剥离中文名，且不留孤儿文件
        tmp_path = os.path.join(template_dir, f"{uuid.uuid4().hex}{ext}")
        file.save(tmp_path)
        try:
            result = upload_template(tmp_path, name, description)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        add_log("template_upload", f"上传模板: {name}")
        return _ok(result)

    except Exception as e:
        return _err(str(e), 500)


@app.route("/api/templates/<template_id>/default", methods=["PUT"])
def api_templates_set_default(template_id):
    """设置默认模板"""
    from services.template_service import set_default_template
    if set_default_template(template_id):
        add_log("template_default", f"设置默认模板: {template_id}")
        return _ok(None, "已设置为默认模板")
    return _err("模板不存在", 404)


@app.route("/api/templates/<template_id>", methods=["DELETE"])
def api_templates_delete(template_id):
    """删除模板（同时清理磁盘文件；删除最后一个默认模板时给出警告）"""
    from database import get_db, get_default_template
    tmpl_path = None
    was_default = False
    with get_db() as conn:
        row = conn.execute(
            "SELECT template_path, is_default FROM reply_templates WHERE id=?",
            (template_id,),
        ).fetchone()
        if row:
            tmpl_path = row["template_path"]
            was_default = bool(row["is_default"])
    if delete_template(template_id):
        if tmpl_path and os.path.exists(tmpl_path):
            try:
                os.remove(tmpl_path)
            except OSError:
                pass
        add_log("template_delete", f"删除模板: {template_id}")
        if was_default and not get_default_template():
            return _ok(
                {"warning": "已删除最后一个默认模板，当前无默认模板，生成回复函时请先指定默认模板"},
                "已删除",
            )
        return _ok(None, "已删除")
    return _err("模板不存在", 404)


# ═══════════════════════════════════════════════════════════════
#  API: 回访管理 + 文档生成
# ═══════════════════════════════════════════════════════════════

@app.route("/api/tickets/<ticket_id>/register-form", methods=["POST"])
def api_tickets_register_form(ticket_id):
    """生成投诉登记表"""
    try:
        ticket = get_ticket(ticket_id)
        if not ticket:
            return _err("工单不存在", 404)

        # 页面未保存的编辑（处理情况/姓名补录）优先于库内值，保证生成即预览所见；覆盖值落库保持 DB=docx 一致
        body = request.get_json(force=True, silent=True) or {}
        ticket = dict(ticket)
        updates = {}
        notes = str(body.get("handling_notes", "")).strip()
        if notes:
            ticket["handling_notes"] = notes
            updates["handling_notes"] = notes
        name = str(body.get("student_name", "")).strip()
        if name:
            ticket["student_name"] = name
            updates["student_name"] = name
        if updates:
            update_ticket(ticket_id, updates)

        from services.visit_service import generate_registration_form
        ticket, ai_sections = _polish_registration_ticket(ticket)
        result = generate_registration_form(
            ticket,
            handling_notes=ticket.get("handling_notes", "") or "",
            final_outcome=ticket.get("final_outcome", ""),
            negotiation_outcome=ticket.get("negotiation_outcome", ""),
            withdraw_status=ticket.get("withdraw_status", ""),
            branch_cooperation=ticket.get("branch_cooperation", ""),
            total_fee=float(ticket.get("total_fee", 0) or 0),
            refund=float(ticket.get("refund_fee", 0) or 0),
            deductions=parse_deductions(ticket.get("deduction_detail")),
            special_warnings=json.loads(ticket.get("special_warnings", "[]")) if isinstance(ticket.get("special_warnings"), str) else ticket.get("special_warnings", []),
            exam_stage=ticket.get("exam_stage", ""),
            ai_sections=ai_sections,
        )

        if result.get("success"):
            update_ticket(ticket_id, {"registration_form_path": result["filepath"]})
            add_log("form_generate", f"生成登记表: {result['filename']}", ticket_id=ticket_id)
            return _ok(result)
        else:
            add_log("form_generate", f"生成失败: {result.get('error')}", success=False, ticket_id=ticket_id)
            return _err(result.get("error"))

    except Exception as e:
        traceback.print_exc()
        add_log("form_generate", f"异常: {str(e)}", success=False)
        return _err(str(e), 500)


@app.route("/api/tickets/<ticket_id>/register-form/preview", methods=["POST"])
def api_tickets_register_form_preview(ticket_id):
    """预览投诉登记表内容（不落盘、不调用 AI，与归档时生成的 docx 共用同一数据构建逻辑）"""
    try:
        ticket = get_ticket(ticket_id)
        if not ticket:
            return _err("工单不存在", 404)

        # 页面未保存的编辑（处理情况/姓名补录）优先于库内值，保证预览即所得
        body = request.get_json(force=True, silent=True) or {}
        ticket = dict(ticket)
        notes = str(body.get("handling_notes", "")).strip()
        if notes:
            ticket["handling_notes"] = notes
        name = str(body.get("student_name", "")).strip()
        if name:
            ticket["student_name"] = name

        from services.visit_service import build_registration_form_data
        data = build_registration_form_data(
            ticket,
            handling_notes=ticket.get("handling_notes", "") or "",
            final_outcome=ticket.get("final_outcome", ""),
            negotiation_outcome=ticket.get("negotiation_outcome", ""),
            total_fee=float(ticket.get("total_fee", 0) or 0),
            refund=float(ticket.get("refund_fee", 0) or 0),
            deductions=parse_deductions(ticket.get("deduction_detail")),
            ai_sections={},
        )
        return _ok(data)

    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


@app.route("/api/tickets/<ticket_id>/detail")
def api_ticket_detail(ticket_id):
    from database import get_ticket
    ticket = get_ticket(ticket_id)
    if not ticket:
        return _err("工单不存在", 404)

    # Parse deductions from JSON string
    deductions = parse_deductions(ticket.get("deduction_detail"))

    communication_records = []

    # Build document list
    documents = []
    if ticket.get("reply_path"):
        documents.append({"type": "reply", "filename": os.path.basename(ticket["reply_path"]), "filepath": ticket["reply_path"]})
    if ticket.get("registration_form_path"):
        documents.append({"type": "form", "filename": os.path.basename(ticket["registration_form_path"]), "filepath": ticket["registration_form_path"]})

    return jsonify({
        "success": True,
            "data": {
            "ticket": ticket,
            "deductions": deductions,
            "communication_records": communication_records,
            "documents": documents,
        }
    })


@app.route("/api/contract/analysis/<ticket_id>")
def api_get_analysis(ticket_id):
    from database import get_ticket
    ticket = get_ticket(ticket_id)
    if not ticket:
        return _err("工单不存在", 404)

    # Check current ticket
    if ticket.get("deduction_detail") and ticket["deduction_detail"] != "[]":
        try:
            data = json.loads(ticket["deduction_detail"]) if isinstance(ticket["deduction_detail"], str) else ticket["deduction_detail"]
            return jsonify({
                "success": True,
                "cached": True,
                "from_history": False,
                "data": data
            })
        except:
            pass

    # Check historical tickets with same ID card
    if ticket.get("id_card"):
        from database import get_db
        with get_db() as conn:
            cursor = conn.execute(
                "SELECT id, deduction_detail, deduction_fee, refund_fee, total_fee FROM complaint_tickets WHERE id_card=? AND id!=? AND deduction_detail IS NOT NULL AND deduction_detail != '[]' AND deduction_detail != '' ORDER BY created_at DESC LIMIT 1",
                (ticket["id_card"], ticket_id)
            )
            row = cursor.fetchone()
            if row:
                row = dict(row)
                try:
                    data = json.loads(row["deduction_detail"]) if isinstance(row["deduction_detail"], str) else row["deduction_detail"]
                    return jsonify({
                        "success": True,
                        "cached": True,
                        "from_history": True,
                        "history_ticket_id": row["id"],
                        "data": data
                    })
                except:
                    pass

    return jsonify({"success": True, "cached": False})


_COMPARISON_ITEM_LABELS = [
    ("total_fee", "合同总额"),
    ("service_fee", "综合服务费"),
    ("theory_fee", "理论培训费"),
    ("subject2_fee", "科目二实操培训费"),
    ("subject2_unit", "科目二学时单价(元/学时)"),
    ("subject3_fee", "科目三实操培训费"),
    ("subject3_unit", "科目三学时单价(元/学时)"),
]

_BREAKDOWN_KEYS = (
    "service_fee", "theory_fee", "subject2_fee", "subject2_unit",
    "subject3_fee", "subject3_unit", "subject2_retrain", "subject3_retrain", "pickup_fee",
)


def _round2(val):
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return 0.0


def _find_deduction_item(deductions, keywords):
    for d in deductions:
        name = str(d.get("item", ""))
        if any(k in name for k in keywords):
            return d
    return None


def _parse_unit_price(raw):
    m = re.search(r"(\d+(?:\.\d+)?)", str(raw or ""))
    return round(float(m.group(1)), 2) if m else None


def _empty_platform() -> dict:
    return {
        "contract_fee": 0, "pay_fee": 0, "supervise_fee": 0,
        "residue_supervise_amt": 0, "supervise_date": "",
        "breakdown": {k: 0 for k in _BREAKDOWN_KEYS},
        "orders": [],
    }


def _build_contract_comparison(ticket: dict, ticket_id: str) -> dict:
    query_result = ticket.get("query_result") if isinstance(ticket.get("query_result"), dict) else {}
    driving_fee = query_result.get("driving_fee")
    platform_available = isinstance(driving_fee, dict) and bool(driving_fee)
    if not platform_available:
        platform = _empty_platform()
    else:
        raw_breakdown = driving_fee.get("breakdown") if isinstance(driving_fee.get("breakdown"), dict) else {}
        platform = {
            "contract_fee": _round2(driving_fee.get("contract_fee")),
            "pay_fee": _round2(driving_fee.get("pay_fee")),
            "supervise_fee": _round2(driving_fee.get("supervise_fee")),
            "residue_supervise_amt": _round2(driving_fee.get("residue_supervise_amt")),
            "supervise_date": driving_fee.get("supervise_date") or "",
            "breakdown": {k: _round2(raw_breakdown.get(k)) for k in _BREAKDOWN_KEYS},
            "orders": driving_fee.get("orders") if isinstance(driving_fee.get("orders"), list) else [],
        }

    deductions = ticket.get("deduction_detail") if isinstance(ticket.get("deduction_detail"), list) else []

    def contract_value(key):
        if key == "total_fee":
            return _round2(ticket.get("total_fee")) if ticket.get("total_fee") else None
        if key == "service_fee":
            d = _find_deduction_item(deductions, ("综合服务费", "服务费"))
        elif key == "theory_fee":
            d = _find_deduction_item(deductions, ("理论培训费", "理论费"))
        elif key.startswith("subject2"):
            d = _find_deduction_item(deductions, ("科目二",))
        else:
            d = _find_deduction_item(deductions, ("科目三",))
        if not d:
            return None
        if key.endswith("_unit"):
            return _parse_unit_price(d.get("unit_price"))
        val = d.get("max_amount") or d.get("amount")
        return _round2(val) if val else None

    items = []
    for key, label in _COMPARISON_ITEM_LABELS:
        p_val = platform["contract_fee"] if key == "total_fee" else platform["breakdown"].get(key)
        c_val = contract_value(key)
        if c_val is None:
            status = "platform_only"
        elif not p_val:
            status = "contract_only"
        elif abs(p_val - c_val) < 0.01:
            status = "match"
        else:
            status = "diff"
        items.append({
            "key": key, "label": label,
            "platform": _round2(p_val), "contract": _round2(c_val),
            "status": status,
        })

    platform_extra = []
    for key, label in (("subject2_retrain", "科目二补训费"), ("subject3_retrain", "科目三补训费"), ("pickup_fee", "接送费")):
        amount = platform["breakdown"].get(key, 0)
        if amount > 0:
            platform_extra.append({"key": key, "label": label, "amount": amount})

    contract_text = ticket.get("contract_text")
    if isinstance(contract_text, str):
        try:
            contract_text = json.loads(contract_text)
        except json.JSONDecodeError:
            contract_text = {}
    if not isinstance(contract_text, dict):
        contract_text = {}
    clauses = contract_text.get("clauses") or []
    text_error = contract_text.get("error", "")
    if not contract_text and ticket.get("contract_path"):
        result = build_contract_clauses(ticket["contract_path"])
        clauses = result.get("clauses") or []
        text_error = result.get("error", "")
        update_ticket(ticket_id, {"contract_text": json.dumps({"clauses": clauses, "error": text_error}, ensure_ascii=False)})

    return {
        "ticket_id": ticket_id,
        "contract_code": ticket.get("contract_code") or "",
        "platform_available": platform_available,
        "platform": platform,
        "items": items,
        "platform_extra": platform_extra,
        "deductions": deductions,
        "clauses": clauses,
        "text_available": bool(clauses),
        "text_error": text_error,
        "profile": _build_contract_profile(ticket, clauses),
    }


def _extract_signing_date(text: str) -> str:
    """从合同文本中提取签订日期，支持多种格式（包括PDF提取时带空格的格式）。"""
    # 先尝试匹配标准格式：日期：2026年07月07日 或 日期：2 0 2 5 年05月 03日（PDF空格）
    # 匹配 digit(带可选空格)年 digit(带可选空格)月 digit(带可选空格)日
    m = re.search(r"(?:签订|合同)?(?:日期|时间)[:：]?\s*((?:\d\s*){4}年\s*(?:\d\s*){1,2}月\s*(?:\d\s*){1,2}日)", text)
    if m:
        raw = m.group(1).strip()
        # 清理多余空格并规范化
        date_str = re.sub(r"\s+", "", raw)  # 移除所有空格: "2 0 2 5 年05月 03日" -> "2025年05月03日"
        # 尝试解析并格式化
        dm = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", date_str)
        if dm:
            y, mo, d = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
            return f"{y}年{mo:02d}月{d:02d}日"
        return date_str
    # 再尝试匹配带横杠的格式：2026-07-07
    m2 = re.search(r"(?:签订|合同)?(?:日期|时间)[:：]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})", text)
    if m2:
        return m2.group(1).strip()
    # 最后尝试匹配纯数字格式：20260707
    m3 = re.search(r"(?:签订|合同)?(?:日期|时间)[:：]?\s*(\d{8})", text)
    if m3:
        raw = m3.group(1)
        return f"{raw[:4]}年{raw[4:6]}月{raw[6:8]}日"
    return ""


def _extract_contract_term(text: str, signing_date: str) -> str:
    """提取合同期限整句：跨行合并 PDF 换行断句；若原文只写到“至 YYYY 年”（月日被换行截断），
    结合签订日期 + 有效期年数推导精确到期日；中文日期统一零填充显示。"""
    m = re.search(r"(本培训服务合同有效期[\s\S]{2,60}?)(?:止|。)", text) or \
        re.search(r"(?:合同期限|有效期)[:：]?\s*([\s\S]{4,80}?)(?:止|。)", text)
    if not m:
        m2 = re.search(r"(?:合同期限|有效期)[:：]?\s*([^\n]{4,40})", text)
        return re.sub(r"\s+", "", m2.group(1)) if m2 else ""
    # 合并跨行并压缩 PDF/换行产生的空白
    term = re.sub(r"\s+", "", m.group(1))
    # 原文只写到年份（如“…有效期为3年，自签订之日起至2029年”）：推导精确到期日
    year_only = re.search(r"至(\d{4})年$", term)
    years_m = re.search(r"有效期[为是]?(\d+)年", term)
    sign_m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", signing_date or "")
    if year_only and years_m and sign_m:
        y, mo, d = int(sign_m.group(1)), int(sign_m.group(2)), int(sign_m.group(3))
        try:
            end = date(y + int(years_m.group(1)), mo, d)
            term = term[:year_only.start()] + f"至{end.year}年{end.month:02d}月{end.day:02d}日"
        except ValueError:
            pass  # 2月29日等边界日期推导失败时保留原文
    # 中文日期统一零填充（2029年5月3日 → 2029年05月03日）
    term = re.sub(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日",
        lambda mm: f"{mm.group(1)}年{int(mm.group(2)):02d}月{int(mm.group(3)):02d}日",
        term,
    )
    return term


def _build_contract_profile(ticket: dict, clauses: list) -> dict:
    """合同关键信息（从合同原文提取）。提取不到的留空。"""
    preamble = next((c.get("body", "") for c in clauses if not c.get("no")), "")
    full_text = preamble + "\n" + "\n".join(
        (c.get("title", "") + "\n" + c.get("body", "")) for c in clauses
    )

    def _rx(pattern):
        m = re.search(pattern, full_text)
        return m.group(1).strip() if m else ""

    signing = _extract_signing_date(full_text)
    if not signing and ticket.get("registration_date"):
        # 合同原文无签署日期时，回退用报名日期（东莞驾培电子合同通常同日签署）
        dm = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", str(ticket["registration_date"]).strip())
        if dm:
            signing = f"{dm.group(1)}年{int(dm.group(2)):02d}月{int(dm.group(3)):02d}日"

    return {
        "contract": [
            {"label": "合同编号", "value": ticket.get("contract_code") or _rx(r"合同(?:编号|编码)[:：]?\s*([A-Z0-9]+)")},
            {"label": "甲方（学驾人）", "value": ticket.get("student_name") or _rx(r"甲\s*方[^名\n]*姓\s*名[:：]\s*(\S+)")},
            {"label": "乙方（驾培机构）", "value": _rx(r"驾培机构[:：]\s*(\S+)")},
            {"label": "培训车型", "value": _rx(r"培训车型[:：]?\s*([A-Z][12])") or str(ticket.get("license_type") or "")},
            {"label": "合同期限", "value": _extract_contract_term(full_text, signing)},
            {"label": "签订日期", "value": signing},
        ],
    }


@app.route("/api/contract/comparison/<ticket_id>")
def api_contract_comparison(ticket_id):
    from database import get_ticket
    ticket = get_ticket(ticket_id)
    if not ticket:
        return _err("工单不存在", 404)
    return _ok(_build_contract_comparison(ticket, ticket_id))


@app.route("/api/contract/comparison/<ticket_id>/refresh", methods=["POST"])
def api_contract_comparison_refresh(ticket_id):
    from database import get_ticket
    ticket = get_ticket(ticket_id)
    if not ticket:
        return _err("工单不存在", 404)
    id_card = (ticket.get("id_card") or "").strip()
    if not id_card:
        return jsonify({"success": False, "error": "工单缺少身份证号，无法查询驾培平台"})

    from crawlers.driving import DrivingCrawler
    try:
        info = DrivingCrawler().query_student(id_card, include_contract_check=False)
    except Exception as e:
        system_logger.warning("[合同对照] 驾培平台查询失败 %s: %s", ticket_id, e)
        return jsonify({"success": False, "error": str(e)})
    if not info:
        return jsonify({"success": False, "error": "驾培平台未查到该学员"})

    driving_fee = {
        "contract_fee": float(info.contract_fee or 0),
        "pay_fee": float(info.pay_fee or 0),
        "supervise_fee": float(info.supervise_fee or 0),
        "residue_supervise_amt": float(info.residue_supervise_amt or 0),
        "supervise_date": info.supervise_date or "",
        "breakdown": info.fee_breakdown or {},
        "orders": info.pay_orders or [],
    }
    query_result = dict(ticket.get("query_result")) if isinstance(ticket.get("query_result"), dict) else {}
    query_result["driving_fee"] = driving_fee
    update_ticket(ticket_id, {"query_result": query_result})
    ticket["query_result"] = query_result

    return _ok(_build_contract_comparison(ticket, ticket_id))


@app.route("/api/contract/save_analysis", methods=["POST"])
def api_save_analysis():
    data = request.get_json(force=True, silent=True)
    if data is None:
        return _err("请求体不是合法JSON", 400)
    ticket_id = data.get("ticket_id")
    analysis_data = data.get("analysis_data", {})
    if not ticket_id:
        return _err("缺少工单ID", 400)

    if not isinstance(analysis_data, dict) or not analysis_data:
        return _err("缺少有效的分析结果数据", 400)

    ticket = get_ticket(ticket_id)
    if not ticket:
        return _err("工单不存在", 404)
    if ticket.get("fee_plan_status") == "confirmed" or ticket.get("archive_status") == "已归档":
        return _err("费用明细已确认锁定；请先调用费用解锁（fee-unlock）再调整")

    def _positive_number(val, name):
        try:
            num = float(val)
        except (TypeError, ValueError):
            return None, f"{name}必须是数值"
        if num < 0:
            return None, f"{name}不能为负数"
        return num, None

    for field, label in (("total_fee", "total_fee"), ("actual_paid", "actual_paid"),
                         ("total_deduction", "total_deduction"), ("refund", "refund")):
        val, err = _positive_number(analysis_data.get(field, 0), label)
        if err:
            return _err(err, 400)

    deduction_detail = analysis_data.get("deduction_detail")
    if deduction_detail is not None:
        details = deduction_detail if isinstance(deduction_detail, list) else []
        if isinstance(deduction_detail, str):
            try:
                details = json.loads(deduction_detail)
            except json.JSONDecodeError:
                details = []
        if isinstance(details, list):
            for _d in details:
                if isinstance(_d, dict):
                    try:
                        _amt = float(_d.get("amount", 0))
                    except (TypeError, ValueError):
                        return _err("扣费明细金额必须为数值", 400)
                    if _amt < 0:
                        return _err("扣费明细金额不能为负数", 400)

    from database import update_ticket
    update_ticket(ticket_id, {
        "total_fee": analysis_data.get("total_fee", 0),
        "actual_paid": analysis_data.get("actual_paid", analysis_data.get("paid_amount", 0)),
        "deduction_fee": analysis_data.get("total_deduction", 0),
        "refund_fee": analysis_data.get("refund", 0),
        "deduction_detail": json.dumps(analysis_data, ensure_ascii=False),
        "contract_code": analysis_data.get("contract_code", ""),
        "special_warnings": analysis_data.get("special_warnings", []),
        "fee_plan_status": "draft",
    })
    return jsonify({"success": True})


# ═══════════════════════════════════════════════════════════════
#  API: 配置管理
# ═══════════════════════════════════════════════════════════════

@app.route("/api/config", methods=["GET"])
def api_config_get():
    cfg = load_config()
    safe = json.loads(json.dumps(cfg))

    for section in ("internal_system", "third_system", "driving_system"):
        pwd = safe.get(section, {}).get("password", "")
        if pwd:
            safe[section]["password"] = pwd[:3] + "••••"

    return jsonify(safe)


@app.route("/api/config", methods=["PUT", "POST"])
def api_config_put():
    try:
        new_config = request.get_json(force=True, silent=True)
        if new_config is None:
            return _err("请求体不是合法JSON", 400)
        current = load_config()

        for section in ("internal_system", "third_system", "driving_system"):
            pwd = new_config.get(section, {}).get("password", "")
            if pwd and ("•" in pwd or "..." in pwd):
                new_config[section]["password"] = current.get(section, {}).get("password", "")

        for section in ("llm", "llm_intake", "llm_contract_vision", "llm_contract_text"):
            llm_cfg = new_config.get(section, {})
            if not isinstance(llm_cfg, dict):
                continue
            llm_cfg["api_url"] = normalize_llm_api_url(llm_cfg.get("api_url", ""))
            new_config[section] = llm_cfg

        save_config(new_config)

        # 重新初始化爬虫实例（不改动 query_engine 全局引用）
        from crawlers.internal import InternalCrawler
        from crawlers.third import ThirdCrawler
        from crawlers.driving import DrivingCrawler
        query_engine._crawlers.clear()
        query_engine._init_crawlers()

        add_log("settings_update", "更新系统配置")
        return _ok(None, "配置已保存")

    except Exception as e:
        return _err(str(e), 500)


@app.route("/api/config/llm/test", methods=["POST"])
def api_config_llm_test():
    """测试大模型连通性：用当前表单参数发一次最小 chat 请求。"""
    try:
        body = request.get_json(force=True, silent=True) or {}
        import requests as _requests
        api_url = normalize_llm_api_url((body.get("api_url") or "").strip())
        api_key = (body.get("api_key") or "").strip()
        model = (body.get("model") or "").strip()
        if not api_url or not api_key or not model:
            return _err("请先填写 API 地址、模型和 API Key", 400)

        started = time.time()
        resp = _requests.post(
            api_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "你好，请回复：连接成功"}],
                "max_tokens": 32,
                "temperature": 0,
            },
            timeout=30,
        )
        latency_ms = int((time.time() - started) * 1000)
        if resp.status_code != 200:
            detail = ""
            try:
                err = resp.json().get("error", {})
                detail = err.get("message", "") if isinstance(err, dict) else str(err)
            except Exception:
                detail = resp.text[:200]
            return _err(f"HTTP {resp.status_code}：{detail or '请求失败'}", 400)
        reply = ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content", "")
        return _ok({"latency_ms": latency_ms, "reply": (reply or "").strip()[:100]}, "连接成功")
    except Exception as e:
        return _err(f"连接失败：{e}", 400)


# ═══════════════════════════════════════════════════════════════
#  API: 处理情况 AI 优化
# ═══════════════════════════════════════════════════════════════

NOTES_POLISH_PROMPT = """你是驾校投诉处理专员。工作人员用随手关键词记录了与学员的沟通处理情况，请将其整理成通顺、有条理的正式表述。
要求：
1. 只能使用原文提供的信息，严禁编造或补充原文没有的姓名、金额、日期、承诺等事实
2. 保留全部关键信息：联系对象、沟通内容、协商结果、后续安排
3. 按逻辑分条陈述（如「1）……；2）……」），语言正式、规范、简洁
4. 只输出整理后的文字，不要任何解释、前缀或后缀

处理情况原文：
"""


@app.route("/api/notes/polish", methods=["POST"])
def api_notes_polish():
    """处理情况 AI 优化：把输入框中用户记录的内容整理为正式表述（忠实原文，不编造）。"""
    try:
        body = request.get_json(force=True, silent=True) or {}
        text = (body.get("text") or "").strip()
        if not text:
            return _err("请先在处理情况输入框记录沟通内容", 400)

        import requests as _requests
        llm = _llm_config("llm")
        if not llm.get("api_url") or not llm.get("api_key") or not llm.get("model"):
            return _err("未配置大模型接口，请在系统设置中填写后重试", 400)
        resp = _requests.post(
            llm["api_url"],
            headers={"Authorization": f"Bearer {llm['api_key']}", "Content-Type": "application/json"},
            json={
                "model": llm["model"],
                "messages": [{"role": "user", "content": NOTES_POLISH_PROMPT + text}],
                "max_tokens": 1024,
                "temperature": 0.3,
                # qwen3 系列文字润色场景需要先"想清楚"才能识别错别字（如"鞋套"→"协调"）、补全上下文；
                # 关闭思考会让模型走短路径直接复述，无法处理拼音错别字与口语化输入
                "enable_thinking": True,
            },
            timeout=60,
        )
        if resp.status_code != 200:
            return _err(_format_llm_error(resp), 400)
        polished = ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content", "")
        polished = (polished or "").strip()
        if not polished:
            return _err("AI 未返回有效内容，请重试", 400)
        return _ok({"polished": polished})
    except Exception as e:
        return _err(f"AI 优化失败：{e}", 500)


# ═══════════════════════════════════════════════════════════════
#  API: 历史记录 + 统计
# ═══════════════════════════════════════════════════════════════

@app.route("/api/complaints")
def api_complaints_list():
    """获取投诉记录列表（兼容旧接口）"""
    status = request.args.get("status", "")
    search = request.args.get("search", "")
    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    # 深分页保护：与 /api/tickets 一致
    limit = min(max(limit, 1), 200)
    offset = min(max(offset, 0), 100000)

    records, total = list_complaints(status=status, limit=limit, offset=offset, search=search)
    return jsonify({"records": records, "total": total})


@app.route("/api/statistics")
def api_statistics():
    return jsonify(get_statistics())


@app.route("/api/ticket-statistics")
def api_ticket_statistics():
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    scope = request.args.get("scope", "all")
    unit_code = request.args.get("unit_code", "")
    if scope not in {"all", "branch", "store"}:
        return _err("scope 只能是 all/branch/store")
    return _ok(get_ticket_statistics(
        start_date=start_date,
        end_date=end_date,
        scope=scope,
        unit_code=unit_code,
    ))


@app.route("/api/org-vehicle-counts", methods=["GET"])
def api_org_vehicle_counts():
    """获取所有网点的车辆数配置"""
    items = get_org_vehicle_count_items()
    return _ok({"items": items, "total_vehicles": sum(item["vehicle_count"] for item in items)})


@app.route("/api/org-vehicle-counts", methods=["PUT"])
def api_org_vehicle_counts_save():
    """批量保存网点车辆数配置"""
    data = request.get_json(force=True, silent=True)
    if data is None:
        return _err("请求体不是合法JSON", 400)
    items = data.get("items", [])
    if not isinstance(items, list):
        return _err("items 必须是数组", 400)
    if not items:
        return _err("items 不能为空：本接口为全量替换，空提交会清空全部网点车辆数配置", 400)
    validated = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            return _err(f"第 {idx + 1} 条车辆数配置格式错误", 400)
        code = str(item.get("unit_code") or "").strip()
        if not code:
            return _err(f"第 {idx + 1} 条车辆数配置缺少 unit_code", 400)
        raw_count = item.get("vehicle_count")
        if isinstance(raw_count, bool) or not isinstance(raw_count, int):
            return _err(f"第 {idx + 1} 条车辆数（{raw_count}）必须是非负整数", 400)
        count = raw_count
        if count < 0:
            return _err(f"第 {idx + 1} 条车辆数不能为负数", 400)
        validated.append({
            "unit_code": code,
            "unit_name": item.get("unit_name", ""),
            "unit_type": item.get("unit_type", ""),
            "vehicle_count": count,
        })
    saved = save_org_vehicle_counts(validated)
    return _ok({"saved": saved})


@app.route("/api/statistics/duration")
def api_duration_stats():
    """处理时长统计"""
    date_start = request.args.get("start_date", "") or request.args.get("date_start", "")
    date_end = request.args.get("end_date", "") or request.args.get("date_end", "")
    unit_code = request.args.get("unit_code", "")
    try:
        stats = get_processing_duration_stats(date_start, date_end, unit_code)
        return jsonify({"success": True, "data": stats})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ═══════════════════════════════════════════════════════════════
#  API: 文件下载
# ═══════════════════════════════════════════════════════════════

@app.route("/api/contract/download-file")
def api_contract_download_file():
    filepath = request.args.get("path", "")
    if not filepath or not os.path.exists(filepath):
        return _err("文件不存在", 404)

    allowed_dirs = [os.path.abspath(ARCHIVE_DIR), os.path.abspath(UPLOAD_DIR), os.path.abspath(REPLY_DIR)]
    if not is_path_within(filepath, allowed_dirs):
        return _err("无权访问", 403)

    return send_file(filepath, as_attachment=True, download_name=os.path.basename(filepath))


@app.route("/api/contract/preview")
def api_contract_preview():
    filepath = request.args.get("path", "")
    if not filepath:
        return _err("缺少文件路径", 400)

    from config import load_config
    cfg = load_config()
    ARCHIVE_DIR = os.path.abspath(cfg["paths"]["reply_dir"])
    UPLOAD_DIR = os.path.abspath(cfg["paths"]["upload_dir"])
    CONTRACT_DIR = os.path.abspath(cfg["paths"]["contract_dir"])
    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
    ARCHIVE_FOLDER = os.path.join(PROJECT_ROOT, "案件归档")

    abs_path = os.path.realpath(filepath)
    allowed_dirs = [ARCHIVE_DIR, UPLOAD_DIR, CONTRACT_DIR, ARCHIVE_FOLDER]
    if not is_path_within(abs_path, allowed_dirs):
        return jsonify({"success": False, "error": "禁止访问"}), 403

    if not os.path.exists(abs_path):
        return jsonify({"success": False, "error": "文件不存在"}), 404

    mimetype, _ = mimetypes.guess_type(abs_path)
    if not mimetype:
        mimetype = "application/octet-stream"

    return send_file(abs_path, mimetype=mimetype, as_attachment=False)


# ═══════════════════════════════════════════════════════════════
#  模块初始化
# ═══════════════════════════════════════════════════════════════

# 创建默认回复模板
from services.template_service import create_default_template
try:
    create_default_template()
except Exception as e:
    system_logger.error("[初始化] 创建默认模板: %s", e)


# ═══════════════════════════════════════════════════════════════
#  启动
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    _start_background_services()
    print("=" * 50)
    print("  驾校投诉处理系统")
    print(f"  访问地址: http://127.0.0.1:5003")
    print(f"  项目目录: {PROJECT_DIR}")
    print("=" * 50)
    app.run(host="127.0.0.1", port=5003)
