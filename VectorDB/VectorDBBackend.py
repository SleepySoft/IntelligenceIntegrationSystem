# vector_api.py
"""
Vector Database API Component

包含 VectorApi 类，用于将其路由注册到现有的 Flask 应用中。
这个类必须在 VectorDB 准备就绪后才被实例化。
"""
import os
import sys
import logging
import time

from flask import Flask, jsonify, request, send_file
from typing import Callable, Optional, Dict


if __name__ == '__main__':
    self_path = os.path.dirname(os.path.abspath(__file__))
    sys.path.append(self_path)

    from VectorDBService import VectorDBService, VectorStoreManager
else:
    from .VectorDBService import VectorDBService, VectorStoreManager


logger = logging.getLogger(__name__)
self_path = os.path.dirname(os.path.abspath(__file__))


class VectorApi:
    """
    一个封装了所有向量数据库API端点的类。
    """

    def __init__(self, app: Flask, service: VectorDBService):
        """
        初始化API组件。

        Args:
            app (Flask): 现有的 Flask app 实例。
            service (VectorDBService): *已经就绪*的 VectorDBService 实例。
        """
        self.app = app
        self.service = service

        # (需求) 获取 store 管理器
        # 我们假设这些是固定的，如果不是，则需要动态获取
        self.stores_config = {
            "intelligence_full_text": {
                "chunk_size": 512,
                "chunk_overlap": 50,
                "description": "索引完整的 'APPENDIX.RAW_DATA' 文本。"
            },
            "intelligence_summary": {
                "chunk_size": 256,
                "chunk_overlap": 30,
                "description": "索引 'EVENT_TITLE' + 'EVENT_BRIEF' + 'EVENT_TEXT' 的组合。"
            }
        }

        # 从服务中获取实际的 store 句柄
        self.store_managers: Dict[str, VectorStoreManager] = {}
        for name, config in self.stores_config.items():
            self.store_managers[name] = service.get_store(
                collection_name=name,
                chunk_size=config["chunk_size"],
                chunk_overlap=config["chunk_overlap"]
            )

        print("[VectorApi] API 已成功初始化并连接到 VectorStoreManagers。")

    def _get_store_or_404(self, store_name: str) -> Optional[VectorStoreManager]:
        """一个辅助函数，用于获取 store 或返回 None（API将处理为404）"""
        return self.store_managers.get(store_name)

    # --- 路由注册 ---

    def register_routes(self, wrapper: Optional[Callable] = None):
        """
        (需求) 按照指定的模式注册所有 API 路由。

        Args:
            wrapper (Callable, optional):
                一个可选的装饰器 (例如 @login_required)
                将被应用于所有受保护的端点。
        """

        def maybe_wrap(fn):
            """如果提供了包装器，则应用它"""
            return wrapper(fn) if wrapper else fn

        # 简单的 lambda 用于包装公共端点 (如果 wrapper 为 None)
        public_route = lambda fn: fn

        self.app.add_url_rule('/vector/api/viewer', 'viewer',
                              public_route(self.db_viewer), methods=['GET'])

        # --- 公共/状态端点 ---
        self.app.add_url_rule('/vector/api/status', 'vector_status',
                              public_route(self.get_status), methods=['GET'])

        self.app.add_url_rule('/vector/api/stores', 'vector_stores_info',
                              public_route(self.get_stores_info), methods=['GET'])

        # --- 受保护的/核心功能端点 ---
        self.app.add_url_rule('/vector/api/browse', 'vector_browse',
                              maybe_wrap(self.browse), methods=['GET'])

        self.app.add_url_rule('/vector/api/search', 'vector_search',
                              maybe_wrap(self.search), methods=['POST'])

        self.app.add_url_rule('/vector/api/add', 'vector_add_document',
                              maybe_wrap(self.add_document), methods=['POST'])

        self.app.add_url_rule('/vector/api/delete', 'vector_delete_document',
                              maybe_wrap(self.delete_document), methods=['DELETE'])

    def db_viewer(self):
        return send_file(os.path.join(self_path, 'VectorDBViewer.html'))

    # --- API 端点实现 ---

    def get_status(self):
        """
        (前端使用) 检查后端 VectorDBService 的状态。
        """
        status_info = self.service.get_status()
        return jsonify(status_info)

    def get_stores_info(self):
        """
        (前端使用) 获取所有可用 store 的列表及其元数据。
        """
        info_list = []
        try:
            for name, manager in self.store_managers.items():
                info_list.append({
                    "name": name,
                    "description": self.stores_config[name].get("description", ""),
                    "chunk_count": manager.count()  # (需求) 让用户知道数据库的情况
                })
            return jsonify(info_list)
        except Exception as e:
            return jsonify({"error": f"获取 stores 信息失败: {e}"}), 500

    def browse(self):
        """
        (前端使用) 浏览一个 store 中的数据 (分页)。
        """
        store_name = request.args.get('store_name')
        try:
            limit = int(request.args.get('limit', 10))
            offset = int(request.args.get('offset', 0))
        except ValueError:
            return jsonify({"error": "limit 和 offset 必须是整数"}), 400

        store = self._get_store_or_404(store_name)
        if not store:
            return jsonify({"error": f"Store '{store_name}' 未找到"}), 404

        try:
            # .get() 是 ChromaDB 的原生方法
            total_count = store.count()
            results = store.collection.get(
                limit=limit,
                offset=offset,
                include=["metadatas", "documents", "embeddings"]
            )

            formatted_data = []

            # 检查 embeddings 列表是否存在
            embeddings_list = results.get('embeddings')

            for i in range(len(results['ids'])):

                embedding_data = None
                # 检查 embeddings_list 是否存在，并且当前项不为 None
                if len(embeddings_list) > 0 and embeddings_list[i] is not None:
                    embedding_data = embeddings_list[i].tolist()

                formatted_data.append({
                    "chunk_id": results['ids'][i],
                    "document": results['documents'][i],
                    "metadata": results['metadatas'][i],
                    "embedding": embedding_data
                })

            return jsonify({
                "data": formatted_data,
                "total_count": total_count,
                "limit": limit,
                "offset": offset
            })
        except Exception as e:
            return jsonify({"error": f"浏览失败: {str(e)}"}), 500

    def search(self):
        """
        (前端使用) 在一个 store 中搜索文本。
        """
        data = request.json
        store_name = data.get('store_name')
        query_text = data.get('query_text')
        top_n = int(data.get('top_n', 5))
        threshold = float(data.get('threshold', 0.5))

        if not query_text:
            return jsonify({"error": "缺少 'query_text'"}), 400

        store = self._get_store_or_404(store_name)
        if not store:
            return jsonify({"error": f"Store '{store_name}' 未找到"}), 404

        try:
            results = store.search(
                query_text=query_text,
                top_n=top_n,
                score_threshold=threshold
            )
            # (需求) 结果已按 doc_id 去重并包含分数
            return jsonify(results)
        except Exception as e:
            return jsonify({"error": f"搜索失败: {e}"}), 500

    def add_document(self):
        """
        (前端使用) 添加一个新文档。
        (设计决策: 同时添加到两个 store)
        """
        data = request.json
        uuid = data.get('uuid')
        full_text = data.get('full_text')
        summary_text = data.get('summary_text')

        if not uuid or not full_text or not summary_text:
            return jsonify({"error": "缺少 'uuid', 'full_text' 或 'summary_text'"}), 400

        try:
            # (需求) 让用户判断是否正确加入
            # 我们同时添加到两个 store
            store_full = self._get_store_or_404("intelligence_full_text")
            store_summary = self._get_store_or_404("intelligence_summary")

            if not store_full or not store_summary:
                return jsonify({"error": "一个或多个 store 未正确初始化"}), 500

            chunks_full = store_full.add_document(full_text, uuid)
            chunks_summary = store_summary.add_document(summary_text, uuid)

            return jsonify({
                "message": f"文档 {uuid} 添加成功",
                "uuid": uuid,
                "full_text_chunks": len(chunks_full),
                "summary_chunks": len(chunks_summary)
            }), 201  # 201 Created

        except Exception as e:
            return jsonify({"error": f"添加文档失败: {e}"}), 500

    def delete_document(self):
        """
        (前端使用) 删除一个文档 (从所有 stores 中)。
        """
        data = request.json
        uuid = data.get('uuid')

        if not uuid:
            return jsonify({"error": "缺少 'uuid'"}), 400

        try:
            # (设计决策) 从所有 store 中删除
            deleted_full = self.store_managers["intelligence_full_text"].delete_document(uuid)
            deleted_summary = self.store_managers["intelligence_summary"].delete_document(uuid)

            if not deleted_full and not deleted_summary:
                return jsonify({"error": f"UUID {uuid} 在任何 store 中都未找到"}), 404

            return jsonify({
                "message": f"文档 {uuid} 已从所有 stores 中删除",
                "uuid": uuid
            })
        except Exception as e:
            return jsonify({"error": f"删除文档失败: {e}"}), 500


# ----------------------------------------------------------------------------------------------------------------------

VECTOR_DB_PATH = "./vector_stores"
MODEL_NAME = 'paraphrase-multilingual-MiniLM-L12-v2'
HOST = "127.0.0.1"
PORT = 5100

def main():
    from flask_cors import CORS

    # --- (3) 协调启动 ---

    print("[Main App] 正在初始化 VectorDB (非阻塞)...")
    # 实例化服务。后台线程开始工作。
    service = VectorDBService(
        db_path=VECTOR_DB_PATH,
        model_name=MODEL_NAME
    )

    print("[Main App] 正在等待 VectorDB 准备就绪...")
    while True:
        status_info = service.get_status()
        if status_info["status"] == "ready":
            print("[Main App] VectorDB 已准备就绪！")
            break
        if status_info["status"] == "error":
            print(f"[Main App] 致命错误: VectorDB 启动失败: {status_info['error']}")
            sys.exit(1)

        print("[Main App] ...正在等待模型和数据库加载 (在后台)...")
        time.sleep(2)

    print("[Main App] 正在初始化 Flask 服务器...")
    app = Flask(__name__)
    # 为本地开发启用 CORS，允许 8080 端口的前端访问 5100 端口的后端
    # CORS(app, resources={r"/vector/api/*": {"origins": "http://127.0.0.1:8080"}})
    CORS(app)

    # --- (4) 实例化并注册 API ---
    # 关键: 将 *已经就绪* 的 service 实例传递给 VectorApi
    print("[Main App] 正在实例化并注册 VectorApi...")
    vector_api_component = VectorApi(app, service)

    # (需求) 调用 register_routes 方法
    # 我们没有登录包装器，所以传入 None
    vector_api_component.register_routes(wrapper=None)

    print("[Main App] API 路由注册完毕。")

    # --- (5) 添加一个根路由用于测试 ---
    @app.route('/')
    def index():
        return "VectorDB API 服务器正在运行。请在 5100 端口访问前端应用。"

    return app


if __name__ == '__main__':
    app = main()
    app.run(host=HOST, port=PORT, debug=False)