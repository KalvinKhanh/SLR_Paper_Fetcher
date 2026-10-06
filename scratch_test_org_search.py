import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://www.sciencedirect.com/user/institution/login")
        print("Page URL:", page.url)
        print("Page Title:", await page.title())
        
        # Look for organization input
        input_elem = page.locator('input[name*="organization" i], input[id*="organization" i], input[type="text"]').first
        if await input_elem.count():
            print("Found input, typing organization...")
            await input_elem.fill("Vietnam National University Ho Chi Minh City")
            await page.wait_for_timeout(3000)
            
            # Print all dropdown items, options, buttons, lists
            options = page.locator('li, [role="option"], ul > li, button, a')
            count = await options.count()
            print(f"Total options/links/buttons: {count}")
            for i in range(count):
                txt = (await options.nth(i).inner_text()).strip()
                if any(k in txt.lower() for k in ["vietnam", "ho chi minh", "law", "university"]):
                    print(f"Match [{i}]: {txt}")
        else:
            print("No text input found")
        await browser.close()

asyncio.run(test())
