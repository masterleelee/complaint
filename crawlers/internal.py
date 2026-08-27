"""
内部系统爬虫 - 重构版
集成登录态缓存，继承BaseCrawler
"""
import json
import ddddocr
import time
from typing import Optional
from dataclasses import dataclass, field

from core.auth_manager import BaseCrawler, SystemType, LoginResult


@dataclass
class InternalStudentInfo:
    """内部系统学员信息"""
    name: str = ""
    id_card: str = ""
    phone: str = ""
    license_type: str = ""
    school_code: str = ""
    school_name: str = ""
    school_short: str = ""
    registration_date: str = ""
    student_status: str = ""
    student_status_code: str = ""
    student_code: str = ""
    student_id: str = ""
    is_owed: bool = False
    timeline: list = field(default_factory=list)
    exam_stage: str = ""
    exam_counts: dict = field(default_factory=dict)
    fees: list = field(default_factory=list)


# 内部系统状态码 → 待考阶段（来源：jxywxt 前端 syUtil.js 的 xyztTypeData）
STATUS_MAP = {
    "-1": "报名", "00": "报名", "09": "报名",
    "10": "科目一", "11": "科目一", "12": "科目一", "13": "科目一",
    "14": "科目一", "15": "科目一", "16": "科目一", "17": "科目一",
    "18": "科目一", "19": "科目一",
    "20": "科目二", "21": "科目二", "22": "科目二", "23": "科目二",
    "28": "科目二", "29": "科目二",
    "30": "科目三", "31": "科目三", "32": "科目三", "33": "科目三",
    "38": "科目三", "39": "科目三",
    "40": "科目四", "41": "科目四", "42": "科目四",
    "48": "科目四", "49": "科目四",
    "99": "已结业",
    "T1": "未知", "TT": "未知",
    "X2": "科目二", "X3": "科目三",
}

_STAGE_ORDER = {"报名": 0, "科目一": 1, "科目二": 2, "科目三": 3, "科目四": 4, "已结业": 5}


def _max_stage(a: str, b: str) -> str:
    """取两个阶段中较靠后的一个。时间轴可能落后于实时状态（如「科二收」不产生时间轴节点）。"""
    return b if _STAGE_ORDER.get(b, -1) > _STAGE_ORDER.get(a, -1) else a

# 内部系统学员状态字典（来源：jxywxt 前端 syUtil.js 的 xyztTypeData）
XYZT_TEXT_MAP = {
    "-1": "待完善", "00": "录入", "09": "总校收", "10": "科一收",
    "11": "受理中", "12": "已受理", "13": "受理退回", "14": "已缴费1190(申请)",
    "15": "科一约考", "16": "科一约成功", "17": "科一已缴费(申请)", "18": "科一未通过",
    "19": "科一通过", "20": "科二收", "21": "科二约考", "22": "科二约成功",
    "23": "科二已缴费(申请)", "28": "科二未通过", "29": "科二通过", "30": "科三收",
    "31": "科三约考", "32": "科三约成功", "33": "科三已缴费(申请)", "38": "科三未通过",
    "39": "科三通过", "40": "科四收", "41": "科四约考", "42": "科四约成功",
    "48": "科四未通过", "49": "科四通过", "99": "已领证", "T1": "退学申请",
    "TT": "已退学(已审核退费)", "X2": "科二五次未过", "X3": "科三五次未过",
}


class InternalAuthenticationExpired(RuntimeError):
    pass


class PhoneLookupAmbiguityError(ValueError):
    """手机号匹配到多个不同学员，无法安全确定身份证号。"""


class InternalCrawler(BaseCrawler):
    """内部系统爬虫（重构版）"""

    SESSION_MAX_AGE_SECONDS = 15 * 60
    DETAIL_DEADLINE_SECONDS = 8
    
    def __init__(self):
        from config import load_config

        cfg = load_config()["internal_system"]
        base_url = cfg["base_url"]
        username = cfg["username"]
        password = cfg["password"]
        
        super().__init__(SystemType.INTERNAL, username, password)
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/sypro_jm"
        self._org_list = []

    def _response_json(self, response):
        text = (getattr(response, "text", "") or "").lstrip()
        if "userController/login.action" in text:
            raise InternalAuthenticationExpired("内部系统登录态已失效")
        data = response.json()
        self.mark_session_verified()
        return data

    def _record_student_list_duration(self, started: float, auth_wait_before: int):
        metrics = self.get_last_query_metrics()
        auth_wait_after = metrics["phase_durations_ms"].get("auth_wait", 0)
        total_ms = int((time.perf_counter() - started) * 1000)
        self._record_query_phase(
            "student_list",
            max(0, total_ms - (auth_wait_after - auth_wait_before)),
        )
    
    def _do_login(self) -> LoginResult:
        """执行登录"""
        from core.ocr_engine import get_ocr
        import time
        start_time = time.time()
        
        try:
            # 1. 先访问首页获取session
            self.http.get(f"{self.api_base}/")
            
            # 2. 获取验证码
            captcha_url = f"{self.api_base}/authImage"
            resp = self.http.get(captcha_url)
            captcha_text = get_ocr().classification(resp.content)
            
            # 3. 登录请求
            login_url = f"{self.api_base}/userController/login.action"
            resp = self.http.post(
                login_url,
                data={
                    "accounts": self.username,
                    "pwd": self.password,
                    "rand": captcha_text
                },
                allow_redirects=True
            )
            
            result = resp.json()
            if result.get("obj") and result["obj"].get("loginName"):
                duration = int((time.time() - start_time) * 1000)
                return LoginResult(True, "登录成功", duration_ms=duration)
            else:
                return LoginResult(False, result.get("msg", "登录失败"))
                
        except Exception as e:
            return LoginResult(False, str(e))
    
    def _load_org_list(self):
        """加载机构列表"""
        try:
            url = f"{self.base_url}/sypro_jm/school/schoolOprAction!getOrgList.action"
            resp = self.http.post(url, data={"orgName": ""})
            self._org_list = resp.json()
        except Exception:
            self._org_list = []
    
    def query_student(self, id_card: str, _retry: int = 0) -> Optional[InternalStudentInfo]:
        """查询学员信息（按身份证）"""
        if _retry == 0:
            self._reset_query_metrics()
        list_started = time.perf_counter()
        auth_wait_before = self.get_last_query_metrics()["phase_durations_ms"].get("auth_wait", 0)
        list_recorded = False
        try:
            url = f"{self.api_base}/xyxxController/listXyxx.action"
            resp = self.post(url, data={
                "sfzh": id_card,
                "page": 1,
                "rows": 10,
                "sort": "createtime",
                "order": "desc"
            }, timeout=8, retries=0)
            data = self._response_json(resp)
            self._record_student_list_duration(list_started, auth_wait_before)
            list_recorded = True
            
            if not data.get("rows"):
                return None
            
            row = data["rows"][0]
            info = self._parse_student_row(row)
            
            # 获取时间轴、考试次数、收费记录（并行）
            if info.student_id:
                import concurrent.futures
                executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
                try:
                    timeline_future = executor.submit(
                        self._timed_detail_query, "timeline", self._get_timeline, info.student_id
                    )
                    fees_future = executor.submit(
                        self._timed_detail_query, "fees", self._query_fees, info.student_id
                    )
                    done, pending = concurrent.futures.wait(
                        {timeline_future, fees_future},
                        timeout=self.DETAIL_DEADLINE_SECONDS,
                    )
                    info.timeline = (
                        timeline_future.result()
                        if timeline_future in done and not timeline_future.exception()
                        else []
                    )
                    info.fees = (
                        fees_future.result()
                        if fees_future in done and not fees_future.exception()
                        else []
                    )
                    for future in pending:
                        future.cancel()
                    tl_stage, info.exam_counts = self._analyze_timeline(info.timeline)
                    info.exam_stage = _max_stage(tl_stage, STATUS_MAP.get(info.student_status_code, ""))
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)
            
            return info
            
        except InternalAuthenticationExpired:
            if not list_recorded:
                self._record_student_list_duration(list_started, auth_wait_before)
                list_recorded = True
            self._increment_query_retry()
            if _retry == 0:
                self.logout()
                if self.ensure_login():
                    return self.query_student(id_card, _retry=1)
            raise
        finally:
            if not list_recorded:
                self._record_student_list_duration(list_started, auth_wait_before)

    def _timed_detail_query(self, phase: str, func, student_id: str):
        started = time.perf_counter()
        try:
            return func(student_id)
        finally:
            self._record_query_phase(phase, (time.perf_counter() - started) * 1000)
    
    def query_by_phone(self, phone: str, _retry: int = 0) -> Optional[InternalStudentInfo]:
        """通过手机号查询学员信息，返回后通过身份证号查完整信息"""
        id_card = self.lookup_id_card_by_phone(phone, _retry=_retry)
        return self.query_student(id_card) if id_card else None

    def lookup_id_card_by_phone(self, phone: str, _retry: int = 0) -> str:
        """仅按手机号获取证件号，不加载时间轴和收费记录。"""
        try:
            url = f"{self.api_base}/xyxxController/listXyxx.action"
            resp = self.post(url, data={
                "sjhm": phone,
                "page": 1,
                "rows": 10,
                "sort": "createtime",
                "order": "desc"
            }, timeout=8, retries=0)
            data = self._response_json(resp)
            
            if not data.get("rows"):
                return ""
            
            id_cards = {
                row.get("sfzh", "")
                for row in data["rows"]
                if row.get("sfzh", "") and len(row.get("sfzh", "")) >= 7
            }
            if len(id_cards) > 1:
                raise PhoneLookupAmbiguityError("手机号匹配多个学员，请补充身份证号")
            return next(iter(id_cards), "")
            
        except InternalAuthenticationExpired:
            if _retry == 0:
                self.logout()
                if self.ensure_login():
                    return self.lookup_id_card_by_phone(phone, _retry=1)
            raise
    
    def _load_org_tree(self):
        """加载机构树（分校/分店 → orgid），用于报名点筛选。失败返回空列表。"""
        if getattr(self, "_org_flat", None):
            return self._org_flat
        try:
            url = f"{self.api_base}/orgController/currentTreeNode.action"
            resp = self.http.post(url, timeout=8, retries=0)
            tree = resp.json()
        except Exception:
            return []
        flat = []

        def walk(nodes):
            for n in nodes or []:
                if n.get("id") and n.get("text"):
                    flat.append({"id": n["id"], "text": n["text"]})
                walk(n.get("children"))

        walk(tree)
        self._org_flat = flat
        return flat

    @staticmethod
    def _norm_org(name: str) -> str:
        for suffix in ("", "招生点", "分校", "分店", "总校"):
            name = name.replace(suffix, "")
        return name.strip()

    def resolve_org_id(self, org_name: str) -> str:
        """按名称模糊匹配机构树节点，返回内部系统 orgid；匹配不到返回空。"""
        key = self._norm_org(org_name or "")
        if not key:
            return ""
        flat = [n for n in self._load_org_tree() if self._norm_org(n["text"])]
        if not flat:
            return ""
        for node in flat:
            if node["text"] == org_name or self._norm_org(node["text"]) == key:
                return node["id"]
        contains = [n for n in flat if key in self._norm_org(n["text"]) or self._norm_org(n["text"]) in key]
        if contains:
            return min(contains, key=lambda n: len(self._norm_org(n["text"])))["id"]
        return ""

    def search_students(self, name: str, org_name: str = "", date_from: str = "",
                        date_to: str = "", limit: int = 50, page: int = 1,
                        _retry: int = 0) -> dict:
        """按姓名(模糊)+报名点+报名时间范围轻量检索学员。

        只调 listXyxx.action 列表接口（sjlx=1 使日期过滤生效），
        不加载时间轴/收费/考试分析。返回 {students, total, org_fallback}。
        """
        params = {
            "name": name, "sjlx": "1",
            "page": max(1, int(page or 1)), "rows": limit,
            "sort": "createtime", "order": "desc",
        }
        org_id = self.resolve_org_id(org_name) if org_name else ""
        if org_id:
            params["orgid"] = org_id
        if date_from:
            params["queryStartTime"] = date_from
        if date_to:
            params["queryEndTime"] = date_to
        org_fallback = False
        try:
            resp = self.post(f"{self.api_base}/xyxxController/listXyxx.action",
                             data=params, timeout=8, retries=0)
            data = self._response_json(resp)
            rows = data.get("rows") or []
            total = int(data.get("total") or 0)
            # 报名点过滤无结果时自动放宽（学员可能已转校，内部系统存当前归属网点）
            if not rows and org_id:
                org_fallback = True
                params.pop("orgid", None)
                resp = self.post(f"{self.api_base}/xyxxController/listXyxx.action",
                                 data=params, timeout=8, retries=0)
                data = self._response_json(resp)
                rows = data.get("rows") or []
                total = int(data.get("total") or 0)
            return {
                "students": [self._parse_student_row(r) for r in rows],
                "total": total,
                "org_fallback": org_fallback,
            }
        except InternalAuthenticationExpired:
            if _retry == 0:
                self.logout()
                if self.ensure_login():
                    return self.search_students(name, org_name, date_from, date_to, limit, page, _retry=1)
            raise

    def _parse_student_row(self, row: dict) -> InternalStudentInfo:
        """解析学员数据行"""
        status_code = str(row.get("xyzt", ""))
        exam_stage = STATUS_MAP.get(status_code, "未知")

        return InternalStudentInfo(
            name=row.get("name", ""),
            id_card=row.get("sfzh", ""),
            phone=row.get("sjhm", ""),
            license_type=row.get("pxcx", ""),
            school_code=row.get("orgid", ""),
            school_name=row.get("orgname", ""),
            school_short=row.get("orgdh", ""),
            registration_date=row.get("bmrq", "") or row.get("createtime", ""),
            student_status=XYZT_TEXT_MAP.get(status_code, f"未知({status_code})"),
            student_status_code=status_code,
            student_code=row.get("xybh", ""),
            student_id=row.get("id", ""),
            is_owed=row.get("isOwefees", False),
            exam_stage=exam_stage,
        )
    
    def _get_timeline(self, student_id: str) -> list:
        """获取学员时间轴"""
        try:
            url = f"{self.api_base}/xyxxController/queryXySjz.action"
            resp = self.http.get(url, params={"xyid": student_id}, timeout=8, retries=0)
            data = resp.json()
            obj_str = data.get("obj", "")
            if obj_str and isinstance(obj_str, str):
                return json.loads(obj_str)
            return []
        except Exception:
            return []

    def _analyze_timeline(self, timeline: list) -> tuple:
        """分析时间轴，提取考试阶段和考试次数"""
        exam_counts = {"科目一": 0, "科目二": 0, "科目三": 0, "科目四": 0}
        current_stage = "报名"
        
        stage_order = {"报名": 0, "科目一": 1, "科目二": 2, "科目三": 3, "科目四": 4, "已结业": 5}
        
        for item in timeline:
            title = item.get("NodeTitle", "")
            
            if "科目1" in title or "科一" in title:
                if stage_order.get("科目一", 0) > stage_order.get(current_stage, 0):
                    current_stage = "科目一"
                if "考试" in title or "通过" in title or "不合格" in title:
                    exam_counts["科目一"] += 1
                    
            elif "科目2" in title or "科二" in title:
                if stage_order.get("科目二", 0) > stage_order.get(current_stage, 0):
                    current_stage = "科目二"
                if "考试" in title or "通过" in title or "不合格" in title:
                    exam_counts["科目二"] += 1
                    
            elif "科目3" in title or "科三" in title:
                if stage_order.get("科目三", 0) > stage_order.get(current_stage, 0):
                    current_stage = "科目三"
                if "考试" in title or "通过" in title or "不合格" in title:
                    exam_counts["科目三"] += 1
                    
            elif "科目4" in title or "科四" in title:
                if stage_order.get("科目四", 0) > stage_order.get(current_stage, 0):
                    current_stage = "科目四"
                if "考试" in title or "通过" in title or "不合格" in title:
                    exam_counts["科目四"] += 1
        
        return current_stage, exam_counts

    def _query_fees(self, student_id: str) -> list:
        """获取收费记录"""
        try:
            url = f"{self.api_base}/xyxxController/queryXySfList.action"
            resp = self.http.get(url, params={"xyid": student_id}, timeout=8, retries=0)
            data = resp.json()
            return data.get("rows", [])
        except Exception:
            return []
