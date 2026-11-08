"""Download local inference models once, before running the application."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

from gateway.config import LOCAL, load_settings


def online(url):
    try:
        with urlopen(url + "/api/tags", timeout=2) as response:
            return response.status == 200
    except (URLError, TimeoutError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-gesture", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    (LOCAL / "models").mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(LOCAL / "models/huggingface"))
    environment = {**os.environ, "OLLAMA_MODELS": str(LOCAL / "ollama/models"),
                   "OLLAMA_HOST": "127.0.0.1:11434"}
    ollama = shutil.which("ollama")
    if not ollama:
        parser.exit(1, "Install Ollama from https://ollama.com/download first.\n")
    server = None
    try:
        if not online(settings.ollama_url):
            server = subprocess.Popen([ollama, "serve"], env=environment)
            for _ in range(80):
                if online(settings.ollama_url):
                    break
                if server.poll() is not None:
                    raise RuntimeError("Ollama exited during startup.")
                time.sleep(0.25)
            else:
                raise RuntimeError("Ollama did not start.")
        subprocess.run([ollama, "pull", settings.ollama_model], env=environment, check=True)
        from faster_whisper import WhisperModel

        WhisperModel(settings.speech_model, device="cpu", compute_type="int8",
                     cpu_threads=2, download_root=str(LOCAL / "models"))
        from gateway.speech_output import VOICE_DIRECTORY, VOICE_NAMES

        subprocess.run([sys.executable, "-m", "piper.download_voices", "--data-dir", str(VOICE_DIRECTORY),
                        *VOICE_NAMES.values()], check=True)
        if args.with_gesture:
            target = LOCAL / "models/gesture_recognizer.task"
            if not target.exists():
                url = "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task"
                with urlopen(url, timeout=60) as response:
                    target.write_bytes(response.read())
        with urlopen(settings.ollama_url + "/api/tags", timeout=5) as response:
            models = json.load(response)
        selected = next(model for model in models["models"] if model["name"] == settings.ollama_model)
        print("Local models ready. LLM digest:", selected["digest"])
    finally:
        if server:
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
