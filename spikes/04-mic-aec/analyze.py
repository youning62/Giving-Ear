"""把录音转成 WAV 并计算各声道 RMS（dBFS）。用法：uv run 04-mic-aec/analyze.py file.caf ..."""
import subprocess, sys, wave
import numpy as np

def load(path):
    wav = path.rsplit(".", 1)[0] + ".wav"
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16", path, wav], check=True)
    with wave.open(wav) as w:
        ch, sr = w.getnchannels(), w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, ch) / 32768.0
    return x, sr

def db(x):
    return 20 * np.log10(np.sqrt(np.mean(x**2)) + 1e-12)

for p in sys.argv[1:]:
    x, sr = load(p)
    x = x[int(0.5 * sr):]  # 跳过开头 0.5 秒的启动瞬态
    print(p.split("/")[-1], f"{sr}Hz {x.shape[1]}ch", " ".join(f"ch{i}:{db(x[:, i]):.1f}" for i in range(x.shape[1])))
