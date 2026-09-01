"""
第三系统爬虫 - 车尚机动车驾驶人计时培训管理平台
http://jppt.dgcheshang.cn:8899
编码: GBK
重构版 - 继承BaseCrawler，集成登录态缓存
"""
import hashlib
import re
import requests
import time
import urllib.parse
from typing import Optional
from dataclasses import dataclass, field
from bs4 import BeautifulSoup

from core.auth_manager import BaseCrawler, SystemType, LoginResult
from config import load_config


@dataclass
class ThirdRegistrationProfile:
    """第三系统「学员申请登记」模块的学员档案（/xyxx/xyxxAction!list.action）。

    与 query_student 走的「阶段审核管理」是两个不同模块，字段互补：
    - 阶段审核管理（schoolOprAction）：只有培训阶段学时/里程，**没有报名时间、手机号**。
    - 学员申请登记（xyxxAction）    ：报名时间、首次备案时间、手机号、统一编号、学员状态。

    典型场景：2017 年老学员在内部系统查无，报名日期只能从这里补。
    """
    id_card: str = ""
    name: str = ""
    phone: str = ""
    student_no: str = ""          # 统一编号（投诉系统无此字段，仅留档不落库）
    license_type: str = ""        # 培训车型
    registration_date: str = ""   # 报名时间 ← 本次要补的字段
    first_filing_date: str = ""   # 首次备案时间
    student_status: str = ""
    school_name: str = ""         # 驾校简称。注意是「」这类驾校名，
                                  # 不是投诉系统的网点代号（南/麻/栅D），不可当 school_short 用。
    branch_code: str = ""         # 分点号（学员详情页）。「南/麻/栅D」这类网点代号，
                                  # 与投诉系统 school_short 同口径，可作报名点。
    branch_name: str = ""         # 分点名称（学员详情页），如「…东城同沙招生点」。
    fid: str = ""                 # 学员详情页 fid（findStudentDetailPage.action?fid=…）


@dataclass
class ThirdStudentInfo:
    """第三系统学员信息"""
    name: str = ""
    id_card: str = ""
    license_type: str = ""
    stages: list = field(default_factory=list)  # 各阶段培训记录
    # 学员申请登记档案（报名时间/手机号等）。仅在内部系统信息不全时才补查，通常为 None。
    registration_profile: Optional[ThirdRegistrationProfile] = None


@dataclass
class ThirdNameSearchResult:
    """按姓名查询第三系统的结果（只解析身份证号 + 姓名）。"""
    candidates: list = field(default_factory=list)  # 去重后的 ThirdStudentInfo
    truncated: bool = False   # 翻页达安全上限仍未取完时置真（正常同名不会触发）
    data_rows: int = 0        # 命中的阶段数据行数（同一学员占 1~4 行）


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
    SESSION_MAX_AGE_SECONDS = 25 * 60

    # pagesize 服务端硬上限 10：pagesize=20/50/100 都只返回 10 行，故固定 pagesize=10。
    # 翻页走 URL query string 的 `currentpage`（页码）+ `pager.offset`，不是 POST body 的
    # `pageNumber`（2026-08-31 真机复核：此前误判为「无分页」，实际可翻页取全同名学员）。
    NAME_SEARCH_ROW_CAP = 10
    # 同名学员极多时翻页安全上限，防止无限翻页（正常同名远小于此）。
    NAME_SEARCH_MAX_PAGES = 50

    # ── 学员申请登记模块（与「阶段审核管理」互补，提供报名时间 / 手机号）──
    REGISTRATION_URL = "/xyxx/xyxxAction!list.action"
    # 学员详情页：列表页行内自带 fid 链接，GET 即可访问（带登录态）。
    # 详情页有「分点号」（网点代号，南/麻/栅D口径）等列表页没有的字段。
    DETAIL_URL = "/xyxx/xyxxAction!findStudentDetailPage.action"
    # 该模块表头 31 列、数据行 21 列（服务端把空列整列省略），靠列数区分两者。
    REGISTRATION_ROW_CELLS = 21
    REGISTRATION_COLS = {
        "seq": 1, "school_name": 2, "id_type": 3, "id_card": 4, "name": 5,
        "phone": 6, "student_no": 7, "business_type": 8, "license_type": 9,
        "upload_status": 10, "filing_status": 11, "filing_modified_at": 12,
        "first_filing_date": 13, "registration_date": 14, "student_status": 15,
        "learning_proof": 16, "banned": 17, "remark": 18, "source": 19, "action": 20,
    }

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
        if _retry == 0:
            self._reset_query_metrics()
        auth_started = time.perf_counter()
        logged_in = self.ensure_login()
        self._record_query_phase("auth_wait", (time.perf_counter() - auth_started) * 1000)
        if not logged_in:
            # 登录失败属于系统异常，不能返回 None —— 那会被上游 _query_third 记为
            # not_found「明确查无」，进而错误放行人工建案。抛出后转成 ERROR 状态。
            raise RuntimeError("第三系统登录失败")

        lookup_started = time.perf_counter()
        lookup_recorded = False
        try:
            resp = self.post(
                f"{self.base_url}/school/schoolOprAction!xsjdshList.action",
                data={"xyxshzVo.sfzmhm": id_card, "pageNumber": 1, "pagesize": 10},
                timeout=15,
            )
            self._record_query_phase(
                "lookup", (time.perf_counter() - lookup_started) * 1000
            )
            lookup_recorded = True

            if resp.status_code in (401, 403) or "login!login.action" in getattr(resp, "url", ""):
                if _retry == 0:
                    self._increment_query_retry()
                    self.logout()
                    if self.ensure_login():
                        return self.query_student(id_card, _retry=1)
                return None
            if resp.status_code != 200 or id_card not in resp.text:
                return None

            return self._parse_student_page(resp.text, id_card)
            
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status in (401, 403) and _retry == 0:
                self._increment_query_retry()
                self.logout()
                if self.ensure_login():
                    return self.query_student(id_card, _retry=1)
            raise
        finally:
            if not lookup_recorded:
                self._record_query_phase(
                    "lookup", (time.perf_counter() - lookup_started) * 1000
                )

    def search_by_name(self, name: str, _retry: int = 0) -> ThirdNameSearchResult:
        """按姓名查询第三系统候选学员。只解析「身份证号 + 姓名」。

        实测结论（2026-08-31 真机复核，勿凭直觉改动）：
        - 表单字段 xyxshzVo.xm 是**全名精确匹配**，不是前缀匹配也不是包含匹配：
          「测试学员甲」命中本人 1 人；「张振」命中的是 3 名真实姓名就叫「张振」的学员
          （已用身份证反查复核，姓名列即为全名）；「张」与「振苹」均 0 命中。
        - 必须以 GBK 编码提交（响应头 charset=GBK）。UTF-8 提交恒 0 命中且不报错，
          是静默失败，排查时极易误判为「系统里没有这个人」。
        - **有分页**：翻页参数 `currentpage`（页码）+ `pagesize` 走 **URL query string**
          （前端 op_Linked 拼到 forms[0].action 后 submit），不是 POST body 的 `pageNumber`。
          pagesize 服务端硬上限 10，故固定 10、靠 currentpage 翻页取全同名学员。
          （此前误判为「无分页」，只拿到第一页 10 条 → 同名 >10 人被截断。）

        按业务口径（合同与收费来自东莞驾培、实操学时取审核有效学时），
        这里**不解析培训阶段的学时/里程**，也不解析任何缴费字段。
        """
        name = (name or "").strip()
        if len(name) < 2:
            return ThirdNameSearchResult()

        if not self.ensure_login():
            # 与 query_student 同口径：登录失败必须抛异常，让上游记为 error
            # 而不是 not_found，否则会被误当作「明确查无此人」放行人工建案。
            raise RuntimeError("第三系统登录失败")

        url = f"{self.base_url}/school/schoolOprAction!xsjdshList.action"
        body = {"xyxshzVo.xm": name}  # 查询条件走 POST body

        ordered: list[ThirdStudentInfo] = []
        seen: set[str] = set()
        total_records = 0
        total_pages = 1

        for page in range(1, self.NAME_SEARCH_MAX_PAGES + 1):
            qs_url = f"{url}?currentpage={page}&pagesize={self.NAME_SEARCH_ROW_CAP}"
            resp = self.post(
                qs_url,
                data=urllib.parse.urlencode(body, encoding="gbk").encode("gbk"),
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=GBK"},
                timeout=15,
            )

            if resp.status_code in (401, 403) or "login!login.action" in getattr(resp, "url", ""):
                if _retry == 0:
                    self._increment_query_retry()
                    self.logout()
                    if self.ensure_login():
                        return self.search_by_name(name, _retry=1)
                return ThirdNameSearchResult()
            if resp.status_code != 200:
                return ThirdNameSearchResult()

            result = self._parse_name_candidates(resp.text, name)
            # 首页从分页条取「共 X 条记录 / Y 页」，作为翻页终止依据。
            if page == 1:
                total_records, total_pages = self._parse_page_meta(resp.text)
            for c in result.candidates:
                if c.id_card not in seen:
                    seen.add(c.id_card)
                    ordered.append(c)
            if page >= total_pages:
                break

        return ThirdNameSearchResult(
            candidates=ordered,
            truncated=total_pages > self.NAME_SEARCH_MAX_PAGES,
            data_rows=total_records,
        )

    def fetch_registration_profile(self, id_card: str, _retry: int = 0) -> Optional[ThirdRegistrationProfile]:
        """抓取学员档案（报名时间 / 手机号 / 学员状态）。

        与 query_student 不同模块：阶段审核管理只有学时里程，报名日期要到
        「学员申请登记」取。内部系统查无的老学员（如测试学员甲 2017 年报名）靠这里补。

        实测要点（2026-08-30）：
        - 表单同样必须 GBK 提交，UTF-8 静默 0 命中。
        - 同一学员在本模块只有 1 行（不像阶段审核按 4 个阶段拆 4 行）。
        """
        id_card = (id_card or "").strip()
        if not id_card:
            return None
        if not self.ensure_login():
            # 与 query_student 同口径：登录失败必须抛，让上游记为 error 而非 not_found
            raise RuntimeError("第三系统登录失败")

        payload = {"ksyyXyxx.sfzmhm": id_card}
        resp = self.post(
            f"{self.base_url}{self.REGISTRATION_URL}",
            data=urllib.parse.urlencode(payload, encoding="gbk").encode("gbk"),
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=GBK"},
            timeout=15,
        )

        if resp.status_code in (401, 403) or "login!login.action" in getattr(resp, "url", ""):
            if _retry == 0:
                self.logout()
                if self.ensure_login():
                    return self.fetch_registration_profile(id_card, _retry=1)
            return None
        if resp.status_code != 200:
            return None

        profile = self._parse_registration_page(resp.text, id_card)
        if profile is None:
            return None

        # 列表页行内自带 fid 链接 → 顺势 GET 学员详情页补「分点号」（报名点）。
        # 详情页失败只降级（branch_code 留空），不影响档案主体返回。
        fid = self._extract_detail_fid(resp.text)
        if fid:
            profile.fid = fid
            try:
                code, name = self._fetch_branch_info(fid, _retry=0)
                profile.branch_code = code
                profile.branch_name = name
            except Exception:
                pass  # 详情页仅是补充信息，任何异常都静默降级（branch_code 留空）
        return profile

    @staticmethod
    def _extract_detail_fid(html: str) -> str:
        """从学员申请登记列表页 HTML 中提取学员详情页 fid。

        列表按证件号查询，整页只会有该学员一行的 fid（2026-08-30 实测）。
        """
        m = re.search(r"findStudentDetailPage\.action\?fid=([0-9a-fA-F]{16,})", html or "")
        return m.group(1) if m else ""

    def _fetch_branch_info(self, fid: str, _retry: int = 0) -> tuple[str, str]:
        """GET 学员详情页，返回 (分点号, 分点名称)。登录失效时重登重试一次。"""
        resp = self.http.get(
            f"{self.base_url}{self.DETAIL_URL}",
            params={"fid": fid, "isDetail": "Y", "isOK": "N"},
            timeout=15,
        )
        if resp.status_code in (401, 403) or "login!login.action" in getattr(resp, "url", ""):
            if _retry == 0:
                self.logout()
                if self.ensure_login():
                    return self._fetch_branch_info(fid, _retry=1)
            return "", ""
        if resp.status_code != 200:
            return "", ""
        return self._parse_detail_branch(resp.text)

    @staticmethod
    def _parse_detail_branch(html: str) -> tuple[str, str]:
        """解析详情页「标签：值」表格，取分点号 / 分点名称。

        实测结构（td 序）：… ['发卡日期 / IC卡编号：', '…', '分点号：', '南'] …
        """
        soup = BeautifulSoup(html, "html.parser")
        code = name = ""
        for row in soup.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            for i, text in enumerate(cells):
                label = (text or "").rstrip("：: ").strip()
                if i + 1 >= len(cells):
                    continue
                if label == "分点号" and not code:
                    code = cells[i + 1].strip()
                elif label == "分点名称" and not name:
                    name = cells[i + 1].strip()
        return code, name

    def _parse_registration_page(self, html: str, id_card: str) -> Optional[ThirdRegistrationProfile]:
        """解析学员申请登记列表。数据行固定 21 列、表头 31 列，按列数区分。"""
        soup = BeautifulSoup(html, "html.parser")
        col = self.REGISTRATION_COLS
        for row in soup.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cells) != self.REGISTRATION_ROW_CELLS:
                continue
            if not cells[col["seq"]].isdigit():   # 第 2 列是序号，表头行不是数字
                continue
            doc_no = self._extract_id_card(cells[col["id_card"]])
            if doc_no and id_card and doc_no != id_card.upper():
                continue                          # 保险起见按证件号再核一次
            return ThirdRegistrationProfile(
                id_card=doc_no or id_card,
                name=cells[col["name"]],
                phone=cells[col["phone"]],
                student_no=cells[col["student_no"]],
                license_type=cells[col["license_type"]],
                registration_date=cells[col["registration_date"]],
                first_filing_date=cells[col["first_filing_date"]],
                student_status=cells[col["student_status"]],
                school_name=cells[col["school_name"]],
            )
        return None

    @staticmethod
    def _extract_id_card(blob: str) -> str:
        """从「身份证 + 学员编号 + 车型 + 学时」混合单元格中取出 18 位身份证号。

        实际样例：'110101199003070011S154544001326634C1129时25分0公里...'
        """
        m = re.search(r"\d{17}[\dXx]", blob or "")
        return m.group(0).upper() if m else ""

    def _parse_name_candidates(self, html: str, name: str) -> ThirdNameSearchResult:
        """解析单页姓名查询结果：按身份证去重，返回候选列表。

        只负责单页解析；翻页取全与最终 truncated 判断由 search_by_name 统一处理。
        """
        soup = BeautifulSoup(html, "html.parser")
        rows = 0
        ordered: list[ThirdStudentInfo] = []
        seen: set[str] = set()

        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 6:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            id_card = self._extract_id_card(texts[2] if len(texts) > 2 else "")
            if not id_card:
                continue
            rows += 1
            if id_card in seen:
                continue
            seen.add(id_card)
            ordered.append(
                ThirdStudentInfo(
                    id_card=id_card,
                    name=(texts[1] if len(texts) > 1 else "") or name,
                )
            )

        return ThirdNameSearchResult(
            candidates=ordered,
            truncated=False,
            data_rows=rows,
        )

    @staticmethod
    def _parse_page_meta(html: str) -> tuple[int, int]:
        """从分页条提取 (总记录数, 总页数)。

        分页条形如：
          共<span class="td10">56</span>条记录&nbsp;<span class="td10">6</span>页…
        提取失败时回退 (0, 1)（按只有一页处理）。
        """
        m = re.search(r'共[^>]*>(\d+)</span>条记录[^>]*>(\d+)</span>页', html or "")
        if not m:
            return 0, 1
        return int(m.group(1)), int(m.group(2))

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
