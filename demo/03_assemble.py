import json
import os
import shutil
import subprocess

import imageio_ffmpeg

DEMO_DIR = os.path.dirname(__file__)
RAW_VIDEO = os.path.join(DEMO_DIR, "raw_video", "raw.webm")
AUDIO_DIR = os.path.join(DEMO_DIR, "audio")
SEGMENTS_DIR = os.path.join(DEMO_DIR, "segments")
FINAL_DIR = os.path.join(DEMO_DIR, "final")
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FPS = 30


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{result.stdout}")


def get_duration(path: str) -> float:
    out = subprocess.run([FFMPEG, "-i", path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            hms = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = hms.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError(f"could not parse duration for {path}")


def main() -> None:
    with open(os.path.join(DEMO_DIR, "script.json"), encoding="utf-8") as f:
        beats = json.load(f)
    with open(os.path.join(DEMO_DIR, "marks.json"), encoding="utf-8") as f:
        marks = json.load(f)

    for d in (SEGMENTS_DIR, FINAL_DIR):
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d)

    raw_duration = get_duration(RAW_VIDEO)
    print(f"[assemble] raw video duration: {raw_duration:.2f}s")

    beat_ids = [b["id"] for b in beats if b["id"] in marks]
    if not beat_ids:
        raise RuntimeError("no beats found in marks.json")

    final_clips: list[str] = []
    for i, beat_id in enumerate(beat_ids):
        start = min(marks[beat_id], max(raw_duration - 0.5, 0.0))
        end = marks[beat_ids[i + 1]] if i + 1 < len(beat_ids) else raw_duration
        end = max(end, start + 0.5)
        seg_len = max(0.5, end - start)
        print(f"[assemble] beat {beat_id}: [{start:.2f}, {end:.2f}) len={seg_len:.2f}s")

        video_seg = os.path.join(SEGMENTS_DIR, f"{beat_id}_video.mp4")
        run([
            FFMPEG, "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", RAW_VIDEO,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", video_seg,
        ])

        mp3_path = os.path.join(AUDIO_DIR, f"{beat_id}.mp3")
        audio_seg = os.path.join(SEGMENTS_DIR, f"{beat_id}_audio.m4a")
        run([
            FFMPEG, "-y", "-i", mp3_path,
            "-af", "adelay=300:all=1,apad",
            "-t", f"{seg_len:.3f}",
            "-c:a", "aac", audio_seg,
        ])

        final_seg = os.path.join(SEGMENTS_DIR, f"{beat_id}_final.mp4")
        run([
            FFMPEG, "-y", "-i", video_seg, "-i", audio_seg,
            "-c:v", "copy", "-c:a", "aac", "-shortest", final_seg,
        ])
        final_clips.append(final_seg)

    concat_list = os.path.join(SEGMENTS_DIR, "concat_list.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for clip in final_clips:
            f.write(f"file '{os.path.abspath(clip)}'\n")

    output_path = os.path.join(FINAL_DIR, "navisai_demo.mp4")
    run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", concat_list, "-c", "copy", output_path])

    print(f"[assemble] done -> {output_path}")


if __name__ == "__main__":
    main()
