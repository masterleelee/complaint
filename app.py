"""驾校投诉处理系统 - Flask 后端"""
import os
import sys
import json
import traceback
import certifi
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename

# 修复 macOS Python 3.14 SSL 证书问题，确保 OCR 库能下载模型
os.environ['SSL_CERT_FILE'] = certifi.where()

# 确保项目根目录在路径中
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config import load_config, save_config, BASE_DIR as PROJECT_DIR
from database import (
    save_ticket, get_ticket, get_ticket_by_idcard, get_latest_ticket_by_idcard,
    list_tickets, update_ticket, get_ticket_statistics,
    save_complaint, get_complaint_by_idcard,
    list_complaints, get_statistics,
    add_log, get_recent_logs,
    save_template, list_templates, get_default_template, delete_template,
    get_distinct_school_short,
)
from core.query_engine import query_engine, query_all_systems_sync
from core.auth_manager import auth_manager, SystemType
from services.contract_service import analyze_contract_from_file
from services.reply_service import generate_reply
from services.feishu_service import FeishuService
from services.intake_service import parse_complaint_file, ensure_upload_dir

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB
app.config["TEMPLATES_AUTO_RELOAD"] = True  # 禁用模板缓存

UPLOAD_DIR = os.path.join(PROJECT_DIR, "uploads")
ARCHIVE_DIR = os.path.join(PROJECT_DIR, "案件归档")
REPLY_DIR = os.path.join(PROJECT_DIR, "回复函")

for d in (UPLOAD_DIR, ARCHIVE_DIR, REPLY_DIR):
    os.makedirs(d, exist_ok=True)


def get_archive_folder(name: str, id_card: str, school_short: str) -> str:
    """获取或创建学员的归档文件夹（优先复用已有文件夹）"""
    # 先查找是否已存在该学员的文件夹（按身份证号匹配）
    if os.path.exists(ARCHIVE_DIR):
        for folder_name in os.listdir(ARCHIVE_DIR):
            if id_card in folder_name:
                return os.path.join(ARCHIVE_DIR, folder_name)
    
    # 不存在则创建新文件夹
    date_str = datetime.now().strftime("%Y%m%d")
    # 路径安全加固：仅替换明确的危险字符，保留中文
    import re
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', name or "未知学员")
    safe_school = re.sub(r'[\\/:*?"<>|]', '_', school_short or "未知校区")
    folder_name = f"{date_str}_{safe_name}_{id_card}_{safe_school}"
    folder_path = os.path.join(ARCHIVE_DIR, folder_name)
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
                print(f"[预登录] {system_type}: 爬虫未初始化，跳过")
                return

            # 检查配置完整性（避免空配置浪费线程）
            cfg = load_config().get(f"{system_type}_system", {})
            if not cfg.get("username") or not cfg.get("password") or cfg.get("password") == "":
                print(f"[预登录] {system_type}: 配置不完整，跳过")
                return

            result = crawler.login()
            print(f"[预登录] {system_type}: {'成功' if result.success else '失败'} - {result.message}")
        except Exception as e:
            print(f"[预登录] {system_type}: 异常 - {e}")

    systems = ["internal", "third", "driving"]
    for system in systems:
        t = threading.Thread(target=_login_system, args=(system,), daemon=True)
        t.start()
    print("[预登录] 已启动后台登录线程")


background_login()


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
    """上传投诉工单文件，LLM 提取关键信息"""
    try:
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

    except Exception as e:
        traceback.print_exc()
        add_log("intake_parse", f"受理失败: {e}", success=False)
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
    其他证件（居留证、港澳台等）不验证，直接放行
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
    # 其他证件不验证，直接放行
    if len(id_card) >= 7:
        return True, ""
    return False, "证件号至少7位"


@app.route("/api/query", methods=["POST"])
def api_query():
    """查询三系统学员信息（支持身份证/居留证优先，手机号备选）"""
    try:
        data = request.get_json(force=True)
        id_card = data.get("id_card", "").strip()
        phone = data.get("phone", "").strip()

        # 证件号校验（18位身份证验真伪，其他证件不验）
        if id_card:
            ok, err = validate_id_card(id_card)
            if not ok:
                return _err(err)

        # 执行查询
        if id_card and len(id_card) >= 7:
            result = query_all_systems_sync(id_card)
        elif phone and len(phone) == 11:
            result = query_all_systems_by_phone(phone)
        else:
            return _err("请输入有效的证件号（至少7位）或手机号(11位)")

        # 保存到 complaint_tickets
        if result.get("name"):
            # 如果 school_short 为空，从 school_name 提取括号内容作为代号
            school_name = result.get("school_name", "")
            school_short = result.get("school_short", "")
            if not school_short and school_name:
                import re
                match = re.search(r'\(([^)]+)\)', school_name)
                if match:
                    school_short = match.group(1)
            
            ticket_id = data.get("ticket_id", "")
            complaint_date = data.get("complaint_date", datetime.now().strftime("%Y-%m-%d"))

            # 同人同日去重：如果今天已有该学员的工单，复用旧工单ID
            if not ticket_id:
                from database import get_ticket_by_idcard
                today_tickets = get_ticket_by_idcard(id_card)
                for t in today_tickets:
                    if t.get("complaint_date") == complaint_date:
                        ticket_id = t["id"]
                        break

            ticket_data = {
                "id_card": id_card or result.get("id_card", ""),
                "student_name": result.get("name", ""),
                "phone": result.get("phone", ""),
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
                "complaint_date": complaint_date,
            }
            if ticket_id:
                ticket_data["id"] = ticket_id
            save_ticket(ticket_data)
            add_log("query", f"查询学员: {result['name']} ({id_card or phone})", ticket_id=ticket_id)

        # 确保返回的 result 包含正确的 school_short（从 school_name 提取）
        if result.get("name"):
            school_name = result.get("school_name", "")
            school_short = result.get("school_short", "")
            if not school_short and school_name:
                import re
                match = re.search(r'\(([^)]+)\)', school_name)
                if match:
                    result["school_short"] = match.group(1)

        return jsonify(result)

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
    from core.query_engine import query_engine, QueryStatus, MergedStudentInfo
    import asyncio
    from core.auth_manager import SystemType
    from concurrent.futures import ThreadPoolExecutor

    try:
        # 第一步：通过手机号在内部系统找到身份证号
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            crawler = query_engine._crawlers.get(SystemType.INTERNAL)
            info = loop.run_in_executor(
                ThreadPoolExecutor(max_workers=1),
                lambda: crawler.query_by_phone(phone) if crawler else None
            )
            result = loop.run_until_complete(info)
        finally:
            loop.close()

        if not result:
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
        id_card = result.id_card
        
        # 处理特殊证件号前缀补全：如 1249468(8) → F1249468(8)
        id_cards_to_try = [id_card]
        if id_card and len(id_card) >= 7 and not id_card[0].isalpha() and '(' in id_card:
            # 可能是居留证缺了首字母前缀，尝试加 F 前缀
            id_cards_to_try.append("F" + id_card)
        
        # 先尝试完整证件号查询，失败则逐个尝试
        merged = None
        for try_id in id_cards_to_try:
            r = query_all_systems_sync(try_id)
            if r.get("name"):
                merged = r
                break
        
        if not merged:
            merged = query_all_systems_sync(id_cards_to_try[0])
        
        return merged

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


# ═══════════════════════════════════════════════════════════════
#  API: 投诉工单 CRUD
# ═══════════════════════════════════════════════════════════════

@app.route("/api/school-codes", methods=["GET"])
def api_school_codes():
    """获取所有代号（校区简称）列表"""
    codes = get_distinct_school_short()
    return _ok(codes)

@app.route("/api/tickets", methods=["GET"])
def api_tickets_list():
    """获取投诉工单列表"""
    status = request.args.get("status", "")
    source = request.args.get("source", "")
    school = request.args.get("school", "")
    search = request.args.get("search", "")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    limit = int(request.args.get("limit", 10))
    offset = int(request.args.get("offset", 0))

    records, total = list_tickets(
        status=status, source=source, school=school, search=search,
        start_date=start_date, end_date=end_date, limit=limit, offset=offset
    )
    return _ok({"records": records, "total": total})


@app.route("/api/tickets/<ticket_id>", methods=["GET"])
def api_tickets_get(ticket_id):
    """获取单个工单详情"""
    ticket = get_ticket(ticket_id)
    if not ticket:
        return _err("工单不存在", 404)
    return _ok(ticket)


@app.route("/api/tickets/<ticket_id>", methods=["PUT"])
def api_tickets_update(ticket_id):
    """更新工单（支持文件归档）"""
    try:
        data = request.get_json(force=True)
        
        # 如果有投诉描述，自动生成 txt 文件归档
        complaint_desc = data.get("complaint_desc", "")
        if complaint_desc:
            ticket = get_ticket(ticket_id)
            if ticket:
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
        
        update_ticket(ticket_id, data)
        add_log("ticket_update", f"更新工单: {ticket_id}", ticket_id=ticket_id)
        return _ok({"id": ticket_id})
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
        date_start = request.args.get("date_start", "")
        date_end = request.args.get("date_end", "")
        
        # 查询工单列表（不分页，获取全部）
        tickets = list_tickets(
            limit=10000,  # 最多导出1万条
            offset=0,
            status=status,
            date_start=date_start,
            date_end=date_end
        )
        
        # 创建工作簿
        wb = Workbook()
        ws = wb.active
        ws.title = "工单数据"
        
        # 设置表头
        headers = [
            "工单编号", "创建日期", "学员姓名", "身份证", "手机号",
            "驾校", "驾照类型", "当前阶段", "合同金额", "实际已交",
            "违约金比例", "总扣费", "应退金额", "处理状态", "投诉渠道",
            "投诉类型", "扣费明细"
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
            deductions = ticket.get("deductions", "[]")
            try:
                if isinstance(deductions, str):
                    deductions = json.loads(deductions)
                deductions_str = "; ".join([f"{d.get('item', '')}:{d.get('amount', 0)}元" for d in deductions]) if deductions else ""
            except:
                deductions_str = str(deductions)[:100]
            
            # 获取分析结果
            analysis = ticket.get("analysis_result", {}) or {}
            
            row_data = [
                ticket.get("ticket_id", ""),
                ticket.get("created_at", ""),
                ticket.get("student_name", ""),
                ticket.get("id_card", ""),
                ticket.get("phone", ""),
                ticket.get("school_short", ""),
                ticket.get("license_type", ""),
                ticket.get("exam_stage", ""),
                analysis.get("total_fee", 0) if isinstance(analysis, dict) else 0,
                analysis.get("actual_paid", 0) if isinstance(analysis, dict) else 0,
                f"{(analysis.get('penalty_rate', 0.2) if isinstance(analysis, dict) else 0.2) * 100}%",
                analysis.get("total_deduction", 0) if isinstance(analysis, dict) else 0,
                analysis.get("refund", 0) if isinstance(analysis, dict) else 0,
                ticket.get("handle_status", "待处理"),
                ticket.get("complaint_channel", ""),
                ticket.get("complaint_type", ""),
                deductions_str,
            ]
            
            for col, value in enumerate(row_data, 1):
                ws.cell(row=row_idx, column=col, value=value)
        
        # 调整列宽
        column_widths = [15, 12, 10, 20, 12, 10, 8, 10, 10, 10, 10, 10, 10, 10, 10, 10, 30]
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
        data = request.get_json(force=True)
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
        data = request.get_json(force=True)
        id_card = data.get("id_card", "").strip()
        student_name = data.get("name", "")
        school_short = data.get("school_short", "未知")
        force = data.get("force", False)  # 是否强制重新下载

        if not id_card:
            return _err("缺少身份证号")

        # 确定存储路径（归档到学员独立文件夹）
        save_dir = get_archive_folder(student_name, id_card, school_short)

        # 检查缓存（除非强制重新下载）
        if not force:
            from database import contract_cache_get
            cached = contract_cache_get(id_card)
            if cached and os.path.exists(cached["file_path"]):
                add_log("contract_cache", f"使用缓存合同: {cached['student_name']} ({id_card})")
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
            return _ok({"filepath": filepath, "filename": filename, "cached": False})
        else:
            return _err("未找到电子合同（可能是2024年3月15日前报名的学员，或该学员确实无电子合同）")

    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


@app.route("/api/contract/upload", methods=["POST"])
def api_contract_upload():
    try:
        # 调试日志：打印接收到的所有字段和文件
        print(f"[UPLOAD DEBUG] Form keys: {list(request.form.keys())}")
        print(f"[UPLOAD DEBUG] Files keys: {list(request.files.keys())}")
        print(f"[UPLOAD DEBUG] Content-Type: {request.content_type}")
        
        # 尝试多种方式获取文件
        files = request.files.getlist("file")
        print(f"[UPLOAD DEBUG] getlist('file'): {len(files)} files")
        
        # 如果 getlist 为空，尝试 get 单个文件
        if not files:
            single_file = request.files.get("file")
            if single_file:
                files = [single_file]
                print(f"[UPLOAD DEBUG] get('file') found 1 file")
        
        # 如果还是为空，尝试遍历所有文件
        if not files:
            all_files = []
            for key in request.files.keys():
                file_list = request.files.getlist(key)
                all_files.extend(file_list)
                print(f"[UPLOAD DEBUG] Key '{key}' has {len(file_list)} files")
            files = all_files
        
        print(f"[UPLOAD DEBUG] Final files count: {len(files)}")
        
        if not files or len(files) == 0:
            return _err("未选择文件")

        id_card = request.form.get("id_card", "").strip()
        name = request.form.get("name", "").strip()
        school_short = request.form.get("school_short", "").strip()
        date_str = datetime.now().strftime("%Y%m%d")

        saved_files = []
        image_paths = [] # 用于合并 PDF 的图片路径
        
        for file in files:
            if not file.filename:
                continue
                
            ext = os.path.splitext(file.filename)[1].lower()
            allowed = (".pdf", ".png", ".jpg", ".jpeg", ".docx", ".xlsx", ".txt")
            if ext not in allowed:
                continue

            if id_card and name:
                save_dir = get_archive_folder(name, id_card, school_short)
                # 文件名标准化：姓名_合同_序号
                safe_orig = secure_filename(file.filename).replace(ext, "")
                filename = f"{name}_合同_{safe_orig}{ext}"
                
                filepath = os.path.join(save_dir, filename)
                n = 1
                while os.path.exists(filepath):
                    filename = f"{name}_合同_{safe_orig}({n}){ext}"
                    filepath = os.path.join(save_dir, filename)
                    n += 1
            else:
                filename = secure_filename(file.filename)
                save_dir = UPLOAD_DIR
                filepath = os.path.join(save_dir, filename)

            os.makedirs(save_dir, exist_ok=True)
            file.save(filepath)
            
            # ── 图片自动压缩 ──
            if ext in [".jpg", ".jpeg", ".png"]:
                try:
                    from services.image_compressor import compress_image
                    compress_image(filepath, filepath)  # 原地压缩
                except Exception as compress_err:
                    print(f"[COMPRESS WARNING] {filename}: {compress_err}")
            
            saved_files.append({"filepath": filepath, "filename": filename})

            # 收集图片路径用于后续合并
            if ext in [".jpg", ".jpeg", ".png"]:
                image_paths.append(filepath)

        if not saved_files:
            return _err("没有可保存的文件（格式可能不支持）")

        # ── 核心逻辑：如果有多张图片，自动合并成 PDF ──
        merged_pdf_path = None
        if len(image_paths) > 1:
            try:
                import img2pdf, glob
                pdf_filename = f"{name}_合同_合并版.pdf"
                merged_pdf_path = os.path.join(save_dir, pdf_filename)
                
                # 清理旧的合并 PDF，避免冗余
                old_pdfs = glob.glob(os.path.join(save_dir, f"{name}_合同_合并版*.pdf"))
                for old_pdf in old_pdfs:
                    try: os.remove(old_pdf)
                    except: pass

                # 确保图片按文件名排序，保证页码顺序正确
                image_paths.sort()
                
                # 去重：避免同一图片被多次添加
                unique_paths = []
                seen = set()
                for p in image_paths:
                    if p not in seen:
                        unique_paths.append(p)
                        seen.add(p)
                image_paths = unique_paths
                
                print(f"[MERGE DEBUG] Final image_paths ({len(image_paths)}): {image_paths}")
                
                # 先压缩图片再合并，减小 PDF 体积
                from services.image_compressor import compress_for_vision_api
                try:
                    compressed_for_pdf = compress_for_vision_api(
                        image_paths, 
                        max_size=(1920, 1080),
                        jpeg_quality=75,  # PDF 用稍低质量，体积更小
                        target_max_mb=1.5
                    )
                    print(f"[MERGE DEBUG] Compressed {len(image_paths)} images for PDF")
                except Exception as compress_err:
                    print(f"[MERGE WARNING] PDF图片压缩失败，使用原图: {compress_err}")
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
                
                saved_files.insert(0, {"filepath": merged_pdf_path, "filename": pdf_filename})
                add_log("contract_merge", f"已将 {len(image_paths)} 张图片合并为 PDF")
            except Exception as merge_err:
                traceback.print_exc()
                add_log("contract_merge_err", f"PDF 合并失败: {str(merge_err)}")
                # 合并失败不影响原始图片上传，继续执行

        add_log("contract_upload", f"上传 {len(saved_files)} 个文件")
        
        # 提取所有原始图片路径，用于后续合并 OCR
        image_paths_for_ocr = [f["filepath"] for f in saved_files if f["filename"].endswith(('.jpg', '.jpeg', '.png'))]
        
        # AI 分析默认使用第一张原始图片（保持兼容）
        ai_filepath = image_paths_for_ocr[0] if image_paths_for_ocr else saved_files[0]["filepath"]
        ai_filename = os.path.basename(ai_filepath)
        
        return _ok({
            "filepath": ai_filepath,
            "filename": ai_filename,
            "image_paths": image_paths_for_ocr,
            "all_files": saved_files,
            "count": len(saved_files)
        })

    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


@app.route("/api/contract/analyze", methods=["POST"])
def api_contract_analyze():
    try:
        data = request.get_json(force=True)
        filepath = data.get("filepath", "")
        exam_stage = data.get("exam_stage", "")
        training_hours = data.get("training_hours", {})
        total_fee = float(data.get("total_fee", 0) or 0)
        ticket_id = data.get("ticket_id", "")
        exam_counts = data.get("exam_counts", {})  # 新增：考试次数

        # 路径穿越防护：只允许项目目录下的文件
        allowed_dirs = [
            os.path.abspath(ARCHIVE_DIR),
            os.path.abspath(UPLOAD_DIR),
            os.path.abspath(REPLY_DIR),
        ]
        abs_filepath = os.path.abspath(filepath)
        if not any(abs_filepath.startswith(d) for d in allowed_dirs):
            return _err("无权访问该文件", 403)

        if not filepath or not os.path.exists(filepath):
            return _err("合同文件不存在")

        # 获取报名日期（用于特殊退费检测）
        registration_date = ""
        if ticket_id:
            ticket = get_ticket(ticket_id)
            if ticket:
                registration_date = ticket.get("registration_date", "")

        result = analyze_contract_from_file(
            filepath=filepath,
            exam_stage=exam_stage,
            training_hours=training_hours,
            total_fee=total_fee,
            image_paths=data.get("image_paths", []),
            exam_counts=exam_counts,
            registration_date=registration_date,
        )

        if result.get("error"):
            add_log("contract_analyze", f"分析失败: {result['error']}", success=False, ticket_id=ticket_id)
        else:
            add_log("contract_analyze", f"分析完成: 应退{result.get('refund', 0)}元", ticket_id=ticket_id)

            # 更新投诉工单
            if ticket_id:
                update_ticket(ticket_id, {
                    "total_fee": result.get("total_fee", 0),
                    "deduction_fee": result.get("total_deduction", 0),
                    "refund_fee": result.get("refund", 0),
                    "deduction_detail": result.get("deductions", []),
                    "contract_code": result.get("contract_code", ""),
                    "contract_path": filepath,
                })
            else:
                # 向下兼容：旧版保存方式
                id_card = data.get("id_card", "")
                if id_card:
                    existing = get_complaint_by_idcard(id_card)
                    if existing:
                        save_complaint({
                            "id": existing["id"],
                            "registration_fee": result.get("total_fee", 0),
                            "deduction_fee": result.get("total_deduction", 0),
                            "refund_fee": result.get("refund", 0),
                            "deduction_detail": result.get("deductions", []),
                            "contract_code": result.get("contract_code", ""),
                            "contract_path": filepath,
                        })

        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return _err(f"分析异常: {str(e)}"), 500


# ═══════════════════════════════════════════════════════════════
#  API: 回复函生成 + 模板管理
# ═══════════════════════════════════════════════════════════════

@app.route("/api/reply/generate", methods=["POST"])
def api_reply_generate():
    try:
        data = request.get_json(force=True)
        ticket_id = data.get("ticket_id", "")

        # 确定回复函保存路径
        reply_name = data.get("name", "")
        reply_id_card = data.get("id_card", "")
        reply_school = data.get("school_short", "")
        if reply_name and reply_id_card:
            reply_dir = get_archive_folder(reply_name, reply_id_card, reply_school)
        else:
            reply_dir = REPLY_DIR

        result = generate_reply(
            name=reply_name,
            id_card=reply_id_card,
            school_short=data.get("school_short", ""),
            registration_date=data.get("registration_date", ""),
            license_type=data.get("license_type", ""),
            school_name=data.get("school_name", ""),
            exam_stage=data.get("exam_stage", ""),
            total_fee=float(data.get("total_fee", 0) or 0),
            deductions=data.get("deductions", []),
            total_deduction=float(data.get("total_deduction", 0) or 0),
            refund=float(data.get("refund", 0) or 0),
            contract_code=data.get("contract_code", ""),
            training_hours=data.get("training_hours", {}),
            output_dir=reply_dir,
            template_id=data.get("template_id", ""),
        )

        if result.get("success"):
            add_log("reply_generate", f"生成回复函: {result['filename']}", ticket_id=ticket_id)
            if ticket_id:
                update_ticket(ticket_id, {"reply_path": result["filepath"]})
            elif data.get("id_card"):
                existing = get_complaint_by_idcard(data["id_card"])
                if existing:
                    save_complaint({
                        "id": existing["id"],
                        "reply_path": result["filepath"],
                    })
        else:
            add_log("reply_generate", f"生成失败: {result.get('error')}", success=False, ticket_id=ticket_id)

        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


@app.route("/api/reply/download")
def api_reply_download():
    filepath = request.args.get("path", "")
    if not filepath or not os.path.exists(filepath):
        return _err("文件不存在", 404)

    allowed_reply = [
        os.path.abspath(REPLY_DIR),
        os.path.abspath(ARCHIVE_DIR),
    ]
    if not any(os.path.abspath(filepath).startswith(d) for d in allowed_reply):
        return _err("无权访问", 403)

    return send_file(filepath, as_attachment=True)


@app.route("/api/templates", methods=["GET"])
def api_templates_list():
    """列出回复模板"""
    return _ok(list_templates())


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
        safe_name = secure_filename(file.filename)
        filepath = os.path.join(template_dir, safe_name)
        file.save(filepath)

        result = upload_template(filepath, name, description)
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
    return _err("设置失败")


@app.route("/api/templates/<template_id>", methods=["DELETE"])
def api_templates_delete(template_id):
    """删除模板"""
    if delete_template(template_id):
        add_log("template_delete", f"删除模板: {template_id}")
        return _ok(None, "已删除")
    return _err("删除失败")


# ═══════════════════════════════════════════════════════════════
#  API: 飞书登记
# ═══════════════════════════════════════════════════════════════

@app.route("/api/feishu/submit", methods=["POST"])
def api_feishu_submit():
    try:
        data = request.get_json(force=True)
        ticket_id = data.get("ticket_id", "")
        svc = FeishuService()
        result = svc.add_record(data)

        if result.get("success"):
            add_log("feishu_submit", f"提交飞书: {result.get('handle_no', '')}", ticket_id=ticket_id)
            if ticket_id:
                update_ticket(ticket_id, {
                    "feishu_record_id": result.get("record_id", ""),
                    "feishu_handle_no": result.get("handle_no", ""),
                    "handle_status": data.get("handle_status", "待处理"),
                })
        else:
            add_log("feishu_submit", f"提交失败: {result.get('error')}", success=False, ticket_id=ticket_id)

        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


@app.route("/api/feishu/test")
def api_feishu_test():
    try:
        svc = FeishuService()
        return jsonify(svc.test_connection())
    except Exception as e:
        return _err(str(e))


# ═══════════════════════════════════════════════════════════════
#  API: 回访管理 + 文档生成
# ═══════════════════════════════════════════════════════════════

@app.route("/api/tickets/<ticket_id>/visit", methods=["POST"])
def api_tickets_visit(ticket_id):
    """保存回访状态"""
    try:
        data = request.get_json(force=True)
        visit_status = data.get("visit_status", "").strip()
        visit_remark = data.get("visit_remark", "").strip()

        if not visit_status:
            return _err("请选择回访状态")
        if not visit_remark:
            return _err("请填写回访备注")
        if visit_status not in ("a", "b", "c", "d"):
            return _err("无效的回访状态")

        from services.visit_service import save_visit
        save_visit(ticket_id, visit_status, visit_remark)
        add_log("visit", f"回访: {visit_status} - {visit_remark}", ticket_id=ticket_id)

        # 回访完成 → 自动"处理中"（如果当前是"待处理"）
        ticket = get_ticket(ticket_id)
        if ticket and ticket.get("handle_status") == "待处理":
            update_ticket(ticket_id, {"handle_status": "处理中"})

        return _ok()

    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


@app.route("/api/tickets/<ticket_id>/register-form", methods=["POST"])
def api_tickets_register_form(ticket_id):
    """生成投诉登记表"""
    try:
        ticket = get_ticket(ticket_id)
        if not ticket:
            return _err("工单不存在", 404)

        from services.visit_service import generate_registration_form
        result = generate_registration_form(ticket)

        if result.get("success"):
            update_ticket(ticket_id, {"registration_form_path": result["filepath"]})
            add_log("form_generate", f"生成登记表: {result['filename']}", ticket_id=ticket_id)
            return _ok(result)
        else:
            add_log("form_generate", f"生成失败: {result.get('error')}", success=False, ticket_id=ticket_id)
            return _err(result.get("error"))

    except Exception as e:
        traceback.print_exc()
        return _err(str(e), 500)


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
    if safe.get("llm", {}).get("api_key"):
        key = safe["llm"]["api_key"]
        safe["llm"]["api_key"] = key[:8] + "••••" + key[-4:]
    if safe.get("feishu", {}).get("app_secret"):
        s = safe["feishu"]["app_secret"]
        safe["feishu"]["app_secret"] = s[:8] + "••••"

    return jsonify(safe)


@app.route("/api/config", methods=["PUT"])
def api_config_put():
    try:
        new_config = request.get_json(force=True)
        current = load_config()

        for section in ("internal_system", "third_system", "driving_system"):
            pwd = new_config.get(section, {}).get("password", "")
            if pwd and ("•" in pwd or "..." in pwd):
                new_config[section]["password"] = current.get(section, {}).get("password", "")

        if new_config.get("llm", {}).get("api_key", ""):
            key = new_config["llm"]["api_key"]
            if "•" in key or key[:8] == current.get("llm", {}).get("api_key", "")[:8]:
                new_config["llm"]["api_key"] = current.get("llm", {}).get("api_key", "")

        if new_config.get("feishu", {}).get("app_secret", ""):
            secret = new_config["feishu"]["app_secret"]
            if "•" in secret:
                new_config["feishu"]["app_secret"] = current.get("feishu", {}).get("app_secret", "")

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


# ═══════════════════════════════════════════════════════════════
#  API: 历史记录 + 统计
# ═══════════════════════════════════════════════════════════════

@app.route("/api/complaints")
def api_complaints_list():
    """获取投诉记录列表（兼容旧接口）"""
    status = request.args.get("status", "")
    search = request.args.get("search", "")
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))

    records, total = list_complaints(status=status, limit=limit, offset=offset, search=search)
    return jsonify({"records": records, "total": total})


@app.route("/api/statistics")
def api_statistics():
    return jsonify(get_statistics())


@app.route("/api/ticket-statistics")
def api_ticket_statistics():
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    return _ok(get_ticket_statistics(start_date=start_date, end_date=end_date))


@app.route("/api/logs/recent")
def api_recent_logs():
    limit = int(request.args.get("limit", 20))
    logs = get_recent_logs(limit=limit)
    return jsonify({"success": True, "logs": logs})


# ═══════════════════════════════════════════════════════════════
#  API: 文件下载
# ═══════════════════════════════════════════════════════════════

@app.route("/api/contract/download-file")
def api_contract_download_file():
    filepath = request.args.get("path", "")
    if not filepath or not os.path.exists(filepath):
        return _err("文件不存在", 404)

    allowed_dirs = [os.path.abspath(ARCHIVE_DIR), os.path.abspath(UPLOAD_DIR)]
    if not any(os.path.abspath(filepath).startswith(d) for d in allowed_dirs):
        return _err("无权访问", 403)

    return send_file(filepath, as_attachment=True)


# ═══════════════════════════════════════════════════════════════
#  模块初始化
# ═══════════════════════════════════════════════════════════════

# 创建默认回复模板
from services.template_service import create_default_template
try:
    create_default_template()
except Exception as e:
    print(f"[初始化] 创建默认模板: {e}")


# ═══════════════════════════════════════════════════════════════
#  启动
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 50)
    print("  驾校投诉处理系统")
    print(f"  访问地址: http://127.0.0.1:5003")
    print(f"  项目目录: {PROJECT_DIR}")
    print("=" * 50)
    app.run(host="127.0.0.1", port=5003)
