"""
登录辅助脚本：手动登录抖音后保存 cookies，供后续录制使用
运行一次即可，cookies 保存到 config/douyin_cookies.json
"""
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

COOKIES_PATH = Path(__file__).parent / "config" / "douyin_cookies.json"


async def save_login():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto("https://www.douyin.com", wait_until="domcontentloaded")

        print("=" * 50)
        print("请在弹出的浏览器窗口中手动登录抖音")
        print("登录完成后回到这里按 Enter 保存 cookies")
        print("=" * 50)
        input("登录完成后按 Enter...")

        cookies = await context.cookies()
        COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        COOKIES_PATH.write_text(json.dumps(cookies, ensure_ascii=False, indent=2))
        print(f"cookies 已保存: {COOKIES_PATH}  ({len(cookies)} 条)")

        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(save_login())
