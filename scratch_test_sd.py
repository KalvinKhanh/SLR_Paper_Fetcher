import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            channel="msedge",
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"]
        )
        context = await browser.new_context()
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        page = await context.new_page()
        await page.goto("https://www.sciencedirect.com/science/article/pii/S1874490726001163?via%3Dihub")
        print("Title:", await page.title())
        await asyncio.sleep(5)
        print("Title after 5s:", await page.title())
        content = await page.content()
        print("Turnstile in content?", "turnstile" in content.lower() or "challenge" in content.lower())
        await browser.close()

asyncio.run(test())
