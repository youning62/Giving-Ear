---
name: speaker-diarization
description: >-
  Speaker diarization for audio recordings: transcribes a wav, mp3 or m4a file with speaker labels
  ("[MM:SS] 说话人 1: …"), fully on-device, Chinese + English. Use when the user gives you a recording (meeting,
  interview, podcast, voice memo) and wants it transcribed by speaker, wants to know who said what, or wants a
  per-speaker summary.
  说话人分离：录音文件按说话人转写，谁说了什么，按人总结。
license: Apache-2.0
compatibility: Python 3.12+ and uv. macOS reads any audio format; elsewhere non-16 kHz-WAV files need ffmpeg.
metadata:
  author: youning62
  version: "0.2.0"
  homepage: https://github.com/youning62/Giving-Ear
---

# Speaker diarization (GivingEar)

GivingEar splits a recording by voice and transcribes it on this machine; nothing is uploaded. If the MCP tool
`diarize_file` is available, use it with the same arguments.

## Steps

1. **Transcribe.** Run `uvx givingear diarize <file>` (or `givingear diarize …` when installed). Done when it
   prints lines like `[03:12] 说话人 2: …`.
   - First run downloads about 400 MB of local models; let it finish.
   - Processing runs faster than real time; a one-hour file takes a few minutes.
   - Add `--json` for structured output (each line with speaker id, time and text).
2. **Put names on the labels.** Read the content for introductions and how people address each other, propose
   who each `说话人 N` is, and confirm with the user. Then rerun with names, or just use them in your answer:
   `uvx givingear diarize <file> --name 说话人1=小王 --name 说话人2=Jennifer`.
3. **Answer.** Quote speakers by name with timestamps. Done when every claim about who said what points to a line
   of the transcript.

## Caveats

- Speech recognition can mishear words and names; flag uncertain ones instead of guessing.
- Overlapping speech goes to the dominant speaker; very short sounds ("嗯") can occasionally form an extra label.
- For a conversation happening live in the room, use the `speaker-identification` skill instead.
