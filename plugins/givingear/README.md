# GivingEar plugin for Claude Code

Speaker identification for Claude: GivingEar listens to an in-room conversation through your Mac's microphone,
recognizes each speaker by voiceprint, and transcribes on-device in Chinese and English, so Claude can answer who
said what and summarize a meeting person by person. It also transcribes audio recordings by speaker.

## What's inside

- Skill `speaker-identification`: listen live, answer who-said-what, name and enroll speakers.
- Skill `speaker-diarization`: transcribe a recording (wav, mp3, m4a) with speaker labels.
- MCP server `givingear` (started with `uvx givingear==0.2.0 mcp`): tools `start_listening`, `stop_listening`,
  `listening_status`, `get_transcript`, `list_speakers`, `name_speaker`, `register_speaker`, `diarize_file`.

## What it runs, stores and sends

- Runs a background listening process on your Mac only after you ask it to start; it stops on request or after
  240 minutes. Its local HTTP API listens on 127.0.0.1 and requires a random token stored in `~/.givingear/listen.json`.
- Stores no audio. The transcript stays in memory and is discarded on stop unless you save it. Enrolled voiceprints
  are kept in an encrypted local account under `~/.givingear/`.
- Downloads open-source speech models (Silero VAD, 3D-Speaker CAM++, SenseVoice) from the sherpa-onnx GitHub
  releases into `~/.cache/givingear/models/` on first use.
- Sends nothing to any server. Requires macOS with a microphone, Python 3.12+ and uv.

Source, license (Apache-2.0) and full documentation: https://github.com/youning62/Giving-Ear
