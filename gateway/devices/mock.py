"""Selectable MCU behavior with the same command/feedback boundary as future BLE."""
import asyncio

from gateway.contracts import DeviceCommand, Feedback, MockSetting


class MockDevices:
    def __init__(self, timeout_seconds):
        self.timeout_seconds = timeout_seconds
        self.settings = {name: MockSetting(device=name, outcome="success") for name in ("lights", "pump")}
        self.executions = {}
        self.results = {}

    def configure(self, setting):
        self.settings[setting.device] = setting

    def snapshot(self):
        return {name: value.model_dump() for name, value in self.settings.items()}

    async def execute(self, command: DeviceCommand):
        # A retry of the same ID shares its execution, rather than actuating twice.
        if command.command_id in self.results:
            return self.results[command.command_id]
        task = self.executions.get(command.command_id)
        if task is None:
            scenario = self.settings[command.device].model_copy()
            task = asyncio.create_task(self._execute(command, scenario))
            self.executions[command.command_id] = task
        return await asyncio.shield(task)

    async def _execute(self, command, scenario):
        try:
            delay = self.timeout_seconds + 0.5 if scenario.outcome == "timeout" else scenario.delay_ms / 1000
            await asyncio.sleep(delay)
            applied = scenario.outcome in ("success", "warning")
            messages = {
                "success": f"Simulated {command.device} confirmed {'on' if command.value else 'off'}.",
                "warning": f"Simulated {command.device} applied the command with a supply warning.",
                "error": f"Simulated {command.device} rejected the command. Its confirmed state stays as last reported.",
                "timeout": f"Simulated {command.device} sent no confirmation.",
            }
            result = Feedback(command_id=command.command_id, device=command.device, outcome=scenario.outcome,
                              reported_value=command.value if applied else None,
                              code="SIMULATED_" + scenario.outcome.upper(), message=messages[scenario.outcome])
            self.results[command.command_id] = result
            if len(self.results) > 200:
                del self.results[next(iter(self.results))]
            return result
        finally:
            self.executions.pop(command.command_id, None)

    async def close(self):
        tasks = list(self.executions.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
