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

from AIClientCenter.AIClientManagerBackend import AIDashboardService
from GlobalConfig import *
from IntelligenceHub import IntelligenceHub
from Tools.MongoDBAccess import MongoDBStorage
from Tools.SystemMonitorService import MonitorAPI
from MyPythonUtility.easy_config import EasyConfig
from ServiceComponent.UserManager import UserManager
from ServiceComponent.RSSPublisher import RSSPublisher
from AIClientCenter.AIClients import OuterTokenRotatingOpenAIClient
from AIClientCenter.AIClientManager import AIClientManager
from Tools.SystemMonotorLauncher import start_system_monitor
from AIClientCenter.OpenAICompatibleAPI import OpenAICompatibleAPI
from AIClientCenter.AIServiceTokenRotator import SiliconFlowServiceRotator
from MyPythonUtility.proc_utils import find_processes, kill_processes, start_program
from IntelligenceHubWebService import IntelligenceHubWebService, WebServiceAccessManager
from PyLoggingBackend import setup_logging, backup_and_clean_previous_log_file, limit_logger_level, LoggerBackend
from VectorDB.VectorDBClient import VectorDBClient

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


def show_intelligence_hub_statistics_forever(hub: IntelligenceHub):
    prev_statistics = {}
    while True:
        if hub.statistics != prev_statistics:
            logger.info(f'Hub queue size: {hub.statistics}')
            prev_statistics = hub.statistics
        time.sleep(2)


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

    client_manager = AIClientManager()

    logger.info(f"AI clients count: {len(AI_CLIENTS)}).")
    logger.info(f"AI group limit count: {len(AI_CLIENT_LIMIT)}).")

    for client in AI_CLIENTS.values():
        client_manager.register_client(client)

    for group, limit in AI_CLIENT_LIMIT.items():
        client_manager.set_group_limit(group, limit)

    return client_manager


def check_start_vector_db_service(config: EasyConfig, force_restart: bool = False):
    vector_enabled = config.get('intelligence_hub.vectordb.enabled', False)
    vector_db_port = config.get('intelligence_hub.vectordb.vector_db_port', 8001)
    vector_db_path = config.get('intelligence_hub.vectordb.vector_db_path', '')
    embedding_model_name = config.get('intelligence_hub.vectordb.embedding_model_name', '')
    vector_stores = config.get('intelligence_hub.vectordb.stores', [])

    vector_db_client = None
    if vector_enabled and vector_db_path and embedding_model_name:
        vector_db_path_abs = vector_db_path \
            if os.path.isabs(vector_db_path) \
            else os.path.join(DATA_PATH, vector_db_path)

        need_launch = False
        pids = find_processes('VectorDBBService.py')
        if pids:
            if force_restart:
                need_launch = True
                killed_count = kill_processes(pids)
                logger.info(f"Found running vector db service {', '.join(str(pids))}, killed {killed_count}.")
            else:
                logger.info(f"Found running vector db service {', '.join(str(pids))}, ignore.")
        else:
            need_launch = True

        if need_launch:
            # vector_db_service_path_abs = os.path.join(self_path, 'VectorDB', 'VectorDBBService.py')
            # command_line = f"python "\
            #                f"{vector_db_service_path_abs} "\
            #                f"--host 127.0.0.1 "\
            #                f"--port {str(vector_db_port)} "\
            #                f"--db-path {vector_db_path_abs} "\
            #                f"--model {embedding_model_name}"
            # logger.info(f"Starting vector DB service, command: `{command_line}`")
            # start_program(command_line, background=True, no_window=False)
            pass

        vector_db_client = VectorDBClient(f"http://localhost:{str(vector_db_port)}")

    return vector_db_client


def start_intelligence_hub_service() -> Tuple[IntelligenceHub, IntelligenceHubWebService, AIClientManager]:
    config = EasyConfig(DEFAULT_CONFIG_FILE)

    logger.info('Apply config: ')
    logger.info(config.dump_text())

    # ------------------------------- AI Service -------------------------------

    client_manager = build_ai_client_manager(config)
    client_manager.start_monitoring()

    # ------------------------------- Vector DB --------------------------------

    vector_db_client = check_start_vector_db_service(config)

    # ------------------------------- Core: IHub -------------------------------

    ai_analysis_thread = config.get('intelligence_hub.ai_analysis_thread', 1)
    ref_host_url = config.get('intelligence_hub_web_service.service.host_url', 'http://127.0.0.1:5000')

    mongodb_host = config.get('mongodb.host', 'localhost')
    mongodb_port = config.get('mongodb.port', 27017)
    mongodb_user = config.get('mongodb.user', '')
    mongodb_pass = config.get('mongodb.password', '')

    hub = IntelligenceHub(
        ref_url=ref_host_url,

        vector_db_client=vector_db_client,

        db_cache=MongoDBStorage(
            host=mongodb_host,
            port=mongodb_port,
            db_name='IntelligenceIntegrationSystem',
            username=mongodb_user,
            password=mongodb_pass,
            collection_name='intelligence_cached'),

        db_archive=MongoDBStorage(
            host=mongodb_host,
            port=mongodb_port,
            db_name='IntelligenceIntegrationSystem',
            username=mongodb_user,
            password=mongodb_pass,
            collection_name='intelligence_archived'),

        db_low_value=MongoDBStorage(
            host=mongodb_host,
            port=mongodb_port,
            db_name='IntelligenceIntegrationSystem',
            username=mongodb_user,
            password=mongodb_pass,
            collection_name='intelligence_low_value'),

        db_recommendation=MongoDBStorage(
            host=mongodb_host,
            port=mongodb_port,
            db_name='IntelligenceIntegrationSystem',
            username=mongodb_user,
            password=mongodb_pass,
            collection_name='intelligence_recommendation'),

            ai_client_manager = client_manager
    )
    hub.startup(ai_analysis_thread)

    # ----------------------- Main Service and Access Control -----------------------

    rpc_api_tokens = config.get('intelligence_hub_web_service.rpc_api.tokens', [])
    collector_tokens = config.get('intelligence_hub_web_service.collector.tokens', [])
    processor_tokens = config.get('intelligence_hub_web_service.processor.tokens', [])

    rss_base_url = config.get('intelligence_hub_web_service.rss.host_prefix', 'http://127.0.0.1:5000')

    access_manager = WebServiceAccessManager(
        rpc_api_tokens=rpc_api_tokens,
        collector_tokens=collector_tokens,
        processor_tokens=processor_tokens,
        user_manager=UserManager(DEFAULT_USER_DB_PATH),
        deny_on_empty_config=True)

    hub_service = IntelligenceHubWebService(
        intelligence_hub = hub,
        access_manager=access_manager,
        rss_publisher=RSSPublisher(rss_base_url)
    )

    hub_service.register_routers(wsgi_app)

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
    limit_logger_level("AIClientCenter.AIServiceTokenRotator")


def run():
    build_dirs()
    config_log()

    # -------------------------------- Service ---------------------------------

    ihub, ihub_service, client_manager = start_intelligence_hub_service()

    log_backend = LoggerBackend(monitoring_file_path=IIS_LOG_FILE, cache_limit_count=100000,
                                link_file_roots={
                                    'conversation': os.path.abspath('conversation')
                                },
                                project_root=PRJ_PATH,
                                with_logger_manager=True)
    log_backend.register_router(app=wsgi_app, wrapper=ihub_service.access_manager.login_required)

    client_manager_backend = AIDashboardService(client_manager)
    client_manager_backend.mount_to_app(
        app=wsgi_app,
        wrapper=ihub_service.access_manager.login_required,
        url_prefix='/monitor/ai-client-dashboard')

    # Monitor in the same process and the same service
    monitor_api = MonitorAPI(app=wsgi_app, wrapper=ihub_service.access_manager.login_required, prefix='/monitor')
    self_pid = os.getpid()
    logger.info(f'Service PID: {self_pid}')
    monitor_api.monitor.add_process(self_pid)
    monitor_api.start()

    # Monitor in standalone process
    start_system_monitor()

    threading.Thread(name='ShowStatistics', target=partial(show_intelligence_hub_statistics_forever, ihub)).start()

try:
    run()
except Exception as e:
    print(str(e))
    print(traceback.format_exc())
finally:
    pass
