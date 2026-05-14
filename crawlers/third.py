"""
第三系统爬虫 - 车尚机动车驾驶人计时培训管理平台
http://jppt.dgcheshang.cn:8899
编码: GBK
重构版 - 继承BaseCrawler，集成登录态缓存
"""
import hashlib
import re
import time
import urllib.parse
from typing import Optional
from dataclasses import dataclass, field
from bs4 import BeautifulSoup

from core.auth_manager import BaseCrawler, SystemType, LoginResult
from config import load_config


@dataclass
class ThirdStudentInfo:
    """第三系统学员信息"""
    name: str = ""
    id_card: str = ""
    license_type: str = ""
    stages: list = field(default_factory=list)  # 各阶段培训记录


@dataclass
class TrainingStage:
    """培训阶段记录"""
    stage_no: int = 0           # 培训部分 1/2/3/4
    stage_name: str = ""        # 第一部分/第二部分...
    training_time: str = ""     # 平台总学时（第6列）
    training_km: str = ""       # 培训里程
    platform_time: str = ""     # 上传省平台总学时（第8列）
    platform_km: str = ""       # 平台里程
    audit_time: str = ""        # 审核有效总学时（第10列）← 扣费计算用这个


class ThirdCrawler(BaseCrawler):
    """第三系统爬虫（重构版）"""

    STAGE_NAMES = {1: "第一部分(科目一)", 2: "第二部分(科目二)", 3: "第三部分(科目三)", 4: "第四部分(科目四)"}

    def __init__(self):
        cfg = load_config()["third_system"]
        base_url = cfg["base_url"].rstrip("/")
        username = cfg["username"]
        password = cfg["password"]
        
        super().__init__(SystemType.THIRD, username, password)
        self.base_url = base_url
    
    def _get_ocr(self):
        """使用全局共享 OCR 引擎"""
        from core.ocr_engine import get_ocr
        return get_ocr()

    def _do_login(self) -> LoginResult:
        """执行实际登录逻辑"""
        start_time = time.time()
        
        try:
            # 1. 访问登录页
            self.http.get(f"{self.base_url}/platform/login!login.action", timeout=10)
            
            # 2. 获取验证码
            img = self.http.get(f"{self.base_url}/servlet/validate_image", timeout=10).content
            captcha = self._get_ocr().classification(img)
            
            # 3. 计算密码MD5
            pwd_md5 = hashlib.md5(self.password.encode()).hexdigest()
            
            # 4. 登录请求
            resp = self.http.post(
                f"{self.base_url}/platform/login!login.action",
                data={
                    "loginId": self.username,
                    "clear_password": self.password,
                    "password": pwd_md5,
                    "rand": captcha,
                    "loginType": "3",
                    "customstyle": "blue",
                },
                allow_redirects=True,
                timeout=10,
            )
            
            # frameset 页面表示登录成功
            if "frameset" in resp.text or "mainframe" in resp.text:
                duration = int((time.time() - start_time) * 1000)
                return LoginResult(True, "登录成功", duration_ms=duration)
            else:
                return LoginResult(False, "登录失败：页面未跳转到主框架")
                
        except Exception as e:
            return LoginResult(False, f"登录异常: {str(e)}")

    def query_student(self, id_card: str, _retry: int = 0) -> Optional[ThirdStudentInfo]:
        """查询阶段审核管理"""
        if not self.ensure_login():
            return None

        try:
            resp = self.post(
                f"{self.base_url}/school/schoolOprAction!xsjdshList.action",
                data={"xyxshzVo.sfzmhm": id_card, "pageNumber": 1, "pagesize": 10},
                timeout=15,
            )

            if resp.status_code != 200 or id_card not in resp.text:
                if _retry == 0:
                    self.logout()
                    if self.ensure_login():
                        return self.query_student(id_card, _retry=1)
                return None

            return self._parse_student_page(resp.text, id_card)
            
        except Exception as e:
            # 仅当是登录态过期导致的失败时才重试一次
            if _retry == 0:
                self.logout()
                if self.ensure_login():
                    return self.query_student(id_card, _retry=1)
            raise

    def _parse_student_page(self, html: str, id_card: str) -> Optional[ThirdStudentInfo]:
        """解析阶段审核管理页面"""
        soup = BeautifulSoup(html, "html.parser")
        info = ThirdStudentInfo(id_card=id_card)

        rows = soup.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 6:
                continue

            texts = [c.get_text(strip=True) for c in cells]
            row_text = "".join(texts)

            if id_card not in row_text:
                continue

            # 提取姓名
            if not info.name:
                for text in texts:
                    if text and id_card not in text and text not in ("", "C1", "C2"):
                        if len(text) <= 5 and not text.isdigit():
                            info.name = text
                            break

            # 提取车型
            for text in texts:
                if text in ("C1", "C2", "C3", "A1", "A2", "B1", "B2"):
                    info.license_type = text
                    break

            # 提取培训阶段信息
            stage = self._parse_stage_row(texts)
            if stage:
                info.stages.append(stage)

        return info if info.name or info.stages else None

    def _parse_stage_row(self, texts: list[str]) -> Optional[TrainingStage]:
        """解析单行阶段数据 - 根据实际HTML结构调整"""
        stage = TrainingStage()
        
        # 实际HTML结构（从调试输出看到）：
        # [0] ''
        # [1] '张晶晶' (姓名)
        # [2] '110101199003070011S171393691510281C2215时49分24公里...' (混合数据)
        # [3] 同上混合数据
        # [4] 'C2' (车型)
        # [5] '2' (阶段编号)
        # [6] '15时49分' (培训时间)
        # [7] '24公里' (培训里程)
        # [8] '13时4分' (平台学时/上传省平台学时)
        # [9] '24公里' (平台里程)
        # [10] '13时4分' (审核有效学时)
        # [11] '24公里' 
        
        for i, text in enumerate(texts):
            # 提取阶段编号（第5列）
            if i == 5 and text.isdigit() and int(text) in (1, 2, 3, 4):
                stage.stage_no = int(text)
                stage.stage_name = self.STAGE_NAMES.get(int(text), f"第{text}部分")

            # 提取培训时间（第6列）- 这是主要显示的学时
            if i == 6:
                time_match = re.search(r"(\d+时\d+分)", text)
                if time_match:
                    stage.training_time = time_match.group(1)

            # 提取培训里程（第7列）
            if i == 7:
                km_match = re.search(r"(\d+公里)", text)
                if km_match:
                    stage.training_km = km_match.group(1)
                    
            # 提取平台学时（第8列）
            if i == 8:
                time_match = re.search(r"(\d+时\d+分)", text)
                if time_match:
                    stage.platform_time = time_match.group(1)
                    
            # 提取平台里程（第9列）
            if i == 9:
                km_match = re.search(r"(\d+公里)", text)
                if km_match:
                    stage.platform_km = km_match.group(1)

            # 提取审核有效总学时（第10列）- 这才是最终有效学时，用于扣费计算
            if i == 10:
                time_match = re.search(r"(\d+时\d+分)", text)
                if time_match:
                    stage.audit_time = time_match.group(1)

        return stage if stage.stage_no > 0 else None
