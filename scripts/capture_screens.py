"""Takes the screenshots used in the project report (docs/report_assets/*.png) from the running app.

Needs the app on http://localhost:8000 with MongoDB, one finished upload of a labelled file in the workspace
below, and Google Chrome installed. Usage: python -m scripts.capture_screens
"""
import os

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
OWNER = "0123456789abcdef0123456789abcdef"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "report_assets")


def main():
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        ctx = browser.new_context(viewport={"width": 1366, "height": 860}, device_scale_factor=1.5)
        ctx.add_init_script(f"localStorage.setItem('bda_owner', '{OWNER}'); localStorage.setItem('bda_dataset', 'paysim');")
        page = ctx.new_page()
        page.add_init_script("document.addEventListener('DOMContentLoaded', () => { const s = document.createElement('style'); s.textContent = '#toasts, .toast { display: none !important }'; document.head.appendChild(s); });")
        shot = lambda name, target=None, **kw: (target or page).screenshot(path=os.path.join(OUT, name + ".png"), **kw)

        page.goto(BASE)
        page.wait_for_selector("#flaggedTable tbody tr")
        page.wait_for_timeout(1200)
        shot("fig_overview_top")  # cards + transaction types

        sections = page.locator("#overviewPaysim > section")
        for i, name in [(2, "fig_hours"), (3, "fig_balance"), (4, "fig_model"), (5, "fig_flagged")]:
            sections.nth(i).scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            shot(name, sections.nth(i))

        page.locator("[data-example=fraud]").click()
        page.wait_for_timeout(500)
        page.locator("#predictForm button[type=submit]").click()
        page.wait_for_selector("#predictResult .result")
        sections.nth(6).scroll_into_view_if_needed()
        shot("fig_predict", sections.nth(6))

        # Explorer: typing shows suggestions
        page.click("[data-view=explorer]")
        page.wait_for_timeout(800)
        page.click("#accountInput")
        page.keyboard.type("C1305", delay=70)
        page.wait_for_selector("#accountList li")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(300)
        shot("fig_suggestions", clip={"x": 0, "y": 0, "width": 1366, "height": 640})

        page.keyboard.press("Enter")
        page.wait_for_selector("#searchTable tbody tr")
        page.wait_for_timeout(600)
        # a fraud sender gives a richer example than the first suggestion
        page.click("[data-search-example=fraudSender]")
        page.wait_for_selector("#searchTable tbody tr")
        page.wait_for_timeout(600)
        page.locator("#searchTable tbody tr").first.click()
        page.wait_for_selector("#txDetail .card")
        page.wait_for_timeout(400)
        shot("fig_explorer", page.locator("#view-explorer"))

        # open a case, then work it in the Cases tab
        page.locator("#txDetail button", has_text="Open case").click()
        page.wait_for_timeout(1200)
        page.click("[data-view=cases]")
        page.wait_for_selector("#caseTable tbody tr")
        page.locator("#caseTable tbody tr").first.click()
        page.wait_for_selector("#caseEditForm")
        form = page.locator("#caseEditForm")
        form.locator("select[name=status]").select_option("investigating")
        form.locator("input[name=assignee]").fill("Analyst 1")
        form.locator("button[type=submit]").click()
        page.wait_for_timeout(1000)
        page.locator("#caseNoteForm textarea").fill("Receiver balance did not change after the transfer; checking with the customer.")
        page.locator("#caseNoteForm button").click()
        page.wait_for_timeout(1000)
        shot("fig_cases", page.locator("#view-cases"))

        # Upload result of the labelled file, and the overview built from it
        page.click("[data-view=upload]")
        page.wait_for_selector("#uploadTable [data-view-id]")
        rows = page.locator("#uploadTable tbody tr")
        for i in range(rows.count()):
            if "with_labels" in rows.nth(i).inner_text():
                rows.nth(i).locator("[data-view-id]").click()
                break
        page.wait_for_selector("#uploadResult .card")
        page.wait_for_timeout(800)
        page.locator("#uploadResult .card").scroll_into_view_if_needed()
        shot("fig_upload", page.locator("#uploadResult .card"), )
        page.click("#overviewUpload2")
        page.wait_for_selector("#upTypeChart")
        page.wait_for_timeout(1500)
        shot("fig_upload_overview", page.locator("#overviewUpload"))
        browser.close()


if __name__ == "__main__":
    main()
