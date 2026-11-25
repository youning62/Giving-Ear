"""有情感的语音合成：通义千问云端（DashScope）和本地 CosyVoice 服务。

接口写法参考：
- Qwen TTS：QianWen-AI/qianwen-ai 的 qianwen-audio-tts（Apache-2.0）——POST 到 DashScope 的 multimodal-generation，
  `input.instructions` 用自然语言控制语气，返回 `output.audio.url`（24 小时有效）。
- CosyVoice：FunAudioLLM/CosyVoice 的 FastAPI 示例（Apache-2.0）——`/inference_instruct` 和 `/inference_instruct2`，返回原始 16 位 PCM 流。
"""

from __future__ import annotations

import io
import wave
from pathlib import Path

import httpx

from .emotions import instruction
from .tts import SynthError

QWEN_PATH = "/services/aigc/multimodal-generation/generation"


def decode_wav(data: bytes) -> tuple[bytes, int]:
    """WAV → (16 位单声道 PCM, 采样率)。多声道取第一个声道。"""
    try:
        with wave.open(io.BytesIO(data)) as w:
            if w.getsampwidth() != 2:
                raise SynthError("语音合成返回的 WAV 不是 16 位")
            pcm, ch, rate = w.readframes(w.getnframes()), w.getnchannels(), w.getframerate()
    except (wave.Error, EOFError):
        raise SynthError("语音合成返回的不是 WAV 音频") from None
    if ch > 1:
        pcm = b"".join(pcm[i : i + 2] for i in range(0, len(pcm) - 2 * ch + 1, 2 * ch))
    return pcm, rate


def _explain(status: int, body: str) -> str:
    hints = {401: "密钥不对或已失效", 403: "没有权限使用这个语音模型", 404: "接口地址不对", 429: "请求太频繁或额度用完了"}
    return f"{hints.get(status, '语音合成接口出错了')}（HTTP {status}）" + (f"：{body.strip()[:160]}" if status not in hints else "")


class QwenCloudTTS:
    def __init__(
        self,
        api_key: str,
        voice: str = "Cherry",
        model: str = "qwen3-tts-instruct-flash",
        base_url: str = "https://dashscope.aliyuncs.com/api/v1",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.voice, self.model = voice, model
        self.url = base_url.rstrip("/") + QWEN_PATH
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def synth(self, text: str, emotion: str | None = None) -> tuple[bytes, int]:
        text = text.strip()
        if not text:
            return b"", 24000
        body: dict = {"text": text, "voice": self.voice}
        if "instruct" in self.model:  # 只有 instruct 模型支持语气指令
            body["instructions"] = instruction(emotion)
            body["optimize_instructions"] = True
        try:
            r = await self._http.post(self.url, headers=self._headers, json={"model": self.model, "input": body})
            if r.status_code != 200:
                raise SynthError(_explain(r.status_code, r.text))
            try:
                audio_url = r.json()["output"]["audio"]["url"]
            except (ValueError, KeyError, TypeError):
                raise SynthError(f"语音合成没有返回音频地址：{r.text[:160]}") from None
            audio = await self._http.get(audio_url)
            if audio.status_code != 200:
                raise SynthError(f"取回合成的音频失败（HTTP {audio.status_code}）")
        except httpx.HTTPError as exc:
            raise SynthError(f"连不上通义千问语音合成（{type(exc).__name__}）。请检查网络") from exc
        return decode_wav(audio.content)

    async def aclose(self) -> None:
        await self._http.aclose()


class CosyVoiceTTS:
    """调用用户自己部署的 CosyVoice FastAPI 服务。

    mode="instruct"：预置音色（speaker 是音色名，适用于 CosyVoice-300M-Instruct）；
    mode="instruct2"：用一段参考录音克隆音色（speaker 是 WAV 文件路径，适用于 CosyVoice2/3）。
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:50000",
        mode: str = "instruct2",
        speaker: str = "",
        sample_rate: int = 24000,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url, self.mode, self.speaker, self.rate = base_url.rstrip("/"), mode, speaker, sample_rate
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def synth(self, text: str, emotion: str | None = None) -> tuple[bytes, int]:
        text = text.strip()
        if not text:
            return b"", self.rate
        data = {"tts_text": text, "instruct_text": instruction(emotion)}
        files = None
        if self.mode == "instruct2":
            path = Path(self.speaker).expanduser()
            if not self.speaker or not path.is_file():
                raise SynthError("CosyVoice 需要一段参考录音（WAV）来决定音色，请在设置里填写它的路径")
            files = {"prompt_wav": (path.name, path.read_bytes(), "audio/wav")}
        else:
            data["spk_id"] = self.speaker or "中文女"
        try:
            async with self._http.stream("POST", f"{self.base_url}/inference_{self.mode}", data=data, files=files) as r:
                if r.status_code != 200:
                    raise SynthError(_explain(r.status_code, (await r.aread()).decode(errors="replace")))
                pcm = b"".join([chunk async for chunk in r.aiter_bytes()])
        except httpx.HTTPError as exc:
            raise SynthError(f"连不上本地 CosyVoice 服务（{self.base_url}）。请确认它已经启动") from exc
        return pcm[: len(pcm) // 2 * 2], self.rate

    async def aclose(self) -> None:
        await self._http.aclose()
