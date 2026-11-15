"""Bounded utterance collection and local Whisper speech transcription."""
from io import BytesIO
import wave

from gateway.config import LOCAL


class SpeechBuffer:
    def __init__(self, threshold=0.012):
        self.threshold = threshold
        self.reset()

    def reset(self):
        self.pcm = bytearray()
        self.voiced = 0
        self.quiet = 0
        self.preroll = b""

    def feed(self, pcm, level):
        samples = len(pcm) // 2
        if not self.pcm and level < self.threshold:
            self.preroll = (self.preroll + pcm)[-6400:]
            return None
        if not self.pcm:
            self.pcm.extend(self.preroll)
        self.pcm.extend(pcm)
        if level >= self.threshold:
            self.voiced += samples
            self.quiet = 0
        else:
            self.quiet += samples
        finished = self.quiet >= 9600 or len(self.pcm) >= 16000 * 2 * 6
        if not finished:
            return None
        audio = bytes(self.pcm) if self.voiced >= 4800 else None
        self.reset()
        return audio


class SpeechRecognizer:
    def __init__(self, model_name):
        self.model_name = model_name
        self.model = None
        self.language = None

    def transcribe(self, pcm):
        from faster_whisper import WhisperModel

        if self.model is None:
            self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8",
                                      cpu_threads=2, download_root=str(LOCAL / "models"))
        wav_bytes = BytesIO()
        with wave.open(wav_bytes, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        wav_bytes.seek(0)
        segments, info = self.model.transcribe(wav_bytes, beam_size=1, vad_filter=True,
                                               condition_on_previous_text=False)
        self.language = info.language if info.language in ("en", "pt") else None
        return " ".join(segment.text.strip() for segment in segments)


def decode_recording(data):
    """Decode browser WebM/Opus or MP4/AAC recordings into the existing Whisper format."""
    from av import AudioResampler, open as open_audio

    pcm = bytearray()
    resampler = AudioResampler(format='s16', layout='mono', rate=16000)
    with open_audio(BytesIO(data)) as source:
        for frame in source.decode(audio=0):
            for chunk in resampler.resample(frame):
                pcm.extend(bytes(chunk.planes[0])[:chunk.samples * 2])
                if len(pcm) > 16000 * 2 * 16:
                    raise ValueError('Recordings are limited to 15 seconds.')
        for chunk in resampler.resample(None):
            pcm.extend(bytes(chunk.planes[0])[:chunk.samples * 2])
    if len(pcm) < 16000:
        raise ValueError('Please hold the button and speak for at least half a second.')
    return bytes(pcm)
