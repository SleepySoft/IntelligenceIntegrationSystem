import os
import time
import urllib
import threading

from typing import Optional
from urllib.parse import urljoin

from flask_cors import CORS
from flask import Flask, jsonify, request, send_file, render_template

# Import the core logic from the previous step
from Tools.governance_core import GovernanceManager, TaskType, Status

self_path = os.path.dirname(os.path.abspath(__file__))


class CrawlerGovernanceBackend:
    def __init__(self,
                 governor: GovernanceManager,
                 host: Optional[str] = "0.0.0.0",
                 port: Optional[int] = 8002,
                 app: Optional[Flask] = None,
                 base_url: Optional[str] = ''
                 ):
        """
        Initialize the Governance Backend Service

        Args:
            governor: Instance of GovernanceManager
            host: Host address for the web service
            port: Port for the web service
            base_url: The prefix of URL
        """
        self.governor = governor
        self.host = host
        self.port = port
        self.app = app
        self.base_url = base_url

        self.own_app = not app
        self.flask_thread = None

        # Last validation time for health checks
        self.last_validation_time = time.time()

    def start_service(self, blocking: bool = False):
        """
        Start the Flask web service either in blocking mode or in a background thread.

        Args:
            blocking: If True, runs in foreground and blocks execution;
                     if False, runs in background thread
        """
        if not self.app:
            self.app = Flask(__name__)
            self.app.secret_key = os.urandom(24)  # Secret key for session management
            CORS(self.app)

        self._register_routes(wrapper=None)

        # Set up template and static file serving
        self.app.template_folder = self_path

        if self.own_app:
            if blocking:
                # Run in foreground (blocking)
                print(f"Starting Governance API Server on http://{self.host}:{self.port}")
                self.app.run(debug=True, host=self.host, port=self.port, use_reloader=False, threaded=True)
            else:
                # Run in background thread (non-blocking)
                def run_flask():
                    """Run Flask app in a separate thread"""
                    print(f"Starting Governance API Server on http://{self.host}:{self.port}")
                    self.app.run(
                        debug=True,
                        host=self.host,
                        port=self.port,
                        use_reloader=False,
                        threaded=True
                    )

                # Create and start daemon thread
                self.flask_thread = threading.Thread(
                    target=run_flask,
                    daemon=True
                )
                self.flask_thread.start()

                # Wait briefly for server initialization
                time.sleep(1)
                print(f"Flask server running in background on http://{self.host}:{self.port}")

    def _register_routes(self, wrapper):
        """
        Register all governance routes to the Flask application

        Args:
            wrapper: Optional wrapper function for routes
        """
        def maybe_wrap(fn):
            return wrapper(fn) if wrapper else fn

        def build_url(endpoint: str) -> str:
            return urljoin(self.base_url, endpoint)

        # Dashboard and monitoring endpoints
        self.app.add_url_rule(build_url('/'), 'read_root', maybe_wrap(self.read_root))

        self.app.add_url_rule(build_url('/api/dashboard/stats'), 'get_dashboard_stats',
                              maybe_wrap(self.get_dashboard_stats), methods=['GET'])
        self.app.add_url_rule(build_url('/api/tasks'), 'get_tasks',
                              maybe_wrap(self.get_tasks), methods=['GET'])
        self.app.add_url_rule(build_url('/api/logs'), 'get_logs',
                              maybe_wrap(self.get_logs), methods=['GET'])
        self.app.add_url_rule(build_url('/api/snapshot/<file_hash>'), 'get_snapshot',
                              maybe_wrap(self.get_snapshot), methods=['GET'])

        # RPC endpoints for external spider processes
        self.app.add_url_rule(build_url('/rpc/register_task'), 'rpc_register_task',
                              maybe_wrap(self.rpc_register_task), methods=['POST'])
        self.app.add_url_rule(build_url('/rpc/should_crawl'), 'rpc_should_crawl',
                              maybe_wrap(self.rpc_should_crawl), methods=['POST'])
        self.app.add_url_rule(build_url('/rpc/report_result'), 'rpc_report_result',
                              maybe_wrap(self.rpc_report_result), methods=['POST'])

    # ------------------------------------------ Web Service Methods ------------------------------------------

    def read_root(self):
        """Render the main dashboard page"""
        return render_template("governance_frontend.html")

    def get_dashboard_stats(self):
        """Aggregates high-level statistics directly via the DB handler"""
        if not self.governor:
            return jsonify({"error": "GovernanceManager not initialized"}), 500

        db = self.governor.db

        # Active spiders (last seen in 5 mins - theoretical implementation)
        # Here we just count unique spiders in logs
        spiders = db.fetch_one("SELECT count(DISTINCT spider) as cnt FROM crawl_log")

        # Success Rate (Last 1000 items)
        success = db.fetch_one("""
            SELECT 
                avg(CASE WHEN status=1 THEN 1 ELSE 0 END) as rate,
                count(*) as total
            FROM crawl_log ORDER BY id DESC LIMIT 1000
        """)

        # Network Errors (Temp Fail) today
        net_errors = db.fetch_one("""
            SELECT count(*) as cnt FROM crawl_log 
            WHERE status=2 AND created_at > date('now')
        """)

        return jsonify({
            "active_spiders": spiders['cnt'],
            "success_rate": round((success['rate'] or 0) * 100, 1),
            "total_requests": success['total'],
            "network_errors": net_errors['cnt']
        })

    def get_tasks(self):
        """Fetch recurrent task registry"""
        if not self.governor:
            return jsonify({"error": "GovernanceManager not initialized"}), 500

        cur = self.governor.db.conn.cursor()
        cur.execute("SELECT * FROM task_registry ORDER BY next_run ASC")
        tasks = [dict(row) for row in cur.fetchall()]
        return jsonify(tasks)

    def get_logs(self):
        """Fetch streaming logs for the 'Waterfall' view"""
        if not self.governor:
            return jsonify({"error": "GovernanceManager not initialized"}), 500

        limit = request.args.get('limit', 100, type=int)
        status = request.args.get('status', type=int)

        query = "SELECT * FROM crawl_log"
        params = []

        if status is not None:
            query += " WHERE status = ?"
            params.append(status)

        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        cur = self.governor.db.conn.cursor()
        cur.execute(query, tuple(params))
        logs = [dict(row) for row in cur.fetchall()]
        return jsonify(logs)

    def get_snapshot(self, file_hash: str):
        """Serve the local HTML snapshot content"""
        # Security check: verify hash format to prevent directory traversal
        if not file_hash.isalnum():
            return jsonify({"error": "Invalid hash"}), 400

        # In a real app, we need to find which folder it is in.
        # For optimization, we might store relative path in DB.
        # Here we search for demo purposes:
        import glob
        files = glob.glob(f"data/files/*/*/{file_hash}*")
        if not files:
            return jsonify({"error": "Snapshot not found"}), 404

        return send_file(files[0])

    # ------------------------------------------ RPC Endpoints ------------------------------------------
    # These endpoints allow external python scripts to use the governance logic
    # without touching the DB file directly.

    def rpc_register_task(self):
        """Register a new task via RPC"""
        if not self.governor:
            return jsonify({"error": "GovernanceManager not initialized"}), 500

        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        # Validate required fields
        required_fields = ['spider', 'group', 'url', 'interval']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        self.governor.spider_name = data['spider']  # Context switch
        self.governor.register_task(data['url'], data['group'], data['interval'])
        return jsonify({"status": "ok"})

    def rpc_should_crawl(self):
        """
        External process asks: 'Should I crawl this?'
        Manager checks DB logic (intervals, retries, etc.)
        """
        if not self.governor:
            return jsonify({"error": "GovernanceManager not initialized"}), 500

        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        # Validate required fields
        required_fields = ['url', 'spider', 'task_type']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        t_type = TaskType.LIST if data['task_type'] == "LIST" else TaskType.ARTICLE
        self.governor.spider_name = data['spider']
        result = self.governor.should_crawl(data['url'], t_type)
        return jsonify({"should_crawl": result})

    def rpc_report_result(self):
        """
        External process reports: 'I finished this task.'
        Manager writes to DB and handles retry logic.
        """
        if not self.governor:
            return jsonify({"error": "GovernanceManager not initialized"}), 500

        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        # Validate required fields
        required_fields = ['spider', 'group', 'url', 'task_type', 'status']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        t_type = TaskType.LIST if data['task_type'] == "LIST" else TaskType.ARTICLE

        # We manually commit using the internal method for RPC support
        # In a real RPC system, we might pass a transaction ID, but here we report atomic results
        self.governor._commit_transaction(
            task_type=t_type,
            spider=data['spider'],
            group=data['group'],
            url=data['url'],
            status=Status(data['status']),
            duration=data.get('duration', 0.0),
            http_code=data.get('http_code', 0),
            error_msg=data.get('error_msg'),
            content_path=None  # File upload not implemented in simple RPC demo
        )
        return jsonify({"status": "acked"})


# ----------------------------------------------------------------------------------------------------------------------
# Helper functions for backward compatibility

def start_governance_web_service(governor: GovernanceManager, host="0.0.0.0", port=8002):
    """
    Start the governance web service (blocking)

    Args:
        governor: Instance of GovernanceManager
        host: Host address
        port: Port number
    """
    backend = CrawlerGovernanceBackend(governor=governor, host=host, port=port)
    backend.start_service(blocking=True)


def start_governance_web_service_async(governor: GovernanceManager, host="0.0.0.0", port=8002) -> threading.Thread:
    """
    Start the governance web service in a background thread

    Args:
        governor: Instance of GovernanceManager
        host: Host address
        port: Port number

    Returns:
        threading.Thread: The background thread running the service
    """
    backend = CrawlerGovernanceBackend(governor=governor, host=host, port=port)
    backend.start_service(blocking=False)
    return backend.flask_thread


if __name__ == "__main__":
    # Example usage
    backend = CrawlerGovernanceBackend(GovernanceManager('Demo'), host="0.0.0.0", port=8002)
    backend.start_service(blocking=True)
