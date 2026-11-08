"""Reference gesture recognizer. Replace this adapter with the team's model."""
from gateway.config import LOCAL
from threading import Lock


class GestureRecognizer:
    def __init__(self):
        self.recognizer = None
        self.lock = Lock()

    def recognize(self, frame, timestamp_ms):
        with self.lock:
            return self._recognize(frame, timestamp_ms)

    def _recognize(self, frame, timestamp_ms):
        import mediapipe as mp
        import numpy as np

        if self.recognizer is None:
            path = LOCAL / "models/gesture_recognizer.task"
            if not path.exists():
                raise RuntimeError("Gesture model missing. Run tools/setup.sh to download it.")
            options = mp.tasks.vision.GestureRecognizerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(path)),
                running_mode=mp.tasks.vision.RunningMode.VIDEO)
            self.recognizer = mp.tasks.vision.GestureRecognizer.create_from_options(options)
        rgb = np.asarray(frame.to_image().convert("RGB"))
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self.recognizer.recognize_for_video(image, timestamp_ms)
        if result.gestures:
            item = result.gestures[0][0]
            return item.category_name, float(item.score)
        return None, 0.0

    def close(self):
        with self.lock:
            if self.recognizer:
                self.recognizer.close()
                self.recognizer = None
