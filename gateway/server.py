"""Local WebRTC media gateway: phone browser -> Mac or Raspberry Pi."""
import argparse
from array import array
import asyncio
from collections import deque
import hmac
from io import BytesIO
import math
from pathlib import Path
import secrets
import ssl
import sys
import time
import wave

from aiohttp import web
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError
from av import AudioResampler
from pydantic import ValidationError

from gateway.config import control_token, load_settings
from gateway.access import Access, USER_COOKIE, CLIENT, ADMIN
from gateway.runtime import SystemRuntime

ROOT = Path(__file__).resolve().parent


class Receiver:
    def __init__(self):
        self.client_id = None
        self.peer = None
        self.session = None
        self.tasks = set()
        self.lock = asyncio.Lock()
        self.audio_sequence = 0
        self.on_audio = self.on_video = self.on_reset = None
        self.reset()

    def reset(self):
        self.jpeg = None
        self.video_frames = self.audio_frames = 0
        self.width = self.height = 0
        self.timestamps = deque(maxlen=120)
        self.preview_at = 0
        self.updated = None
        self.video_enabled = self.audio_enabled = False
        self.audio_updated = None
        self.audio_level = 0
        self.audio_chunks = deque()
        self.audio_bytes = 0
        if self.on_reset:
            self.on_reset()

    async def close(self):
        peer, self.peer = self.peer, None
        self.session = None
        self.client_id = None
        if peer is not None:
            await peer.close()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.reset()

    async def consume(self, track):
        resampler = AudioResampler(format="s16", layout="mono", rate=16000) if track.kind == "audio" else None
        try:
            while True:
                frame = await track.recv()
                if track.kind == "video":
                    self.video_frames += 1
                    self.width, self.height = frame.width, frame.height
                    now = time.monotonic()
                    self.timestamps.append(now)
                    self.updated = now
                    if self.on_video:
                        self.on_video(frame)
                    # Gesture inference consumes the original frame through on_video.
                    # JPEG is only the gateway's monitoring preview.
                    if now - self.preview_at >= 0.1:
                        self.jpeg = await asyncio.to_thread(self.preview, frame)
                        self.preview_at = now
                else:
                    self.audio_frames += 1
                    for chunk in resampler.resample(frame):
                        # Standard notebook/model input: 16 kHz, mono, signed PCM16.
                        pcm = bytes(chunk.planes[0])[:chunk.samples * 2]
                        samples = array("h", pcm)
                        if sys.byteorder != "little":
                            samples.byteswap()
                        self.audio_level = math.sqrt(sum(s * s for s in samples) / max(1, len(samples))) / 32768
                        self.audio_updated = time.monotonic()
                        if self.on_audio:
                            self.on_audio(pcm, self.audio_level)
                        self.audio_sequence += 1
                        self.audio_chunks.append((self.audio_sequence, pcm))
                        self.audio_bytes += len(pcm)
                        # Keep only five seconds in memory, never a growing recording.
                        while self.audio_bytes > 16000 * 2 * 5:
                            self.audio_bytes -= len(self.audio_chunks.popleft()[1])
        except MediaStreamError:
            pass

    def audio(self, after):
        if self.audio_updated is None or time.monotonic() - self.audio_updated > 3:
            return None
        chunks = [(seq, pcm) for seq, pcm in self.audio_chunks if seq > after]
        if not chunks:
            return None
        output = BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"".join(pcm for _, pcm in chunks))
        return output.getvalue(), chunks[-1][0], after > 0 and chunks[0][0] > after + 1

    @staticmethod
    def preview(frame):
        image = frame.to_image()
        image.thumbnail((960, 720))
        output = BytesIO()
        image.save(output, format="JPEG", quality=85)
        return output.getvalue()

    def status(self):
        recent = [t for t in self.timestamps if time.monotonic() - t < 2]
        fps = (len(recent) - 1) / (recent[-1] - recent[0]) if len(recent) > 1 else 0
        return {
            "state": self.peer.connectionState if self.peer else "idle",
            "video_frames": self.video_frames,
            "audio_frames": self.audio_frames,
            "width": self.width, "height": self.height, "fps": round(fps, 1),
            "frame_age_ms": round((time.monotonic() - self.updated) * 1000) if self.updated else None,
            "video_enabled": self.video_enabled, "audio_enabled": self.audio_enabled,
            "audio_level": round(self.audio_level, 5) if self.audio_updated and time.monotonic() - self.audio_updated < 3 else 0,
            "audio_age_ms": round((time.monotonic() - self.audio_updated) * 1000) if self.audio_updated else None,
            "audio_sequence": self.audio_sequence,
            "audio_buffer_seconds": round(self.audio_bytes / 32000, 2),
        }


RECEIVER = web.AppKey("receiver", Receiver)
EXPIRY_TASKS = web.AppKey("expiry_tasks", set)
SYSTEM = web.AppKey("system", SystemRuntime)


def create_app(system=False):
    receiver = Receiver()
    runtime = SystemRuntime(receiver, load_settings(), control_token()) if system else None
    access = Access()

    @web.middleware
    async def headers(request, handler):
        if request.path.startswith("/internal/"):
            if runtime is None or request.remote not in ("127.0.0.1", "::1"):
                raise web.HTTPForbidden()
            if not hmac.compare_digest(request.headers.get("Authorization", "").encode(), ("Bearer " + runtime.token).encode()):
                raise web.HTTPUnauthorized()
        client = request.cookies.get(USER_COOKIE, '')
        if len(client) != 32 or not all(character.isalnum() or character in '-_' for character in client):
            client = secrets.token_urlsafe(24)
        request[CLIENT] = client
        request[ADMIN] = access.is_admin(request)
        admin_paths = {'/api/system', '/api/mock', '/api/recognition', '/api/status',
                       '/api/frame.jpg', '/api/audio.wav', '/api/speech/test'}
        if request.path in admin_paths and not request[ADMIN]:
            raise web.HTTPUnauthorized(text='Admin login required.')
        if request.path.startswith('/api/') and request.method != 'GET':
            origin = request.headers.get('Origin')
            if origin and origin != f'{request.scheme}://{request.host}':
                raise web.HTTPForbidden(text='Use the app from this gateway URL.')
        try:
            response = await handler(request)
        except ValidationError as error:
            raise web.HTTPBadRequest(text=str(error.errors(include_input=False)))
        except (ValueError, KeyError) as error:
            raise web.HTTPBadRequest(text=str(error))
        if request.cookies.get(USER_COOKIE) != client:
            response.set_cookie(USER_COOKIE, client, httponly=True, samesite='Strict',
                                secure=request.secure, max_age=24 * 3600)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(self)"
        return response

    app = web.Application(middlewares=[headers], client_max_size=8 * 1024 * 1024)
    app[RECEIVER] = receiver
    app.router.add_post("/api/admin/login", access.login)
    app.router.add_post("/api/admin/logout", access.logout)
    async def admin_status(request):
        return web.json_response({"admin": request[ADMIN]})

    app.router.add_get("/api/admin", admin_status)
    if runtime:
        app[SYSTEM] = runtime
        runtime.register(app)

        async def startup(app):
            await runtime.start()

        app.on_startup.append(startup)

    async def index(request):
        return web.Response(text=(ROOT / "web/index.html").read_text(), content_type="text/html")

    async def system_script(request):
        return web.Response(text=(ROOT / "web/system.js").read_text(), content_type="application/javascript")

    async def offer(request):
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        try:
            params = await request.json()
            if params.get("type") != "offer" or not isinstance(params.get("sdp"), str):
                raise ValueError()
        except (ValueError, AttributeError):
            raise web.HTTPBadRequest(text="A WebRTC offer is required.")
        async with receiver.lock:
            if receiver.peer and receiver.peer.connectionState not in ("closed", "failed"):
                raise web.HTTPConflict(text="Another phone is connected. Stop sharing on that phone first.")
            await receiver.close()
            # No external STUN/TURN servers: this first prototype stays on the LAN.
            peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            receiver.peer = peer
            receiver.session = secrets.token_urlsafe(12)
            receiver.client_id = request[CLIENT]

            @peer.on("track")
            def track_received(track):
                if track.kind == "video":
                    receiver.video_enabled = True
                elif track.kind == "audio":
                    receiver.audio_enabled = True
                task = asyncio.create_task(receiver.consume(track))
                receiver.tasks.add(task)
                task.add_done_callback(receiver.tasks.discard)

            @peer.on("connectionstatechange")
            async def connection_changed():
                if peer.connectionState == "failed" and receiver.peer is peer:
                    asyncio.create_task(receiver.close())

            try:
                await peer.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type="offer"))
                await peer.setLocalDescription(await peer.createAnswer())
            except Exception:
                await receiver.close()
                raise web.HTTPBadRequest(text="Could not negotiate this media stream.")

            async def expire_unconnected():
                await asyncio.sleep(30)
                if receiver.peer is peer and peer.connectionState != "connected":
                    await receiver.close()

            task = asyncio.create_task(expire_unconnected())
            app[EXPIRY_TASKS].add(task)
            task.add_done_callback(app[EXPIRY_TASKS].discard)
            return web.json_response({"sdp": peer.localDescription.sdp,
                                      "type": peer.localDescription.type, "session": receiver.session})

    async def status(request):
        return web.json_response(receiver.status())

    async def image(request):
        if receiver.jpeg is None or receiver.updated is None or time.monotonic() - receiver.updated > 3:
            return web.Response(status=204)
        return web.Response(body=receiver.jpeg, content_type="image/jpeg")

    async def audio(request):
        try:
            after = int(request.query.get("after", "0"))
            if after < 0:
                raise ValueError()
        except ValueError:
            raise web.HTTPBadRequest(text="after must be a non-negative audio sequence number.")
        result = receiver.audio(after)
        if result is None:
            return web.Response(status=204)
        data, sequence, gap = result
        return web.Response(body=data, content_type="audio/wav",
                            headers={"X-Audio-Sequence": str(sequence), "X-Audio-Gap": str(gap).lower()})

    async def stop(request):
        params = await request.json()
        async with receiver.lock:
            if params.get("session") != receiver.session or request[CLIENT] != receiver.client_id:
                raise web.HTTPConflict(text="That session has already ended.")
            await receiver.close()
        return web.Response(status=204)

    app[EXPIRY_TASKS] = set()
    app.router.add_get("/", index)
    app.router.add_get("/system.js", system_script)
    app.router.add_post("/api/offer", offer)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/frame.jpg", image)
    app.router.add_get("/api/audio.wav", audio)
    app.router.add_delete("/api/session", stop)

    async def shutdown(app):
        for task in app[EXPIRY_TASKS]:
            task.cancel()
        await asyncio.gather(*app[EXPIRY_TASKS], return_exceptions=True)
        if runtime:
            await runtime.close()
        await receiver.close()

    app.on_shutdown.append(shutdown)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--cert", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--monitor-port", type=int, default=8080,
                        help="Loopback-only HTTP monitor when HTTPS is enabled")
    parser.add_argument("--system", action="store_true", help="Enable the Node-RED and local AI workflow")
    args = parser.parse_args()
    if bool(args.cert) != bool(args.key):
        parser.error("Provide both --cert and --key.")
    context = None
    if args.cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.cert, args.key)
    elif args.host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("Phone access over Wi-Fi requires --cert and --key for HTTPS.")
    app = create_app(system=args.system)

    async def serve():
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, args.host, args.port, ssl_context=context).start()
            if context:
                await web.TCPSite(runner, "127.0.0.1", args.monitor_port).start()
                print(f"Mac/Pi monitor: http://localhost:{args.monitor_port}/?view=monitor", flush=True)
                print(f"Phone: https://<gateway Wi-Fi address>:{args.port}/", flush=True)
            else:
                print(f"Local preview: http://localhost:{args.port}/", flush=True)
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
