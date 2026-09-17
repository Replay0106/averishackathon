import json
import os
import shutil
import time

from playwright.sync_api import sync_playwright

DEMO_DIR = os.path.dirname(__file__)
APP_URL = "http://localhost:8501"
VIDEO_DIR = os.path.join(DEMO_DIR, "raw_video")
VIEWPORT = {"width": 1280, "height": 800}

FAKE_CURSOR_SCRIPT = """
(() => {
  const cursor = document.createElement('div');
  cursor.id = '__fake_cursor__';
  Object.assign(cursor.style, {
    position: 'fixed', top: '0px', left: '0px', width: '18px', height: '18px',
    borderRadius: '50%', background: 'rgba(255,90,30,0.9)',
    border: '2px solid white', zIndex: 2147483647, pointerEvents: 'none',
    transform: 'translate(-50%, -50%)', transition: 'top 30ms linear, left 30ms linear',
    boxShadow: '0 0 6px rgba(0,0,0,0.5)'
  });
  document.addEventListener('DOMContentLoaded', () => document.body.appendChild(cursor));
  window.addEventListener('mousemove', (e) => {
    cursor.style.left = e.clientX + 'px';
    cursor.style.top = e.clientY + 'px';
    if (!document.body.contains(cursor)) document.body.appendChild(cursor);
  }, true);
  window.addEventListener('mousedown', (e) => {
    const ripple = document.createElement('div');
    Object.assign(ripple.style, {
      position: 'fixed', top: e.clientY + 'px', left: e.clientX + 'px',
      width: '10px', height: '10px', borderRadius: '50%',
      border: '3px solid rgba(255,90,30,0.9)', transform: 'translate(-50%, -50%)',
      zIndex: 2147483646, pointerEvents: 'none'
    });
    document.body.appendChild(ripple);
    const start = performance.now();
    function grow(ts) {
      const t = (ts - start) / 400;
      if (t >= 1) { ripple.remove(); return; }
      const scale = 1 + t * 3;
      ripple.style.opacity = String(1 - t);
      ripple.style.width = (10 * scale) + 'px';
      ripple.style.height = (10 * scale) + 'px';
      requestAnimationFrame(grow);
    }
    requestAnimationFrame(grow);
  }, true);
})();
"""


class Recorder:
    def __init__(self, page):
        self.page = page
        self.pos = (VIEWPORT["width"] // 2, VIEWPORT["height"] // 2)

    def move_to(self, x: float, y: float, steps: int = 25):
        x0, y0 = self.pos
        for i in range(1, steps + 1):
            nx = x0 + (x - x0) * i / steps
            ny = y0 + (y - y0) * i / steps
            self.page.mouse.move(nx, ny)
            self.page.wait_for_timeout(8)
        self.pos = (x, y)

    def click_locator(self, locator, label: str):
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        if box is None:
            raise RuntimeError(f"no bounding box for {label}")
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        self.move_to(cx, cy)
        self.page.wait_for_timeout(150)
        locator.click(timeout=10000)


def safe(fn, warn: str):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] {warn}: {exc}")


def wait_for_report(page, rec, max_seconds=90, retry_click_name=None):
    waited = 0.0
    retried = False
    while waited < max_seconds:
        page.wait_for_timeout(3000)
        waited += 3
        try:
            body_text = page.inner_text("body")
        except Exception as exc:  # noqa: BLE001
            print(f"[poll] body read failed at {waited:.0f}s: {exc}")
            continue
        if "CLEARED FOR BANK PRESENTATION" in body_text or "DISCREPANCIES DETECTED" in body_text:
            print(f"[poll] report ready at {waited:.0f}s")
            return
        if "Audit failed" in body_text:
            idx = body_text.find("Audit failed")
            print(f"[poll] audit failed at {waited:.0f}s: {body_text[idx:idx + 180]}")
            if retry_click_name and not retried:
                retried = True
                print(f"[poll] retrying {retry_click_name} click...")
                try:
                    btn = page.get_by_role("button", name=retry_click_name, exact=False)
                    rec.click_locator(btn, f"{retry_click_name} retry")
                except Exception as exc:  # noqa: BLE001
                    print(f"[poll] retry click failed: {exc}")
                continue
            raise RuntimeError("audit failed and retry exhausted")
        print(f"[poll] still waiting at {waited:.0f}s")
    raise TimeoutError(f"report not ready after {max_seconds}s")


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

    def hold_for(beat_id: str, floor_extra: float = 0.5):
        elapsed = time.time() - t_start - marks[beat_id]
        target = durations.get(beat_id, 3.0) + floor_extra
        remaining = max(0.0, target - elapsed)
        if remaining > 0:
            time.sleep(remaining)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=VIDEO_DIR,
            record_video_size=VIEWPORT,
        )
        context.add_init_script(FAKE_CURSOR_SCRIPT)
        page = context.new_page()
        rec = Recorder(page)

        print("[record] navigating to app...")
        safe(lambda: page.goto(APP_URL, wait_until="domcontentloaded", timeout=30000), "initial navigation slow/failed")
        page.wait_for_timeout(1500)

        # 01: intro
        mark("01_intro")
        rec.move_to(VIEWPORT["width"] / 2, 120)
        hold_for("01_intro")

        # 02: sidebar / upload panel
        mark("02_sidebar")
        def open_sidebar():
            toggle = page.locator('[data-testid="stSidebarCollapsedControl"] button')
            if toggle.count() > 0 and toggle.first.is_visible():
                rec.click_locator(toggle.first, "sidebar toggle")
                page.wait_for_timeout(500)
            heading = page.get_by_text("Upload Shipment Documents")
            heading.first.wait_for(state="visible", timeout=8000)
        safe(open_sidebar, "sidebar not visible")
        hold_for("02_sidebar")

        # 03: load discrepant shipment
        mark("03_load_discrepant")
        def click_discrepant():
            btn = page.get_by_role("button", name="Load Discrepant Shipment", exact=False)
            btn.wait_for(state="visible", timeout=10000)
            rec.click_locator(btn, "Load Discrepant Shipment button")
        safe(click_discrepant, "Load Discrepant Shipment click failed")
        hold_for("03_load_discrepant", floor_extra=0.2)

        # 04: stage 1 running
        mark("04_stage1_running")
        safe(lambda: wait_for_report(page, rec, retry_click_name="Load Discrepant Shipment"),
             "discrepant audit did not complete in time")
        page.wait_for_timeout(400)
        hold_for("04_stage1_running")

        # 05: stage 1 result — banner + KPIs
        mark("05_stage1_result")
        safe(lambda: page.evaluate("window.scrollTo(0,0)"), "scroll to top failed")
        rec.move_to(300, 260)
        hold_for("05_stage1_result")

        # 06: router assignment detail
        mark("06_router_detail")
        def open_router_expander():
            exp = page.get_by_text("Router assignment per document")
            exp.first.wait_for(state="visible", timeout=8000)
            rec.click_locator(exp.first, "router expander")
            page.wait_for_timeout(600)
        safe(open_router_expander, "router expander interaction failed")
        hold_for("06_router_detail")

        # 07: system health & governance hub
        mark("07_health_hub")
        def open_health_hub():
            exp = page.get_by_text("System Health & AI Governance Hub")
            exp.first.wait_for(state="visible", timeout=8000)
            rec.click_locator(exp.first, "health hub expander")
            page.wait_for_timeout(600)
        safe(open_health_hub, "health hub interaction failed")
        hold_for("07_health_hub")

        # 08: discrepancy tab with citations
        mark("08_discrepancy_tab")
        def show_discrepancy_tab():
            tab = page.get_by_role("tab", name="Discrepancy & LC Audit")
            if tab.count() > 0:
                rec.click_locator(tab.first, "Discrepancy tab")
                page.wait_for_timeout(500)
            heading = page.get_by_text("Cross-Document & LC Discrepancies")
            safe(lambda: heading.first.scroll_into_view_if_needed(), "scroll to discrepancies heading")
        safe(show_discrepancy_tab, "discrepancy tab interaction failed")
        hold_for("08_discrepancy_tab")

        # 09: visual redline
        mark("09_redline")
        def show_redline():
            heading = page.get_by_text("Visual Document Redline")
            heading.first.wait_for(state="visible", timeout=15000)
            heading.first.scroll_into_view_if_needed()
        safe(show_redline, "redline section not found")
        hold_for("09_redline")

        # 10: apply redline toggle
        mark("10_apply_toggle")
        def apply_redline_toggle():
            toggle = page.get_by_text("Apply & Regenerate Verified Doc").first
            toggle.scroll_into_view_if_needed()
            rec.click_locator(toggle, "Apply & Regenerate toggle")
            page.wait_for_timeout(700)
        safe(apply_redline_toggle, "apply redline toggle failed")
        hold_for("10_apply_toggle")

        # 11: statutory gate (red)
        mark("11_statutory_gate")
        def show_statutory_gate():
            tab = page.get_by_role("tab", name="International Trade Statutory Gate")
            rec.click_locator(tab.first, "Statutory Gate tab")
            page.wait_for_timeout(600)
            heading = page.get_by_text("International Trade Statutory Gate — Trade Law Dictionary")
            safe(lambda: heading.first.scroll_into_view_if_needed(), "scroll statutory heading")
        safe(show_statutory_gate, "statutory gate tab click failed")
        hold_for("11_statutory_gate")

        # 12: dispatch engine
        mark("12_dispatch_engine")
        def show_dispatch_engine():
            tab = page.get_by_role("tab", name="Navis Dispatch Engine")
            rec.click_locator(tab.first, "Dispatch Engine tab")
            page.wait_for_timeout(600)
            heading = page.get_by_text("Navis Dispatch Engine — Actionable Operational Routing")
            safe(lambda: heading.first.scroll_into_view_if_needed(), "scroll dispatch heading")
        safe(show_dispatch_engine, "dispatch engine tab click failed")
        hold_for("12_dispatch_engine")

        # 13: scheduler + simulate instant dispatch
        mark("13_scheduler")
        def run_scheduler():
            heading = page.get_by_text("Auto-Schedule & Dispatch Pipeline")
            heading.first.wait_for(state="visible", timeout=15000)
            heading.first.scroll_into_view_if_needed()
            btn = page.get_by_role("button", name="Simulate Instant Dispatch", exact=False)
            if btn.count() > 0:
                rec.click_locator(btn.first, "Simulate Instant Dispatch")
                page.wait_for_timeout(2200)
        safe(run_scheduler, "dispatch scheduler interaction failed")
        hold_for("13_scheduler")

        # 14: execute carrier amendment flip
        mark("14_execute_flip")
        def click_execute_flip():
            btn = page.get_by_role("button", name="Execute 1-Click Carrier Amendment", exact=False)
            btn.wait_for(state="visible", timeout=10000)
            rec.click_locator(btn, "Execute Carrier Amendment button")
        safe(click_execute_flip, "Execute Carrier Amendment click failed")
        hold_for("14_execute_flip", floor_extra=0.3)

        # 15: flip result — green dashboard
        mark("15_flip_result")
        safe(lambda: page.evaluate("window.scrollTo(0,0)"), "scroll to top after flip failed")
        page.wait_for_timeout(500)
        rec.move_to(300, 260)
        hold_for("15_flip_result")

        # 16: statutory gate recheck (green)
        mark("16_statutory_recheck")
        def recheck_statutory_gate():
            tab = page.get_by_role("tab", name="International Trade Statutory Gate")
            rec.click_locator(tab.first, "Statutory Gate tab recheck")
            page.wait_for_timeout(600)
            heading = page.get_by_text("International Trade Statutory Gate — Trade Law Dictionary")
            safe(lambda: heading.first.scroll_into_view_if_needed(), "scroll statutory heading recheck")
        safe(recheck_statutory_gate, "statutory gate recheck failed")
        hold_for("16_statutory_recheck")

        # 17: document cross-comparison tab
        mark("17_cross_comparison")
        def show_cross_comparison():
            tab = page.get_by_role("tab", name="Document Cross-Comparison")
            rec.click_locator(tab.first, "Cross-Comparison tab")
            page.wait_for_timeout(700)
        safe(show_cross_comparison, "cross-comparison tab click failed")
        hold_for("17_cross_comparison")

        # 18: export permit & COO tab
        mark("18_export_coo")
        def show_export_coo():
            tab = page.get_by_role("tab", name="Export Permit & COO Verification")
            rec.click_locator(tab.first, "Export/COO tab")
            page.wait_for_timeout(700)
        safe(show_export_coo, "export/COO tab click failed")
        hold_for("18_export_coo")

        # 19: JSON export button
        mark("19_json_export")
        def show_json_export():
            btn = page.get_by_text("Download averis_audit_report.json")
            btn.first.scroll_into_view_if_needed()
            box = btn.first.bounding_box()
            if box:
                rec.move_to(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        safe(show_json_export, "json export scroll failed")
        hold_for("19_json_export")

        # 20: Ask Navis chat section
        mark("20_ask_navis")
        def show_ask_navis():
            heading = page.get_by_text("Ask Navis — Compliance Copilot")
            heading.first.wait_for(state="visible", timeout=8000)
            heading.first.scroll_into_view_if_needed()
        safe(show_ask_navis, "Ask Navis section not found")
        hold_for("20_ask_navis")

        # 21: outro
        mark("21_outro")
        safe(lambda: page.evaluate("window.scrollTo(0,0)"), "final scroll to top failed")
        rec.move_to(VIEWPORT["width"] / 2, 100)
        hold_for("21_outro", floor_extra=0.6)

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
    print(f"[record] marks: {os.path.join(DEMO_DIR, 'marks.json')}")


if __name__ == "__main__":
    main()
