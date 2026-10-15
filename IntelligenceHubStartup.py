import os.path
import time
import uuid
import logging
import datetime
import threading
import traceback
from flask import Flask
from typing import Tuple
from pathlib import Path
from functools import partial

from AIClientCenter.web.dashboard import AIDashboardService
from GlobalConfig import *
from ServiceComponent.HubApplication import HubApplication
from ServiceComponent.adapters import (
    EventV4PipelinePorts, IISAsyncTranslationExtension,
    IISUnarchivedReplayExtension)
from ServiceComponent.runtime import ScheduledMaintenancePlugin, RuntimeControlState
from ServiceComponent.runtime.status import HubStatusLogGate, format_hub_status
from Tools.SystemMonitorService import MonitorAPI
from Tools.SystemdWatchdog import is_watchdog_enabled, notify_ready, notify_alive, notify_stopping
from MyPythonUtility.easy_config import EasyConfig
from ServiceComponent.UserManager import UserManager
from ServiceComponent.RSSPublisher import RSSPublisher
from ServiceComponent.SubsystemRegistry import SubsystemRegistry
from ServiceComponent.manual_debug_analysis import ManualDebugAnalysisService
from ServiceComponent.analysis_failure_dump import AnalysisFailureRecorder
from ServiceComponent.analysis_shadow import ShadowAnalysisRecorder
from ServiceComponent.intelligence_contracts import APPENDIX_TIME_ARCHIVED, appendix_path
from AIClientCenter.core.manager import AIClientManager
from AIClientCenter.core.state_logger import ClientStateSQLiteLogger
from AIClientCenter.core.observation import CallObservationSQLiteLogger
from MyPythonUtility.proc_utils import find_processes
from IntelligenceHubWebService import IntelligenceHubWebService, WebServiceAccessManager, exclude_raw_data
from PyLoggingBackend import setup_logging, backup_and_clean_previous_log_file, limit_logger_level, LoggerBackend
from Tools.PerformanceLogger import setup_performance_logging, PERF_LOGGER_NAME

wsgi_app = Flask(__name__)
wsgi_app.secret_key = str(uuid.uuid4())
wsgi_app.permanent_session_lifetime = datetime.timedelta(days=7)
wsgi_app.config.update(
    # SESSION_COOKIE_SECURE=True,  # 仅通过HTTPS发送（生产环境必须）
    SESSION_COOKIE_HTTPONLY=True,  # 防止JavaScript访问（安全）
    SESSION_COOKIE_SAMESITE='Lax'  # 防止CSRF攻击
)


logger = logging.getLogger(__name__)

self_path = os.path.dirname(os.path.abspath(__file__))


def show_intelligence_hub_statistics_forever(
        hub: HubApplication, *, poll_interval: float = 2.0,
        unchanged_log_interval: float = 60.0):
    gate = HubStatusLogGate(unchanged_log_interval)
    while True:
        statistics = hub.statistics
        now = time.monotonic()
        if gate.should_log(statistics, now):
            logger.info(format_hub_status(statistics))
        time.sleep(poll_interval)


def build_ai_client_manager(config: EasyConfig):
    try:
        from _config.ai_client_config import AI_CLIENTS, AI_CLIENT_LIMIT
    except Exception:
        logger.exception(
            "\n"
            "==================== AI Client Configuration Load Failed ====================\n"
            "Unable to import: _config.ai_client_config\n"
            "Falling back to example config: _config.ai_client_config_example (placeholder)\n"
            "\n"
            "How to fix:\n"
            "  - Copy _config/ai_client_config_example.py to _config/ai_client_config.py\n"
            "  - Then review and adjust the settings for your environment.\n"
            "=============================================================================\n"
        )
        from _config.ai_client_config_example import AI_CLIENTS, AI_CLIENT_LIMIT

    sqlite_db_file = config.get('ai_client_center.sqlite_db_file', 'ai_client_state_log.db')
    heartbeat_interval_sec = config.get('ai_client_center.heartbeat_interval_sec', 30)
    heartbeat_grace_sec = config.get('heartbeat_grace_sec', 120)

    state_logger = ClientStateSQLiteLogger(
        sqlite_db_path=os.path.join(DATA_PATH, sqlite_db_file),
        run_id=str(time.time()),
        heartbeat_interval_sec=heartbeat_interval_sec,
        heartbeat_grace_sec=heartbeat_grace_sec
    )
    state_logger.start()

    observation_config = config.get('ai_client_center.call_observation', {}) or {}
    call_observer = None
    if observation_config.get('enabled', True):
        try:
            observation_db_file = observation_config.get(
                'sqlite_db_file', 'ai_client_observations.db')
            retention_days = observation_config.get('retention_days', 90)
            call_observer = CallObservationSQLiteLogger(
                sqlite_db_path=os.path.join(DATA_PATH, observation_db_file),
                retention_days=retention_days,
            )
        except Exception:
            logger.exception("AI Client 调用观测初始化失败，主调用链继续运行。")

    client_manager = AIClientManager(
        state_logger=state_logger,
        call_observer=call_observer,
    )

    logger.info(f"AI clients count: {len(AI_CLIENTS)}).")
    logger.info(f"AI group limit count: {len(AI_CLIENT_LIMIT)}).")

    for client in AI_CLIENTS.values():
        client_manager.register_client(client)

    for group, limit in AI_CLIENT_LIMIT.items():
        client_manager.set_group_limit(group, limit)

    return client_manager


def start_intelligence_hub_service(config) -> Tuple[HubApplication, IntelligenceHubWebService, AIClientManager]:

    # ------------------------------- AI Service -------------------------------

    client_manager = build_ai_client_manager(config)
    client_manager.start_monitoring()

    # ------------------------------- Core: IHub -------------------------------

    ai_analysis_thread = config.get('intelligence_hub.ai_analysis_thread', 1)
    ai_client_wait_timeout = config.get('intelligence_hub.ai_client_wait_timeout_sec', 60)
    max_inflight = config.get('intelligence_hub.max_inflight', 2000)
    submission_ack_timeout = config.get('intelligence_hub.submission_ack_timeout_sec', 30)
    mongodb_host = config.get('mongodb.host', 'localhost')
    mongodb_port = config.get('mongodb.port', 27017)
    mongodb_user = config.get('mongodb.user', '')
    mongodb_pass = config.get('mongodb.password', '')

    # ------------------------------- Subsystems ------------------------------

    subsystem_registry = SubsystemRegistry.build_from_config(
        config,
        mongodb_params={
            'host': mongodb_host,
            'port': mongodb_port,
            'username': mongodb_user,
            'password': mongodb_pass,
        },
        db_name='IntelligenceIntegrationSystem',
    )
    logger.info(f"Subsystems: default='{subsystem_registry.default_name}', "
                f"list={subsystem_registry.describe()}")

    scheduling_capacity = client_manager.get_scheduling_capacity()
    if ai_analysis_thread > scheduling_capacity:
        logger.warning(
            "AI analysis worker count (%d) exceeds schedulable client capacity (%d). "
            "建议将 intelligence_hub.ai_analysis_thread 调整为不大于 %d，"
            "或增加启用的 AI 客户端/分组并发上限。",
            ai_analysis_thread,
            scheduling_capacity,
            scheduling_capacity,
        )

    # 可选能力在组合根创建并注入；HubApplication 不知道翻译、VectorDB、集合或索引线程。
    failure_dump_directory = config.get(
        'intelligence_hub.analysis_failure_dump.directory',
        os.path.join(DATA_PATH, 'analysis_failures'),
    )
    if not os.path.isabs(failure_dump_directory):
        failure_dump_directory = os.path.join(self_path, failure_dump_directory)
    failure_recorder = AnalysisFailureRecorder(
        failure_dump_directory,
        enabled=config.get('intelligence_hub.analysis_failure_dump.enabled', True),
        max_files=config.get('intelligence_hub.analysis_failure_dump.max_files', 500),
    )
    # 影子链路：保守灰度。生产归档仍由抽样表版本产生，影子版本并行分析同一输入，
    # 结果只写入 analysis_shadow 目录供离线对比，不进入归档与索引。
    shadow_versions = config.get('intelligence_hub.shadow_analysis.versions', [])
    shadow_recorder = None
    if shadow_versions:
        shadow_directory = config.get(
            'intelligence_hub.shadow_analysis.directory',
            os.path.join(DATA_PATH, 'analysis_shadow'),
        )
        if not os.path.isabs(shadow_directory):
            shadow_directory = os.path.join(self_path, shadow_directory)
        shadow_recorder = ShadowAnalysisRecorder(
            shadow_directory,
            enabled=config.get('intelligence_hub.shadow_analysis.enabled', True),
            max_files=config.get('intelligence_hub.shadow_analysis.max_files', 1000),
        )
        logger.info(f'Shadow analysis enabled for prompt versions: {shadow_versions}')
    # 运行控制开关：组合根创建唯一实例，分别注入 pipeline 与 Web 层。
    control_state = RuntimeControlState()
    pipeline_ports = EventV4PipelinePorts(
        subsystem_registry,
        client_manager,
        client_wait_timeout=ai_client_wait_timeout,
        analysis_worker_count=ai_analysis_thread,
        failure_recorder=failure_recorder,
        shadow_versions=shadow_versions,
        shadow_recorder=shadow_recorder,
        control_state=control_state,
    )
    extensions = []
    services = {}
    manual_debug_service = ManualDebugAnalysisService(
        pipeline_ports,
        subsystem_registry,
        max_results=config.get('intelligence_hub.manual_debug.max_results', 50),
        worker_count=config.get('intelligence_hub.manual_debug.worker_count', 1),
    )
    extensions.append(manual_debug_service)
    services["manual_debug_analysis"] = manual_debug_service
    if config.get('intelligence_hub.replay_unarchived', True):
        replay_extension = IISUnarchivedReplayExtension(subsystem_registry)
        extensions.append(replay_extension)
        services["unarchived_replay"] = replay_extension
    translation_enabled = config.get('intelligence_hub.translation.enabled', True)
    translation_extension = None
    if translation_enabled:
        from ServiceComponent.AsyncTranslationPatch import AsyncTranslationPatch, needs_translation

        def create_translation_patch(shutdown_flag, on_patched):
            return AsyncTranslationPatch(
                mongo_db_archive=subsystem_registry.default().event_v4_intelligence_collection,
                query_engine=subsystem_registry.default().event_v4_query_engine,
                ai_client_manager=client_manager,
                shutdown_flag=shutdown_flag,
                on_patched=on_patched,
                translated_revision="tr_patch_20260311",
                backfill_enabled=True,
                backfill_scan_limit_per_round=200,
                backfill_interval_sec=600,
            )

        translation_extension = IISAsyncTranslationExtension(
            default_subsystem=subsystem_registry.default_name,
            translator_factory=create_translation_patch,
            needs_translation=needs_translation,
        )
        extensions.append(translation_extension)
        services["translation"] = translation_extension

    # ------------------------- 向量子系统（可拆卸装配） -------------------------
    # 组合根只调用装配函数：VectorDB 客户端、索引扩展、聚合/图谱延迟服务、
    # 集合创建与就绪重试均封装在 VectorIntelligence 包内。装配失败不影响主链路。
    vector_search = None
    vector_handles = None
    try:
        from VectorIntelligence.iis_integration import create_vector_intelligence
        vector_handles = create_vector_intelligence(
            config, subsystem_registry=subsystem_registry)
    except Exception:
        logger.exception("向量子系统装配失败，IIS 主服务继续运行（向量能力禁用）。")
    if vector_handles is not None:
        extensions.extend(vector_handles.plugins)
        services.update(vector_handles.services)
        vector_search = vector_handles.search

    # 实体统计、导出和聚合触发均为外围维护能力，不参与主处理链。
    from ServiceComponent.IntelligenceEntityFrequencyEngine import EntityFrequencyEngine
    from MyPythonUtility.AdvancedScheduler import AdvancedScheduler

    entity_frequency_engine = EntityFrequencyEngine(
        db_path=os.path.join(DATA_PATH, "entity_frequency.db"),
        event_collection=subsystem_registry.default().event_v4_event_collection,
    )
    services["entity_frequency"] = entity_frequency_engine

    def export_weekly():
        now = datetime.datetime.now()
        year, week, _ = now.isocalendar()
        context = subsystem_registry.default()
        if context.event_v4_intelligence_storage:
            context.event_v4_intelligence_storage.export_by_week(
                year=year, week=week, directory=os.path.join(EXPORT_PATH, "mongo_db_archive"),
                time_field=appendix_path(APPENDIX_TIME_ARCHIVED), add_timestamp=True)
        if context.mongo_db_cache:
            context.mongo_db_cache.export_by_week(
                year=year, week=week, directory=os.path.join(EXPORT_PATH, "mongo_db_cache"),
                time_field="created_at", add_timestamp=True)

    def export_monthly():
        target = datetime.datetime.now().replace(day=1) - datetime.timedelta(days=1)
        context = subsystem_registry.default()
        if context.event_v4_intelligence_storage:
            context.event_v4_intelligence_storage.export_by_month(
                year=target.year, month=target.month, directory=os.path.join(EXPORT_PATH, "mongo_db_archive"),
                time_field=appendix_path(APPENDIX_TIME_ARCHIVED), add_timestamp=True)
        if context.mongo_db_cache:
            context.mongo_db_cache.export_by_month(
                year=target.year, month=target.month, directory=os.path.join(EXPORT_PATH, "mongo_db_cache"),
                time_field="created_at", add_timestamp=True)

    def run_aggregation():
        # 聚合调度逻辑（24h 窗口判定等）在向量子系统内部，此处仅转发。
        if vector_handles is not None:
            vector_handles.run_aggregation()

    def build_entity_frequency():
        end = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        for _ in entity_frequency_engine.build_cache_with_progress(end - datetime.timedelta(days=1), end):
            pass

    def bootstrap_maintenance():
        end = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        for _ in entity_frequency_engine.build_cache_with_progress(end - datetime.timedelta(days=30), end):
            pass

    def configure_maintenance(scheduler):
        scheduler.add_weekly_task(export_weekly, task_id="export_mongodb_weekly_task", day_of_week="sun", use_new_thread=True)
        scheduler.add_monthly_task(export_monthly, task_id="export_mongodb_monthly_task", day=1, use_new_thread=True)
        scheduler.add_hourly_task(run_aggregation, task_id="summary_aggregation_hourly", use_new_thread=True)
        scheduler.add_hourly_task(build_entity_frequency, task_id="entity_frequency_hourly", use_new_thread=True)
        # 对话记录按周归档：散文件保留 7 天，周日将超过保留期的记录 append 进其所在周的 zip。
        retention_days = config.get('intelligence_hub.conversation_retention_days', 7)

        def compact_conversations():
            from ServiceComponent.conversation_compaction import compact_conversation_records
            compact_conversation_records(
                os.path.join(self_path, 'conversation'), older_than_days=retention_days)

        scheduler.add_weekly_task(compact_conversations, task_id="compact_conversation_weekly",
                                  day_of_week="sun", use_new_thread=True)

    extensions.append(ScheduledMaintenancePlugin(
        lambda: AdvancedScheduler(logger=logging.getLogger("Scheduler")),
        configure_maintenance,
        bootstrap_maintenance,
    ))

    hub = HubApplication(
        subsystem_registry=subsystem_registry,
        ai_client_manager=client_manager,
        worker_count=ai_analysis_thread,
        pipeline_ports=pipeline_ports,
        extensions=extensions,
        vector_search=vector_search,
        services=services,
        max_inflight=max_inflight,
        submission_ack_timeout=submission_ack_timeout,
    )
    hub.startup()

    # ----------------------- Main Service and Access Control -----------------------

    rpc_api_tokens = config.get('intelligence_hub_web_service.rpc_api.tokens', [])
    collector_tokens = config.get('intelligence_hub_web_service.collector.tokens', [])

    rss_base_url = config.get('intelligence_hub_web_service.rss.host_prefix', 'http://127.0.0.1:5000')
    public_search_limits = config.get('intelligence_hub_web_service.public_search', None)

    access_manager = WebServiceAccessManager(
        rpc_api_tokens=rpc_api_tokens,
        collector_tokens=collector_tokens,
        user_manager=UserManager(DEFAULT_USER_DB_PATH),
        deny_on_empty_config=True)

    hub_service = IntelligenceHubWebService(
        intelligence_hub = hub,
        access_manager=access_manager,
        rss_publisher=RSSPublisher(rss_base_url),
        public_search_limits=public_search_limits,
        subsystem_registry=subsystem_registry,
        control_state=control_state,
    )

    hub_service.register_routers(wsgi_app)

    # 向量子系统的 /vector/ Blueprint（页面与 API）。仅当向量子系统装配成功时挂载；
    # 挂载同时把向量搜索的单一实现注入 Core Web 委托点，旧搜索入口行为不变。
    if vector_handles is not None:
        try:
            from VectorIntelligence.iis_integration import mount_to_iis as mount_vector_intelligence
            mount_vector_intelligence(
                wsgi_app,
                config=config,
                hub=hub,
                hub_service=hub_service,
                wrapper=hub_service.access_manager.login_required,
                doc_cleaner=exclude_raw_data,
                handles=vector_handles,
            )
        except Exception:
            logger.exception("向量子系统 Web 挂载失败，IIS 主服务继续运行。")

    # 态势视图是只读的可拆卸 Web 扩展：复用子系统注册表，但不进入 Hub 事件链。
    # 模块内部会隔离地名解析或路由初始化失败，避免影响 IIS 主服务启动。
    try:
        from SituationViews.iis_integration import mount_to_iis as mount_situation_views
        mount_situation_views(
            wsgi_app,
            config=config,
            subsystem_registry=subsystem_registry,
            wrapper=hub_service.access_manager.login_required,
            data_path=DATA_PATH,
        )
    except Exception:
        logger.exception("态势视图模块加载失败，IIS 主服务继续运行。")

    # --------------------------------- End of Init ---------------------------------

    return hub, hub_service, client_manager


# ----------------------------------------------------------------------------------------------------------------------


# ------------------------------------- Path --------------------------------------

def build_dirs():
    # TODO: All not-project files will be put in this path. It's good for docker deployment.
    Path(LOG_PATH).mkdir(parents=True, exist_ok=True)
    Path(DATA_PATH).mkdir(parents=True, exist_ok=True)
    Path(CONFIG_PATH).mkdir(parents=True, exist_ok=True)
    Path(EXPORT_PATH).mkdir(parents=True, exist_ok=True)
    Path(PRODUCTS_PATH).mkdir(parents=True, exist_ok=True)


# -------------------------------------- Log --------------------------------------

IIS_LOG_FILE = os.path.join(LOG_PATH, 'iis.log')
HISTORY_LOG_FOLDER = os.path.join(LOG_PATH, 'history_log')


def config_log():
    backup_and_clean_previous_log_file(IIS_LOG_FILE, HISTORY_LOG_FOLDER)

    setup_logging(IIS_LOG_FILE)
    setup_performance_logging(os.path.join(LOG_PATH, 'perf.log'))

    # Disable 3-party library's log
    limit_logger_level("core")
    limit_logger_level("base")
    limit_logger_level("asyncio")
    limit_logger_level("pymongo")
    limit_logger_level("waitress")
    limit_logger_level("connectionpool")
    limit_logger_level("WaitressServer")
    limit_logger_level("proactor_events")

    limit_logger_level("urllib3")
    limit_logger_level("urllib3.connection")
    limit_logger_level("urllib3.connectionpool")
    limit_logger_level("urllib3.poolmanager")
    limit_logger_level("urllib3.response")
    limit_logger_level("urllib3.util.retry")

    # My modules
    limit_logger_level("Tools.RequestTracer")
    limit_logger_level("Tools.DateTimeUtility")
    limit_logger_level("PyLoggingBackend.LoggerBackend")
    limit_logger_level("AIClientCenter.services.token_rotator")


def run():
    build_dirs()
    config_log()

    # --------------------------------- Config --------------------------------

    config = EasyConfig(
        config_file=DEFAULT_CONFIG_FILE,
        config_file_alter=DEFAULT_ALTER_CONFIG_FILE
    )

    logger.info('Apply config: ')
    logger.info(config.dump_text())

    # -------------------------------- Service ---------------------------------

    ihub, ihub_service, client_manager = start_intelligence_hub_service(config)

    log_backend = LoggerBackend(monitoring_file_path=IIS_LOG_FILE, cache_limit_count=100000,
                                link_file_roots={
                                    'conversation': os.path.abspath('conversation')
                                },
                                project_root=PRJ_PATH,
                                with_logger_manager=True)
    log_backend.register_router(app=wsgi_app, wrapper=ihub_service.access_manager.login_required)

    # 此面板挂在登录保护下；启用手动调用页以便验证指定 API/Harness Client。
    client_manager_backend = AIDashboardService(client_manager, enable_manual_calls=True)
    client_manager_backend.mount_to_app(
        app=wsgi_app,
        wrapper=ihub_service.access_manager.login_required,
        url_prefix='/monitor/ai-client-dashboard')

    # Monitor in the same process and the same service
    def _resolve_thread_name(tid: int):
        """将原生线程 ID 解析为 Python 线程名（仅对本进程有效）。"""
        for t in threading.enumerate():
            if t.ident == tid:
                return t.name
        return None

    monitor_update_interval = config.get('monitor.update_interval_sec', 5.0)
    monitor_thread_interval = config.get('monitor.thread_stats_interval_sec', 10.0)
    monitor_api = MonitorAPI(
        app=wsgi_app,
        wrapper=ihub_service.access_manager.login_required,
        prefix='/monitor',
        thread_name_resolver=_resolve_thread_name,
        update_interval_sec=monitor_update_interval,
        thread_stats_interval_sec=monitor_thread_interval,
    )
    self_pid = os.getpid()
    logger.info(f'Service PID: {self_pid}')
    monitor_api.monitor.add_process(self_pid)
    # 伴随进程（爬虫、向量库）可能单独调试或由独立 systemd 单元管理，启动顺序与
    # pid 都会变化；用周期重扫替代一次性注册。失效 pid 由 SystemMonitor 自行摘除
    # （NoSuchProcess 时 remove_process），这里只负责“加新”。
    companion_scripts = ('CrawlerServiceEngine.py', 'VectorDBBService.py')

    def refresh_companion_processes():
        for script_name in companion_scripts:
            try:
                for pid in find_processes(script_name):
                    if pid != self_pid and pid not in monitor_api.monitor.monitored_pids:
                        monitor_api.monitor.add_process(pid)
                        logger.info(f'Monitoring companion process: {script_name} (pid={pid})')
            except Exception:
                logger.warning(f'伴随进程监控扫描失败: {script_name}', exc_info=True)

    def _companion_monitor_worker():
        while True:
            refresh_companion_processes()
            time.sleep(60)

    refresh_companion_processes()
    threading.Thread(
        name='CompanionProcessMonitor', target=_companion_monitor_worker, daemon=True).start()
    monitor_api.start()
    ihub_service.set_monitor_api(monitor_api)

    # --------------------------- systemd watchdog ---------------------------
    watchdog_enabled = config.get('intelligence_hub.systemd_watchdog.enabled', True)
    watchdog_interval = config.get('intelligence_hub.systemd_watchdog.interval_sec', 10)
    watchdog_stop_event = None

    def _watchdog_worker():
        """后台线程：定时向 systemd 发送 WATCHDOG=1。"""
        if not is_watchdog_enabled():
            logger.info("systemd watchdog not enabled (NOTIFY_SOCKET/WATCHDOG_USEC not set). Skipping.")
            return

        notify_ready()
        logger.info(f"systemd watchdog started, interval={watchdog_interval}s")
        while not watchdog_stop_event.is_set():
            notify_alive()
            watchdog_stop_event.wait(watchdog_interval)
        notify_stopping()

    if watchdog_enabled:
        watchdog_stop_event = threading.Event()
        threading.Thread(name='SystemdWatchdog', target=_watchdog_worker, daemon=True).start()
    else:
        logger.info("systemd watchdog is disabled by config.")

    # ------------------------------------------------------------------------

    threading.Thread(name='ShowStatistics', target=partial(show_intelligence_hub_statistics_forever, ihub)).start()

try:
    run()
except Exception as e:
    print(str(e))
    print(traceback.format_exc())
finally:
    # 在进程退出前通知 systemd（如果启用 watchdog），这是 no-op on Windows/non-systemd。
    try:
        from Tools.SystemdWatchdog import notify_stopping
        notify_stopping()
    except Exception:
        pass
