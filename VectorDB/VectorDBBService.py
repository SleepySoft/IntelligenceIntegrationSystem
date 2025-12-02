import os
import logging
import argparse
import tempfile
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

        # --- Error Handling ---
        class ServiceUnavailable(Exception):
            pass

        @bp.errorhandler(ServiceUnavailable)
        def handle_service_unavailable(e):
            return jsonify({"error": str(e), "status": "initializing"}), 503

        # --- Helper Method ---
        def get_repo_strict(name: str):
            """
            Retrieves a repository strictly.
            Raises ServiceUnavailable if engine is loading.
            Raises ValueError if collection does not exist.
            """
            # 1. Check readiness
            if not self.engine.is_ready():
                status = self.engine.get_status()
                if status["status"] == "error":
                    # 500 Internal Server Error if init failed permanently
                    raise Exception(f"Engine failed to start: {status['error']}")
                else:
                    # 503 Service Unavailable if just loading
                    raise ServiceUnavailable("Engine is initializing")

            # 2. Retrieve Repo
            repo = self.engine.get_repository(name)
            if not repo:
                # Raise exception to be caught and returned as 404
                raise ValueError(f"Collection '{name}' not found. Please create it via POST /api/collections first.")
            return repo

        # --- Routes ---

        @route("/")
        def serve_ui():
            """Serves the Single Page Application."""
            if os.path.exists(self._frontend_path):
                return send_file(self._frontend_path)
            return "Frontend HTML not found.", 404

        @route("/api/status")
        def server_status():
            """Returns the engine initialization status."""
            return jsonify(self.engine.get_status())

        @route("/api/health")
        def health_check():
            return jsonify({"status": "ok", "service": "VectorDBService"})

        @route("/api/collections", methods=["POST"])
        def create_collection():
            """
            Creates a new collection or updates config of existing one.
            Body: { "name": str, "chunk_size": int, "chunk_overlap": int }
            """
            data = request.json or {}
            name = data.get("name")
            if not name:
                return jsonify({"error": "Collection name is required"}), 400

            chunk_size = int(data.get("chunk_size", 512))
            chunk_overlap = int(data.get("chunk_overlap", 50))

            try:
                # Use ensure_repository: creates if missing, updates config if exists
                repo = self.engine.ensure_repository(name, chunk_size, chunk_overlap)
                return jsonify({
                    "status": "success",
                    "message": f"Collection '{name}' ready.",
                    "config": repo.get_config()
                })
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @route("/api/collections/<name>/upsert", methods=["POST"])
        def upsert_document(name):
            try:
                # STRICT MODE: Only allow upsert to existing collections.
                # If the collection does not exist, get_repo_strict raises ValueError (404).
                repo = get_repo_strict(name)

                # Call the repository method (ensure method name matches your VectorStorageEngine implementation)
                # Usually 'add_document' or 'upsert_document' depending on your naming.
                chunk_ids = repo.upsert_document(
                    request.json['doc_id'],
                    request.json['text'],
                    request.json.get('metadata', {})
                )
                return jsonify({"status": "success", "chunks_created": len(chunk_ids)})

            except ValueError as e:
                return jsonify({"error": str(e)}), 404
            except ServiceUnavailable as e:
                return jsonify({"error": "Engine initializing", "retry_after": 5}), 503
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @route("/api/collections/<name>/stats", methods=["GET"])
        def get_stats(name):
            try:
                repo = get_repo_strict(name)
                return jsonify({"collection": name, "chunk_count": repo.count()})
            except ValueError as e:
                return jsonify({"error": str(e)}), 404
            except ServiceUnavailable as e:
                return jsonify({"error": "Engine initializing", "retry_after": 5}), 503
            except Exception as e:
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
                repo = get_repo_strict(name)
                results = repo.search(
                    query_text=query,
                    top_n=top_n,
                    score_threshold=score_threshold,
                    filter_criteria=filter_criteria
                )
                return jsonify(results)
            except ValueError as e:
                return jsonify({"error": str(e)}), 404
            except ServiceUnavailable as e:
                return jsonify({"error": "Engine initializing", "retry_after": 5}), 503
            except Exception as e:
                logger.error(f"Search failed: {e}")
                return jsonify({"error": str(e)}), 500

        @route("/api/collections/<name>/documents/<doc_id>", methods=["DELETE"])
        def delete_document(name, doc_id):
            try:
                repo = get_repo_strict(name)
                success = repo.delete_document(doc_id)
                if success:
                    return jsonify({"status": "success", "doc_id": doc_id})
                else:
                    return jsonify({"status": "warning", "message": "Document not found"}), 404
            except ValueError as e:
                return jsonify({"error": str(e)}), 404
            except ServiceUnavailable as e:
                return jsonify({"error": "Engine initializing", "retry_after": 5}), 503
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @route("/api/collections/<name>/clear", methods=["POST"])
        def clear_collection(name):
            try:
                repo = get_repo_strict(name)
                repo.clear()
                return jsonify({"status": "cleared", "collection": name})
            except ValueError as e:
                return jsonify({"error": str(e)}), 404
            except ServiceUnavailable as e:
                return jsonify({"error": "Engine initializing", "retry_after": 5}), 503
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @route("/api/admin/backup", methods=["GET"])
        def download_backup():
            """
            Triggers a backup and returns the zip file.
            """
            try:
                # Create a temporary directory to store the zip
                temp_dir = tempfile.mkdtemp()
                zip_path = self.engine.create_backup(temp_dir)

                # Send file and perform cleanup afterwards if possible
                filename = os.path.basename(zip_path)
                return send_file(
                    zip_path,
                    as_attachment=True,
                    download_name=filename,
                    mimetype='application/zip'
                )
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @route("/api/admin/restore", methods=["POST"])
        def upload_restore():
            """
            Accepts a zip file upload and restores the database.
            Restarting the service is recommended but Hot Reload is attempted.
            """
            if 'file' not in request.files:
                return jsonify({"error": "No file part"}), 400

            file = request.files['file']
            if file.filename == '':
                return jsonify({"error": "No selected file"}), 400

            if not file.filename.endswith('.zip'):
                return jsonify({"error": "Only .zip files are allowed"}), 400

            try:
                # Save uploaded file to temp
                temp_fd, temp_path = tempfile.mkstemp(suffix=".zip")
                os.close(temp_fd)
                file.save(temp_path)

                # Trigger engine restore
                self.engine.restore_backup(temp_path)

                # Cleanup upload
                os.remove(temp_path)

                return jsonify({"status": "success", "message": "Database restored and reloaded."})
            except Exception as e:
                return jsonify({"error": f"Restore failed: {str(e)}"}), 500

        # Get list of all collections
        @route("/api/collections", methods=["GET"])
        def list_all_collections():
            try:
                names = self.engine.list_collections()
                return jsonify({"collections": names})
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        # Paginated document browsing
        @route("/api/collections/<name>/documents", methods=["GET"])
        def list_documents(name):
            limit = int(request.args.get("limit", 20))
            offset = int(request.args.get("offset", 0))

            try:
                repo = get_repo_strict(name)
                data = repo.list_documents(limit=limit, offset=offset)
                return jsonify(data)
            except ValueError as e:
                return jsonify({"error": str(e)}), 404
            except ServiceUnavailable as e:
                return jsonify({"error": "Engine initializing", "retry_after": 5}), 503
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

    def run_standalone(self, host="0.0.0.0", port=8001, debug=False):
        """Run as a standalone Flask app."""
        app = Flask(__name__)
        
        # Mount at root for standalone usage
        self.mount_to_app(app, url_prefix="")
        
        print(f"Starting standalone VectorDB at http://{host}:{port}")
        app.run(host=host, port=port, debug=debug)


# --- Usage Examples ---


# ----------------------------------------------------------------------------------------------------------------------

if __name__ == "__main__":
    # --- Configuration Logic ---
    parser = argparse.ArgumentParser(description="VectorDB Standalone Service")

    # 1. Network Config
    parser.add_argument("--host", type=str,
                        default=os.getenv("VECTOR_HOST", "0.0.0.0"),
                        help="Host to bind (default: 0.0.0.0 or env VECTOR_HOST)")

    parser.add_argument("--port", type=int,
                        default=int(os.getenv("VECTOR_PORT", 8001)),
                        help="Port to bind (default: 8001 or env VECTOR_PORT)")

    # 2. Storage Config
    parser.add_argument("--db-path", type=str,
                        default=os.getenv("VECTOR_DB_PATH", "./chroma_data"),
                        help="Path to save vector data (default: ./chroma_data or env VECTOR_DB_PATH)")

    # 3. Model Config
    parser.add_argument("--model", type=str,
                        default=os.getenv("VECTOR_MODEL", "all-MiniLM-L6-v2"),
                        help="SentenceTransformer model name (default: all-MiniLM-L6-v2 or env VECTOR_MODEL)")

    args = parser.parse_args()

    print("=" * 50)
    print(f"Starting VectorDB Service")
    print(f" - Host:      {args.host}:{args.port}")
    print(f" - DB Path:   {args.db_path}")
    print(f" - Model:     {args.model}")
    print("=" * 50)

    # --- Initialization ---

    # 1. Initialize Engine with Config
    # Note: ensure directory exists or let Chroma create it
    os.makedirs(args.db_path, exist_ok=True)

    engine_instance = VectorStorageEngine(
        db_path=args.db_path,
        model_name=args.model
    )

    # 2. Initialize Service
    service = VectorDBService(engine=engine_instance)

    # 3. Run Standalone
    service.run_standalone(host=args.host, port=args.port, debug=False)
