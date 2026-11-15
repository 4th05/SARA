import asyncio
from array import array
from io import BytesIO
import math
import unittest
import wave

from aiohttp.test_utils import TestClient, TestServer
from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame
from PIL import Image

from server import RECEIVER, create_app


class RedVideo(VideoStreamTrack):
    async def recv(self):
        pts, time_base = await self.next_timestamp()
        frame = VideoFrame.from_image(Image.new("RGB", (320, 240), (230, 25, 20)))
        frame.pts, frame.time_base = pts, time_base
        return frame


class ToneAudio(AudioStreamTrack):
    async def recv(self):
        frame = await super().recv()
        samples = array("h", (int(12000 * math.sin(2 * math.pi * 440 * (frame.pts + i) / frame.sample_rate))
                              for i in range(frame.samples)))
        frame.planes[0].update(samples.tobytes())
        return frame


class GatewayTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = create_app()
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.peer = None
        await self.client.get('/')
        response = await self.client.post('/api/admin/login', json={'password': 'admin'})
        self.assertEqual(response.status, 200)
        self.auth = {}

    async def asyncTearDown(self):
        if self.peer:
            await self.peer.close()
        await self.client.close()

    async def test_authentication_and_idle_preview(self):
        response = await self.client.get("/api/status")
        self.assertEqual(response.status, 200)
        response = await self.client.get("/api/frame.jpg", headers=self.auth)
        self.assertEqual(response.status, 204)
        response = await self.client.get("/api/audio.wav")
        self.assertEqual(response.status, 204)
        response = await self.client.get("/api/audio.wav", headers=self.auth)
        self.assertEqual(response.status, 204)
        response = await self.client.post("/api/offer", json={"type": "answer"}, headers=self.auth)
        self.assertEqual(response.status, 400)

    async def test_admin_login_and_guess_limit(self):
        await self.client.post('/api/admin/logout', json={})
        for _ in range(10):
            response = await self.client.post('/api/admin/login', json={'password': 'wrong'})
            self.assertEqual(response.status, 401)
        response = await self.client.post('/api/admin/login', json={'password': 'wrong'})
        self.assertEqual(response.status, 429)

    async def connect(self, video=True, audio=True):
        self.peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        if video:
            self.peer.addTrack(RedVideo())
        if audio:
            self.peer.addTrack(ToneAudio())
        await self.peer.setLocalDescription(await self.peer.createOffer())
        response = await self.client.post("/api/offer", headers=self.auth,
                                          json={"sdp": self.peer.localDescription.sdp, "type": "offer"})
        self.assertEqual(response.status, 200)
        answer = await response.json()
        await self.peer.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
        async with asyncio.timeout(15):
            while True:
                status = self.app[RECEIVER].status()
                if (not video or (status["video_frames"] >= 3 and self.app[RECEIVER].jpeg)) and (not audio or status["audio_frames"] >= 3):
                    break
                await asyncio.sleep(0.05)
        return answer

    async def test_webrtc_video_audio_preview_and_stop(self):
        answer = await self.connect()
        status = self.app[RECEIVER].status()
        self.assertTrue(status["audio_enabled"])
        self.assertTrue(status["video_enabled"])
        self.assertEqual((status["width"], status["height"]), (320, 240))
        response = await self.client.get("/api/frame.jpg", headers=self.auth)
        self.assertEqual(response.status, 200)
        image = Image.open(BytesIO(await response.read()))
        self.assertEqual(image.size, (320, 240))
        red, green, blue = image.getpixel((160, 120))
        self.assertGreater(red, 190)
        self.assertLess(green, 50)
        self.assertLess(blue, 50)
        response = await self.client.delete("/api/session", headers=self.auth, json={"session": "wrong-session"})
        self.assertEqual(response.status, 409)
        response = await self.client.delete("/api/session", headers=self.auth, json={"session": answer["session"]})
        self.assertEqual(response.status, 204)
        self.assertIsNone(self.app[RECEIVER].jpeg)
        self.assertEqual(self.app[RECEIVER].status()["state"], "idle")
        self.assertEqual(self.app[RECEIVER].audio_bytes, 0)
        self.assertFalse(self.app[RECEIVER].status()["audio_enabled"])

    async def test_video_only(self):
        await self.connect(audio=False)
        status = self.app[RECEIVER].status()
        self.assertTrue(status["video_enabled"])
        self.assertFalse(status["audio_enabled"])
        self.assertEqual(status["audio_frames"], 0)
        response = await self.client.get("/api/audio.wav", headers=self.auth)
        self.assertEqual(response.status, 204)

    async def test_audio_only_notebook_input_and_bounded_buffer(self):
        await self.connect(video=False)
        status = self.app[RECEIVER].status()
        self.assertFalse(status["video_enabled"])
        self.assertTrue(status["audio_enabled"])
        self.assertEqual(status["video_frames"], 0)
        self.assertGreater(status["audio_level"], 0.1)
        self.assertIsNotNone(status["audio_age_ms"])
        response = await self.client.get("/api/frame.jpg", headers=self.auth)
        self.assertEqual(response.status, 204)
        response = await self.client.get("/api/audio.wav", headers=self.auth)
        self.assertEqual(response.status, 200)
        sequence = int(response.headers["X-Audio-Sequence"])
        with wave.open(BytesIO(await response.read()), "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 16000))
            samples = array("h", wav.readframes(wav.getnframes()))
            self.assertGreater(max(abs(s) for s in samples), 5000)
        self.assertIsNone(self.app[RECEIVER].audio(sequence))
        await asyncio.sleep(0.1)
        response = await self.client.get(f"/api/audio.wav?after={sequence}", headers=self.auth)
        self.assertEqual(response.status, 200)
        self.assertGreater(int(response.headers["X-Audio-Sequence"]), sequence)
        self.assertEqual(response.headers["X-Audio-Gap"], "false")
        response = await self.client.get("/api/audio.wav?after=-1", headers=self.auth)
        self.assertEqual(response.status, 400)
        async with asyncio.timeout(10):
            while self.app[RECEIVER].audio_sequence < 270:
                await asyncio.sleep(0.05)
        self.assertLessEqual(self.app[RECEIVER].audio_bytes, 16000 * 2 * 5)
        self.assertGreater(self.app[RECEIVER].status()["audio_buffer_seconds"], 4.8)
        response = await self.client.get("/api/audio.wav?after=1", headers=self.auth)
        self.assertEqual(response.headers["X-Audio-Gap"], "true")
        cursor = self.app[RECEIVER].audio_sequence
        await self.app[RECEIVER].close()
        self.assertIsNone(self.app[RECEIVER].audio(0))
        self.assertEqual(self.app[RECEIVER].audio_sequence, cursor)


if __name__ == "__main__":
    unittest.main()
