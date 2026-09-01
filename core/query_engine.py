"""
异步并行查询引擎
支持并发查询多个系统，自动处理登录态，统一结果合并
"""
import asyncio
import re
import time
from datetime import date, datetime
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from concurrent.futures import ThreadPoolExecutor

from core.auth_manager import AuthManager, SystemType, auth_manager
from crawlers.internal import InternalCrawler, InternalStudentInfo
from crawlers.third import ThirdCrawler, ThirdStudentInfo
from crawlers.driving import DrivingCrawler, DrivingStudentInfo
from utils.logger import system_logger


# 东莞驾培电子合同上线日期：此前报名的学员系统中无电子合同，无需登录查询
DRIVING_ECONTRACT_START_DATE = date(2024, 3, 15)


class QueryStatus(Enum):
    """查询状态"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    NO_CONTRACT = "no_contract"
    NOT_QUERIED = "not_queried"  # 本轮未查询（无证件号 / 依赖前置未命中），不等于查无
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
    phase_durations_ms: Dict[str, int] = field(default_factory=dict)
    retry_count: int = 0


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
    # 第三系统「学员申请登记」里的驾校主体名（如「」）。
    # 只作展示用，绝不可并入 school_name：school_name 在本系统语义是**网点/分校名**，
    # 回复函模板写死「在{school_name}{unit_type}网点报名」（reply_docx.py:84），
    # 填进去会生成「在网点报名」病句，登记表「经营单位」同理。
    third_school_name: str = ""
    # 第三系统学员详情页的「分点名称」（如「…东城同沙招生点」）。
    # 内部系统查无时，前端「驾校」栏优先展示它——比「」主体名更有辨识度。
    third_branch_name: str = ""
    
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
    
    # 东莞驾培费用信息（合同金额/监管/交费订单）
    driving_fee: Dict = field(default_factory=dict)
    
    # 合同信息
    contract_available: bool = False
    contract_check_deferred: bool = False
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
    query_durations_ms: Dict[str, int] = field(default_factory=dict)
    system_phase_durations_ms: Dict[str, Dict[str, int]] = field(default_factory=dict)
    query_retry_counts: Dict[str, int] = field(default_factory=dict)
    system_errors: Dict[str, str] = field(default_factory=dict)


def _parse_date_str(s: str) -> Optional[date]:
    """解析报名/考试日期，支持多种格式，失败返回 None：
    - "2024-05-03" / "2024/05/03" / "2024.05.03"（含更长串中的子串）
    - 紧凑 "20240503"（8 位纯数字）
    - Unix 时间戳（10~13 位，秒或毫秒）
    """
    s = (s or "").strip()
    if not s:
        return None

    # 紧凑 YYYYMMDD
    if re.fullmatch(r"\d{8}", s):
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None

    # Unix 时间戳（秒 / 毫秒）
    if re.fullmatch(r"\d{10,13}", s):
        try:
            ts = int(s)
            if ts > 10_000_000_000:  # 毫秒
                ts = ts / 1000
            return datetime.fromtimestamp(ts).date()
        except (ValueError, OSError, OverflowError):
            return None

    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


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
        self._system_executors = {
            system_type: ThreadPoolExecutor(max_workers=1)
            for system_type in SystemType
        }
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

    @staticmethod
    def _get_query_metrics(crawler) -> dict:
        if hasattr(crawler, "get_last_query_metrics"):
            return crawler.get_last_query_metrics()
        return {"phase_durations_ms": {}, "retry_count": 0}
    
    async def query_all(
        self,
        id_card: str,
        timeout: float = 60.0,
        on_update: Callable[[MergedStudentInfo], None] = None,
    ) -> MergedStudentInfo:
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
                if on_update:
                    on_update(result)
                return result  # 直接返回缓存结果
            else:
                del self._query_cache[cache_key]  # 过期清除

        loop = asyncio.get_event_loop()
        
        # 创建查询任务
        executors = getattr(self, "_system_executors", {})
        internal_task = loop.run_in_executor(
            executors.get(SystemType.INTERNAL, self._executor),
            self._query_internal,
            id_card,
        )
        # 第三系统档案补查（报名时间/手机号）：等内部结果后决定是否发起。
        # 一次查询两处共用——third 结果合并补位 + driving 跳过闸门补判，
        # 避免同一档案页被查两次。
        profile_task = asyncio.ensure_future(self._profile_task(id_card, internal_task))
        tasks = {
            SystemType.INTERNAL: internal_task,
            # 第三系统：阶段学时与内部并行；档案补查结果由共用任务提供
            SystemType.THIRD: asyncio.ensure_future(
                self._third_task(id_card, internal_task, profile_task)
            ),
            # 东莞驾培等待内部结果后再决定是否查询（2024-03-15 前报名无需登录）；
            # 内部查无时再等第三档案报名日期补判（2017 老学员缺口）
            SystemType.DRIVING: asyncio.ensure_future(
                self._driving_task(id_card, internal_task, profile_task)
            ),
        }
        
        results = {}
        task_systems = {task: system_type for system_type, task in tasks.items()}
        pending = set(tasks.values())
        deadline = loop.time() + timeout

        while pending:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            done, pending = await asyncio.wait(
                pending,
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                break
            for task in done:
                system_type = task_systems[task]
                try:
                    results[system_type] = task.result()
                except Exception as e:
                    results[system_type] = QueryResult(
                        system=system_type.value,
                        status=QueryStatus.ERROR,
                        error=str(e),
                    )

            if on_update:
                progressive_results = dict(results)
                for task in pending:
                    system_type = task_systems[task]
                    progressive_results[system_type] = QueryResult(
                        system=system_type.value,
                        status=QueryStatus.RUNNING,
                    )
                on_update(self._merge_results(id_card, progressive_results))

        for task in pending:
            task.cancel()
            system_type = task_systems[task]
            results[system_type] = QueryResult(
                system=system_type.value,
                status=QueryStatus.TIMEOUT,
                error="查询超时",
            )
        
        # 合并结果
        merged = self._merge_results(id_card, results)
        if on_update:
            on_update(merged)
        
        # 缓存结果（含“查无此人/无合同”，避免重复触发慢查询），超时或错误结果不缓存。
        if not any(
            result.status in (QueryStatus.ERROR, QueryStatus.TIMEOUT)
            for result in results.values()
        ):
            self._query_cache[f"query:{id_card}"] = (time.time(), merged)
        
        return merged
    
    def _query_internal(self, id_card: str) -> QueryResult:
        """查询内部系统"""
        start_time = time.time()
        crawler = None
        try:
            crawler = self._get_internal()
            result = crawler.query_student(id_card)
            duration = int((time.time() - start_time) * 1000)
            metrics = self._get_query_metrics(crawler)
            
            if result:
                return QueryResult(
                    system="internal",
                    status=QueryStatus.SUCCESS,
                    data=result,
                    duration_ms=duration,
                    phase_durations_ms=metrics["phase_durations_ms"],
                    retry_count=metrics["retry_count"],
                )
            else:
                return QueryResult(
                    system="internal",
                    status=QueryStatus.NOT_FOUND,
                    duration_ms=duration,
                    phase_durations_ms=metrics["phase_durations_ms"],
                    retry_count=metrics["retry_count"],
                )
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            metrics = self._get_query_metrics(crawler) if crawler else self._get_query_metrics(None)
            return QueryResult(
                system="internal",
                status=QueryStatus.ERROR,
                error=str(e),
                duration_ms=duration,
                phase_durations_ms=metrics["phase_durations_ms"],
                retry_count=metrics["retry_count"],
            )
    
    def _query_third(self, id_card: str) -> QueryResult:
        """查询第三系统"""
        start_time = time.time()
        crawler = None
        try:
            crawler = self._get_third()
            result = crawler.query_student(id_card)
            duration = int((time.time() - start_time) * 1000)
            metrics = self._get_query_metrics(crawler)
            
            if result:
                return QueryResult(
                    system="third",
                    status=QueryStatus.SUCCESS,
                    data=result,
                    duration_ms=duration,
                    phase_durations_ms=metrics["phase_durations_ms"],
                    retry_count=metrics["retry_count"],
                )
            else:
                return QueryResult(
                    system="third",
                    status=QueryStatus.NOT_FOUND,
                    duration_ms=duration,
                    phase_durations_ms=metrics["phase_durations_ms"],
                    retry_count=metrics["retry_count"],
                )
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            metrics = self._get_query_metrics(crawler) if crawler else self._get_query_metrics(None)
            return QueryResult(
                system="third",
                status=QueryStatus.ERROR,
                error=str(e),
                duration_ms=duration,
                phase_durations_ms=metrics["phase_durations_ms"],
                retry_count=metrics["retry_count"],
            )
    
    def _query_driving(self, id_card: str) -> QueryResult:
        """查询东莞驾培系统"""
        start_time = time.time()
        crawler = None
        try:
            crawler = self._get_driving()
            result = crawler.query_student(id_card, include_contract_check=True)
            duration = int((time.time() - start_time) * 1000)
            metrics = self._get_query_metrics(crawler)
            
            if result:
                return QueryResult(
                    system="driving",
                    status=QueryStatus.SUCCESS,
                    data=result,
                    duration_ms=duration,
                    phase_durations_ms=metrics["phase_durations_ms"],
                    retry_count=metrics["retry_count"],
                )
            else:
                return QueryResult(
                    system="driving",
                    status=QueryStatus.NOT_FOUND,
                    duration_ms=duration,
                    phase_durations_ms=metrics["phase_durations_ms"],
                    retry_count=metrics["retry_count"],
                )
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            metrics = self._get_query_metrics(crawler) if crawler else self._get_query_metrics(None)
            system_logger.error("[Query] driving 查询失败: %s", e)
            return QueryResult(
                system="driving",
                status=QueryStatus.ERROR,
                error=str(e),
                duration_ms=duration,
                phase_durations_ms=metrics["phase_durations_ms"],
                retry_count=metrics["retry_count"],
            )

    @staticmethod
    def _should_skip_driving(
        internal_result: Optional[QueryResult] = None,
        third_profile: Any = None,
    ) -> bool:
        """2024-03-15 前报名（报名日期可判定）的学员，东莞驾培无电子合同，跳过查询。

        报名日期来源优先级：内部系统 > 第三系统档案补查。
        内部查无的 2017 老学员（如测试学员甲）内部给不出日期，靠第三档案补判，
        避免对其白查一轮驾培（约 10s+）。两者都给不出日期时不跳过（维持原行为）。
        """
        reg_date = getattr(getattr(internal_result, "data", None), "registration_date", "")
        parsed = _parse_date_str(reg_date)
        if parsed is None:
            parsed = _parse_date_str(getattr(third_profile, "registration_date", "") or "")
        if parsed is None:
            return False
        return parsed < DRIVING_ECONTRACT_START_DATE

    @staticmethod
    def _need_third_profile(internal_result: Optional[QueryResult]) -> bool:
        """内部系统已给出完整基本信息时，不必再查第三系统档案页（避免无谓请求）。

        档案页能补的是报名时间 / 手机号 / 学员状态——内部这三项都齐了就没必要补。
        """
        if not internal_result or internal_result.status != QueryStatus.SUCCESS:
            return True
        data = getattr(internal_result, "data", None)
        if data is None:
            return True
        return not all(
            str(getattr(data, f, "") or "").strip()
            for f in ("registration_date", "phone", "student_status")
        )

    def _fetch_third_profile(self, id_card: str):
        """补查第三系统学员档案。任何异常一律吞掉返回 None——它只是补充，不能影响主流程。"""
        try:
            return self._get_third().fetch_registration_profile(id_card)
        except Exception as e:
            system_logger.warning("[Query] third 档案补查失败: %s", e)
            return None

    async def _profile_task(self, id_card: str, internal_task):
        """第三系统档案补查任务（third 合并补位与 driving 跳过判断共用）。

        内部信息齐全（报名时间/手机号/状态都有）时不发起，立即返回 None；
        否则沿 third 专用线程池补查档案页。任何异常由 _fetch_third_profile 吞掉返回 None。
        """
        loop = asyncio.get_event_loop()
        executors = getattr(self, "_system_executors", {})
        third_executor = executors.get(SystemType.THIRD, self._executor)
        try:
            internal_result = await internal_task
        except asyncio.CancelledError:
            raise
        except Exception:
            internal_result = None
        if not self._need_third_profile(internal_result):
            return None
        return await loop.run_in_executor(third_executor, self._fetch_third_profile, id_card)

    async def _third_task(self, id_card: str, internal_task, profile_task) -> QueryResult:
        """第三系统查询任务：阶段学时立即与内部并行发起，档案补查由共用任务提供。"""
        loop = asyncio.get_event_loop()
        executors = getattr(self, "_system_executors", {})
        third_executor = executors.get(SystemType.THIRD, self._executor)

        base_task = loop.run_in_executor(third_executor, self._query_third, id_card)
        try:
            internal_result = await internal_task
        except asyncio.CancelledError:
            raise
        except Exception:
            internal_result = None

        try:
            base = await base_task
        except Exception as e:
            base = QueryResult(system="third", status=QueryStatus.ERROR, error=str(e))

        try:
            profile = await profile_task
        except asyncio.CancelledError:
            raise
        except Exception:
            profile = None

        if profile is not None and base.data is not None:
            base.data.registration_profile = profile
        return base

    def _warm_driving_login(self) -> bool:
        """预热驾培登录态：登录不依赖内部系统结果，提前并行执行以隐藏耗时"""
        try:
            return self._get_driving().ensure_login()
        except Exception:
            return False

    async def _driving_task(self, id_card: str, internal_task, profile_task) -> QueryResult:
        """东莞驾培查询任务：登录与内部系统查询并行；是否跳过等报名日期决定——
        优先内部系统，内部给不出日期（查无/缺字段）时用第三系统档案补判。
        跳过时预热登录作废（后台自行结束，不影响结果）。"""
        loop = asyncio.get_event_loop()
        executors = getattr(self, "_system_executors", {})
        driving_executor = executors.get(SystemType.DRIVING, self._executor)
        login_task = loop.run_in_executor(driving_executor, self._warm_driving_login)

        def _skipped() -> QueryResult:
            return QueryResult(
                system="driving",
                status=QueryStatus.NO_CONTRACT,
                duration_ms=0,
                phase_durations_ms={},
                retry_count=0,
            )

        try:
            internal_result = await internal_task
        except asyncio.CancelledError:
            raise
        except Exception:
            internal_result = None

        if self._should_skip_driving(internal_result):
            return _skipped()

        # 内部没有可判定的报名日期 → 等第三档案补查结果再判一次。
        # 档案补查只在内部信息不全时才真正发起（_need_third_profile 闸门），正常学员零开销。
        internal_reg = getattr(getattr(internal_result, "data", None), "registration_date", "")
        if _parse_date_str(internal_reg) is None:
            try:
                profile = await profile_task
            except asyncio.CancelledError:
                raise
            except Exception:
                profile = None
            if self._should_skip_driving(internal_result, profile):
                return _skipped()

        return await loop.run_in_executor(
            driving_executor,
            self._query_driving,
            id_card,
        )

    @staticmethod
    def _short_branch_name(name: str) -> str:
        """分点名称展示口径（2026-08-31 用户拍板）：去掉公司主体前缀。

        「东莞市快捷汽车驾驶员培训有限公司东城同沙招生点」→「东城同沙招生点」。
        规则：含「有限公司」时取其后的部分；截完为空则保留原名兜底。
        """
        name = (name or "").strip()
        if "有限公司" in name:
            name = name.rsplit("有限公司", 1)[1].strip() or name
        return name

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
        info.query_durations_ms = {
            "internal": results.get(SystemType.INTERNAL, QueryResult("internal", QueryStatus.ERROR)).duration_ms,
            "third": results.get(SystemType.THIRD, QueryResult("third", QueryStatus.ERROR)).duration_ms,
            "driving": results.get(SystemType.DRIVING, QueryResult("driving", QueryStatus.ERROR)).duration_ms,
        }
        info.system_phase_durations_ms = {
            "internal": results.get(SystemType.INTERNAL, QueryResult("internal", QueryStatus.ERROR)).phase_durations_ms,
            "third": results.get(SystemType.THIRD, QueryResult("third", QueryStatus.ERROR)).phase_durations_ms,
            "driving": results.get(SystemType.DRIVING, QueryResult("driving", QueryStatus.ERROR)).phase_durations_ms,
        }
        info.query_retry_counts = {
            "internal": results.get(SystemType.INTERNAL, QueryResult("internal", QueryStatus.ERROR)).retry_count,
            "third": results.get(SystemType.THIRD, QueryResult("third", QueryStatus.ERROR)).retry_count,
            "driving": results.get(SystemType.DRIVING, QueryResult("driving", QueryStatus.ERROR)).retry_count,
        }
        # 系统查询错误详情（落库便于排查）
        info.system_errors = {
            result.system: result.error
            for result in results.values()
            if result.error
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

        # 第三系统「学员申请登记」档案补位：内部系统与驾培都没给报名日期 / 手机号时，
        # 从这里补（2017 年老学员内部查无，但第三系统有完整报名档案）。
        # school_short 取学员详情页的「分点号」（branch_code），与投诉系统网点代号
        # （南/麻/栅D）同口径；列表页 school_name 是「」驾校简称，仍不并入。
        if third:
            prof = getattr(third, "registration_profile", None)
            if prof:
                if not info.registration_date:
                    info.registration_date = prof.registration_date
                if not info.phone:
                    info.phone = prof.phone
                if not info.student_status:
                    info.student_status = prof.student_status
                if not info.license_type:
                    info.license_type = prof.license_type
                if not info.name:
                    info.name = prof.name
                if not info.school_short and prof.branch_code:
                    info.school_short = prof.branch_code
                # 驾校主体名只作展示，不并入 school_name（见字段注释）
                if not info.third_school_name:
                    info.third_school_name = prof.school_name
                if not info.third_branch_name:
                    # 展示口径（2026-08-31 用户拍板）：去掉公司主体前缀，
                    # 「东莞市快捷汽车驾驶员培训有限公司东城同沙招生点」→「东城同沙招生点」
                    info.third_branch_name = self._short_branch_name(prof.branch_name)
        
        # 培训时长（来自第三系统）
        if third and third.stages:
            stage_map = {1: "科目一", 2: "科目二", 3: "科目三", 4: "科目四"}
            for stage in third.stages:
                subject = stage_map.get(stage.stage_no, f"阶段{stage.stage_no}")
                # 扣费以审核有效学时为准；无审核数据时才回退到平台展示值。
                display_time = stage.audit_time or stage.training_time or stage.platform_time or "0时0分"
                info.training_hours[subject] = display_time
                
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
            info.contract_check_deferred = not driving.contract_checked
            info.contract_code = driving.contract_code
            info.contract_url = driving.contract_url
            info.driving_fee = {
                "contract_fee": driving.contract_fee,
                "pay_fee": driving.pay_fee,
                "supervise_fee": driving.supervise_fee,
                "residue_supervise_amt": driving.residue_supervise_amt,
                "supervise_date": driving.supervise_date,
                "breakdown": driving.fee_breakdown,
                "orders": driving.pay_orders,
            }
        
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

        today = date.today()

        # ── 合同状态：报名日期 + 3年 ──
        contract_status = ""
        if registration_date:
            reg = _parse_date_str(registration_date)
            if reg:
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
                m2 = _parse_date_str(date_str)
                if m2:
                    s1_date = m2
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
            "timeline": [],
            "driving_fee": {},
            "contract_available": False,
            "contract_check_deferred": False,
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
