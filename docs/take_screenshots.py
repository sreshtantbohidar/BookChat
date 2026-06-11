import asyncio, os
from playwright.async_api import async_playwright

OUTPUT = '/workspaces/codespaces-blank/ai_summary/bookchat/docs/screenshots'
os.makedirs(OUTPUT, exist_ok=True)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = await browser.new_page(viewport={'width': 1280, 'height': 900})
        
        await page.goto('http://localhost:5000', wait_until='networkidle', timeout=10000)
        await page.wait_for_timeout(2000)
        
        # 1: Main page - full view
        await page.screenshot(path=f'{OUTPUT}/01_main_page.png')
        print("1: Main page")
        
        # 2: Upload zone hover
        box = await page.query_selector('#uploadZone')
        if box:
            await box.hover()
            await page.wait_for_timeout(500)
        await page.screenshot(path=f'{OUTPUT}/02_upload_zone.png')
        print("2: Upload zone")
        
        # 3: Select first document
        first = await page.query_selector('.doc-item')
        if first:
            await first.click()
            await page.wait_for_timeout(1000)
            await page.screenshot(path=f'{OUTPUT}/03_doc_selected.png')
            print("3: Doc selected")
            
            # 4: Type question
            await page.fill('#queryInput', 'What is this document about?')
            await page.screenshot(path=f'{OUTPUT}/04_typed_question.png')
            print("4: Typed question")
            
            # 5: Send and wait for response
            await page.click('#sendBtn')
            print("Waiting 50s for LLM response...")
            await page.wait_for_timeout(50000)
            await page.screenshot(path=f'{OUTPUT}/05_chat_response.png')
            print("5: Chat response")
        
        # 6: All docs mode
        all_btn = await page.query_selector('#chatAllBtn')
        if all_btn:
            await all_btn.click()
            await page.wait_for_timeout(1000)
            await page.screenshot(path=f'{OUTPUT}/06_all_docs_mode.png')
            print("6: All docs mode")
        
        # 7: Mode selector area
        await page.screenshot(path=f'{OUTPUT}/07_mode_selector.png')
        print("7: Mode selector")
        
        await browser.close()
        print(f"\nAll screenshots saved to {OUTPUT}/")

asyncio.run(main())
