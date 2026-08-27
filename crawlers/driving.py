"""
东莞驾培系统爬虫 - 管家鞋驾培平台
https://www.guanjiaxie.com:8089
使用 ddddocr + 智能重试策略识别算术验证码
重构版 - 继承BaseCrawler，集成登录态缓存
"""
import os
import re
import random
import requests
import time
import io
import numpy as np
from PIL import Image
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime

from core.auth_manager import BaseCrawler, SystemType, LoginResult
from config import load_config
from utils.logger import system_logger


@dataclass
class DrivingStudentInfo:
    """东莞驾培学员信息"""
    name: str = ""
    id_card: str = ""
    phone: str = ""
    license_type: str = ""
    registration_date: str = ""
    student_id: str = ""
    contract_code: str = ""
    contract_url: str = ""
    contract_available: bool = False
    contract_checked: bool = False
    contract_fee: float = 0.0
    pay_fee: float = 0.0
    supervise_fee: float = 0.0
    residue_supervise_amt: float = 0.0
    supervise_date: str = ""
    fee_breakdown: dict = field(default_factory=dict)
    pay_orders: list = field(default_factory=list)


class DrivingAuthenticationExpired(RuntimeError):
    """登录态失效（服务端返回登录页而非JSON）"""


class DrivingCrawler(BaseCrawler):
    """东莞驾培系统爬虫（重构版）"""

    LOGIN_MAX_ATTEMPTS = 6
    LOGIN_TIMEOUT = 8
    SESSION_MAX_AGE_SECONDS = 25 * 60

    @staticmethod
    def _is_login_page(resp, text: str) -> bool:
        """判断响应是否被服务端重定向到登录页（会话失效特征）。"""
        url = (getattr(resp, "url", "") or "").lower()
        if url.endswith("/login") or url.endswith("/schoolLogin") or "/login?" in url:
            return True
        return "schoolLogin" in text or "登录东莞市" in text

    @staticmethod
    def _is_auth_error(data: dict) -> bool:
        code = data.get("code")
        message = str(data.get("msg", "")).lower()
        return code in (401, 403) or any(
            token in message for token in ("未登录", "登录失效", "请登录", "session")
        )

    def __init__(self):
        cfg = load_config()["driving_system"]
        base_url = cfg["base_url"].rstrip("/")
        username = cfg["username"]
        password = cfg["password"]
        
        super().__init__(SystemType.DRIVING, username, password)
        self.base_url = base_url

    def _get_ocr(self):
        """使用全局共享 OCR 引擎"""
        from core.ocr_engine import get_ocr
        return get_ocr()

    # ── 运算符字形模板匹配（基于 79 张人工标注校准）──
    _OP_WIN = (56, 95)
    _OPW = 38
    _TEMPL_SZ = 32
    _OP_TEMPLATES_PATH = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "captcha_op_templates.npz"
    )
    _op_templates_cache = None
    # ddddocr 把运算符误读为字母；'a' 是 =/? 后缀噪声需剔除；'l' 是 ÷（除号）
    _op_map = {
        't': '+', 'x': '+', 'y': '-', 'i': '+', 'j': '+',
        'h': '*', 'k': '*',
        'l': '/',
    }
    _NOISE = set('a')  # =/? 后缀被 ddddocr 读成 a，属噪声，剔除

    @staticmethod
    def _gray(img: bytes) -> "np.ndarray":
        """验证码字节 -> 灰度 numpy 数组"""
        return np.asarray(Image.open(io.BytesIO(img)).convert("L"))

    @staticmethod
    def _otsu(arr: "np.ndarray") -> int:
        """Otsu 自动阈值（字形为暗，背景为亮）"""
        hist = np.bincount(arr.ravel(), minlength=256)
        total = arr.size
        sumv = (np.arange(256) * hist).sum()
        sumb = 0
        wb = 0
        maxvar = 0
        thr = 127
        for i in range(256):
            wb += hist[i]
            if wb == 0:
                continue
            wf = total - wb
            if wf == 0:
                break
            sumb += i * hist[i]
            mb = sumb / wb
            mf = (sumv - sumb) / wf
            between = wb * wf * (mb - mf) ** 2
            if between > maxvar:
                maxvar = between
                thr = i
        return thr

    @staticmethod
    def _norm_crop(im: "np.ndarray", win) -> "np.ndarray":
        """窗口裁剪 -> 二值归一化 32x32（字形=255）"""
        x0, x1 = win
        if x1 <= x0:
            return np.zeros((DrivingCrawler._TEMPL_SZ, DrivingCrawler._TEMPL_SZ), np.uint8)
        sub = im[:, x0:x1]
        thr = DrivingCrawler._otsu(sub)
        bw = (sub < thr).astype(np.uint8)
        rows = np.where(bw.sum(axis=1) > 0)[0]
        y0, y1 = (rows[0], rows[-1]) if len(rows) else (0, sub.shape[0] - 1)
        g = (bw[y0:y1 + 1] > 0).astype(np.uint8) * 255
        return np.array(Image.fromarray(g).resize(
            (DrivingCrawler._TEMPL_SZ, DrivingCrawler._TEMPL_SZ), Image.NEAREST))

    def _load_op_templates(self):
        if DrivingCrawler._op_templates_cache is None:
            data = np.load(DrivingCrawler._OP_TEMPLATES_PATH, allow_pickle=True)
            DrivingCrawler._op_templates_cache = (data["templates"], list(data["labels"]))
        return DrivingCrawler._op_templates_cache

    def _match_op(self, img: bytes):
        """定位并匹配运算符字形，返回 '+'/'-'/'*'/'/' 或 None"""
        templates, labels = self._load_op_templates()
        if templates is None or len(templates) == 0:
            return None
        im = self._gray(img)
        best = None
        best_d = 1e9
        for p in range(40, 92):
            c = self._norm_crop(im, (p, p + self._OPW))
            dists = np.mean(c != templates, axis=(1, 2))
            d = float(dists.min())
            if d < best_d:
                best_d = d
                best = str(labels[int(dists.argmin())])
        return best

    def _resolve_operator(self, img: bytes, chars, opi):
        """优先用字形模板，失败回退 ddddocr 字母映射"""
        try:
            tmpl = self._match_op(img)
            if tmpl in ("+", "-", "*", "/"):
                return tmpl
        except Exception:
            pass
        if opi is not None and opi < len(chars):
            ch = chars[opi]
            return ch if ch in ("+", "-", "*", "/") else self._op_map.get(ch, "?")
        return "?"

    def _solve_captcha(self, img: bytes) -> list:
        """
        解析算术验证码：[单数字][+-*/][单数字]=?
        关键规律（79 张人工标注验证）：
          - 操作数恒为单 digit；第二个操作数紧邻 '=结果' 区，偶尔被粘连读成多位数，
            此时首位数字是真值、其余为噪声 -> 每个操作数只取首位数。
          - 真正瓶颈是运算符（ddddocr 误读成字母），用字形模板匹配覆盖 remap。
        验证码单次有效，只返回首候选。
        """
        try:
            ocr = self._get_ocr()
            text = ocr.classification(img)

            # 1. 提取有效字符（数字 / 运算符 / 映射字母），剔除 =? 噪声 'a'
            chars = [
                ch for ch in text
                if (ch.isdigit() or ch in ("+", "-", "*", "/") or ch in self._op_map)
                and ch not in self._NOISE
            ]

            # 2. 运算符位置
            opi = None
            for i, ch in enumerate(chars):
                if ch in ("+", "-", "*", "/") or ch in self._op_map:
                    opi = i
                    break

            # 3. 每个操作数只取首位数（噪声规律）
            if opi is not None:
                pre = [c for c in chars[:opi] if c.isdigit()]
                post = [c for c in chars[opi + 1:] if c.isdigit()]
            else:
                pre = [c for c in chars if c.isdigit()][:1]
                post = []

            digits = [int(c) for c in chars if c.isdigit()]
            if len(pre) >= 1 and len(post) >= 1:
                d1 = int(pre[0])
                d2 = int(post[0])
            elif len(digits) >= 2:
                # ddddocr 未识别运算符字母、无法按运算符切分时的兜底：取前两个数字
                d1 = digits[0]
                d2 = digits[1]
            else:
                return []

            # 4. 运算符：字形模板优先，回退 ddddocr 映射
            op = self._resolve_operator(img, chars, opi)
            if op not in ("+", "-", "*", "/"):
                return []

            r = self._eval_op(d1, op, d2)
            if r is not None and 0 <= r <= 81:
                return [r]

            return []

        except Exception:
            return []

    @staticmethod
    def _eval_op(a: int, op: str, b: int) -> int | None:
        if op == '+':
            return a + b
        if op == '-':
            return a - b
        if op == '*':
            return a * b
        if op == '/' and b != 0 and a % b == 0:
            return a // b
        return None

    def _do_login(self) -> LoginResult:
        """执行实际登录逻辑"""
        start_time = time.time()
        max_retries = self.LOGIN_MAX_ATTEMPTS
        
        try:
            for attempt in range(1, max_retries + 1):
                try:
                    # 1. 获取验证码（无需先访问登录页，服务端会在响应中种下会话 Cookie）
                    img = self.http.get(
                        f"{self.base_url}/captcha/captchaImage?type=math&s={random.random()}",
                        timeout=self.LOGIN_TIMEOUT,
                        retries=0,
                    ).content
                    
                    if len(img) < 100:
                        continue
                    
                    # 2. 解析验证码（验证码单次有效，只试首候选）
                    possible_answers = self._solve_captcha(img)
                    if not possible_answers:
                        continue
                    
                    # 3. 只试首候选，失败立即换新验证码
                    resp = self.http.post(
                        f"{self.base_url}/schoolLogin",
                        data={
                            "schoolType": "1",
                            "username": self.username,
                            "password": self.password,
                            "validateCode": str(possible_answers[0]),
                            "rememberMe": "false",
                        },
                        timeout=self.LOGIN_TIMEOUT,
                        retries=0,
                    )
                    
                    result = resp.json()
                    if result.get("code") == 0:
                        duration = int((time.time() - start_time) * 1000)
                        return LoginResult(True, "登录成功", duration_ms=duration)
                        
                except Exception:
                    continue
            
            return LoginResult(False, f"登录失败，已尝试{max_retries}次")
            
        except Exception as e:
            return LoginResult(False, f"登录异常: {str(e)}")

    def query_student(self, id_card: str, _retry: int = 0, include_contract_check: bool = True) -> Optional[DrivingStudentInfo]:
        """查询学员信息"""
        if _retry == 0:
            self._reset_query_metrics()
        auth_started = time.perf_counter()
        logged_in = self.ensure_login()
        self._record_query_phase("auth_wait", (time.perf_counter() - auth_started) * 1000)
        if not logged_in:
            raise RuntimeError("东莞驾培登录失败，无法查询学员信息")

        # 交费订单改为查到学员后再顺序请求：该站对同一会话的并发请求近似串行，
        # 并行会导致一方等待超时后重试，实测反而更慢（日志 lookup 阶段累计 > 总耗时）；
        # 且学员不存在时可直接跳过订单查询，节省一次 API 调用。
        lookup_started = time.perf_counter()
        lookup_recorded = False
        try:
            resp = self.post(
                f"{self.base_url}/business/student/list",
                data={"pageNum": 1, "pageSize": 10, "identity": id_card},
                timeout=10,
                retries=0,
            )
            self._record_query_phase(
                "lookup", (time.perf_counter() - lookup_started) * 1000
            )
            lookup_recorded = True
            text = (getattr(resp, "text", "") or "").lstrip()
            if self._is_login_page(resp, text):
                raise DrivingAuthenticationExpired("东莞驾培登录态已失效，服务端返回登录页")
            data = resp.json()

            if data.get("code") != 0:
                if self._is_auth_error(data):
                    self._increment_query_retry()
                    if _retry == 0:
                        self.logout()
                        if self.ensure_login():
                            return self.query_student(
                                id_card,
                                _retry=1,
                                include_contract_check=include_contract_check,
                            )
                    raise DrivingAuthenticationExpired(
                        str(data.get("msg") or "东莞驾培登录态已失效")
                    )
                return None

            rows = data.get("rows", [])
            if not rows:
                return None

            # 精确匹配身份证号
            student = None
            for row in rows:
                if row.get("identity", "").upper() == id_card.upper():
                    student = row
                    break
            if not student:
                return None

            info = DrivingStudentInfo(
                name=student.get("name", ""),
                id_card=id_card,
                phone=student.get("mobile", ""),
                license_type=student.get("licenseType", ""),
                registration_date=student.get("createTime", ""),
                student_id=str(student.get("id", "")),
            )

            info.contract_fee = float(student.get("contractFee", 0) or 0)
            info.pay_fee = float(student.get("payFee", 0) or 0)
            info.supervise_fee = float(student.get("superviseFee", 0) or 0)
            info.residue_supervise_amt = float(student.get("residueSuperviseAmt", 0) or 0)
            info.supervise_date = student.get("superviseDate", "")
            info.fee_breakdown = {
                "service_fee": float(student.get("serviceFee", 0) or 0),
                "theory_fee": float(student.get("textTrainFee", 0) or 0),
                "subject2_fee": float(student.get("operateFee", 0) or 0),
                "subject2_unit": float(student.get("studyTimeFee", 0) or 0),
                "subject3_fee": float(student.get("operateFee2", 0) or 0),
                "subject3_unit": float(student.get("studyTimeFee2", 0) or 0),
                "subject2_retrain": float(student.get("phase2Fee1", 0) or 0),
                "subject3_retrain": float(student.get("phase3Fee1", 0) or 0),
                "pickup_fee": float(student.get("jiesongFee", 0) or 0),
            }
            orders_started = time.perf_counter()
            info.pay_orders = self.query_pay_orders(id_card)
            self._record_query_phase(
                "pay_orders", (time.perf_counter() - orders_started) * 1000
            )

            # 检查合同（快速模式：只检查是否有合同，不获取URL）
            if include_contract_check and info.student_id:
                contract_started = time.perf_counter()
                info.contract_available = self._check_contract_exists(info.student_id)
                info.contract_checked = True
                self._record_query_phase(
                    "contract_check", (time.perf_counter() - contract_started) * 1000
                )

            return info
            
        except DrivingAuthenticationExpired:
            self._increment_query_retry()
            if _retry == 0:
                self.logout()
                if self.ensure_login():
                    return self.query_student(
                        id_card,
                        _retry=1,
                        include_contract_check=include_contract_check,
                    )
            raise
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status in (401, 403) and _retry == 0:
                self._increment_query_retry()
                self.logout()
                if self.ensure_login():
                    return self.query_student(id_card, _retry=1, include_contract_check=include_contract_check)
            if status in (500, 502, 503, 504) and _retry == 0:
                # 服务端瞬时错误：快速重试一次，无需重新登录
                self._increment_query_retry()
                time.sleep(0.5)
                return self.query_student(id_card, _retry=1, include_contract_check=include_contract_check)
            raise
        except (ValueError, requests.RequestException) as e:
            # JSON 解析失败或连接异常：视为瞬时错误重试一次
            if _retry == 0:
                self._increment_query_retry()
                time.sleep(0.5)
                return self.query_student(id_card, _retry=1, include_contract_check=include_contract_check)
            raise
        finally:
            if not lookup_recorded:
                self._record_query_phase(
                    "lookup", (time.perf_counter() - lookup_started) * 1000
                )

    def query_pay_orders(self, id_card: str) -> list:
        """查询学员交费订单明细（divsionOrder/list，按身份证号）"""
        try:
            resp = self.post(
                f"{self.base_url}/business/divsionOrder/list",
                data={"pageNum": 1, "pageSize": 20, "studentIdcard": id_card},
                timeout=15,
                retries=0,
            )
            data = resp.json()
            if data.get("code") != 0:
                return []
            orders = []
            for row in data.get("rows", []) or []:
                if not isinstance(row, dict) or "orderNo" not in row:
                    continue
                orders.append({
                    "order_no": row.get("orderNo", ""),
                    "order_time": row.get("orderTime", ""),
                    "pay_time": row.get("payTime", ""),
                    "total_fee": float(row.get("totalFee", 0) or 0),
                    "supervise_fee": float(row.get("superviseFee", 0) or 0),
                    "commission_fee": float(row.get("commissionFee", 0) or 0),
                    "school_fee": float(row.get("schoolFee", 0) or 0),
                    "registration_fee": float(row.get("registrationFee", 0) or 0),
                    "is_pay": row.get("isPay", ""),
                })
            return orders
        except Exception:
            return []

    def _check_contract_exists(self, student_id: str) -> bool:
        """快速检查学员是否有合同（不获取URL）"""
        if not student_id:
            return False
        try:
            resp = self.post(
                f"{self.base_url}/business/student/checkContract",
                data={"id": student_id},
                timeout=10,
                retries=0,
            )
            check = resp.json()
            return check.get("code") == 0
        except Exception:
            return False

    def _get_contract_info(self, student_id: str) -> dict | None:
        """获取学员合同信息（完整版，用于下载）"""
        if not student_id:
            return None

        try:
            # 1. 检查是否有合同
            resp = self.post(
                f"{self.base_url}/business/student/checkContract",
                data={"id": student_id},
                timeout=30,
                retries=0,
            )
            check = resp.json()
            if check.get("code") != 0:
                msg = check.get('msg', '')
                system_logger.info("[Driving] checkContract 返回 code=%s, msg=%s", check.get("code"), msg)
                if "可调用数为0" in msg or "调用数" in msg:
                    raise RuntimeError("API_QUOTA_EXCEEDED")
                return None

            # 2. 获取合同页面，提取PDF路径
            resp = self.get(
                f"{self.base_url}/business/student/viewContract/{student_id}",
                timeout=30,
                retries=0,
            )

            # 从HTML中提取PDF链接
            pdf_paths = re.findall(r'["\']([^"\']*\.pdf[^"\']*)["\']', resp.text)
            if not pdf_paths:
                # 尝试更宽松的正则
                pdf_paths = re.findall(r'(https?://[^"\s]+\.pdf)', resp.text)
            
            if not pdf_paths:
                system_logger.warning("[Driving] 未从HTML中提取到PDF链接，HTML长度: %d", len(resp.text))
                raise RuntimeError("CONTRACT_PAGE_ERROR")

            pdf_path = pdf_paths[0]
            pdf_url = f"{self.base_url}{pdf_path}" if pdf_path.startswith('/') else pdf_path
            system_logger.info("[Driving] 成功提取合同URL: %s", pdf_url)
            return {"code": "", "url": pdf_url}

        except RuntimeError as e:
            if str(e) == "API_QUOTA_EXCEEDED":
                raise  # 向上抛出，让 app.py 处理
            return None
        except Exception as e:
            system_logger.warning("[Driving] _get_contract_info 异常: %s", e)
            return None

    def download_contract(self, id_card: str, save_dir: str, student_name: str = "") -> str | None:
        """下载合同PDF，返回保存路径"""
        if not self.ensure_login():
            return None

        student_info = self.query_student(id_card, include_contract_check=False)
        if not student_info or not student_info.student_id:
            return None

        contract_info = self._get_contract_info(student_info.student_id)
        if not contract_info or not contract_info.get("url"):
            return None

        if contract_info.get("error"):
            return None  # error already logged

        try:
            pdf_url = contract_info["url"]
            resp = self.get(pdf_url, timeout=60, retries=0)

            if resp.status_code != 200 or len(resp.content) < 500:
                return None

            os.makedirs(save_dir, exist_ok=True)
            name = student_name or student_info.name or "未知"
            date_str = datetime.now().strftime("%Y%m%d")
            filename = f"{date_str}+{name}+{id_card}+合同.pdf"
            filepath = os.path.join(save_dir, filename)

            # 避免覆盖
            if os.path.exists(filepath):
                base, ext = os.path.splitext(filepath)
                n = 1
                while os.path.exists(f"{base}({n}){ext}"):
                    n += 1
                filepath = f"{base}({n}){ext}"

            with open(filepath, 'wb') as f:
                f.write(resp.content)

            return filepath

        except Exception:
            return None
