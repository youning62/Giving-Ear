"""04 验证：macOS 麦克风权限 + 系统回声消除（AVAudioEngine 语音处理模式）。

用法：
  uv run 04-mic-aec/aec_test.py status
  uv run 04-mic-aec/aec_test.py record --vp on|off --play ref.aiff|none --seconds 10 --out out.caf
"""

import argparse
import sys
import threading
import time

import AVFoundation as AVF
from Foundation import NSURL


def permission_status() -> str:
    status = AVF.AVCaptureDevice.authorizationStatusForMediaType_(AVF.AVMediaTypeAudio)
    return {0: "notDetermined", 1: "restricted", 2: "denied", 3: "authorized"}.get(status, str(status))


def request_permission(timeout: float = 60.0) -> bool:
    done = threading.Event()
    result = {}

    def handler(granted):
        result["granted"] = bool(granted)
        done.set()

    AVF.AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVF.AVMediaTypeAudio, handler)
    done.wait(timeout)
    return result.get("granted", False)


def record(vp: bool, play: str | None, seconds: float, out: str, mono: bool = False) -> None:
    engine = AVF.AVAudioEngine.alloc().init()
    input_node = engine.inputNode()
    # 必须先触发输出端和混音器的初始化，再开启语音处理，否则引擎启动时输出端初始化失败（-10875）
    engine.outputNode()
    engine.mainMixerNode()

    if vp:
        ok, err = input_node.setVoiceProcessingEnabled_error_(True, None)
        if not ok:
            sys.exit(f"开启语音处理失败: {err}")

    player = None
    if play:
        ref_file, err = AVF.AVAudioFile.alloc().initForReading_error_(NSURL.fileURLWithPath_(play), None)
        if ref_file is None:
            sys.exit(f"读取参考音频失败: {err}")
        player = AVF.AVAudioPlayerNode.alloc().init()
        engine.attachNode_(player)
        engine.connect_to_format_(player, engine.mainMixerNode(), ref_file.processingFormat())

    fmt = input_node.outputFormatForBus_(0)
    print(f"input format: {fmt.sampleRate()} Hz, {fmt.channelCount()} ch, vp={vp}, mono_tap={mono}")
    tap_node = input_node
    if mono:
        # 经过一个静音的混音器，由系统把多声道输入转成单声道，再在混音器上取音频
        tap_node = AVF.AVAudioMixerNode.alloc().init()
        engine.attachNode_(tap_node)
        engine.connect_to_format_(input_node, tap_node, fmt)
        mono_fmt = AVF.AVAudioFormat.alloc().initStandardFormatWithSampleRate_channels_(fmt.sampleRate(), 1)
        engine.connect_to_format_(tap_node, engine.mainMixerNode(), mono_fmt)
        tap_node.setOutputVolume_(0.0)
        fmt = mono_fmt

    out_file, err = AVF.AVAudioFile.alloc().initForWriting_settings_error_(
        NSURL.fileURLWithPath_(out), fmt.settings(), None
    )
    if out_file is None:
        sys.exit(f"创建输出文件失败: {err}")

    frames = {"n": 0}

    def tap(buffer, when):
        out_file.writeFromBuffer_error_(buffer, None)
        frames["n"] += buffer.frameLength()

    tap_node.installTapOnBus_bufferSize_format_block_(0, 4096, fmt, tap)

    ok, err = engine.startAndReturnError_(None)
    if not ok:
        sys.exit(f"启动音频引擎失败: {err}")

    if player is not None:
        loops = int(seconds / (ref_file.length() / ref_file.processingFormat().sampleRate())) + 1
        for _ in range(loops):
            player.scheduleFile_atTime_completionHandler_(ref_file, None, None)
        player.play()

    time.sleep(seconds)
    tap_node.removeTapOnBus_(0)
    engine.stop()
    print(f"recorded {frames['n'] / fmt.sampleRate():.1f}s -> {out}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("request")
    rec = sub.add_parser("record")
    rec.add_argument("--vp", choices=["on", "off"], required=True)
    rec.add_argument("--play", default="none")
    rec.add_argument("--seconds", type=float, default=10)
    rec.add_argument("--out", required=True)
    rec.add_argument("--mono", action="store_true")
    args = parser.parse_args()

    if args.cmd == "status":
        print(permission_status())
    elif args.cmd == "request":
        print("granted" if request_permission() else "not granted", permission_status())
    else:
        record(args.vp == "on", None if args.play == "none" else args.play, args.seconds, args.out, args.mono)


if __name__ == "__main__":
    main()
