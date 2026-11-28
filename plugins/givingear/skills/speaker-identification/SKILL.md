---
name: speaker-identification
description: >-
  Speaker identification for live, in-room conversations: listens through this Mac's microphone, tells who said
  what by voiceprint, and transcribes on-device (Chinese + English). Use when the user wants you to listen to a
  meeting or conversation happening around them, asks who said what or what someone said, wants a per-speaker
  transcript or summary, or wants to name or enroll the people speaking.
  说话人识别：用麦克风实时听现场多人对话，按声纹分辨谁说了什么，会议记录、按人总结、声纹登记。
license: Apache-2.0
compatibility: macOS (Apple Silicon recommended) with a microphone, Python 3.12+ and uv. Runs fully on-device.
metadata:
  author: youning62
  version: "0.2.0"
  homepage: https://github.com/youning62/Giving-Ear
---

# Speaker identification (GivingEar)

GivingEar is a local *ear*: a background process records the room, recognizes each speaker by voiceprint, and
transcribes on this Mac. You are the brain: you read its speaker-labeled transcript and answer.

Run every command as `uvx givingear …` (or plain `givingear …` when it is already installed). If these MCP tools
are available instead, use them: `start_listening`, `get_transcript`, `list_speakers`, `name_speaker`,
`stop_listening`, `listening_status`, `register_speaker`.

## Steps

1. **Consent.** Tell the user, in one sentence, to let everyone in the room know the conversation is being
   transcribed. Done when you have said it.
2. **Start.** Run `uvx givingear listen start`. Done when it prints that it is listening in the background.
   - First start downloads about 400 MB of local models; let the command run (it waits up to 10 minutes).
   - macOS asks for microphone access the first time: tell the user to click Allow.
   - Listening stops by itself after 240 minutes (`--max-minutes` to change).
3. **Answer from the transcript.** Whenever the user asks what was said, fetch the slice you need, then answer
   with names and times:
   - `uvx givingear transcript --since 10` (last 10 minutes), `--speaker Jennifer`, `--last 20`, `--json`
   - `uvx givingear who` lists the speakers heard so far.
   - Lines look like `[14:03:21] Jennifer: …`; consecutive lines from one person are merged.
4. **Name people.** `说话人 1` / `说话人 2` means the person is not known yet. When the transcript shows someone
   introducing themselves, or the user says who is who, run
   `uvx givingear name "说话人 2" Jennifer --alias 珍妮弗 --alias Jenny`. Earlier lines are relabeled too.
5. **Stop.** When the user is done, run `uvx givingear listen stop`. The transcript lives only in memory and is
   discarded on stop; add `--save notes.md` to keep it as text. Fetch anything you still need first.

## Reading the transcript well

- It comes from on-device speech recognition: words, and especially names, can be misheard (Jennifer → 杰夫).
  Prefer the known speaker whose name sounds closest, and confirm with the user when it matters.
- Very short sounds ("嗯", "对") can occasionally appear as an extra speaker; if two labels are clearly the same
  person, say so and name both the same.
- Overlapping speech is attributed to the dominant speaker.

## Remembering people across sessions (optional)

Voiceprints are stored only in an encrypted local account, unlocked with a password the user types themselves:

1. The user runs, in their own terminal: `givingear account create <name>` once, then
   `givingear listen start --account <name>` (instead of step 2). Enrolled people are recognized by name.
2. Only when a person explicitly asks to be remembered: `uvx givingear register "说话人 2" Jennifer`. If it says
   more speech is needed, ask them to keep talking for a bit and retry.

## When something fails

- `现在没有在听`: start listening first (step 2).
- No microphone permission: System Settings → Privacy & Security → Microphone → allow the terminal app
  (or Claude) that runs the command, then start again.
- Model download fails or is slow (e.g. in mainland China): set `GIVINGEAR_DOWNLOAD_MIRROR=<mirror prefix>` and
  retry; the error message names the files and where to put them manually.
- `uvx: command not found`: install uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`).
- Not on macOS: live listening needs a Mac; for recordings use the `speaker-diarization` skill.
