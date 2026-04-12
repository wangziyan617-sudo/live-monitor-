"""
HLS 流录制模块：从抖音直播间提取 m3u8 直链地址
三种策略依次尝试：
  1. video.src 直接读取（video 标签上附带了流地址）
  2. page.route() 拦截网络请求，捕获第一个 .m3u8 URL
  3. 从页面 JS 上下文调用抖音内部 web API 获取 stream_url
任意一条成功即返回 m3u8 URL，三条均失败返回 None。
"""
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent.parent))

COOKIES_PATH = Path(__file__).parent.parent / "config" / "douyin_cookies.json"


async def extract_m3u8_url(page) -> str | None:
    """
    在已加载直播间页面的 page 上尝试三种策略提取 m3u8 URL。
    """
    m3u8_url: str | None = None

    # ── Strategy 1：直接读 video.src ──────────────────────────────────────
    try:
        src = await page.evaluate("""() => {
            const video = document.querySelector('video');
            return video ? video.src : null;
        }""")
        if src and ".m3u8" in src:
            print(f"[hls_recorder] Strategy 1 成功: {src[:80]}")
            return src
        else:
            print(f"[hls_recorder] Strategy 1 无 m3u8（src={src[:80] if src else None}）")
    except Exception as e:
        print(f"[hls_recorder] Strategy 1 异常: {e}")

    # ── Strategy 2：拦截网络请求 ────────────────────────────────────────────
    captured_url: str | None = None

    async def handle_route(route):
        nonlocal captured_url
        url = route.request.url
        if ".m3u8" in url and captured_url is None:
            captured_url = url
            print(f"[hls_recorder] Strategy 2 捕获到 m3u8: {url[:80]}")
        await route.continue_()

    try:
        await page.route("**/*.m3u8*", handle_route)
        # 等待最多 8 秒让请求发出
        await page.wait_for_timeout(8000)
        await page.unroute("**/*.m3u8*")
        if captured_url:
            return captured_url
        print("[hls_recorder] Strategy 2 未捕获到 m3u8 请求")
    except Exception as e:
        print(f"[hls_recorder] Strategy 2 异常: {e}")
        try:
            await page.unroute("**/*.m3u8*")
        except Exception:
            pass

    # ── Strategy 3：调用抖音内部 web API ───────────────────────────────────
    try:
        # 抖音直播间通常通过 /aweme/v1/web/room/feed/ 或类似接口获取流信息
        # 这里从页面 JS 上下文发起请求，cookie 自动跟随
        m3u8_from_api = await page.evaluate("""async () => {
            // 尝试从 RENDER_DATA 或 __NEXT_DATA__ 中提取 room_id
            const getRoomId = () => {
                // 方法1：从 URL 提取
                const match = location.pathname.match(/\\/live\\/(\\d+)/);
                if (match) return match[1];

                // 方法2：从页面 script 标签提取
                const scripts = document.querySelectorAll('script');
                for (const s of scripts) {
                    const text = s.textContent || '';
                    const m = text.match(/"room_id"?[:\\s]+"?(\\d+)"?/);
                    if (m) return m[1];
                }
                return null;
            };

            const room_id = getRoomId();
            if (!room_id) return null;

            // 抖音 web API 获取直播流
            const url = `https://www.douyin.com/aweme/v1/web/room/feed/?room_id=${room_id}&device_platform=webapp&aid=6383`;
            try {
                const resp = await fetch(url, { credentials: 'include' });
                const data = await resp.json();
                if (data && data.data && data.data.stream_url && data.data.stream_url.hls_pull_url) {
                    return data.data.stream_url.hls_pull_url.HD || null;
                }
            } catch (e) {}
            return null;
        }""")
        if m3u8_from_api and ".m3u8" in m3u8_from_api:
            print(f"[hls_recorder] Strategy 3 成功: {m3u8_from_api[:80]}")
            return m3u8_from_api
        else:
            print(f"[hls_recorder] Strategy 3 无 m3u8")
    except Exception as e:
        print(f"[hls_recorder] Strategy 3 异常: {e}")

    return None


async def fetch_m3u8_url(competitor: dict) -> str | None:
    """
    启动 Playwright 浏览器，加载 cookies，导航到直播间，提取 m3u8 URL。
    返回 m3u8 URL 字符串或 None。
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        if COOKIES_PATH.exists():
            cookies = json.loads(COOKIES_PATH.read_text())
            await context.add_cookies(cookies)

        page = await context.new_page()

        live_url = competitor.get("live_url")
        if not live_url:
            live_url = competitor.get("url", "")

        print(f"[hls_recorder] 打开直播间: {live_url}")
        try:
            await page.goto(live_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[hls_recorder] 页面加载失败: {e}")
            await context.close()
            await browser.close()
            return None

        # 等待页面初始化
        await page.wait_for_timeout(5000)

        m3u8_url = await extract_m3u8_url(page)

        await context.close()
        await browser.close()

        if m3u8_url:
            return m3u8_url

        print("[hls_recorder] HLS 提取失败")
        return None


if __name__ == "__main__":
    import asyncio
    from config.settings import COMPETITORS

    competitor = next(
        (c for c in COMPETITORS if c.get("live_url")),
        {"name": "测试", "live_url": "https://live.douyin.com/90127779527"},
    )
    result = asyncio.run(fetch_m3u8_url(competitor))
    print(f"m3u8 URL: {result}")
