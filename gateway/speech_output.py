"""Local neural speech, with warm voice models and replayable in-memory WAV audio."""
import asyncio
from collections import OrderedDict
from io import BytesIO
from threading import Lock
import wave

from gateway.config import LOCAL

VOICE_NAMES = {"en": "en_US-lessac-medium", "pt": "pt_BR-faber-medium"}
VOICE_DIRECTORY = LOCAL / "models/piper"


class SpeechOutput:
    def __init__(self):
        self.backend = "Piper neural voice"
        self.cache = OrderedDict()
        self.voices = {}
        self.lock = asyncio.Lock()
        self.inference_lock = Lock()

    @property
    def available(self):
        return all((VOICE_DIRECTORY / (name + suffix)).exists()
                   for name in VOICE_NAMES.values() for suffix in (".onnx", ".onnx.json"))

    async def synthesize(self, text, language="en"):
        key = (text, language)
        async with self.lock:
            if key not in self.cache:
                self.cache[key] = await asyncio.to_thread(self._synthesize, text, language)
                while len(self.cache) > 12:
                    self.cache.popitem(last=False)
            return self.cache[key]

    def _synthesize(self, text, language):
        # Piper's phonemizer and voice sessions are shared, including during request cancellation.
        with self.inference_lock:
            import onnxruntime
            from piper import PiperVoice, SynthesisConfig

            onnxruntime.disable_telemetry_events()
            name = VOICE_NAMES.get(language, VOICE_NAMES["en"])
            path = VOICE_DIRECTORY / (name + ".onnx")
            if not path.exists() or not path.with_suffix(".onnx.json").exists():
                raise RuntimeError("Neural voice missing. Run tools/setup.sh to download the voices.")
            try:
                if name not in self.voices:
                    self.voices[name] = PiperVoice.load(path)
                output = BytesIO()
                with wave.open(output, "wb") as audio:
                    self.voices[name].synthesize_wav(text, audio,
                                                   syn_config=SynthesisConfig(length_scale=1.0))
                return output.getvalue()
            except Exception as error:
                raise RuntimeError("Neural speech generation failed: " + str(error)[:200]) from error
