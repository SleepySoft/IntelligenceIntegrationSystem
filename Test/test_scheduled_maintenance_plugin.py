from ServiceComponent.runtime import HubRuntime, ScheduledMaintenancePlugin


class FakeScheduler:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.jobs = []

    def start_scheduler(self):
        self.started = True

    def shutdown(self, wait=False):
        self.stopped = True


def test_scheduled_maintenance_is_composed_and_stopped_with_runtime():
    scheduler = FakeScheduler()
    bootstrapped = []
    plugin = ScheduledMaintenancePlugin(
        lambda: scheduler,
        lambda value: value.jobs.append("hourly"),
        lambda: bootstrapped.append(True),
    )
    runtime = HubRuntime()
    runtime.install(plugin)
    runtime.start()
    plugin._bootstrap_thread.join(timeout=1)
    runtime.stop()

    assert scheduler.started and scheduler.stopped
    assert scheduler.jobs == ["hourly"]
    assert bootstrapped == [True]
