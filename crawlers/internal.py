"""
内部系统爬虫 - 重构版
集成登录态缓存，继承BaseCrawler
"""
import json
import ddddocr
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


STATUS_MAP = {
    "1": "报名", "2": "科目一待考", "3": "科目二待考",
    "4": "科目三待考", "5": "科目四待考", "6": "已结业",
    "7": "已领证", "8": "已注销", "9": "已退学",
    "10": "暂停培训", "12": "科二待考",
    "18": "科一未通过",
    "20": "科二收", "30": "科三收", "49": "科四收",
}


class InternalCrawler(BaseCrawler):
    """内部系统爬虫（重构版）"""
    
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
        try:
            url = f"{self.api_base}/xyxxController/listXyxx.action"
            resp = self.post(url, data={
                "sfzh": id_card,
                "page": 1,
                "rows": 10,
                "sort": "createtime",
                "order": "desc"
            })
            data = resp.json()
            
            if not data.get("rows"):
                if _retry == 0:
                    self.logout()
                    if self.ensure_login():
                        return self.query_student(id_card, _retry=1)
                return None
            
            row = data["rows"][0]
            info = self._parse_student_row(row)
            
            # 获取时间轴、考试次数、收费记录（并行）
            if info.student_id:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                    timeline_future = executor.submit(self._get_timeline, info.student_id)
                    fees_future = executor.submit(self._query_fees, info.student_id)
                    
                    info.timeline = timeline_future.result()
                    info.fees = fees_future.result()
                    info.exam_stage, info.exam_counts = self._analyze_timeline(info.timeline)
            
            return info
            
        except Exception:
            if _retry == 0:
                self.logout()
                if self.ensure_login():
                    return self.query_student(id_card, _retry=1)
            raise
    
    def query_by_phone(self, phone: str, _retry: int = 0) -> Optional[InternalStudentInfo]:
        """通过手机号查询学员信息，返回后通过身份证号查完整信息"""
        try:
            url = f"{self.api_base}/xyxxController/listXyxx.action"
            resp = self.post(url, data={
                "sjhm": phone,
                "page": 1,
                "rows": 10,
                "sort": "createtime",
                "order": "desc"
            })
            data = resp.json()
            
            if not data.get("rows"):
                return None
            
            row = data["rows"][0]
            id_card = row.get("sfzh", "")
            if id_card and len(id_card) >= 7:
                return self.query_student(id_card)
            
            # 如果没有身份证号，返回基本信息
            return self._parse_student_row(row)
            
        except Exception:
            if _retry == 0:
                self.logout()
                if self.ensure_login():
                    return self.query_by_phone(phone, _retry=1)
            raise
    
    def _parse_student_row(self, row: dict) -> InternalStudentInfo:
        """解析学员数据行"""
        status_code = str(row.get("xyzt", ""))
        exam_stage = STATUS_MAP.get(status_code, "未知")
        
        # 判断考试阶段
        if status_code in ["20"]:
            exam_stage = "科目二"
        elif status_code in ["30", "3"]:
            exam_stage = "科目三"
        elif status_code in ["49", "4", "5"]:
            exam_stage = "科目四"
        elif status_code in ["6", "7"]:
            exam_stage = "已结业"
        elif status_code in ["1", "2"]:
            exam_stage = "科目一"
        
        return InternalStudentInfo(
            name=row.get("name", ""),
            id_card=row.get("sfzh", ""),
            phone=row.get("sjhm", ""),
            license_type=row.get("pxcx", ""),
            school_code=row.get("orgid", ""),
            school_name=row.get("orgname", ""),
            school_short=row.get("orgdh", ""),
            registration_date=row.get("bmrq", "") or row.get("createtime", ""),
            student_status=STATUS_MAP.get(status_code, f"未知({status_code})"),
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
            resp = self.http.get(url, params={"xyid": student_id})
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
            resp = self.http.get(url, params={"xyid": student_id})
            data = resp.json()
            return data.get("rows", [])
        except Exception:
            return []
