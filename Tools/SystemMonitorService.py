"""
System Monitoring API Module.

This module provides a RESTful web interface for monitoring system resources
and specific processes, including per-thread CPU usage. It offers real-time
statistics in JSON format, along with an auto-refreshing HTML dashboard.

API Endpoints:
    GET    /api/stats              Retrieve complete system and process statistics
    GET    /api/system             Get system-wide statistics only
    GET    /api/processes          List all monitored processes with basic info
    GET    /api/process/<pid>      Get detailed statistics for specific process
    GET    /api/threads            Get per-thread stats for all monitored processes
    GET    /api/threads/<int:pid>  Get per-thread stats for a specific process
    POST   /api/process            Add a new process to monitoring
    DELETE /api/process/<pid>      Remove a process from monitoring
    GET    /api/dashboard          HTML dashboard with auto-refresh
"""

import os
import sys
import argparse
from flask import Flask, Blueprint, jsonify, request, abort, render_template

root_path = os.path.dirname(os.path.abspath(__file__))
sys.path.append(root_path)

from SystemMonitor import SystemMonitor

DEFAULT_PORT = 8000


class MonitorAPI:
    """Web API for system monitoring data and management."""

    def __init__(self,
                 app=None,
                 wrapper=None,
                 host: str = '0.0.0.0',
                 port: int = DEFAULT_PORT,
                 prefix: str = '',
                 thread_name_resolver=None):
        """
        Initialize the monitoring API.

        Args:
            app: External Flask app instance (optional). If provided, routes will be registered to this app.
            host: Host address to bind to (used only in standalone mode)
            port: Port number to listen on (used only in standalone mode)
            prefix: URL prefix for all routes (e.g., '/monitor')
            thread_name_resolver: Optional callable(tid)->name for resolving Python thread names.
        """
        self.host = host
        self.port = port
        self.prefix = prefix.rstrip('/')
        self.monitor = SystemMonitor(thread_name_resolver=thread_name_resolver)
        self.wrapper = wrapper or (lambda fn: fn)

        # Create a blueprint for all monitoring routes
        self.blueprint = Blueprint('monitor', __name__, template_folder='../templates')

        # Setup routes on the blueprint
        self._setup_routes()

        # Register blueprint with the provided app or create own app
        if app is not None:
            # External app mode: register blueprint to external app with prefix
            self.app = app
            app.register_blueprint(self.blueprint, url_prefix=self.prefix)
            self._is_standalone = False
        else:
            # Standalone mode: create own app and register blueprint with prefix
            self.app = Flask(__name__)
            self.app.register_blueprint(self.blueprint, url_prefix=self.prefix)
            self._is_standalone = True

    def _setup_routes(self):
        """Set up Flask routes for the API on the blueprint."""

        @self.blueprint.route('/api/stats', methods=['GET'])
        @self.wrapper
        def get_all_stats():
            """Get complete system, process and thread statistics."""
            return jsonify(self.monitor.get_all_stats())

        @self.blueprint.route('/api/system', methods=['GET'])
        @self.wrapper
        def get_system_stats():
            """Get system-wide statistics."""
            return jsonify(self.monitor.get_system_stats())

        @self.blueprint.route('/api/processes', methods=['GET'])
        @self.wrapper
        def get_processes():
            """Get list of monitored processes."""
            return jsonify(self.monitor.get_monitored_processes())

        @self.blueprint.route('/api/process/<int:pid>', methods=['GET'])
        @self.wrapper
        def get_process_stats(pid: int):
            """Get statistics for specific process."""
            stats = self.monitor.get_process_stats(pid)
            if not stats:
                abort(404, description=f"Process {pid} not found or not monitored")
            return jsonify(stats)

        @self.blueprint.route('/api/threads', methods=['GET'])
        @self.wrapper
        def get_all_threads():
            """Get per-thread stats for all monitored processes."""
            all_stats = self.monitor.get_all_stats()
            return jsonify(all_stats.get('threads', {}))

        @self.blueprint.route('/api/threads/<int:pid>', methods=['GET'])
        @self.wrapper
        def get_process_threads(pid: int):
            """Get per-thread stats for a specific process."""
            threads = self.monitor.get_thread_stats(pid)
            if threads is None:
                abort(404, description=f"Process {pid} not found or not monitored")
            return jsonify(threads)

        @self.blueprint.route('/api/process', methods=['POST'])
        @self.wrapper
        def add_process():
            """Add a process to monitoring."""
            data = request.get_json()
            if not data or 'pid' not in data:
                abort(400, description="PID required")

            pid = int(data['pid'])
            if self.monitor.add_process(pid):
                return jsonify({'status': 'success', 'pid': pid})
            else:
                abort(400, description=f"Could not monitor process {pid}")

        @self.blueprint.route('/api/process/<int:pid>', methods=['DELETE'])
        @self.wrapper
        def remove_process(pid: int):
            """Remove a process from monitoring."""
            if self.monitor.remove_process(pid):
                return jsonify({'status': 'success', 'pid': pid})
            else:
                abort(404, description=f"Process {pid} not found in monitoring list")

        @self.blueprint.route('/api/dashboard', methods=['GET'])
        @self.wrapper
        def get_dashboard():
            """HTML dashboard with detailed process and thread monitoring data."""
            stats = self.monitor.get_all_stats()
            return render_template(
                'monitor_dashboard.html',
                prefix=self.prefix,
                stats=stats
            )

    def start(self):
        """Start the monitoring system and web server (only in standalone mode)."""
        self.monitor.start_monitoring()
        if self._is_standalone:
            base_url = f"http://{self.host}:{self.port}{self.prefix}"
            print(f"Starting monitoring API on {base_url}")
            print(f" - Dashboard: {base_url}/api/dashboard")
            self.app.run(host=self.host, port=self.port, debug=False)
        else:
            print(f"Monitoring routes registered to external Flask application with prefix: {self.prefix}")

    def stop(self):
        """Stop the monitoring system."""
        self.monitor.stop_monitoring()


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='System Monitoring API')

    parser.add_argument('--host', default='0.0.0.0',
                        help='Host address to bind to')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT,
                        help='Port number to listen on')

    # 自定义 action：把空串或非数字串过滤掉
    class PidAction(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            pids = []
            for v in values:
                if v == '':          # 跳过显式空串
                    continue
                try:
                    pids.append(int(v))
                except ValueError:
                    parser.error(f"invalid PID {v!r}: must be an integer")
            setattr(namespace, self.dest, pids)

    parser.add_argument('--pid', nargs='*', action=PidAction, default=[],
                        help='PIDs to monitor initially (can be given multiple times)')
    parser.add_argument('--add-self', action='store_true',
                        help='Add current process to monitoring')
    return parser.parse_args()


def main():
    """Main entry point for the monitoring API (standalone mode)."""
    args = parse_arguments()

    # Create MonitorAPI instance in standalone mode (no external app provided)
    api = MonitorAPI(host=args.host, port=args.port)

    # Add initial PIDs if specified
    if args.pid:
        for pid in args.pid:
            if api.monitor.add_process(pid):
                print(f"Added PID {pid} to monitoring")
            else:
                print(f"Failed to add PID {pid}")

    # Add self if requested
    if args.add_self:
        self_pid = os.getpid()
        if api.monitor.add_process(self_pid):
            print(f"Added self (PID {self_pid}) to monitoring")

    try:
        api.start()
    except KeyboardInterrupt:
        print("Shutting down monitoring system...")
        api.stop()


if __name__ == '__main__':
    main()
