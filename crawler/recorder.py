"""
录屏模块：
- 本地 Mac：Playwright 打开直播间 + ffmpeg 录制屏幕区域（带系统音频需 BlackHole，否则只录画面）
- Linux/GitHub Actions：Playwright + ffmpeg avfoundation/x11grab + PulseAudio 虚拟声卡
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import RECORD_DURATION_SECONDS, RECORD_OUTPUT_DIR

COOKIES_PATH = Path(__file__).parent.parent / "config" / "douyin_cookies.json"


async def find_live_room(page, competitor: dict) -> str | None:
    """找到直播间 URL，优先用 live_url 直链。"""
    if competitor.get("live_url"):
        live_url = competitor["live_url"]
        print(f"[recorder] 直接访问直播间: {live_url}")
        await page.goto(live_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(4000)
        video_count = await page.locator('video').count()
        if video_count > 0:
            print(f"[recorder] 直播间已加载，video元素: {video_count}")
            return live_url
        print(f"[recorder] 直播间无视频，可能未在播")
        return None

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


def _build_ffmpeg_cmd(output_path: Path, duration: int) -> list:
    """根据运行环境构建 ffmpeg 录制命令。"""
    is_linux = sys.platform == "linux"

    if is_linux:
        # GitHub Actions / Linux：x11grab 录屏 + PulseAudio 录音
        display = os.environ.get("DISPLAY", ":99")
        return [
            "ffmpeg", "-y",
            "-f", "x11grab", "-r", "25",
            "-s", "1280x720",
            "-i", f"{display}.0+0,0",
            "-f", "pulse", "-i", "VirtualSink.monitor",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-c:a", "aac", "-b:a", "64k",
            str(output_path),
        ]
    else:
        # Mac：avfoundation 录屏，尝试用 BlackHole 录音，没有就只录画面
        audio_devices = subprocess.run(
            ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True, text=True
        ).stderr
        has_blackhole = "BlackHole" in audio_devices

        if has_blackhole:
            return [
                "ffmpeg", "-y",
                "-f", "avfoundation", "-r", "25",
                "-i", "1:BlackHole 2ch",
                "-t", str(duration),
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                "-c:a", "aac", "-b:a", "64k",
                str(output_path),
            ]
        else:
            # 只录画面，无音频（本地测试用）
            print("[recorder] 未检测到 BlackHole，仅录制画面（无音频）")
            return [
                "ffmpeg", "-y",
                "-f", "avfoundation", "-r", "25",
                "-i", "1:none",
                "-t", str(duration),
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                "-an",
                str(output_path),
            ]


async def record_live_room(competitor: dict, duration: int = RECORD_DURATION_SECONDS) -> Path | None:
    """录制直播间视频，返回保存的视频路径。"""
    RECORD_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    safe_name = competitor["name"].replace(" ", "_")
    output_path = RECORD_OUTPUT_DIR / f"{safe_name}_{date_str}.mp4"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # 必须 False，否则 x11grab 录不到内容（Linux 上配合 Xvfb）
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

        import json
        if COOKIES_PATH.exists():
            cookies = json.loads(COOKIES_PATH.read_text())
            await context.add_cookies(cookies)

        page = await context.new_page()

        try:
            live_url = await find_live_room(page, competitor)
            if not live_url:
                await context.close()
                await browser.close()
                return None

            print(f"[recorder] 等待直播画面加载...")
            try:
                await page.wait_for_selector('video', timeout=20000)
                await page.wait_for_timeout(3000)
            except Exception:
                print(f"[recorder] 等待视频超时，继续录制...")

            # 启动 ffmpeg 录制
            ffmpeg_cmd = _build_ffmpeg_cmd(output_path, duration)
            print(f"[recorder] 开始录制 {competitor['name']}，时长 {duration}s ...")
            print(f"[recorder] ffmpeg cmd: {' '.join(ffmpeg_cmd)}")
            ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            await asyncio.sleep(duration)
            ffmpeg_proc.terminate()
            stdout, stderr = ffmpeg_proc.communicate(timeout=10)
            if stderr:
                # 只打印最后几行（ffmpeg 输出很多）
                stderr_lines = stderr.decode('utf-8', errors='replace').strip().split('\n')
                for line in stderr_lines[-5:]:
                    print(f"[ffmpeg] {line}")
            print(f"[recorder] 录制完成: {output_path}")

        except Exception as e:
            print(f"[recorder] 录制出错: {e}")
            await context.close()
            await browser.close()
            return None

        await context.close()
        await browser.close()

    if output_path.exists() and output_path.stat().st_size > 10000:
        return output_path

    print(f"[recorder] 视频文件异常，可能录制失败")
    return None


if __name__ == "__main__":
    from config.settings import COMPETITORS
    competitor = next(c for c in COMPETITORS if c["name"] == "猿辅导")
    result = asyncio.run(record_live_room(competitor, duration=30))
    print(f"结果: {result}")
