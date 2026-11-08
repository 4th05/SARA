"""Connect live media workers to the Node-RED workflow and Python state."""
import asyncio
from collections import OrderedDict
import time
import uuid

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from gateway.contracts import (Decision, DeviceCommand, Feedback, InputEvent,
                               MockSetting, RecognitionSetting)
from gateway.devices.mock import MockDevices
from gateway.recognition.gesture import GestureRecognizer
from gateway.recognition.intent import IntentService
from gateway.recognition.speech import SpeechBuffer, SpeechRecognizer
from gateway.state import SystemState


class SystemRuntime:
    def __init__(self, receiver, settings, token, intent=None):
        self.receiver = receiver
        self.settings = settings
        self.token = token
        self.state = SystemState(settings.command_timeout_seconds)
        self.devices = MockDevices(settings.command_timeout_seconds)
        self.intent = intent or IntentService(settings)
        self.recognition = RecognitionSetting()
        self.speech_buffer = SpeechBuffer(settings.speech_threshold)
        self.speech = SpeechRecognizer(settings.speech_model)
        self.gestures = GestureRecognizer()
        self.audio_queue = asyncio.Queue(maxsize=2)
        self.video_queue = asyncio.Queue(maxsize=1)
        self.requests = OrderedDict()
        self.tasks = []
        self.client = None
        self.last_video_at = 0
        self.voice_status = "Ready; share microphone audio and speak a short command."
        self.last_transcript = ""
        self.last_gesture = ""
        self.health = {"node_red": False, "ollama": False, "model": settings.ollama_model}

    async def start(self):
        self.client = ClientSession(timeout=ClientTimeout(total=self.settings.inference_timeout_seconds + 15))
        self.receiver.on_audio = self.feed_audio
        self.receiver.on_video = self.feed_video
        self.receiver.on_reset = self.speech_buffer.reset
        self.tasks = [asyncio.create_task(worker()) for worker in
                      (self.watchdog, self.voice_worker, self.gesture_worker, self.health_worker)]
        self.state.event("ready", "Workflow ready with simulated light and pump controllers.")

    async def close(self):
        self.receiver.on_audio = self.receiver.on_video = self.receiver.on_reset = None
        tasks = self.tasks + [task for _, task in self.requests.values() if not task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.devices.close()
        # The adapter waits for in-progress inference before releasing its model.
        await asyncio.to_thread(self.gestures.close)
        if self.client:
            await self.client.close()

    def snapshot(self):
        return {**self.state.snapshot(), "mocks": self.devices.snapshot(),
                "recognition": self.recognition.model_dump(), "health": self.health.copy(),
                "voice_status": self.voice_status, "last_transcript": self.last_transcript,
                "last_gesture": self.last_gesture}

    async def submit(self, event):
        previous = self.requests.get(event.request_id)
        fingerprint = event.model_dump_json()
        if previous:
            if previous[0] != fingerprint:
                raise ValueError("Request ID already used with different input.")
            return await asyncio.shield(previous[1])
        pending = sum(not task.done() for _, task in self.requests.values())
        if pending >= 8:
            raise ValueError("The workflow is busy. Please wait for the current request.")
        if len(self.requests) >= 200:
            old = next((key for key, (_, task) in self.requests.items() if task.done()), None)
            if old:
                del self.requests[old]
        task = asyncio.create_task(self._submit(event))
        self.requests[event.request_id] = fingerprint, task
        return await asyncio.shield(task)

    async def _submit(self, event):
        self.state.event("input", event.text, request_id=event.request_id, source=event.source)
        try:
            async with self.client.post(self.settings.workflow_url, json=event.model_dump(),
                                        headers={"Authorization": "Bearer " + self.token}) as response:
                if response.status != 200:
                    raise RuntimeError(f"Node-RED returned HTTP {response.status}.")
                result = await response.json()
                if result.get("request_id") != event.request_id:
                    raise RuntimeError("Unexpected response from the Node-RED workflow.")
                return result
        except (ClientError, TimeoutError, OSError, RuntimeError, ValueError) as error:
            self.state.fail(event.request_id, f"Workflow connection failed: {error}")
            return {"request_id": event.request_id, "status": "error", "reply": str(error)}

    def feed_audio(self, pcm, level):
        if not self.recognition.voice_enabled:
            self.speech_buffer.reset()
            return
        utterance = self.speech_buffer.feed(pcm, level)
        if utterance:
            if self.audio_queue.full():
                self.state.event("warning", "Speech processing is busy. Please repeat after the current request.")
                return
            self.audio_queue.put_nowait((self.receiver.session, utterance))

    def feed_video(self, frame):
        now = time.monotonic()
        if self.recognition.gesture_enabled and now - self.last_video_at >= 0.3 and not self.video_queue.full():
            self.video_queue.put_nowait((self.receiver.session, frame))
            self.last_video_at = now

    async def voice_worker(self):
        while True:
            session, pcm = await self.audio_queue.get()
            try:
                if session != self.receiver.session or not self.recognition.voice_enabled:
                    continue
                self.voice_status = "Transcribing speech locally…"
                text = await asyncio.to_thread(self.speech.transcribe, pcm)
                if session != self.receiver.session:
                    continue
                self.last_transcript = text
                if text:
                    self.voice_status = "Interpreting speech through Node-RED and Ollama…"
                    await self.submit(InputEvent(request_id=str(uuid.uuid4()), conversation_id=session or "voice",
                                                 source="voice", text=text))
                self.voice_status = "Listening for the next command."
            except Exception as error:
                self.voice_status = "Speech processing failed. Check the event log."
                self.state.event("error", f"Speech processing failed: {error}")
            finally:
                self.audio_queue.task_done()

    async def gesture_worker(self):
        candidate, repeats, latched, current_session = None, 0, None, None
        while True:
            session, frame = await self.video_queue.get()
            try:
                if session != self.receiver.session or not self.recognition.gesture_enabled:
                    continue
                if session != current_session:
                    candidate, repeats, latched, current_session = None, 0, None, session
                label, confidence = await asyncio.to_thread(self.gestures.recognize, frame,
                                                            time.monotonic_ns() // 1_000_000)
                self.last_gesture = f"{label or 'No hand'} ({confidence:.0%})"
                if label not in ("Open_Palm", "Closed_Fist") or confidence < 0.8:
                    candidate, repeats, latched = None, 0, None
                    continue
                repeats = repeats + 1 if label == candidate else 1
                candidate = label
                if repeats >= 3 and latched != label and session == self.receiver.session:
                    latched = label
                    await self.submit(InputEvent(request_id=str(uuid.uuid4()), conversation_id=session or "gesture",
                                                 source="gesture", text=label, confidence=confidence))
            except Exception as error:
                self.recognition.gesture_enabled = False
                self.state.event("error", f"Gesture processing stopped: {error}")
            finally:
                self.video_queue.task_done()

    async def watchdog(self):
        while True:
            self.state.expire()
            await asyncio.sleep(0.1)

    async def health_worker(self):
        while True:
            for name, url in (("node_red", self.settings.workflow_url.replace("/event", "/health")),
                              ("ollama", self.settings.ollama_url + "/api/tags")):
                try:
                    async with self.client.get(url, headers={"Authorization": "Bearer " + self.token},
                                               timeout=ClientTimeout(total=2)) as response:
                        data = await response.json()
                        ok = response.status == 200
                        if name == "ollama":
                            ok = ok and any(item["name"] == self.settings.ollama_model for item in data.get("models", []))
                        self.health[name] = ok
                except Exception:
                    self.health[name] = False
            await asyncio.sleep(5)

    def register(self, app):
        async def system(request):
            return web.json_response(self.snapshot())

        async def submit(request):
            event = InputEvent.model_validate(await request.json())
            return web.json_response(await self.submit(event))

        async def mock_setting(request):
            setting = MockSetting.model_validate(await request.json())
            self.devices.configure(setting)
            self.state.event("configuration", f"{setting.device} simulator will return {setting.outcome}.")
            return web.json_response(self.devices.snapshot())

        async def recognition_setting(request):
            self.recognition = RecognitionSetting.model_validate(await request.json())
            self.speech_buffer.reset()
            return web.json_response(self.recognition.model_dump())

        async def interpret(request):
            event = InputEvent.model_validate(await request.json())
            try:
                decision = await self.intent.interpret(event, self.state.snapshot())
            except Exception as error:
                raise web.HTTPServiceUnavailable(text=f"Local interpretation failed: {str(error)[:300]}")
            return web.json_response(decision.model_dump())

        async def begin(request):
            params = await request.json()
            event = InputEvent.model_validate(params["event"])
            decision = Decision.model_validate(params["decision"])
            if decision.kind != "command":
                raise ValueError("A command decision is required.")
            command = self.state.begin(event, decision)
            return web.json_response(DeviceCommand.model_validate({key: command[key] for key in
                                      ("command_id", "device", "value")}).model_dump())

        async def execute(request):
            command = DeviceCommand.model_validate(await request.json())
            known = self.state.commands.get(command.command_id)
            if not known or (known["device"], known["value"]) != (command.device, command.value):
                raise ValueError("Device execution requires a matching registered command.")
            if known["status"] != "pending":
                return web.json_response(known["feedback"])
            return web.json_response((await self.devices.execute(command)).model_dump())

        async def feedback(request):
            value = Feedback.model_validate(await request.json())
            command = self.state.feedback(value)
            # The watchdog may already have recorded a timeout before late feedback.
            result = command.get("feedback", value.model_dump())
            return web.json_response({"request_id": value.command_id, "status": command["status"],
                                      "reply": result["message"], "device": value.device})

        async def reply(request):
            params = await request.json()
            event = InputEvent.model_validate(params["event"])
            decision = Decision.model_validate(params["decision"])
            if decision.kind == "command":
                raise ValueError("Commands must pass through execution and feedback.")
            self.state.event(decision.kind, decision.reply, request_id=event.request_id)
            return web.json_response({"request_id": event.request_id, "status": decision.kind,
                                      "reply": decision.reply})

        async def failure(request):
            params = await request.json()
            command_id = str(params.get("request_id", "unknown"))[:80]
            message = str(params.get("message", "Workflow failed."))[:500]
            command = self.state.fail(command_id, message)
            result = {"request_id": command_id, "status": "error", "reply": message}
            if command and command["status"] != "pending":
                result.update(status=command["status"], reply=command["feedback"]["message"])
            return web.json_response(result)

        app.router.add_get("/api/system", system)
        app.router.add_post("/api/input", submit)
        app.router.add_post("/api/mock", mock_setting)
        app.router.add_post("/api/recognition", recognition_setting)
        app.router.add_post("/internal/interpret", interpret)
        app.router.add_post("/internal/commands", begin)
        app.router.add_post("/internal/mock/execute", execute)
        app.router.add_post("/internal/feedback", feedback)
        app.router.add_post("/internal/reply", reply)
        app.router.add_post("/internal/failure", failure)
