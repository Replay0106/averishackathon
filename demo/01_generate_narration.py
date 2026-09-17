import json
import os
import subprocess
import sys

import imageio_ffmpeg

DEMO_DIR = os.path.dirname(__file__)
AUDIO_DIR = os.path.join(DEMO_DIR, "audio")
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
VOICE = "en-US-GuyNeural"


def get_duration(path: str) -> float:
    out = subprocess.run(
        [FFMPEG, "-i", path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    ).stdout
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            hms = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = hms.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError(f"Could not parse duration for {path}")


def main() -> None:
    os.makedirs(AUDIO_DIR, exist_ok=True)
    with open(os.path.join(DEMO_DIR, "script.json"), encoding="utf-8") as f:
        beats = json.load(f)

    durations = {}
    for beat in beats:
        mp3_path = os.path.join(AUDIO_DIR, f"{beat['id']}.mp3")
        print(f"[narration] generating {beat['id']}...")
        result = subprocess.run(
            [sys.executable, "-m", "edge_tts", "--voice", VOICE,
             "--text", beat["text"], "--write-media", mp3_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"edge-tts failed for {beat['id']}: {result.stderr}")
        dur = get_duration(mp3_path)
        durations[beat["id"]] = dur
        print(f"[narration] {beat['id']}: {dur:.2f}s")

    with open(os.path.join(DEMO_DIR, "durations.json"), "w", encoding="utf-8") as f:
        json.dump(durations, f, indent=2)
    print("[narration] done ->", os.path.join(DEMO_DIR, "durations.json"))


if __name__ == "__main__":
    main()
