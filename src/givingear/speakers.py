"""说话人识别：计算声纹特征，与本次会话的档案比对；认不出就新建临时档案。"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import sherpa_onnx

from . import models
from .audio import SAMPLE_RATE

# 低于这个相似度就当作陌生人。默认值来自合成语音的初步测量，待 03 用真人语料校准
DEFAULT_THRESHOLD = 0.45


class EmbeddingExtractor:
    def __init__(self, num_threads: int = 2):
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(models.ensure(models.SPEAKER_MODEL)), num_threads=num_threads
        )
        self._ext = sherpa_onnx.SpeakerEmbeddingExtractor(config)

    def embed(self, samples: np.ndarray) -> np.ndarray:
        stream = self._ext.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        stream.input_finished()
        e = np.array(self._ext.compute(stream), np.float32)
        return e / (np.linalg.norm(e) + 1e-9)


@dataclass
class Profile:
    id: str
    name: str
    embeddings: list[np.ndarray] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)  # 名字的其他写法、音译、容易被听成的样子
    named: bool = False  # 是否已经知道真名（否则名字是"说话人 N"）

    @property
    def label(self) -> str:
        """固定的编号名，改名后也不变，例如"说话人 2"。"""
        return f"说话人 {self.id[1:]}"

    @property
    def centroid(self) -> np.ndarray:
        c = np.mean(self.embeddings, axis=0)
        return c / (np.linalg.norm(c) + 1e-9)

    def score(self, emb: np.ndarray) -> float:
        return float(self.centroid @ emb)


@dataclass
class Match:
    profile: Profile
    score: float
    is_new: bool


class SessionProfiles:
    """本次会话的临时档案，只存在内存里。"""

    def __init__(
        self,
        threshold: float = DEFAULT_THRESHOLD,
        max_embeddings: int = 20,
    ):
        self.threshold = threshold
        self.max_embeddings = max_embeddings
        self.profiles: list[Profile] = []
        self._next = 1

    def best(self, emb: np.ndarray) -> tuple[Profile | None, float]:
        scored = [(p, p.score(emb)) for p in self.profiles]
        return max(scored, key=lambda x: x[1], default=(None, -1.0))

    def identify(self, emb: np.ndarray) -> Match:
        """认出是谁；认不出就新建档案。"""
        profile, score = self.best(emb)
        if profile is not None and score >= self.threshold:
            return Match(profile, score, is_new=False)
        profile = Profile(id=f"s{self._next}", name="", embeddings=[emb])
        profile.name = profile.label
        self._next += 1
        self.profiles.append(profile)
        return Match(profile, 1.0, is_new=True)

    def find(self, ref: str) -> Profile | None:
        """按名字或编号找档案，忽略空格和大小写（"说话人2" 和 "说话人 2" 都能找到）。"""
        key = "".join(ref.split()).lower()
        for p in self.profiles:
            if key in ("".join(p.name.split()).lower(), "".join(p.label.split()), p.id):
                return p
        return None

    def add(self, profile: Profile, emb: np.ndarray) -> None:
        profile.embeddings.append(emb)
        del profile.embeddings[: -self.max_embeddings]
