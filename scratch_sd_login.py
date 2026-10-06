import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = await context.new_page()
        print("Going to ScienceDirect...")
        await page.goto('https://www.sciencedirect.com/science/article/pii/S092523122100806X')
        print("Title:", await page.title())
        
        btn = page.locator('button:has-text("Access through your organization"), a:has-text("Access through your organization")').first
        if await btn.count() > 0:
            print("Found Access through your organization button")
            await btn.click()
            await page.wait_for_timeout(3000)
            print("Title after click:", await page.title())
            
            org_input = page.get_by_label('Organization name or email', exact=False).first
            if await org_input.count() > 0:
                print("Found organization input")
                await org_input.fill('Vietnam National University Ho Chi Minh City')
                await page.wait_for_timeout(3000)
                
                options = page.locator('[role="option"], li')
                count = await options.count()
                print("Found options:", count)
                for i in range(count):
                    try:
                        text = await options.nth(i).inner_text(timeout=1000)
                        print(f"Option {i+1}: {text.strip()}")
                    except:
                        pass
                
        else:
            print("Button not found. Content saved.")
            with open('sd_content.html', 'w', encoding='utf-8') as f:
                f.write(await page.content())
        await browser.close()

asyncio.run(test())
