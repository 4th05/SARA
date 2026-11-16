"""Connect live media workers to the Node-RED workflow and Python state."""
import asyncio
from collections import OrderedDict
import time
import uuid

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from gateway.contracts import (Decision, DeviceCommand, Feedback,
                               FeedbackBatch, InputEvent, MockSetting, RecognitionSetting, ROOMS)
from gateway.devices.mock import MockDevices
from gateway.recognition.gesture import GestureRecognizer, MIN_GESTURE_CONFIDENCE
from gateway.recognition.intent import IntentService
from gateway.recognition.speech import SpeechBuffer, SpeechRecognizer, decode_recording
from gateway.state import SystemState
from gateway.access import CLIENT, ADMIN
from gateway.speech_output import SpeechOutput


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
        self.speech_output = SpeechOutput()
        self.audio_queue = asyncio.Queue(maxsize=2)
        self.video_queue = asyncio.Queue(maxsize=1)
        self.video_captures = OrderedDict()
        self.gesture_jobs = {}
        self.requests = OrderedDict()
        self.tasks = []
        self.client = None
        self.last_video_at = 0
        self.voice_status = "Ready; share microphone audio and speak a short command."
        self.last_transcript = ""
        self.recording = None
        self.voice_busy = False
        self.processing = set()
        self.summary_lock = asyncio.Lock()
        self.last_gesture = ""
        self.health = {"node_red": False, "ollama": False, "model": settings.ollama_model}

    async def start(self):
        self.client = ClientSession(timeout=ClientTimeout(total=2 * self.settings.inference_timeout_seconds + 15))
        self.receiver.on_audio = self.feed_audio
        self.receiver.on_video = self.feed_video
        self.receiver.on_reset = self.reset_recording
        self.tasks = [asyncio.create_task(worker()) for worker in
                      (self.watchdog, self.voice_worker, self.gesture_worker, self.health_worker)]
        self.state.event("ready", "Voice workflow ready. Bathroom, kitchen, and bedroom controllers are simulated.")

    async def close(self):
        self.receiver.on_audio = self.receiver.on_video = self.receiver.on_reset = None
        tasks = self.tasks + list(self.gesture_jobs) + [task for _, task in self.requests.values() if not task.done()]
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
                "last_gesture": self.last_gesture, "recording": self.recording is not None, "voice_busy": self.voice_busy,
                "speech_output": {"available": self.speech_output.available,
                                  "backend": self.speech_output.backend}}

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
        self.state.owners[event.request_id] = event.conversation_id
        if len(self.state.owners) > 200:
            self.state.owners.pop(next(iter(self.state.owners)))
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
            return self.workflow_failure(event.request_id, f"Workflow connection failed: {error}")

    def workflow_failure(self, request_id, message):
        group = self.state.groups.get(request_id)
        if group:
            for identifier in group["command_ids"]:
                self.state.fail(identifier, message)
            return self.state.complete_batch(request_id)
        self.state.event("error", message, request_id=request_id, speak=True, language="en")
        return {"request_id": request_id, "status": "error", "reply": message}

    def reset_recording(self):
        self.recording = None
        self.speech_buffer.reset()

    def finish_recording(self):
        recording = self.recording
        self.recording = None
        if not recording or len(recording[1]) < 16000:
            raise ValueError("Recording too short. Speak for at least half a second.")
        if self.audio_queue.full() or self.voice_busy:
            raise ValueError("Please wait for the current command to finish.")
        self.processing.add(recording[0])
        self.audio_queue.put_nowait((recording[0], bytes(recording[1])))
        self.voice_status = "Transcribing your recording…"

    def feed_audio(self, pcm, level):
        if not self.receiver.client_id:
            return
        if not self.recognition.voice_enabled:
            self.reset_recording()
            return
        if self.recognition.voice_mode == "manual":
            if self.recording and self.recording[0] == self.receiver.client_id:
                self.recording[1].extend(pcm)
                if len(self.recording[1]) >= 16000 * 2 * 15:
                    self.finish_recording()
            return
        utterance = self.speech_buffer.feed(pcm, level)
        if utterance and not self.audio_queue.full():
            self.processing.add(self.receiver.client_id)
            self.audio_queue.put_nowait((self.receiver.client_id, utterance))

    def feed_video(self, frame):
        session, owner = self.receiver.session, self.receiver.client_id
        if not session or not owner:
            return
        if session not in self.video_captures:
            self.video_captures[session] = {'owner': owner, 'status': 'watching', 'job': None}
            if len(self.video_captures) > 200:
                self.video_captures.popitem(last=False)
        now = time.monotonic()
        if (self.recognition.gesture_enabled and self.video_captures[session]['status'] == 'watching'
                and now - self.last_video_at >= 0.3 and not self.video_queue.full()):
            self.video_queue.put_nowait((session, owner, frame))
            self.last_video_at = now

    async def voice_worker(self):
        while True:
            client_id, pcm = await self.audio_queue.get()
            try:
                self.voice_busy = True
                self.voice_status = "Transcribing speech locally…"
                text = await asyncio.to_thread(self.speech.transcribe, pcm)
                self.last_transcript = text
                if text:
                    self.voice_status = "Interpreting speech through Node-RED and Ollama…"
                    await self.submit(InputEvent(request_id=str(uuid.uuid4()), conversation_id=client_id,
                                                 source="voice", text=text, language=self.speech.language))
                if not text:
                    self.state.event("clarify", "I could not hear a command. Please try again.", speak=True, language="en", conversation_id=client_id)
                self.voice_status = "Ready. Hold to capture."
            except Exception as error:
                self.voice_status = "Speech processing failed. Check the event log."
                self.state.event("error", f"Speech processing failed: {error}", speak=True, language="en", conversation_id=client_id)
            finally:
                self.voice_busy = False
                self.processing.discard(client_id)
                self.audio_queue.task_done()

    async def gesture_worker(self):
        candidate, repeats, current_session = None, 0, None
        while True:
            session, owner, frame = await self.video_queue.get()
            try:
                captured = self.video_captures.get(session)
                if not captured or captured['status'] != 'watching' or not self.recognition.gesture_enabled:
                    continue
                if session != current_session:
                    candidate, repeats, current_session = None, 0, session
                label, confidence = await asyncio.to_thread(self.gestures.recognize, frame,
                                                            time.monotonic_ns() // 1_000_000)
                self.last_gesture = f"{label or 'No hand'} ({confidence:.0%})"
                if label not in ("Thumb_Up", "Thumb_Down") or confidence < MIN_GESTURE_CONFIDENCE:
                    candidate, repeats = None, 0
                    continue
                repeats = repeats + 1 if label == candidate else 1
                candidate = label
                if repeats >= 3:
                    # Accept one stable gesture per hold. Its workflow survives camera release.
                    captured['status'] = 'accepted'
                    event = InputEvent(request_id=str(uuid.uuid4()), conversation_id=owner,
                                       source="gesture", text=label, confidence=confidence)
                    job = asyncio.create_task(self.process_gesture(event))
                    captured['job'] = job
                    self.gesture_jobs[job] = owner
                    job.add_done_callback(lambda done: self.gesture_jobs.pop(done, None))
            except Exception as error:
                self.recognition.gesture_enabled = False
                if captured:
                    captured['status'] = 'error'
                self.state.event("error", f"Gesture processing stopped: {error}", speak=True,
                                 language='en', conversation_id=owner)
            finally:
                self.video_queue.task_done()

    async def process_gesture(self, event):
        try:
            await self.submit(event)
        except Exception as error:
            self.state.event('error', f'Gesture request failed: {error}', speak=True,
                             language='en', conversation_id=event.conversation_id)

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

        async def user_state(request):
            client = request[CLIENT]
            return web.json_response({
                'devices': {name: self.state.devices[name] for name in ROOMS},
                'events': [event for event in self.state.events if event.get('speak')
                           and event.get('conversation_id') == client],
                'busy': client in self.processing or client in self.gesture_jobs.values(),
                'recording': self.recording is not None and self.recording[0] == client,
            })

        async def video_complete(request):
            params = await request.json()
            session = params.get('session')
            if not isinstance(session, str) or not 1 <= len(session) <= 128:
                raise ValueError('A camera session is required.')
            captured = self.video_captures.get(session)
            if captured and captured['owner'] != request[CLIENT]:
                raise web.HTTPNotFound()
            # Finish classification of the last captured frames, without waiting for the LLM.
            await self.video_queue.join()
            if captured and captured['status'] == 'accepted':
                return web.json_response({'status': 'processing' if not captured['job'].done() else 'complete'})
            if captured and captured['status'] in ('error', 'unrecognized'):
                return web.json_response({'status': captured['status']})
            if captured:
                captured['status'] = 'unrecognized'
            if params.get('notify', True):
                message = ('I did not recognize a thumbs-up or thumbs-down. Hold your hand steady for about a second and try again.'
                           if self.recognition.gesture_enabled else 'Gesture recognition is unavailable. Please use audio or ask the administrator to check it.')
                self.state.event('clarify', message, speak=True, language='en', conversation_id=request[CLIENT])
            return web.json_response({'status': 'unrecognized'})

        async def submit(request):
            params = await request.json()
            params["conversation_id"] = request[CLIENT]
            event = InputEvent.model_validate(params)
            if event.source == "gesture" and not request[ADMIN]:
                raise web.HTTPForbidden()
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

        async def speech_audio(request):
            sequence = int(request.match_info["sequence"])
            event = next((event for event in self.state.events
                          if event["sequence"] == sequence and event.get("speak")
                          and (request[ADMIN] or event.get("conversation_id") == request[CLIENT])), None)
            if event is None:
                raise web.HTTPNotFound(text="This spoken reply is no longer in the recent event history.")
            try:
                audio = await self.speech_output.synthesize(event["message"], event.get("language", "en"))
            except (OSError, RuntimeError, TimeoutError) as error:
                raise web.HTTPServiceUnavailable(text=str(error))
            return web.Response(body=audio, content_type="audio/wav")

        async def test_speech(request):
            event = self.state.event("reply", "Voice feedback is ready. You can now speak a command.",
                                     speak=True, language="en", conversation_id=request[CLIENT])
            return web.json_response(event)

        async def upload_recording(request):
            if request[CLIENT] in self.processing or self.audio_queue.full():
                raise web.HTTPConflict(text='Please wait for your current request to finish.')
            if not request.content_type.startswith('audio/'):
                raise web.HTTPUnsupportedMediaType(text='A browser audio recording is required.')
            data = await request.read()
            try:
                pcm = await asyncio.to_thread(decode_recording, data)
            except Exception:
                self.state.event('clarify', 'I could not read that recording. Please hold the button, speak for at least half a second, and release.',
                                 speak=True, language='en', conversation_id=request[CLIENT])
                return web.json_response({'status': 'clarify'}, status=202)
            if request[CLIENT] in self.processing or self.audio_queue.full():
                raise web.HTTPConflict(text='Please wait for your current request to finish.')
            self.processing.add(request[CLIENT])
            self.audio_queue.put_nowait((request[CLIENT], pcm))
            return web.json_response({'status': 'processing'}, status=202)

        async def record_voice(request):
            params = await request.json()
            if (params.get("session") != self.receiver.session or not self.receiver.session
                    or request[CLIENT] != self.receiver.client_id):
                raise ValueError("Connect the microphone before recording.")
            if params.get("recording") is True:
                if self.voice_busy or not self.audio_queue.empty() or self.recording is not None:
                    raise ValueError("Please wait for the current command to finish.")
                if not self.receiver.audio_enabled:
                    raise ValueError("Microphone audio is not connected.")
                self.recognition.voice_mode = "manual"
                self.recognition.voice_enabled = True
                self.recording = (request[CLIENT], bytearray())
                self.voice_status = "Recording… release to send."
            elif params.get("recording") is False:
                if self.recording is not None:
                    self.finish_recording()
            else:
                raise ValueError("recording must be true or false.")
            return web.json_response({"recording": self.recording is not None})

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
                                      "reply": result["message"], "device": value.device, "feedback": result})

        async def begin_batch(request):
            params = await request.json()
            event = InputEvent.model_validate(params["event"])
            decision = Decision.model_validate(params["decision"])
            if decision.kind != "command":
                raise ValueError("A command decision is required.")
            return web.json_response(self.state.begin_batch(event, decision).model_dump())

        async def feedback_batch(request):
            batch = FeedbackBatch.model_validate(await request.json())
            registered = self.state.command_batch(batch.request_id)
            expected = {command.command_id: command for command in registered.commands}
            if len(batch.feedback) != len(expected) or {value.command_id for value in batch.feedback} != set(expected):
                raise ValueError("Feedback must match every command in the registered batch.")
            for value in batch.feedback:
                command = expected[value.command_id]
                if value.device != command.device:
                    raise ValueError("Feedback device does not match its command.")
                if value.outcome == "success" and value.reported_value != command.value:
                    raise ValueError("Successful feedback must confirm the requested value.")
            for value in batch.feedback:
                self.state.feedback(value)
            async with self.summary_lock:
                group = self.state.groups[batch.request_id]
                result = self.state.complete_batch(batch.request_id, announce=False)
                if not group.get("announced"):
                    try:
                        spoken = await self.intent.summarize(result, self.state.snapshot(), group["language"])
                        result = self.state.announce_result(batch.request_id, spoken.reply, spoken.language, "ollama")
                    except Exception:
                        result = self.state.announce_result(batch.request_id, source="controller_fallback")
                return web.json_response(result)

        async def reply(request):
            params = await request.json()
            event = InputEvent.model_validate(params["event"])
            decision = Decision.model_validate(params["decision"])
            if decision.kind == "command":
                raise ValueError("Commands must pass through execution and feedback.")
            self.state.event(decision.kind, decision.reply, request_id=event.request_id,
                             speak=True, language=decision.language)
            return web.json_response({"request_id": event.request_id, "status": decision.kind,
                                      "reply": decision.reply})

        async def failure(request):
            params = await request.json()
            command_id = str(params.get("request_id", "unknown"))[:80]
            message = str(params.get("message", "Workflow failed."))[:500]
            return web.json_response(self.workflow_failure(command_id, message))

        app.router.add_get("/api/user", user_state)
        app.router.add_post("/api/video/complete", video_complete)
        app.router.add_get("/api/system", system)
        app.router.add_post("/api/input", submit)
        app.router.add_post("/api/mock", mock_setting)
        app.router.add_post("/api/recognition", recognition_setting)
        app.router.add_post("/api/voice", record_voice)
        app.router.add_post("/api/recording", upload_recording)
        app.router.add_get("/api/speech/{sequence:\\d+}.wav", speech_audio)
        app.router.add_post("/api/speech/test", test_speech)
        app.router.add_post("/internal/interpret", interpret)
        app.router.add_post("/internal/commands", begin)
        app.router.add_post("/internal/mock/execute", execute)
        app.router.add_post("/internal/feedback", feedback)
        app.router.add_post("/internal/commands/batch", begin_batch)
        app.router.add_post("/internal/feedback/batch", feedback_batch)
        app.router.add_post("/internal/reply", reply)
        app.router.add_post("/internal/failure", failure)
