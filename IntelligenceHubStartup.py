import os
import time
import uuid
import logging
import datetime
import threading
import traceback
from pathlib import Path

from flask import Flask
from typing import Tuple
from functools import partial

from GlobalConfig import *
from IntelligenceHub import IntelligenceHub
from ServiceComponent.AIServiceRotator import SiliconFlowServiceRotator
from Tools.MongoDBAccess import MongoDBStorage
from Tools.OpenAIClient import OpenAICompatibleAPI
from Tools.SystemMonitorService import MonitorAPI
from Tools.SystemMonotorLauncher import start_system_monitor
from MyPythonUtility.easy_config import EasyConfig
from ServiceComponent.UserManager import UserManager
from ServiceComponent.RSSPublisher import RSSPublisher
# from Tools.VectorDatabase import VectorDatabase
from IntelligenceHubWebService import IntelligenceHubWebService, WebServiceAccessManager
from PyLoggingBackend import setup_logging, backup_and_clean_previous_log_file, limit_logger_level, LoggerBackend
from VectorDB.VectorDBService import VectorDBService

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


def start_intelligence_hub_service() -> Tuple[IntelligenceHub, IntelligenceHubWebService]:
    config = EasyConfig()

    logger.info('Apply config: ')
    logger.info(config.dump_text())

    # ---------------------------------- Path ----------------------------------

    # TODO: All generated data will be put in this path. It's good for docker deployment.
    data_path = os.path.join(self_path, 'data')

    Path(data_path).mkdir(parents=True, exist_ok=True)

    # ------------------------------- AI Service -------------------------------

    ai_service_url = config.get('intelligence_hub.ai_service.url', OPEN_AI_API_BASE_URL_SELECT)
    ai_service_token = config.get('intelligence_hub.ai_service.token', 'Sleepy')
    ai_service_model = config.get('intelligence_hub.ai_service.model', MODEL_SELECT)
    ai_service_proxies = config.get('intelligence_hub.ai_service.proxies', None)

    api_client = OpenAICompatibleAPI(
        api_base_url=ai_service_url,
        token=ai_service_token,
        default_model=ai_service_model,
        proxies=ai_service_proxies
    )

    # ------------------------------- Vector DB --------------------------------

    vector_enabled = config.get('intelligence_hub.vectordb.enabled', False)
    vector_db_path = config.get('intelligence_hub.vectordb.vector_db_path', '')
    embedding_model_name = config.get('intelligence_hub.vectordb.embedding_model_name', '')
    vector_stores = config.get('intelligence_hub.vectordb.stores', [])

    if vector_enabled and vector_db_path and embedding_model_name:
        # Start from here: put vector db in data path.
        vector_db_path_abs = vector_db_path \
            if os.path.isabs(vector_db_path) \
            else os.path.join(data_path, vector_db_path)

        vector_db_service = VectorDBService(
            db_path = vector_db_path_abs,
            model_name = embedding_model_name,
            store_config = vector_stores
        )
    else:
        vector_db_service = None

    # ------------------------------- Core: IHub -------------------------------

    ref_host_url = config.get('intelligence_hub_web_service.service.host_url', 'http://127.0.0.1:5000')

    mongodb_host = config.get('mongodb.host', 'localhost')
    mongodb_port = config.get('mongodb.port', 27017)
    mongodb_user = config.get('mongodb.user', '')
    mongodb_pass = config.get('mongodb.password', '')

    hub = IntelligenceHub(
        ref_url=ref_host_url,

        vector_db_service=vector_db_service,

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

        db_recommendation=MongoDBStorage(
            host=mongodb_host,
            port=mongodb_port,
            db_name='IntelligenceIntegrationSystem',
            username=mongodb_user,
            password=mongodb_pass,
            collection_name='intelligence_recommendation'),

        ai_client = api_client
    )
    hub.startup()

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

    # --------------------------------- Key Rotator ---------------------------------

    key_rotator_enabled = config.get('ai_service_rotator.enabled', False)
    key_rotator_key_file = config.get('ai_service_rotator.key_file', '')
    key_rotator_threshold = config.get('ai_service_rotator.threshold', 0.5)

    if key_rotator_enabled and key_rotator_key_file:
        logger.info(f'AI Service Key Rotator Enabled. key file: '
                    f'{key_rotator_key_file}, threshold: {key_rotator_threshold}')

        ai_token_rotator = SiliconFlowServiceRotator(
            ai_client=api_client,
            keys_file=os.path.join(self_path, key_rotator_key_file),
            threshold=float(key_rotator_threshold)
        )

        quit_flag = threading.Event()
        rotator_thread = threading.Thread(
            target=ai_token_rotator.run_forever,
            args=(quit_flag,),
            name="KeyRotatorThread",
            daemon=True
        )
        rotator_thread.start()

    # --------------------------------- End of Init ---------------------------------

    return hub, hub_service


# ----------------------------------------------------------------------------------------------------------------------

IIS_LOG_FILE = 'iis.log'
HISTORY_LOG_FOLDER = 'history_log'


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
    limit_logger_level("ServiceComponent.AIServiceRotator", level=logging.INFO)


def run():
    config_log()

    ihub, ihub_service = start_intelligence_hub_service()

    log_backend = LoggerBackend(monitoring_file_path=IIS_LOG_FILE, cache_limit_count=100000,
                                link_file_roots={
                                    'conversation': os.path.abspath('conversation')
                                },
                                project_root=self_path,
                                with_logger_manager=True)
    log_backend.register_router(app=wsgi_app, wrapper=ihub_service.access_manager.login_required)

    # Monitor in the same process and the same service
    monitor_api = MonitorAPI(app=wsgi_app, wrapper=ihub_service.access_manager.login_required, prefix='/monitor')
    self_pid = os.getpid()
    logger.info(f'Service PID: {self_pid}')
    monitor_api.monitor.add_process(self_pid)
    monitor_api.start()

    # Monitor in standalone process
    start_system_monitor()

    threading.Thread(target=partial(show_intelligence_hub_statistics_forever, ihub)).start()

try:
    run()
except Exception as e:
    print(str(e))
    print(traceback.format_exc())
finally:
    pass
