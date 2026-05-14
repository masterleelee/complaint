"""
异步并行查询引擎
支持并发查询多个系统，自动处理登录态，统一结果合并
"""
import asyncio
import time
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from concurrent.futures import ThreadPoolExecutor

from core.auth_manager import AuthManager, SystemType, auth_manager
from crawlers.internal import InternalCrawler, InternalStudentInfo
from crawlers.third import ThirdCrawler, ThirdStudentInfo
from crawlers.driving import DrivingCrawler, DrivingStudentInfo


class QueryStatus(Enum):
    """查询状态"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass
class QueryResult:
    """单个系统查询结果"""
    system: str
    status: QueryStatus
    data: Any = None
    error: str = ""
    duration_ms: int = 0


@dataclass
class MergedStudentInfo:
    """合并后的学员信息"""
    # 基本信息
    name: str = ""
    id_card: str = ""
    phone: str = ""
    license_type: str = ""
    registration_date: str = ""
    
    # 驾校信息
    school_name: str = ""
    school_short: str = ""
    
    # 学习状态
    student_status: str = ""
    exam_stage: str = ""
    exam_counts: Dict = field(default_factory=dict)
    
    # 培训信息
    training_hours: Dict[str, str] = field(default_factory=dict)
    training_details: list = field(default_factory=list)
    
    # 费用信息
    fees: list = field(default_factory=list)
    timeline: list = field(default_factory=list)
    
    # 合同信息
    contract_available: bool = False
    contract_code: str = ""
    contract_url: str = ""
    
    # 查询来源状态
    sources: Dict[str, str] = field(default_factory=dict)
    
    # 时间轴展示
    timeline_display: list = field(default_factory=list)
    
    # 合规状态提醒
    contract_status: str = ""   # "valid" | "expired"
    skill_cert_status: str = "" # "valid" | "expired" | "no_subject1"
    skill_cert_date: str = ""   # 科目一通过日期（技能证起始日期）


class QueryEngine:
    """
    异步并行查询引擎
    
    特性：
    1. 并发查询多个系统
    2. 自动登录态管理
    3. 统一结果合并
    4. 超时控制
    5. 错误处理和重试
    """
    
    def __init__(self, auth_mgr: AuthManager = None, max_workers: int = 3):
        self.auth_manager = auth_mgr or auth_manager
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._crawlers: Dict[SystemType, Any] = {}
        self._query_cache: Dict[str, tuple] = {}  # key -> (timestamp, result)
        self._cache_ttl = 300  # 5分钟缓存
        self._init_crawlers()
    
    def _init_crawlers(self):
        """初始化爬虫实例并注册到认证管理器"""
        # 内部系统
        internal = InternalCrawler()
        self._crawlers[SystemType.INTERNAL] = internal
        self.auth_manager.register(internal)
        
        # 第三系统
        third = ThirdCrawler()
        self._crawlers[SystemType.THIRD] = third
        self.auth_manager.register(third)

        # 东莞驾培
        driving = DrivingCrawler()
        self._crawlers[SystemType.DRIVING] = driving
        self.auth_manager.register(driving)

    def _get_internal(self) -> InternalCrawler:
        return self._crawlers[SystemType.INTERNAL]
    
    def _get_third(self) -> ThirdCrawler:
        return self._crawlers[SystemType.THIRD]
    
    def _get_driving(self) -> DrivingCrawler:
        return self._crawlers[SystemType.DRIVING]
    
    async def query_all(self, id_card: str, timeout: float = 60.0) -> MergedStudentInfo:
        """
        并行查询所有系统（带结果缓存）
        
        Args:
            id_card: 身份证号
            timeout: 总超时时间（秒）
            
        Returns:
            合并后的学员信息
        """
        # 检查缓存
        cache_key = f"query:{id_card}"
        cached = self._query_cache.get(cache_key)
        if cached:
            ts, result = cached
            if time.time() - ts < self._cache_ttl:
                return result  # 直接返回缓存结果
            else:
                del self._query_cache[cache_key]  # 过期清除

        loop = asyncio.get_event_loop()
        
        # 创建查询任务
        tasks = {
            SystemType.INTERNAL: loop.run_in_executor(
                self._executor, self._query_internal, id_card
            ),
            SystemType.THIRD: loop.run_in_executor(
                self._executor, self._query_third, id_card
            ),
            SystemType.DRIVING: loop.run_in_executor(
                self._executor, self._query_driving, id_card
            ),
        }
        
        # 等待所有任务完成（带超时）
        results = {}
        start_time = time.time()
        
        for system_type, task in tasks.items():
            remaining = timeout - (time.time() - start_time)
            if remaining <= 0:
                results[system_type] = QueryResult(
                    system=system_type.value,
                    status=QueryStatus.TIMEOUT,
                    error="查询超时"
                )
                continue
            
            try:
                result = await asyncio.wait_for(task, timeout=remaining)
                results[system_type] = result
            except asyncio.TimeoutError:
                results[system_type] = QueryResult(
                    system=system_type.value,
                    status=QueryStatus.TIMEOUT,
                    error="查询超时"
                )
            except Exception as e:
                results[system_type] = QueryResult(
                    system=system_type.value,
                    status=QueryStatus.ERROR,
                    error=str(e)
                )
        
        # 合并结果
        merged = self._merge_results(id_card, results)
        
        # 写入缓存（仅当查到学员信息时）
        if merged.name:
            self._query_cache[f"query:{id_card}"] = (time.time(), merged)
        
        return merged
    
    def _query_internal(self, id_card: str) -> QueryResult:
        """查询内部系统"""
        start_time = time.time()
        try:
            crawler = self._get_internal()
            result = crawler.query_student(id_card)
            duration = int((time.time() - start_time) * 1000)
            
            if result:
                return QueryResult(
                    system="internal",
                    status=QueryStatus.SUCCESS,
                    data=result,
                    duration_ms=duration
                )
            else:
                return QueryResult(
                    system="internal",
                    status=QueryStatus.NOT_FOUND,
                    duration_ms=duration
                )
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            return QueryResult(
                system="internal",
                status=QueryStatus.ERROR,
                error=str(e),
                duration_ms=duration
            )
    
    def _query_third(self, id_card: str) -> QueryResult:
        """查询第三系统"""
        start_time = time.time()
        try:
            crawler = self._get_third()
            result = crawler.query_student(id_card)
            duration = int((time.time() - start_time) * 1000)
            
            if result:
                return QueryResult(
                    system="third",
                    status=QueryStatus.SUCCESS,
                    data=result,
                    duration_ms=duration
                )
            else:
                return QueryResult(
                    system="third",
                    status=QueryStatus.NOT_FOUND,
                    duration_ms=duration
                )
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            return QueryResult(
                system="third",
                status=QueryStatus.ERROR,
                error=str(e),
                duration_ms=duration
            )
    
    def _query_driving(self, id_card: str) -> QueryResult:
        """查询东莞驾培系统"""
        start_time = time.time()
        try:
            crawler = self._get_driving()
            result = crawler.query_student(id_card)
            duration = int((time.time() - start_time) * 1000)
            
            if result:
                return QueryResult(
                    system="driving",
                    status=QueryStatus.SUCCESS,
                    data=result,
                    duration_ms=duration
                )
            else:
                return QueryResult(
                    system="driving",
                    status=QueryStatus.NOT_FOUND,
                    duration_ms=duration
                )
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            return QueryResult(
                system="driving",
                status=QueryStatus.ERROR,
                error=str(e),
                duration_ms=duration
            )
    
    def _merge_results(self, id_card: str, results: Dict[SystemType, QueryResult]) -> MergedStudentInfo:
        """合并三系统查询结果"""
        internal_result = results.get(SystemType.INTERNAL)
        third_result = results.get(SystemType.THIRD)
        driving_result = results.get(SystemType.DRIVING)
        
        internal: Optional[InternalStudentInfo] = internal_result.data if internal_result and internal_result.status == QueryStatus.SUCCESS else None
        third: Optional[ThirdStudentInfo] = third_result.data if third_result and third_result.status == QueryStatus.SUCCESS else None
        driving: Optional[DrivingStudentInfo] = driving_result.data if driving_result and driving_result.status == QueryStatus.SUCCESS else None
        
        info = MergedStudentInfo()
        info.id_card = id_card
        
        # 来源状态
        info.sources = {
            "internal": results.get(SystemType.INTERNAL, QueryResult("internal", QueryStatus.ERROR)).status.value,
            "third": results.get(SystemType.THIRD, QueryResult("third", QueryStatus.ERROR)).status.value,
            "driving": results.get(SystemType.DRIVING, QueryResult("driving", QueryStatus.ERROR)).status.value,
        }
        
        # 基本信息优先级：内部系统 > 东莞驾培 > 第三系统
        if internal:
            info.name = internal.name
            info.phone = internal.phone
            info.license_type = internal.license_type
            info.registration_date = internal.registration_date
            info.school_name = internal.school_name
            info.school_short = internal.school_short
            info.student_status = internal.student_status
            info.exam_stage = internal.exam_stage
            info.exam_counts = internal.exam_counts
            info.fees = internal.fees
            info.timeline = internal.timeline
        
        if driving and not info.name:
            info.name = driving.name
        if driving and not info.phone:
            info.phone = driving.phone
        if driving and not info.license_type:
            info.license_type = driving.license_type
        if driving and not info.registration_date:
            info.registration_date = driving.registration_date
        
        if third and not info.name:
            info.name = third.name
        if third and not info.license_type:
            info.license_type = third.license_type
        
        # 培训时长（来自第三系统）
        if third and third.stages:
            stage_map = {1: "科目一", 2: "科目二", 3: "科目三", 4: "科目四"}
            for stage in third.stages:
                subject = stage_map.get(stage.stage_no, f"阶段{stage.stage_no}")
                # 优先用"审核有效总学时"，其次"平台总学时"，最后"培训时间"
                display_time = stage.audit_time or stage.platform_time or stage.training_time or "0时0分"
                info.training_hours[subject] = display_time

                # 如果内部没提供考试阶段但有培训数据，用培训最多的阶段推断
                if stage.training_time and stage.training_time not in ("0时0分", "-"):
                    if not info.exam_stage or info.exam_stage in ("未知", "报名", ""):
                        for s_num, s_name in [
                            (4, "科目四"), (3, "科目三"), (2, "科目二"), (1, "科目一")
                        ]:
                            if stage.stage_no >= s_num and display_time not in ("0时0分", "-"):
                                info.exam_stage = s_name
                                break
                
                # 保存详细数据用于前端展示
                info.training_details.append({
                    "subject": subject,
                    "stage_no": stage.stage_no,
                    "training_time": stage.training_time or "-",
                    "training_km": stage.training_km or "-",
                    "platform_time": stage.platform_time or "-",
                    "platform_km": stage.platform_km or "-",
                })
        
        # 合同信息（来自东莞驾培）
        if driving:
            info.contract_available = driving.contract_available
            info.contract_code = driving.contract_code
            info.contract_url = driving.contract_url
        
        # 构建时间轴展示
        info.timeline_display = self._build_timeline_display(info.timeline)
        
        # 计算合规状态
        info.contract_status, info.skill_cert_status, info.skill_cert_date = self._calc_compliance_status(
            info.registration_date, info.timeline
        )
        
        return info
    
    def _build_timeline_display(self, timeline: list[dict]) -> list[dict]:
        """将内部系统时间轴转为前端展示格式（保留原始文案）"""
        display = []
        
        for event in timeline:
            title = event.get("NodeTitle", "")
            date = event.get("NodeTime", "") or event.get("NodeDate", "")
            
            # 判断状态
            status = "normal"
            if any(w in title for w in ("通过", "合格", "成功", "领证", "结业", "收", "提交", "报名", "拍仪")):
                status = "pass"
            if any(w in title for w in ("不合格", "失败", "缺考", "退学", "未通过")):
                status = "fail"
            
            if title and date:
                display.append({"date": date, "title": title, "status": status})
        
        return display
    
    def _calc_compliance_status(self, registration_date: str, timeline: list[dict]) -> tuple[str, str, str]:
        """计算合同到期和技能证到期状态

        Returns:
            (contract_status, skill_cert_status, skill_cert_date)
            contract_status: "valid" | "expired" | ""
            skill_cert_status: "valid" | "expired" | "no_subject1"
            skill_cert_date: 科目一通过日期字符串，为空表示未过文科
        """
        from datetime import date, timedelta
        import re

        today = date.today()

        # ── 合同状态：报名日期 + 3年 ──
        contract_status = ""
        if registration_date:
            # 支持 "2024-05-03" 或 "2024/05/03" 或 "2024.05.03"
            m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", registration_date)
            if m:
                reg = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                expire = date(reg.year + 3, reg.month, reg.day)
                contract_status = "expired" if today >= expire else "valid"

        # ── 技能证状态：科目一通过日期 + 3年 ──
        skill_cert_status = "no_subject1"  # 默认未过文科
        skill_cert_date = ""
        for event in timeline:
            title = event.get("NodeTitle", "")
            if ("科目1" in title or "科一" in title or "科目一" in title) and "通过" in title:
                # 找到科目一通过节点，提取日期
                date_str = event.get("NodeTime", "") or event.get("NodeDate", "")
                m2 = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", date_str)
                if m2:
                    s1_date = date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
                    cert_expire = date(s1_date.year + 3, s1_date.month, s1_date.day)
                    skill_cert_status = "expired" if today >= cert_expire else "valid"
                    # 格式化日期用于展示
                    skill_cert_date = f"{s1_date.year}-{s1_date.month:02d}-{s1_date.day:02d}"
                break

        return contract_status, skill_cert_status, skill_cert_date

    def get_auth_status(self) -> Dict[str, Any]:
        """获取认证状态概览"""
        return self.auth_manager.get_status()
    
    def login_all(self, background: bool = True) -> Dict[SystemType, Any]:
        """登录所有系统"""
        return self.auth_manager.login_all(background=background)
    
    def clear_all_cache(self):
        """清除所有系统缓存（包括查询结果缓存）"""
        self._query_cache.clear()
        self.auth_manager.clear_all_cache()
    
    def close(self):
        """关闭查询引擎，释放资源"""
        self._executor.shutdown(wait=True)


# 全局查询引擎实例
query_engine = QueryEngine()


# 兼容旧版接口的同步包装函数
def query_all_systems_sync(id_card: str, timeout: float = 60.0) -> dict:
    """
    同步方式查询所有系统（兼容旧接口）
    
    Args:
        id_card: 身份证号
        timeout: 超时时间（秒）
        
    Returns:
        合并后的学员信息字典
    """
    try:
        # 使用 asyncio.run 替代手动创建/关闭事件循环
        # asyncio.run 会自动创建和关闭，更安全
        info = asyncio.run(query_engine.query_all(id_card, timeout))
        return info.__dict__
    except Exception as e:
        # 降级处理：返回空结果
        return {
            "name": "",
            "id_card": id_card,
            "phone": "",
            "license_type": "",
            "registration_date": "",
            "school_name": "",
            "school_short": "",
            "student_status": "",
            "exam_stage": "",
            "exam_counts": {},
            "training_hours": {},
            "training_details": [],
            "fees": [],
            "contract_available": False,
            "contract_code": "",
            "contract_url": "",
            "timeline_display": [],
            "contract_status": "",
            "skill_cert_status": "",
            "skill_cert_date": "",
            "sources": {
                "internal": "error",
                "third": "error",
                "driving": "error",
            },
            "error": str(e),
        }
