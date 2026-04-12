"""
HLS 流录制模块：从抖音直播间提取 m3u8 直链地址并录制
三种策略依次尝试：
  1. video.src 直接读取（video 标签上附带了流地址）
  2. page.route() 拦截网络请求，捕获第一个 .m3u8 URL
  3. httpx 获取页面 HTML，解析 roomStore.roomInfo.web_stream_url
任意一条成功即返回 m3u8 URL，三条均失败返回 None。
"""
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import RECORD_OUTPUT_DIR

COOKIES_PATH = Path(__file__).parent.parent / "config" / "douyin_cookies.json"


async def extract_m3u8_url(page, live_url: str) -> str | None:
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

    # ── Strategy 3：httpx 获取页面 HTML，解析 web_stream_url（需立即录制）
    try:
        room_id_match = re.search(r"live\.douyin\.com/(\d+)", live_url)
        if not room_id_match:
            print("[hls_recorder] Strategy 3：无法从 URL 提取 room_id")
        else:
            room_id = room_id_match.group(1)

            # 加载 cookie 字典
            cookies = {}
            if COOKIES_PATH.exists():
                for c in json.loads(COOKIES_PATH.read_text()):
                    cookies[c["name"]] = c["value"]

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Referer": f"https://live.douyin.com/{room_id}/",
                "Accept": "text/html,application/xhtml+xml",
            }

            resp = httpx.get(
                f"https://live.douyin.com/{room_id}",
                cookies=cookies,
                headers=headers,
                timeout=15,
                follow_redirects=True,
            )
            html = resp.text

            # 从 HTML 中提取 web_stream_url（双转义 JSON：HTML 中是 \"）
            # 优先提取 flv URL（抖音直播常用格式），其次 m3u8
            flv_match = re.search(
                r'\\"flv_pull_url\\"\s*:\{[^}]*?\\"([^\\"]{20,})\.flv', html
            )
            if flv_match:
                stream_url = flv_match.group(1) + ".flv"
                print(f"[hls_recorder] Strategy 3 成功 (FLV): {stream_url[:80]}")
                return stream_url

            # 尝试直接提取 m3u8 URL
            m3u8_match = re.search(
                r'\\"web_stream_url\\"\s*:\s*\\"([^\\"]{10,})\.m3u8', html
            )
            if m3u8_match:
                stream_url = m3u8_match.group(1) + ".m3u8"
                print(f"[hls_recorder] Strategy 3 成功 (m3u8): {stream_url[:80]}")
                return stream_url

            # 检查是否为 null（房间未直播）
            null_match = re.search(r'\\"web_stream_url\\"\s*:\s*null', html)
            if null_match:
                print("[hls_recorder] Strategy 3：web_stream_url 为空（房间可能未在直播）")
            else:
                print("[hls_recorder] Strategy 3：页面无流数据（房间可能未在直播）")

    except Exception as e:
        print(f"[hls_recorder] Strategy 3 异常: {e}")

    return None


async def fetch_m3u8_url(competitor: dict) -> str | None:
    """
    启动 Playwright 浏览器，加载 cookies，导航到直播间，提取流 URL。
    优先通过 Strategy 2 拦截网络请求（实时有效），次用 Strategy 3 HTML 解析。
    返回流 URL 字符串或 None。
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

        # ── Strategy 2：先设好拦截器，再导航（捕获实时有效 URL）──────────────
        captured_url: str | None = None

        async def handle_route(route):
            nonlocal captured_url
            url = route.request.url
            if (".m3u8" in url or ".flv" in url) and captured_url is None:
                captured_url = url
                print(f"[hls_recorder] Strategy 2 捕获到流: {url[:80]}")
            await route.continue_()

        # 先设路由处理器，再导航
        await page.route("**/*.m3u8*", handle_route)
        await page.route("**/stream*", handle_route)

        print(f"[hls_recorder] 打开直播间: {live_url}")
        try:
            await page.goto(live_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[hls_recorder] 页面加载失败: {e}")
            await context.close()
            await browser.close()
            return None

        # 等待流请求发出（最多 8 秒）
        await page.wait_for_timeout(8000)
        await page.unroute("**/*.m3u8*")
        await page.unroute("**/stream*")

        # 优先返回拦截到的 URL（最新鲜）
        if captured_url:
            await context.close()
            await browser.close()
            return captured_url

        # Strategy 2 失败，尝试 Strategy 3（HTML 解析）
        m3u8_url = await extract_m3u8_url(page, live_url)

        await context.close()
        await browser.close()

        if m3u8_url:
            return m3u8_url

        print("[hls_recorder] HLS 提取失败")
        return None


def record_m3u8(m3u8_url: str, competitor: dict, duration: int = 120) -> Path | None:
    """
    用 ffmpeg 直接下载 m3u8 流并 remux 到 mp4，全程不重编码。
    最多重试 1 次。验证输出包含 audio 流。
    返回 Path（成功）或 None（失败）。
    """
    RECORD_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    safe_name = competitor["name"].replace(" ", "_")
    output_path = RECORD_OUTPUT_DIR / f"{safe_name}_{date_str}.mp4"

    last_exc: Exception | None = None
    for attempt in range(2):  # 0=首次，1=重试
        if attempt > 0:
            print(f"[hls_recorder] ffmpeg 录制失败，重试一次...")
            time.sleep(2)

        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", m3u8_url,
                    "-c", "copy",          # 不重编码，全程 remux
                    "-t", str(duration),
                    "-timeout", "30000",   # 单次操作 30s 超时（ffmpeg 内部）
                    "-headers",
                    "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\r\n"
                    "Referer: https://live.douyin.com/\r\n",
                    str(output_path),
                ],
                capture_output=True,
                timeout=duration + 60,
            )
            if proc.returncode != 0:
                stderr = proc.stderr.decode("utf-8", errors="replace")
                # 常见的暂时性错误：network timeout、connection reset、403 → 重试
                retryable = any(kw in stderr for kw in [
                    "Connection timed out", "Connection reset", "Server returned 5",
                    "Server returned 403", "End of file", "Invalid data found",
                ])
                if retryable and attempt == 0:
                    last_exc = RuntimeError(f"ffmpeg retryable error: {stderr[:200]}")
                    continue
                print(f"[hls_recorder] ffmpeg 错误: {stderr[:300]}")
                last_exc = RuntimeError(f"ffmpeg failed with {proc.returncode}")
                break

            # 验证 audio 流存在
            verify = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "a",
                    "-show_entries", "stream=codec_type",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if verify.returncode == 0 and verify.stdout.strip():
                print(f"[hls_recorder] 录制完成（含音频流）: {output_path}")
                return output_path
            else:
                print(f"[hls_recorder] 录制文件无音频流: {output_path}")
                # 无音频也返回路径（录到了内容），但记录警告
                return output_path

        except subprocess.TimeoutExpired:
            last_exc = RuntimeError(f"ffmpeg 超时（{duration + 60}s）")
            if attempt == 0:
                continue
            break
        except Exception as e:
            last_exc = e
            if attempt == 0:
                continue
            break

    print(f"[hls_recorder] m3u8 录制失败: {last_exc}")
    return None


async def record_live_room(competitor: dict, duration: int = 120) -> Path | None:
    """
    统一的录制入口：优先尝试 HLS 流录制，失败时返回 None（由调用方降级）。
    与 recorder.record_live_room() 接口一致，scheduler 可直接替换调用。
    """
    # 尝试 HLS 方案
    try:
        m3u8_url = await fetch_m3u8_url(competitor)
        if m3u8_url:
            video_path = record_m3u8(m3u8_url, competitor, duration=duration)
            if video_path:
                return video_path
    except Exception as e:
        print(f"[hls_recorder] HLS 录制异常: {e}")

    # HLS 失败，返回 None 供调用方降级
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
