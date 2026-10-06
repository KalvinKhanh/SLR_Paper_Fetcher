import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        page = await browser.new_page()
        await page.goto("https://www.sciencedirect.com/user/institution/login", wait_until="networkidle")
        print("Page URL:", page.url)
        print("Page Title:", await page.title())
        inputs = page.locator("input")
        count = await inputs.count()
        print("Input count:", count)
        for i in range(count):
            inp = inputs.nth(i)
            print(f"Input {i}: name={await inp.get_attribute('name')} id={await inp.get_attribute('id')} placeholder={await inp.get_attribute('placeholder')}")
        await browser.close()

asyncio.run(test())
