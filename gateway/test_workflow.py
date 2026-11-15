"""Integration checks exercise the saved Node-RED flow with deterministic test recognition."""
import asyncio
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest

from aiohttp import ClientSession, CookieJar
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PROJECT
from gateway.contracts import Action, Decision, Feedback, InputEvent, MockSetting, ROOMS, SpokenReply, LightPlan
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
        if event.text in ("rooms on", "rooms off", "Thumb_Up", "Thumb_Down"):
            actions = [Action(device=room, value=event.text in ("rooms on", "Thumb_Up")) for room in ROOMS]
            return Decision(kind="command", actions=actions, reply="Request received.")
        return Decision(kind="command", device="lights", value=event.text != "off", reply="Request received.")

    async def summarize(self, result, snapshot, language):
        return SpokenReply(reply=result['reply'], language=language)


class StateTest(unittest.TestCase):
    def test_busy_room_rejects_entire_batch_before_changes(self):
        state = SystemState()
        state.begin(InputEvent(request_id="busy", text="kitchen on"),
                    Decision(kind="command", device="kitchen", value=True, reply="Waiting."))
        decision = Decision(kind="command", actions=[Action(device="bathroom", value=True),
                            Action(device="kitchen", value=True)], reply="Waiting.")
        with self.assertRaises(ValueError):
            state.begin_batch(InputEvent(request_id="two-rooms", text="both on"), decision)
        self.assertIsNone(state.devices["bathroom"]["pending"])
        self.assertNotIn("two-rooms", state.groups)
        self.assertEqual(len(state.commands), 1)

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


class IntentValidationTest(unittest.IsolatedAsyncioTestCase):
    async def test_gesture_label_reaches_llm_and_uses_its_plan(self):
        class Model:
            async def ainvoke(self, messages):
                self.messages = messages
                return LightPlan(bathroom=False, kitchen=False, bedroom=False, response_kind='reply',
                                 reply='Model-selected actions.', language='en')
        service = IntentService(load_settings())
        service.structured = Model()
        event = InputEvent(request_id='gesture-model', source='gesture', text='Thumb_Up', confidence=0.73)
        result = await service.interpret(event, SystemState().snapshot())
        self.assertEqual(service.structured.messages[-1], ('human', 'Recognized gesture: Thumb_Up'))
        self.assertEqual(result.actions, [Action(device=room, value=False) for room in ROOMS])

    async def test_novel_wording_reaches_model_without_keyword_filter(self):
        class Model:
            async def ainvoke(self, messages):
                return LightPlan(bathroom=None, kitchen=False, bedroom=None, response_kind='reply', reply='I will darken the kitchen.', language='en')
        service = IntentService(load_settings())
        service.structured = Model()
        result = await service.interpret(InputEvent(request_id='novel', text='The cooking area is too bright. Make it dark.'), SystemState().snapshot())
        self.assertEqual(result.actions, [Action(device='kitchen', value=False)])

    async def test_duplicate_actions_rejected(self):
        with self.assertRaises(ValueError):
            Decision(kind='command', actions=[Action(device='bedroom', value=True), Action(device='bedroom', value=False)], reply='Conflicting')


class WorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        node = next((str(path) for path in [Path('/opt/homebrew/opt/node@24/bin/node'),
                    Path('/usr/local/opt/node@24/bin/node')] if path.exists()), shutil.which('node'))
        script = PROJECT / 'orchestration/node-red/node_modules/node-red/red.js'
        if not node or not script.exists():
            self.skipTest('Install Node.js and run npm ci for the Node-RED workspace.')
        self.app = create_app(system=True)
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
        await self.client.get('/')
        response = await self.client.post('/api/admin/login', json={'password':'admin'})
        self.assertEqual(response.status, 200)
        self.auth = {}

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

    async def test_real_flow_fans_out_rooms_and_preserves_partial_results(self):
        result = await self.submit('all-on', 'rooms on')
        self.assertEqual(result['status'], 'success')
        for room in ROOMS:
            self.assertTrue(self.runtime.state.devices[room]['confirmed'])
        for room, outcome in [('bathroom', 'warning'), ('kitchen', 'error'), ('bedroom', 'timeout')]:
            response = await self.client.post('/api/mock', headers=self.auth,
                             json={'device': room, 'outcome': outcome, 'delay_ms': 10})
            self.assertEqual(response.status, 200)
        result = await self.submit('mixed-outcomes', 'rooms off')
        self.assertEqual(result['status'], 'partial')
        outcomes = {value['device']: value['outcome'] for value in result['feedback']}
        self.assertEqual(outcomes, {'bathroom': 'warning', 'kitchen': 'error', 'bedroom': 'timeout'})
        self.assertFalse(self.runtime.state.devices['bathroom']['confirmed'])
        self.assertTrue(self.runtime.state.devices['kitchen']['confirmed'])
        self.assertTrue(self.runtime.state.devices['bedroom']['confirmed'])
        self.assertFalse(self.runtime.devices.pins['bathroom'])
        self.assertTrue(self.runtime.devices.pins['kitchen'])
        self.assertTrue(self.runtime.devices.pins['bedroom'])
        spoken = [event for event in self.runtime.state.events if event.get('speak')]
        self.assertEqual([event['kind'] for event in spoken],
                         ['acknowledgement', 'success', 'acknowledgement', 'partial'])
        repeated = await self.submit('mixed-outcomes', 'rooms off')
        self.assertEqual(repeated, result)
        self.assertEqual(len(self.runtime.devices.results), 6)

    async def test_spoken_reply_endpoint_requires_ownership_or_admin(self):
        async def synthesize(text, language):
            self.assertIn('Voice feedback is ready', text)
            return b'RIFF-test-audio'

        self.runtime.speech_output.synthesize = synthesize
        response = await self.client.post('/api/speech/test', headers=self.auth, json={})
        event = await response.json()
        path = f"/api/speech/{event['sequence']}.wav"
        response = await self.client.get(path)
        self.assertEqual(response.status, 200)
        response = await self.client.get(path, headers=self.auth)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, 'audio/wav')
        self.assertEqual(await response.read(), b'RIFF-test-audio')
        response = await self.client.get('/api/speech/999999.wav', headers=self.auth)
        self.assertEqual(response.status, 404)

    async def test_public_access_admin_gate_and_private_replies(self):
        await self.client.post('/api/admin/logout', json={})
        for path in ['/api/system', '/api/status', '/api/frame.jpg', '/api/audio.wav']:
            response = await self.client.get(path)
            self.assertEqual(response.status, 401)
        response = await self.client.post('/api/mock', json={'device':'kitchen','outcome':'success'})
        self.assertEqual(response.status, 401)
        response = await self.client.get('/api/user')
        self.assertEqual(response.status, 200)
        result = await self.submit('public-command')
        self.assertEqual(result['status'], 'success')
        response = await self.client.get('/api/user')
        events = (await response.json())['events']
        self.assertEqual([event['kind'] for event in events], ['acknowledgement', 'success'])
        async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as other:
            async with other.get(self.client.make_url('/api/user')) as response:
                self.assertEqual((await response.json())['events'], [])
            async with other.get(self.client.make_url(f"/api/speech/{events[-1]['sequence']}.wav")) as response:
                self.assertEqual(response.status, 404)

    async def test_uploaded_recording_survives_media_disconnect(self):
        from io import BytesIO
        import wave
        self.runtime.speech.transcribe = lambda pcm: 'on'
        output = BytesIO()
        with wave.open(output, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b'\x10\x10' * 16000)
        await self.client.post('/api/admin/logout', json={})
        response = await self.client.post('/api/recording', data=output.getvalue(), headers={'Content-Type':'audio/wav'})
        self.assertEqual(response.status, 202, await response.text())
        await self.runtime.receiver.close()
        async with asyncio.timeout(5):
            while self.runtime.state.devices['lights']['status'] != 'success':
                await asyncio.sleep(0.05)
        response = await self.client.get('/api/user')
        data = await response.json()
        self.assertTrue(data['devices']['kitchen']['confirmed'])
        self.assertEqual(data['events'][-1]['kind'], 'success')

    async def test_thumb_gestures_debounce_survive_release_and_return_owned_feedback(self):
        owner = self.client.session.cookie_jar.filter_cookies(self.client.make_url('/'))['ioe_user'].value
        await self.client.post('/api/admin/logout', json={})
        for index, label in enumerate(('Thumb_Up', 'Thumb_Down')):
            session = f'gesture-{index}'
            self.runtime.receiver.session = session
            self.runtime.receiver.client_id = owner
            self.runtime.gestures.recognize = lambda frame, timestamp: (label, 0.95)
            for _ in range(2):
                self.runtime.last_video_at = 0
                self.runtime.feed_video(None)
                await self.runtime.video_queue.join()
            self.assertEqual(self.intent.calls, index)
            self.runtime.last_video_at = 0
            self.runtime.feed_video(None)
            # Release while the last frame is still queued/in inference.
            await self.runtime.receiver.close()
            response = await self.client.post('/api/video/complete', json={'session': session})
            self.assertEqual(response.status, 200, await response.text())
            await self.runtime.video_captures[session]['job']
            # Completion is idempotent and cannot replay the accepted command.
            await self.client.post('/api/video/complete', json={'session': session})
            self.runtime.receiver.session = session
            self.runtime.receiver.client_id = owner
            self.runtime.last_video_at = 0
            self.runtime.feed_video(None)
            await self.runtime.video_queue.join()
            self.assertEqual(self.intent.calls, index + 1)
            for room in ROOMS:
                self.assertEqual(self.runtime.state.devices[room]['confirmed'], label == 'Thumb_Up')
            data = await (await self.client.get('/api/user')).json()
            self.assertFalse(data['busy'])
            self.assertEqual([event['kind'] for event in data['events']],
                             ['acknowledgement', 'success'] * (index + 1))
        async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as other:
            async with other.get(self.client.make_url('/api/user')) as response:
                self.assertEqual((await response.json())['events'], [])
            async with other.post(self.client.make_url('/api/video/complete'), json={'session': session}) as response:
                self.assertEqual(response.status, 404)

    async def test_unrecognized_video_returns_spoken_retry_without_commands(self):
        owner = self.client.session.cookie_jar.filter_cookies(self.client.make_url('/'))['ioe_user'].value
        self.runtime.receiver.session = 'unstable'
        self.runtime.receiver.client_id = owner
        for label, confidence in [('Open_Palm', 0.99), ('Thumb_Up', 0.65), ('Thumb_Up', 0.99),
                                  ('Thumb_Down', 0.99), (None, 0)]:
            self.runtime.gestures.recognize = lambda frame, timestamp: (label, confidence)
            self.runtime.last_video_at = 0
            self.runtime.feed_video(None)
            await self.runtime.video_queue.join()
        await self.runtime.receiver.close()
        for _ in range(2):
            response = await self.client.post('/api/video/complete', json={'session': 'unstable'})
            self.assertEqual((await response.json())['status'], 'unrecognized')
        data = await (await self.client.get('/api/user')).json()
        self.assertEqual([event['kind'] for event in data['events']], ['clarify'])
        self.assertTrue(data['events'][0]['speak'])
        self.assertEqual(self.intent.calls, 0)

    async def test_manual_recording_waits_for_stop(self):
        self.runtime.speech.transcribe = lambda pcm: 'on'
        self.runtime.receiver.session = 'manual-test'
        self.runtime.receiver.client_id = self.client.session.cookie_jar.filter_cookies(self.client.make_url('/'))['ioe_user'].value
        self.runtime.receiver.audio_enabled = True
        self.runtime.feed_audio(b'\x10\x10' * 16000, 0.2)
        self.assertTrue(self.runtime.audio_queue.empty())
        response = await self.client.post('/api/voice', headers=self.auth,
                         json={'session': 'manual-test', 'recording': True})
        self.assertEqual(response.status, 200)
        self.runtime.feed_audio(b'\x10\x10' * 16000, 0.2)
        self.assertEqual(self.intent.calls, 0)
        response = await self.client.post('/api/voice', headers=self.auth,
                         json={'session': 'manual-test', 'recording': False})
        self.assertEqual(response.status, 200)
        async with asyncio.timeout(5):
            while self.runtime.state.devices['lights']['status'] != 'success':
                await asyncio.sleep(0.05)
        self.assertTrue(self.runtime.state.devices['lights']['confirmed'])
        self.assertIsNone(self.runtime.recording)

    async def test_internal_api_auth_and_live_audio_worker(self):
        response = await self.client.post('/internal/feedback', json={})
        self.assertEqual(response.status, 401)
        response = await self.client.get('/api/system')
        self.assertEqual(response.status, 200)
        self.runtime.recognition.voice_mode = 'automatic'
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
