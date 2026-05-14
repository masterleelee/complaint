"""
东莞驾培系统爬虫 - 管家鞋驾培平台
https://www.guanjiaxie.com:8089
使用 ddddocr + 智能重试策略识别算术验证码
重构版 - 继承BaseCrawler，集成登录态缓存
"""
import os
import re
import random
import time
from typing import Optional
from dataclasses import dataclass
from datetime import datetime

from core.auth_manager import BaseCrawler, SystemType, LoginResult
from config import load_config


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


class DrivingCrawler(BaseCrawler):
    """东莞驾培系统爬虫（重构版）"""

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

    def _solve_captcha(self, img: bytes) -> list[int]:
        """
        解析算术验证码，利用固定结构 [数字][运算符][数字]=? 精确计算
        策略：先尝试识别运算符直接计算，失败则穷举
        """
        try:
            ocr = self._get_ocr()
            text = ocr.classification(img)
            
            # 清理文本
            text_clean = text.replace(' ', '').replace('?', '').replace('=', '').lower()
            
            # 提取所有数字（0-9）
            digits = [int(c) for c in text_clean if c.isdigit()]
            if len(digits) < 2:
                return []
            
            # 算术验证码结构固定: [数字][运算符][数字]
            a, b = digits[0], digits[1]
            
            # 识别运算符（按优先级）
            op = None
            if any(c in text_clean for c in ['+', '＋', '十']):
                op = '+'
            elif any(c in text_clean for c in ['-', '−', '–', '—']):
                op = '-'
            elif any(c in text_clean for c in ['×', '*', 'x']):
                op = '*'
            elif any(c in text_clean for c in ['÷', '/']):
                op = '/'
            
            # 如果识别到运算符，直接计算返回
            if op:
                if op == '+':
                    return [a + b]
                elif op == '-':
                    return [a - b]
                elif op == '*':
                    return [a * b]
                elif op == '/' and b != 0 and a % b == 0:
                    return [a // b]
            
            # 无法识别运算符，穷举常见情况
            results = [a + b, a - b, a * b]
            if b != 0 and a % b == 0:
                results.append(a // b)
            
            # 过滤并去重
            seen = set()
            unique_results = []
            for r in results:
                if 0 <= r <= 81 and r not in seen:
                    seen.add(r)
                    unique_results.append(r)
            
            return unique_results
        except Exception:
            return []

    def _do_login(self) -> LoginResult:
        """执行实际登录逻辑"""
        start_time = time.time()
        max_retries = 20
        
        try:
            for attempt in range(1, max_retries + 1):
                try:
                    # 1. 访问登录页
                    self.http.get(f"{self.base_url}/schoolLogin", timeout=30)
                    
                    # 2. 获取验证码
                    img = self.http.get(
                        f"{self.base_url}/captcha/captchaImage?type=math&s={random.random()}",
                        timeout=30,
                    ).content
                    
                    if len(img) < 100:
                        continue
                    
                    # 3. 解析验证码
                    possible_answers = self._solve_captcha(img)
                    if not possible_answers:
                        continue
                    
                    # 4. 尝试每个可能的答案
                    for answer in possible_answers:
                        resp = self.http.post(
                            f"{self.base_url}/schoolLogin",
                            data={
                                "schoolType": "1",
                                "username": self.username,
                                "password": self.password,
                                "validateCode": str(answer),
                                "rememberMe": "false",
                            },
                            timeout=30,
                        )
                        
                        result = resp.json()
                        if result.get("code") == 0:
                            duration = int((time.time() - start_time) * 1000)
                            return LoginResult(True, "登录成功", duration_ms=duration)
                            
                except Exception:
                    time.sleep(0.5)
                    continue
            
            return LoginResult(False, f"登录失败，已尝试{max_retries}次")
            
        except Exception as e:
            return LoginResult(False, f"登录异常: {str(e)}")

    def query_student(self, id_card: str, _retry: int = 0) -> Optional[DrivingStudentInfo]:
        """查询学员信息"""
        if not self.ensure_login():
            return None

        try:
            resp = self.post(
                f"{self.base_url}/business/student/list",
                data={"pageNum": 1, "pageSize": 10, "identity": id_card},
                timeout=30,
            )
            data = resp.json()

            if data.get("code") != 0:
                if _retry == 0:
                    self.logout()
                    self.login()
                    return self.query_student(id_card, _retry=1)
                return None

            rows = data.get("rows", [])
            if not rows:
                # 空结果 + 未重试过 → 可能 session 过期，强制重新登录后重试
                if _retry == 0:
                    self.logout()
                    self.login()
                    return self.query_student(id_card, _retry=1)
                return None

            # 精确匹配身份证号
            student = None
            for row in rows:
                if row.get("identity", "").upper() == id_card.upper():
                    student = row
                    break
            if not student:
                student = rows[0]

            info = DrivingStudentInfo(
                name=student.get("name", ""),
                id_card=id_card,
                phone=student.get("mobile", ""),
                license_type=student.get("licenseType", ""),
                registration_date=student.get("createTime", ""),
                student_id=str(student.get("id", "")),
            )

            # 检查合同（快速模式：只检查是否有合同，不获取URL）
            if info.student_id:
                info.contract_available = self._check_contract_exists(info.student_id)

            return info
            
        except Exception as e:
            # 仅当是登录态过期导致的失败时才重试一次
            if _retry == 0:
                self.logout()
                if self.ensure_login():
                    return self.query_student(id_card, _retry=1)
            raise

    def _check_contract_exists(self, student_id: str) -> bool:
        """快速检查学员是否有合同（不获取URL）"""
        if not student_id:
            return False
        try:
            resp = self.post(
                f"{self.base_url}/business/student/checkContract",
                data={"id": student_id},
                timeout=10,
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
            )
            check = resp.json()
            if check.get("code") != 0:
                msg = check.get('msg', '')
                print(f"[Driving] checkContract 返回 code={check.get('code')}, msg={msg}")
                if "可调用数为0" in msg or "调用数" in msg:
                    raise RuntimeError("API_QUOTA_EXCEEDED")
                return None

            # 2. 获取合同页面，提取PDF路径
            resp = self.get(
                f"{self.base_url}/business/student/viewContract/{student_id}",
                timeout=30,
            )

            # 从HTML中提取PDF链接
            pdf_paths = re.findall(r'["\']([^"\']*\.pdf[^"\']*)["\']', resp.text)
            if not pdf_paths:
                # 尝试更宽松的正则
                pdf_paths = re.findall(r'(https?://[^"\s]+\.pdf)', resp.text)
            
            if not pdf_paths:
                print(f"[Driving] 未从HTML中提取到PDF链接，HTML长度: {len(resp.text)}")
                raise RuntimeError("CONTRACT_PAGE_ERROR")

            pdf_path = pdf_paths[0]
            pdf_url = f"{self.base_url}{pdf_path}" if pdf_path.startswith('/') else pdf_path
            print(f"[Driving] 成功提取合同URL: {pdf_url}")
            return {"code": "", "url": pdf_url}

        except RuntimeError as e:
            if str(e) == "API_QUOTA_EXCEEDED":
                raise  # 向上抛出，让 app.py 处理
            return None
        except Exception as e:
            print(f"[Driving] _get_contract_info 异常: {e}")
            return None

    def download_contract(self, id_card: str, save_dir: str, student_name: str = "") -> str | None:
        """下载合同PDF，返回保存路径"""
        if not self.ensure_login():
            return None

        student_info = self.query_student(id_card)
        if not student_info or not student_info.student_id:
            return None

        contract_info = self._get_contract_info(student_info.student_id)
        if not contract_info or not contract_info.get("url"):
            return None

        if contract_info.get("error"):
            return None  # error already logged

        try:
            pdf_url = contract_info["url"]
            resp = self.get(pdf_url, timeout=60)

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
