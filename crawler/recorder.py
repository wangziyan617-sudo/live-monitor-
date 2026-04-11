"""
录屏模块：用 Playwright 打开抖音直播间，录制指定时长的视频
- 自动检测直播间是否在播
- 录制完成后保存到 storage/videos/
"""
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import RECORD_DURATION_SECONDS, RECORD_OUTPUT_DIR

COOKIES_PATH = Path(__file__).parent.parent / "config" / "douyin_cookies.json"


async def find_live_room(page, competitor: dict) -> str | None:
    """
    找到直播间 URL。优先用 live_url 直链，否则从主页检测。
    """
    # 优先用直播间直链
    if competitor.get("live_url"):
        live_url = competitor["live_url"]
        print(f"[recorder] 直接访问直播间: {live_url}")
        await page.goto(live_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(4000)
        # 检查是否真的在播（有 video 元素）
        video_count = await page.locator('video').count()
        if video_count > 0:
            print(f"[recorder] 直播间已加载，video元素: {video_count}")
            return live_url
        print(f"[recorder] 直播间无视频，可能未在播")
        return None

    # 没有直链，从主页检测
    home_url = competitor["url"]
    print(f"[recorder] 访问主页: {home_url}")
    await page.goto(home_url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(3000)

    live_badge = page.locator('text=直播中').first
    if await live_badge.count() > 0:
        await live_badge.click()
        await page.wait_for_timeout(3000)
        print(f"[recorder] 找到直播间: {page.url}")
        return page.url

    live_links = await page.locator('a[href*="/live/"]').all()
    if live_links:
        href = await live_links[0].get_attribute("href")
        live_url = f"https://www.douyin.com{href}" if href.startswith("/") else href
        await page.goto(live_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        print(f"[recorder] 找到直播间: {live_url}")
        return live_url

    print(f"[recorder] {competitor['name']} 当前未在直播")
    return None


async def record_live_room(competitor: dict, duration: int = RECORD_DURATION_SECONDS) -> Path | None:
    """
    录制直播间视频，返回保存的视频路径。
    duration: 录制秒数
    """
    RECORD_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    safe_name = competitor["name"].replace(" ", "_")
    video_path = RECORD_OUTPUT_DIR / f"{safe_name}_{date_str}.webm"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # 抖音检测无头浏览器，先用有头模式
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            record_video_dir=str(RECORD_OUTPUT_DIR),
            record_video_size={"width": 1280, "height": 720},
        )

        # 加载登录 cookies
        if COOKIES_PATH.exists():
            cookies = json.loads(COOKIES_PATH.read_text())
            await context.add_cookies(cookies)
            print(f"[recorder] 已加载登录 cookies ({len(cookies)} 条)")
        else:
            print(f"[recorder] 警告: 未找到 cookies，请先运行 login_helper.py 登录")

        page = await context.new_page()

        try:
            live_url = await find_live_room(page, competitor)
            if not live_url:
                await context.close()
                await browser.close()
                return None

            # 等待直播画面真正加载出来（视频元素出现且不再显示"加载中"）
            print(f"[recorder] 等待直播画面加载...")
            try:
                await page.wait_for_selector('video', timeout=20000)
                # 额外等待几秒让视频流稳定
                await page.wait_for_timeout(5000)
                print(f"[recorder] 直播画面已加载，开始录制 {competitor['name']}，时长 {duration}s ...")
            except Exception:
                print(f"[recorder] 等待视频超时，直接开始录制...")

            await asyncio.sleep(duration)
            print(f"[recorder] 录制完成")

        except Exception as e:
            print(f"[recorder] 录制出错: {e}")
            await context.close()
            await browser.close()
            return None

        await context.close()
        await browser.close()

    # Playwright 录制的视频文件名是自动生成的，找最新的 webm
    videos = sorted(RECORD_OUTPUT_DIR.glob("*.webm"), key=lambda f: f.stat().st_mtime)
    if videos:
        latest = videos[-1]
        target = RECORD_OUTPUT_DIR / f"{safe_name}_{date_str}.webm"
        latest.rename(target)
        print(f"[recorder] 视频保存至: {target}")
        return target

    return None


if __name__ == "__main__":
    # 快速测试：录制30秒
    from config.settings import COMPETITORS
    # 用猿辅导测试
    competitor = next(c for c in COMPETITORS if c["name"] == "猿辅导")
    result = asyncio.run(record_live_room(competitor, duration=30))
    print(f"结果: {result}")
