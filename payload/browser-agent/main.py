from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
from typing import List, Optional
from fastapi import FastAPI
from pydantic import BaseModel
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

app = FastAPI(title="Luminax Browser Agent")

class NavRequest(BaseModel):
    url: str

class ScreenshotRequest(BaseModel):
    url: str
    full_page: bool = True

class ChainAction(BaseModel):
    action: str
    url: Optional[str] = None
    selector: Optional[str] = None
    text: Optional[str] = None

class ChainRequest(BaseModel):
    actions: List[ChainAction]

@app.get("/health")
async def health():
    return {"status":"ok","capabilities":["navigate","chain","screenshot"],"engine":"playwright"}

@app.post("/navigate")
async def navigate(req: NavRequest):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(req.url, wait_until="networkidle", timeout=120000)
        title = await page.title()
        current_url = page.url
        html = await page.content()
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        await browser.close()
        return {"title": title, "url": current_url, "text_preview": text[:5000], "html_preview": html[:5000]}

@app.post("/screenshot")
async def screenshot(req: ScreenshotRequest):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(req.url, wait_until="networkidle", timeout=120000)
        title = await page.title()
        current_url = page.url
        png = await page.screenshot(full_page=req.full_page)
        import base64
        encoded = base64.b64encode(png).decode("utf-8")
        await browser.close()
        return {"title": title, "url": current_url, "image_base64": encoded}

@app.post("/chain")
async def chain(req: ChainRequest):
    steps = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        for item in req.actions:
            if item.action == "goto":
                await page.goto(item.url, wait_until="networkidle", timeout=120000)
                steps.append({"action": "goto", "url": page.url, "title": await page.title()})
            elif item.action == "click":
                await page.click(item.selector, timeout=30000)
                await page.wait_for_load_state("networkidle", timeout=30000)
                steps.append({"action": "click", "selector": item.selector, "url": page.url, "title": await page.title()})
            elif item.action == "fill":
                await page.fill(item.selector, item.text or "", timeout=30000)
                steps.append({"action": "fill", "selector": item.selector})
        title = await page.title()
        current_url = page.url
        html = await page.content()
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        await browser.close()
    return {"steps": steps, "title": title, "url": current_url, "text_preview": text[:5000], "html_preview": html[:5000]}


install_runtime_guard(app, 'browser-agent', require_auth=True, required_feature='LUMINAX_BROWSER_ACTIONS_ENABLED')
