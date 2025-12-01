import os
import logging
from typing import Optional, Callable, Dict, Any
from flask import Flask, Blueprint, request, jsonify, send_file, Response

# Import the core engine defined in the previous step
try:
    from VectorStorageEngine import VectorStorageEngine
except ImportError:
    from .VectorStorageEngine import VectorStorageEngine


# Configure Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class VectorDBService:
    """
    VectorDBService: The Web API Layer.
    
    Responsibilities:
    1. Wraps the VectorStorageEngine with a REST API.
    2. Serves the VectorDBFrontend.html UI.
    3. Can run standalone or mount onto an existing Flask app.
    """

    def __init__(self, engine: VectorStorageEngine, frontend_filename: str = "VectorDBFrontend.html"):
        """
        Args:
            engine (VectorStorageEngine): The initialized storage engine instance.
            frontend_filename (str): The HTML file name located in the same directory.
        """
        self.engine = engine
        self.frontend_filename = frontend_filename
        self._is_registered = False
        
        # Locate the frontend file relative to this script
        self._base_dir = os.path.dirname(os.path.abspath(__file__))
        self._frontend_path = os.path.join(self._base_dir, self.frontend_filename)

        if not os.path.exists(self._frontend_path):
            logger.warning(f"Frontend file not found at: {self._frontend_path}")

    def create_blueprint(self, wrapper: Optional[Callable] = None) -> Blueprint:
        """
        Creates the Flask Blueprint containing all API routes.
        
        Args:
            wrapper (Callable): Optional decorator to wrap all routes (e.g., for auth).
        """
        bp = Blueprint("vector_db", __name__, static_folder=None)

        # Helper to apply wrapper if it exists
        def route(rule, **options):
            def decorator(f):
                endpoint = options.pop("endpoint", None)
                if wrapper:
                    f = wrapper(f)
                bp.add_url_rule(rule, endpoint, f, **options)
                return f
            return decorator

        # --- Helper Method ---
        def get_repo(name: str):
            # Parse query params for config
            chunk_size = int(request.args.get("chunk_size", 512))
            chunk_overlap = int(request.args.get("chunk_overlap", 50))
            return self.engine.get_repository(name, chunk_size, chunk_overlap)

        # --- Routes ---

        @route("/")
        def serve_ui():
            """Serves the Single Page Application."""
            if os.path.exists(self._frontend_path):
                return send_file(self._frontend_path)
            return "Frontend HTML not found.", 404

        @route("/api/health")
        def health_check():
            return jsonify({"status": "ok", "service": "VectorDBService"})

        @route("/api/collections/<name>/stats", methods=["GET"])
        def get_stats(name):
            try:
                repo = get_repo(name)
                return jsonify({
                    "collection": name,
                    "chunk_count": repo.count()
                })
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @route("/api/collections/<name>/upsert", methods=["POST"])
        def upsert_document(name):
            """
            Expects JSON: { "doc_id": str, "text": str, "metadata": dict }
            """
            data = request.json or {}
            doc_id = data.get("doc_id")
            text = data.get("text")
            metadata = data.get("metadata", {})

            if not doc_id or not text:
                return jsonify({"error": "doc_id and text are required"}), 400

            try:
                repo = get_repo(name)
                # Perform the upsert (delete-then-insert)
                chunk_ids = repo.add_document(doc_id, text, metadata)
                return jsonify({
                    "status": "success",
                    "doc_id": doc_id,
                    "chunks_created": len(chunk_ids)
                })
            except Exception as e:
                logger.error(f"Upsert failed: {e}")
                return jsonify({"error": str(e)}), 500

        @route("/api/collections/<name>/search", methods=["POST"])
        def search(name):
            """
            Expects JSON: { "query": str, "top_n": int, "score_threshold": float, "filter_criteria": dict }
            """
            data = request.json or {}
            query = data.get("query")
            if not query:
                return jsonify({"error": "query string is required"}), 400

            top_n = data.get("top_n", 5)
            score_threshold = data.get("score_threshold", 0.0)
            filter_criteria = data.get("filter_criteria", None)

            try:
                repo = get_repo(name)
                results = repo.search(
                    query_text=query,
                    top_n=top_n,
                    score_threshold=score_threshold,
                    filter_criteria=filter_criteria
                )
                return jsonify(results)
            except Exception as e:
                logger.error(f"Search failed: {e}")
                return jsonify({"error": str(e)}), 500

        @route("/api/collections/<name>/documents/<doc_id>", methods=["DELETE"])
        def delete_document(name, doc_id):
            try:
                repo = get_repo(name)
                success = repo.delete_document(doc_id)
                if success:
                    return jsonify({"status": "success", "doc_id": doc_id})
                else:
                    return jsonify({"status": "warning", "message": "Document not found"}), 404
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @route("/api/collections/<name>/clear", methods=["POST"])
        def clear_collection(name):
            try:
                repo = get_repo(name)
                repo.clear()
                return jsonify({"status": "cleared", "collection": name})
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        return bp

    def mount_to_app(self, app: Flask, url_prefix: str = "/vector-db", wrapper: Optional[Callable] = None) -> bool:
        """
        Mount the dashboard to an existing Flask app instance.
        
        Args:
            app (Flask): The main application instance.
            url_prefix (str): Base URL for the dashboard (e.g. /vector-db).
            wrapper (Callable): Optional auth/logging wrapper for routes.
        """
        if self._is_registered:
            logger.warning("VectorDB blueprint already registered.")
            return False

        bp = self.create_blueprint(wrapper)
        app.register_blueprint(bp, url_prefix=url_prefix)
        self._is_registered = True
        logger.info(f"VectorDB Service mounted at {url_prefix}")
        return True

    def run_standalone(self, host="0.0.0.0", port=8000, debug=False):
        """Run as a standalone Flask app."""
        app = Flask(__name__)
        
        # Mount at root for standalone usage
        self.mount_to_app(app, url_prefix="")
        
        print(f"Starting standalone VectorDB at http://{host}:{port}")
        app.run(host=host, port=port, debug=debug)


# --- Usage Examples ---

if __name__ == "__main__":
    # 1. Initialize the heavy engine
    # Ideally, do this outside and pass it in, but for standalone script execution:
    engine_instance = VectorStorageEngine(
        db_path="./chroma_db_data", 
        model_name="paraphrase-multilingual-MiniLM-L12-v2"
    )

    # 2. Initialize the API Service
    service = VectorDBService(engine=engine_instance)

    # 3. Run Standalone
    service.run_standalone(port=8001, debug=False)
