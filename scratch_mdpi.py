import asyncio
from playwright.async_api import async_playwright
from pathlib import Path

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            accept_downloads=True
        )
        page = await context.new_page()
        print("Going to landing page...")
        await page.goto("https://www.mdpi.com/2076-3417/12/3/1023", wait_until="domcontentloaded")
        print("Page title:", await page.title())
        
        pdf_link = await page.query_selector('a.show-pdf, a[href*="/pdf"]')
        if pdf_link:
            print("Clicking PDF link...")
            async with page.expect_download(timeout=20000) as dl_info:
                await pdf_link.click()
            dl = await dl_info.value
            out_file = Path("downloads") / "applsci-12-01023.pdf"
            await dl.save_as(str(out_file))
            print("SUCCESS! File saved to:", out_file, "size:", out_file.stat().st_size)
        else:
            print("No PDF link selector found")
        await browser.close()

asyncio.run(test())
