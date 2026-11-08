"""Integration checks exercise the saved Node-RED flow with deterministic test recognition."""
import asyncio
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest

from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PROJECT
from gateway.contracts import Decision, Feedback, InputEvent, MockSetting
from gateway.server import SYSTEM, create_app
from gateway.state import SystemState
from gateway.config import load_settings
from gateway.recognition.intent import IntentService


class TestIntent:
    def __init__(self):
        self.calls = 0

    async def interpret(self, event, snapshot):
        self.calls += 1
        if event.text == "broken interpreter":
            raise RuntimeError("Simulated interpreter failure")
        if event.text == "ambiguous":
            return Decision(kind="clarify", device=None, value=None, reply="Which device do you mean?")
        return Decision(kind="command", device="lights", value=event.text != "off", reply="Request received.")


class StateTest(unittest.TestCase):
    def test_feedback_identity_and_terminal_state(self):
        state = SystemState()
        event = InputEvent(request_id="one", text="on")
        decision = Decision(kind="command", device="lights", value=True, reply="Request received.")
        state.begin(event, decision)
        self.assertIsNone(state.devices["lights"]["confirmed"])
        with self.assertRaises(ValueError):
            state.feedback(Feedback(command_id="one", device="pump", outcome="success",
                                    reported_value=True, code="OK", message="Wrong device"))
        with self.assertRaises(ValueError):
            state.feedback(Feedback(command_id="one", device="lights", outcome="success",
                                    reported_value=False, code="OK", message="Wrong value"))
        state.feedback(Feedback(command_id="one", device="lights", outcome="error",
                                reported_value=None, code="FAILED", message="Rejected"))
        state.feedback(Feedback(command_id="one", device="lights", outcome="success",
                                reported_value=True, code="LATE", message="Too late"))
        self.assertIsNone(state.devices["lights"]["confirmed"])
        self.assertEqual(state.commands["one"]["status"], "error")


class IntentGroundingTest(unittest.IsolatedAsyncioTestCase):
    async def test_ambiguous_model_guess_requires_clarification(self):
        class GuessingModel:
            async def ainvoke(self, messages):
                return Decision(kind="command", device="lights", value=False, reply="Guessed lights")

        service = IntentService(load_settings())
        service.structured = GuessingModel()
        result = await service.interpret(InputEvent(request_id="ambiguous", text="Turn it off"),
                                         SystemState().snapshot())
        self.assertEqual(result.kind, "clarify")
        self.assertIsNone(result.device)


class WorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        node = next((str(path) for path in [Path('/opt/homebrew/opt/node@24/bin/node'),
                    Path('/usr/local/opt/node@24/bin/node')] if path.exists()), shutil.which('node'))
        script = PROJECT / 'orchestration/node-red/node_modules/node-red/red.js'
        if not node or not script.exists():
            self.skipTest('Install Node.js and run npm ci for the Node-RED workspace.')
        self.app = create_app('123456', system=True)
        self.runtime = self.app[SYSTEM]
        self.intent = TestIntent()
        self.runtime.intent = self.intent
        self.runtime.state.timeout_seconds = 0.6
        self.runtime.devices.timeout_seconds = 0.6
        self.runtime.devices.configure(MockSetting(device="lights", outcome="success", delay_ms=10))
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.runtime.settings.workflow_url = f'http://127.0.0.1:{port}/ioe/event'
        self.temp = tempfile.TemporaryDirectory(prefix='ioe-node-red-test-')
        environment = {**os.environ, 'IOE_GATEWAY_URL': str(self.client.make_url('')).rstrip('/')}
        self.node_red = subprocess.Popen([node, str(script), '--settings',
             str(PROJECT / 'orchestration/node-red/settings.js'), '--userDir', self.temp.name, '--port', str(port)],
             cwd=PROJECT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        async with ClientSession() as session:
            async with asyncio.timeout(15):
                while True:
                    try:
                        async with session.get(f'http://127.0.0.1:{port}/ioe/health',
                              headers={'Authorization': 'Bearer ' + self.runtime.token}) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        pass
                    await asyncio.sleep(0.05)
        self.auth = {'Authorization': 'Bearer 123456'}

    async def asyncTearDown(self):
        if hasattr(self, 'node_red'):
            self.node_red.terminate()
            await asyncio.to_thread(self.node_red.wait, timeout=5)
            self.temp.cleanup()
            await self.client.close()

    async def submit(self, request_id, text='on'):
        response = await self.client.post('/api/input', headers=self.auth,
                      json={'request_id': request_id, 'text': text})
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    async def scenario(self, outcome):
        response = await self.client.post('/api/mock', headers=self.auth,
                         json={'device': 'lights', 'outcome': outcome, 'delay_ms': 10})
        self.assertEqual(response.status, 200)

    async def test_success_warning_error_timeout_and_replay(self):
        for index, outcome in enumerate(['success', 'warning', 'error', 'timeout']):
            await self.scenario(outcome)
            result = await self.submit(f'scenario-{index}', 'on' if index == 0 else 'off')
            self.assertEqual(result['status'], outcome)
        self.assertFalse(self.runtime.state.devices['lights']['confirmed'])
        self.assertIsNone(self.runtime.state.devices['lights']['pending'])
        repeated = await self.submit('scenario-0')
        self.assertEqual(repeated['status'], 'success')
        self.assertEqual(self.intent.calls, 4)
        self.assertEqual(len(self.runtime.devices.results), 4)
        response = await self.client.post('/api/input', headers=self.auth,
                                          json={'request_id': 'scenario-0', 'text': 'different'})
        self.assertEqual(response.status, 400)

    async def test_clarification_and_interpreter_error_do_not_execute(self):
        result = await self.submit('clarify', 'ambiguous')
        self.assertEqual(result['status'], 'clarify')
        result = await self.submit('broken', 'broken interpreter')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(self.runtime.devices.results, {})
        self.assertIsNone(self.runtime.state.devices['lights']['confirmed'])

    async def test_internal_api_auth_and_live_audio_worker(self):
        response = await self.client.post('/internal/feedback', json={})
        self.assertEqual(response.status, 401)
        response = await self.client.get('/api/system')
        self.assertEqual(response.status, 401)
        self.runtime.speech.transcribe = lambda pcm: 'on'
        self.runtime.receiver.session = 'audio-test'
        self.runtime.feed_audio(b'\x10\x10' * 6400, 0.2)
        self.runtime.feed_audio(b'\x00\x00' * 9600, 0)
        async with asyncio.timeout(5):
            while self.runtime.state.devices['lights']['status'] != 'success':
                await asyncio.sleep(0.05)
        self.assertTrue(self.runtime.state.devices['lights']['confirmed'])
        self.assertEqual(self.runtime.last_transcript, 'on')


if __name__ == '__main__':
    unittest.main()
