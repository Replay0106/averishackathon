import json
import os
import shutil
import time

from playwright.sync_api import sync_playwright

DEMO_DIR = os.path.dirname(__file__)
PAGE_PATH = os.path.join(DEMO_DIR, "page", "navisai_pipeline.html")
VIDEO_DIR = os.path.join(DEMO_DIR, "raw_video")
VIEWPORT = {"width": 1280, "height": 800}

SMOOTH_SCROLL_SCRIPT = """
document.documentElement.style.scrollBehavior = 'smooth';
"""


def safe(fn, warn: str):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] {warn}: {exc}")


def main() -> None:
    with open(os.path.join(DEMO_DIR, "durations.json"), encoding="utf-8") as f:
        durations = json.load(f)

    if os.path.isdir(VIDEO_DIR):
        shutil.rmtree(VIDEO_DIR)
    os.makedirs(VIDEO_DIR, exist_ok=True)

    marks: dict[str, float] = {}
    t_start = time.time()

    def mark(beat_id: str):
        marks[beat_id] = round(time.time() - t_start, 3)
        print(f"[record] beat {beat_id} @ {marks[beat_id]:.2f}s")

    def hold_for(beat_id: str, floor_extra: float = 0.6):
        elapsed = time.time() - t_start - marks[beat_id]
        target = durations.get(beat_id, 3.0) + floor_extra
        remaining = max(0.0, target - elapsed)
        if remaining > 0:
            time.sleep(remaining)

    def scroll_to_text(page, text: str, warn: str):
        def _do():
            loc = page.get_by_text(text, exact=False).first
            loc.wait_for(state="visible", timeout=8000)
            loc.evaluate("el => el.scrollIntoView({behavior:'smooth', block:'center'})")
        safe(_do, warn)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=VIDEO_DIR,
            record_video_size=VIEWPORT,
        )
        page = context.new_page()

        print("[record] loading explainer page...")
        safe(lambda: page.goto(f"file:///{PAGE_PATH}", wait_until="load", timeout=15000), "page load failed")
        page.add_init_script(SMOOTH_SCROLL_SCRIPT)
        safe(lambda: page.evaluate(SMOOTH_SCROLL_SCRIPT), "smooth scroll enable failed")
        page.wait_for_timeout(1200)

        # 01: hero
        mark("01_hero")
        safe(lambda: page.evaluate("window.scrollTo({top:0, behavior:'instant'})"), "scroll top failed")
        hold_for("01_hero")

        # 02: stage 1
        mark("02_stage1")
        scroll_to_text(page, "Ingestion & Adaptive Audit", "stage1 scroll failed")
        hold_for("02_stage1")

        # 03: stage 2
        mark("03_stage2")
        scroll_to_text(page, "Visual Redline Diff", "stage2 scroll failed")
        hold_for("03_stage2")

        # 04: stage 3
        mark("04_stage3")
        scroll_to_text(page, "Cross-Border Statutory Gate", "stage3 scroll failed")
        hold_for("04_stage3")

        # 05: stage 4
        mark("05_stage4")
        scroll_to_text(page, "Navis Dispatch Engine", "stage4 scroll failed")
        hold_for("05_stage4")

        # 06: flip before/after
        mark("06_flip")
        scroll_to_text(page, "Applying a correction updates", "flip scroll failed")
        hold_for("06_flip")

        # 07: stats
        mark("07_stats")
        scroll_to_text(page, "Why this matters to the business", "stats scroll failed")
        hold_for("07_stats")

        # 08: context
        mark("08_context")
        scroll_to_text(page, "Who uses this, and on what", "context scroll failed")
        hold_for("08_context")

        # 09: outro
        mark("09_outro")
        def _footer():
            page.evaluate("window.scrollTo({top: document.body.scrollHeight, behavior:'smooth'})")
        safe(_footer, "footer scroll failed")
        hold_for("09_outro", floor_extra=0.8)

        print("[record] closing context to flush video...")
        video_path = page.video.path() if page.video else None
        context.close()
        browser.close()

    with open(os.path.join(DEMO_DIR, "marks.json"), "w", encoding="utf-8") as f:
        json.dump(marks, f, indent=2)

    final_path = os.path.join(VIDEO_DIR, "raw.webm")
    if video_path and os.path.exists(video_path) and video_path != final_path:
        shutil.move(video_path, final_path)
        video_path = final_path

    print(f"[record] done. raw video: {video_path}")


if __name__ == "__main__":
    main()
