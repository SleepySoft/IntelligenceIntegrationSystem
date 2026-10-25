#!/usr/bin/env python3
"""
Universal WSGI Server Management and Monitoring Script
Function: Starts the IntelligenceHubLauncher application using either Waitress or Gunicorn,
          monitors its health status, and automatically restarts when necessary.
Design: Uses adapter pattern to support multiple WSGI servers with common interface.
"""

import sys
import time
import logging
import requests
import threading
import platform
import subprocess
from abc import ABC, abstractmethod

from GlobalConfig import resolve_iis_config_files
from IISConfig import load_iis_config


_launcher_config_file, _launcher_fallback_file = resolve_iis_config_files()
LAUNCHER_CONFIG = load_iis_config(
    _launcher_config_file, fallback_file=_launcher_fallback_file)
from IntelligenceHubStartup import wsgi_app

# ==================== CONFIGURATION SECTION ====================

# Server selection (auto-detection with manual override)
SERVER_TYPE = None  # Set to 'waitress', 'gunicorn', or None for auto-detection

# =================== Common server parameters ===================

HOST = LAUNCHER_CONFIG.settings.server.host
PORT = LAUNCHER_CONFIG.settings.server.port
THREADS = LAUNCHER_CONFIG.settings.server.threads
WORKERS = LAUNCHER_CONFIG.settings.server.workers

# ================== Gunicorn server parameters ==================

WORKER_CLASS = 'gevent'  # Worker type (gevent for async)
ACCESS_LOG = './access.log'  # Gunicorn access log
ERROR_LOG = './error.log'  # Gunicorn error log
MANAGER_LOG = './server_manager.log'  # Manager application log

# Application configuration
FLASK_APP_MODULE = "IntelligenceHubStartup"  # Your Python file name
FLASK_APP_INSTANCE = "wsgi_app"  # Your Flask app instance name


# ================== Health check configuration ==================

HEALTH_CHECK_URL = f"http://127.0.0.1:{PORT}/maintenance/ping"
HEALTH_CHECK_TIMEOUT = 10
CHECK_INTERVAL = 30
RESTART_COOLDOWN = 300  # Seconds between restart attempts

# Restart configuration
MAX_RESTART_ATTEMPTS = 5

# ======================= Log configuration =======================

LOG_LEVEL = logging.INFO
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
LOG_FILE = "./server_manager.log"

# ====================== Platform detection ======================

IS_WINDOWS = platform.system().lower() == 'windows'


class ServerAdapter(ABC):
    """Abstract base class for WSGI server adapters"""

    def __init__(self, host, port, workers, threads):
        self.host = host
        self.port = port
        self.workers = workers
        self.threads = threads
        self.process = None
        self.server_thread = None
        self.server_running = False
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def start_server(self):
        """Start the WSGI server"""
        pass

    @abstractmethod
    def stop_server(self):
        """Stop the WSGI server"""
        pass

    @abstractmethod
    def get_server_info(self):
        """Get server information for logging"""
        pass

    def is_running(self):
        """Check if server is running"""
        return self.server_running


# ==================== END CONFIGURATION ====================

class FlaskAppManager(ServerAdapter):
    """
    Adapter for Flask's built-in development server.
    Note: This is not suitable for production deployment.
    """

    def __init__(self, host, port, workers, threads):
        """
        Initialize Flask development server adapter.

        Args:
            host (str): Host address to bind to
            port (int): Port number to listen on
            workers (int): Not used in Flask dev server (kept for interface compatibility)
            threads (int): Not used in Flask dev server (kept for interface compatibility)
        """
        super().__init__(host, port, workers, threads)
        self.host = host
        self.port = port
        self.debug = True  # Flask development server typically runs in debug mode
        self.logger = logging.getLogger("FlaskAppManager")

    def start_server(self):
        """
        Start Flask development server.

        Returns:
            bool: True if server started successfully, False otherwise
        """
        try:
            self.logger.info(f"Starting Flask development server on {self.host}:{self.port}")
            self.logger.info(f"Flask server configuration: {self.get_server_info()}")
            self.logger.warning("Flask development server is for testing purposes only - not for production use")

            # Run Flask server in a separate thread
            def run_flask_server():
                try:
                    # Import and run the Flask app directly
                    from IntelligenceHubStartup import wsgi_app
                    wsgi_app.run(
                        host=self.host,
                        port=self.port,
                        debug=self.debug,
                        use_reloader=False  # Disable reloader to avoid subprocess creation
                    )
                except Exception as e:
                    self.logger.error(f"Flask server error: {str(e)}")
                finally:
                    self.server_running = False

            self.server_thread = threading.Thread(target=run_flask_server)
            self.server_thread.daemon = True
            self.server_thread.start()
            self.server_running = True

            # Wait for server to start
            time.sleep(3)

            # Verify server is running
            if self.server_thread.is_alive():
                self.logger.info("Flask development server started successfully")
                return True
            else:
                self.logger.error("Flask server thread failed to start")
                return False

        except Exception as e:
            self.logger.error(f"Failed to start Flask server: {str(e)}")
            return False

    def stop_server(self):
        """
        Stop Flask development server.
        Note: Flask development server doesn't have a clean shutdown API,
        so we rely on the daemon thread property for termination.
        """
        self.server_running = False
        self.logger.info("Flask development server stopped")

    def get_server_info(self):
        """
        Get Flask server configuration information.

        Returns:
            str: Server configuration details
        """
        return f"host={self.host}, port={self.port}, debug={self.debug}"


class WaitressServer(ServerAdapter):
    """Adapter for Waitress server"""

    def start_server(self):
        """Start Waitress server"""
        try:
            from waitress import serve

            self.logger.info(f"Starting Waitress server on {self.host}:{self.port}")
            self.logger.info(f"Waitress configuration: {self.get_server_info()}")

            # Run Waitress in a separate thread
            def run_waitress():
                try:
                    serve(wsgi_app, host=self.host, port=self.port, threads=self.threads)
                except Exception as e:
                    self.logger.error(f"Waitress server error: {str(e)}")
                finally:
                    self.server_running = False

            self.server_thread = threading.Thread(target=run_waitress)
            self.server_thread.daemon = True
            self.server_thread.start()
            self.server_running = True

            # Wait for server to start
            time.sleep(3)
            self.logger.info("Waitress server started successfully")
            return True

        except ImportError:
            self.logger.error("Waitress not installed. Please install with: pip install waitress")
            return False
        except Exception as e:
            self.logger.error(f"Failed to start Waitress: {str(e)}")
            return False

    def stop_server(self):
        """Stop Waitress server"""
        # Waitress runs in a thread, so we just mark it as stopped
        self.server_running = False
        self.logger.info("Waitress server stopped")

    def get_server_info(self):
        """Get Waitress configuration info"""
        return f"host={self.host}, port={self.port}, threads={self.threads}"


class GunicornServer(ServerAdapter):
    """Adapter for Gunicorn server"""

    def start_server(self):
        """Start Gunicorn server using subprocess"""
        try:
            # Build Gunicorn command
            gunicorn_cmd = [
                "gunicorn",
                "-b", f"{self.host}:{self.port}",
                "-w", str(self.workers),
                "-k", WORKER_CLASS,
                "--access-logfile", ACCESS_LOG,
                "--error-logfile", ERROR_LOG,
                "--pythonpath", ".",
                f"{FLASK_APP_MODULE}:{FLASK_APP_INSTANCE}"
            ]

            self.logger.info(f"Starting Gunicorn server with command: {' '.join(gunicorn_cmd)}")
            self.logger.info(f"Gunicorn configuration: {self.get_server_info()}")

            # Start Gunicorn process
            self.process = subprocess.Popen(
                gunicorn_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            self.server_running = True
            self.logger.info(f"Gunicorn started successfully, PID: {self.process.pid}")

            # Wait for server to start
            time.sleep(5)
            return True

        except Exception as e:
            self.logger.error(f"Failed to start Gunicorn: {str(e)}")
            return False

    def stop_server(self):
        """Stop Gunicorn server"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=10)
                self.logger.info("Gunicorn process terminated normally")
            except subprocess.TimeoutExpired:
                try:
                    self.process.kill()
                    self.logger.warning("Gunicorn process forcibly terminated")
                except Exception as e:
                    self.logger.error(f"Failed to kill Gunicorn process: {str(e)}")
            except Exception as e:
                self.logger.error(f"Error terminating Gunicorn process: {str(e)}")
            finally:
                self.server_running = False
        else:
            self.logger.info("Gunicorn server stopped")

    def get_server_info(self):
        """Get Gunicorn configuration info"""
        return f"host={self.host}, port={self.port}, workers={self.workers}"


class ServerManager:
    """Main server manager that handles both Waitress and Gunicorn"""

    def __init__(self):
        self.logger = None
        self.server = None
        self.restart_count = 0
        self.last_restart_time = 0
        self.setup_logging()
        self.determine_server_type()

    def setup_logging(self):
        """Configure logging"""
        self.logger = logging.getLogger("ServerManager")

    def determine_server_type(self):
        """Determine which server to use based on configuration and platform"""
        global SERVER_TYPE
        server_type = SERVER_TYPE

        # Use explicitly configured server type if specified
        if server_type:
            server_type_lower = server_type.lower()
            if server_type_lower == 'waitress':
                self.server = WaitressServer(HOST, PORT, WORKERS, THREADS)
                self.logger.info(f"Using explicitly configured server: Waitress")
            elif server_type_lower == 'gunicorn':
                if IS_WINDOWS:
                    self.logger.warning("Gunicorn is not recommended on Windows. Consider using Waitress instead.")
                self.server = GunicornServer(HOST, PORT, WORKERS, THREADS)
                self.logger.info(f"Using explicitly configured server: Gunicorn")
            elif server_type_lower == 'flask':
                self.server = FlaskAppManager(HOST, PORT, WORKERS, THREADS)
                self.logger.info(f"Using explicitly configured server: Flask Development Server")
            else:
                self.logger.error(f"Unknown server type: {server_type}. Using auto-detection.")
                server_type = None

        # Auto-detect if not explicitly configured
        if not server_type:
            if IS_WINDOWS:
                self.server = WaitressServer(HOST, PORT, WORKERS, THREADS)
                self.logger.info("Auto-selected Waitress server (Windows platform)")
            else:
                # Try to use Gunicorn on Unix systems, fall back to Waitress if not available
                try:
                    import gunicorn
                    self.server = GunicornServer(HOST, PORT, WORKERS, THREADS)
                    self.logger.info("Auto-selected Gunicorn server (Unix platform)")
                except ImportError:
                    self.logger.warning("Gunicorn not available. Falling back to Waitress.")
                    self.server = WaitressServer(HOST, PORT, WORKERS, THREADS)

    def check_health(self):
        """Check service health status"""
        try:
            response = requests.get(HEALTH_CHECK_URL, timeout=HEALTH_CHECK_TIMEOUT)
            if response.status_code == 200:
                self.logger.debug("Health check successful")
                return True
            else:
                self.logger.warning(f"Health check returned non-200 status: {response.status_code}")
                return False
        except requests.exceptions.RequestException as e:
            self.logger.warning(f"Health check request failed: {str(e)}")
            return False
        except Exception as e:
            self.logger.error(f"Unknown error during health check: {str(e)}")
            return False

    def restart_server(self):
        """Restart the server with cooldown and attempt limits"""
        current_time = time.time()

        # Check restart cooldown
        if current_time - self.last_restart_time < RESTART_COOLDOWN:
            remaining = int(RESTART_COOLDOWN - (current_time - self.last_restart_time))
            self.logger.warning(f"Still in restart cooldown period, can restart in {remaining} seconds")
            return False

        # Check maximum attempts
        if self.restart_count >= MAX_RESTART_ATTEMPTS:
            self.logger.error(f"Maximum restart attempts ({MAX_RESTART_ATTEMPTS}) reached")
            return False

        self.logger.info("Attempting to restart server...")

        # Stop current server
        self.server.stop_server()

        # Start new server
        success = self.server.start_server()
        if success:
            self.restart_count += 1
            self.last_restart_time = current_time
            self.logger.info(f"Restart successful (attempt {self.restart_count})")
        else:
            self.logger.error("Restart failed")

        return success

    def run(self):
        """Main run loop"""
        self.logger.info("Universal WSGI Server Manager starting")
        self.logger.info(f"Platform: {platform.system()} {platform.release()}")
        self.logger.info(f"Health check URL: {HEALTH_CHECK_URL}")
        self.logger.info(f"Check interval: {CHECK_INTERVAL} seconds")
        self.logger.info(f"Max restart attempts: {MAX_RESTART_ATTEMPTS}")
        self.logger.info(f"Restart cooldown: {RESTART_COOLDOWN} seconds")

        # Initial startup
        if not self.server.start_server():
            self.logger.error("Initial startup failed, script exiting")
            return

        # Main monitoring loop
        try:
            while True:
                # For Gunicorn, check if process is still running
                if isinstance(self.server, GunicornServer) and self.server.process:
                    if self.server.process.poll() is not None:
                        exit_code = self.server.process.poll()
                        self.logger.error(f"Server process exited with code: {exit_code}")
                        self.restart_server()

                # For Waitress, check if thread is still alive
                if isinstance(self.server, WaitressServer) and self.server.server_thread:
                    if not self.server.server_thread.is_alive():
                        self.logger.error("Server thread has stopped")
                        self.restart_server()

                # Optional: Health check (uncomment if needed)
                # if not self.check_health():
                #     self.logger.warning("Health check failed, attempting restart")
                #     self.restart_server()

                # Wait for next check
                time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            self.logger.info("Interrupt signal received, stopping server...")
        except Exception as e:
            self.logger.error(f"Unexpected error in monitoring loop: {str(e)}")
        finally:
            self.cleanup()

    def cleanup(self):
        """Clean up resources"""
        self.logger.info("Cleaning up resources...")
        self.server.stop_server()
        self.logger.info("Server manager exiting")


def create_server_adapter(server_type, host, port, workers, threads):
    """Factory function to create server adapters"""
    server_type_lower = server_type.lower()
    if server_type_lower == 'waitress':
        return WaitressServer(host, port, workers, threads)
    elif server_type_lower == 'gunicorn':
        return GunicornServer(host, port, workers, threads)
    elif server_type_lower == 'flask':
        return FlaskAppManager(host, port, workers, threads)
    else:
        raise ValueError(f"Unknown server type: {server_type}")


if __name__ == "__main__":
    manager = ServerManager()
    manager.run()
