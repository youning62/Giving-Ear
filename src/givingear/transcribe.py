"""本地转文字：每句话说完后转出文字，这样文字一定能对应到说话人。只用于显示和名字比对，不影响送给 AI 的音频。"""

from __future__ import annotations

import numpy as np
import sherpa_onnx

from . import models
from .audio import SAMPLE_RATE


class Transcriber:
    def __init__(self, language: str = "auto", num_threads: int = 2):
        d = models.ensure_dir(models.SENSE_VOICE_MODEL)
        self._rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(d / "model.int8.onnx"),
            tokens=str(d / "tokens.txt"),
            num_threads=num_threads,
            use_itn=True,  # 把"七点"之类转成更易读的写法，并加上标点
            language=language,
        )

    def transcribe(self, samples: np.ndarray) -> str:
        stream = self._rec.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        self._rec.decode_stream(stream)
        return stream.result.text.strip()
