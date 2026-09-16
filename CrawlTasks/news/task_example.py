import time
import logging


logger = logging.getLogger(__name__)
context = None


def module_init(service_context):
    if service_context:
        global logger
        global context
        context = service_context           # Keep context if necessary
        logger = context.logger or logger   # Use service logger if necessary
        # Optional - Actually in most case plugin will share the same sys with main project.
        service_context.solve_import_path()


def start_task(stop_event):
    # It's OK to make a loop here. But don't forget to check stop_event.
    # Actually the service will drive this function in an infinite loop.
    for _ in range(0, 10):
        if not stop_event.is_set():
            break
        print("Example task is running...")
        time.sleep(5)
