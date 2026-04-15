#!/usr/bin/env python3
"""
静态数据生成脚本：从 data.db 导出完整 JSON，供 GitHub Pages 直接读取（无后端）。
输出到 docs/data/ 目录。
"""
import json
import os
import sys
import sys as _sys
from pathlib import Path
from datetime import datetime

_ROOT = Path(__file__).parent

# --group 参数决定使用哪套数据库和输出目录
_group = "group1"
if "--group" in _sys.argv:
    _idx = _sys.argv.index("--group")
    if _idx + 1 < len(_sys.argv):
        _group = _sys.argv[_idx + 1]

if _group == "group2":
    DB_PATH = _ROOT / "storage" / "data2.db"
    OUTPUT_DIR = _ROOT / "docs" / "data2"
    BOARD_HTML_SRC = _ROOT / "docs" / "board2" / "index.html"
    BOARD_HTML_DST = _ROOT / "docs" / "board2" / "index.html"
else:
    DB_PATH = _ROOT / "storage" / "data.db"
    OUTPUT_DIR = _ROOT / "docs" / "data"
    BOARD_HTML_SRC = _ROOT / "docs" / "index.html"
    BOARD_HTML_DST = _ROOT / "docs" / "index.html"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_conn():
    import sqlite3
    return sqlite3.connect(DB_PATH)


def _parse_json_field(raw):
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else [val]
    except Exception:
        return []


# ── 导出 ──────────────────────────────────────────────────────────────────────

def export_competitors():
    conn = get_conn()
    # 确保 brand/stage/profile_url 列存在（向后兼容旧数据库）
    for col, dtype in [("brand", "TEXT DEFAULT ''"), ("stage", "TEXT DEFAULT ''"), ("profile_url", "TEXT DEFAULT ''")]:
        try:
            conn.execute(f"ALTER TABLE competitors ADD COLUMN {col} {dtype}")
        except Exception:
            pass
    rows = conn.execute(
        "SELECT id, name, douyin_id, url, profile_url, created_at, brand, stage FROM competitors ORDER BY id"
    ).fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "douyin_id": r[2] or "", "url": r[3] or "", "profile_url": r[4] or "", "created_at": r[5],
             "brand": r[6] or "", "stage": r[7] or "", "monitoring": True} for r in rows]


def export_sessions():
    conn = get_conn()
    rows = conn.execute("""
        SELECT s.id, s.competitor_id, s.recorded_at, s.video_path,
               s.duration, s.status, c.name AS competitor_name
        FROM sessions s
        LEFT JOIN competitors c ON c.id = s.competitor_id
        ORDER BY s.recorded_at DESC
    """).fetchall()
    conn.close()
    return [{"id": r[0], "competitor_id": r[1], "competitor": r[6] or "未知",
             "recorded_at": r[2], "video_path": r[3] or "", "duration": r[4] or 0, "status": r[5]} for r in rows]


def export_analyses():
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, session_id, key_products, sales_scripts,
               user_persona, strategy_summary, highlights, created_at
        FROM analysis ORDER BY id
    """).fetchall()
    conn.close()
    result = []
    for r in rows:
        kps = _parse_json_field(r[2])
        scripts = _parse_json_field(r[3])
        highlights = _parse_json_field(r[6])
        result.append({
            "id": r[0], "session_id": r[1],
            "key_products": kps, "sales_scripts": scripts,
            "user_persona": r[4] or "", "strategy_summary": r[5] or "",
            "highlights": highlights, "created_at": r[7],
        })
    return result


def export_transcripts():
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, session_id, archive_path, full_text, created_at
        FROM transcripts ORDER BY id
    """).fetchall()
    conn.close()
    return [{"id": r[0], "session_id": r[1], "archive_path": r[2] or "",
             "full_text": r[3] or "", "created_at": r[4]} for r in rows]


def export_compare():
    """按日期聚合横向对比数据。"""
    conn = get_conn()
    date_rows = conn.execute("""
        SELECT DISTINCT date(recorded_at) AS day
        FROM sessions WHERE status = 'analyzed'
        ORDER BY day DESC LIMIT 30
    """).fetchall()
    dates = [r[0] for r in date_rows]
    conn.close()

    latest = dates[0] if dates else None
    competitors_on_day = []

    if latest:
        conn = get_conn()
        day_sessions = conn.execute("""
            SELECT s.id, c.name, s.recorded_at,
                   a.key_products, a.user_persona, a.strategy_summary, a.highlights
            FROM sessions s
            LEFT JOIN competitors c ON c.id = s.competitor_id
            LEFT JOIN analysis a ON a.session_id = s.id
            WHERE date(s.recorded_at) = ? AND s.status = 'analyzed'
            ORDER BY s.recorded_at
        """, (latest,)).fetchall()
        conn.close()

        for r in day_sessions:
            kps = _parse_json_field(r[3])
            highlights = _parse_json_field(r[6])
            competitors_on_day.append({
                "session_id": r[0], "competitor": r[1] or "未知",
                "recorded_at": r[2],
                "key_products": kps, "user_persona": r[4] or "",
                "strategy_summary": r[5] or "", "highlights": highlights,
            })

    return {"dates": dates, "latest_day": latest, "competitors_on_day": competitors_on_day}


def export_trend():
    """
    竞品趋势数据：每个竞品每天的分析摘要，
    用于跨天对比发现动态变化。
    """
    conn = get_conn()
    rows = conn.execute("""
        SELECT
            date(s.recorded_at) AS day,
            c.name AS competitor,
            s.id AS session_id,
            a.key_products,
            a.highlights,
            a.strategy_summary
        FROM sessions s
        LEFT JOIN competitors c ON c.id = s.competitor_id
        LEFT JOIN analysis a ON a.session_id = s.id
        WHERE s.status = 'analyzed'
        ORDER BY day DESC, competitor
    """).fetchall()
    conn.close()

    # 按 [day][competitor] 聚合（每天每竞品可能多场，全部保留）
    seen = {}
    for r in rows:
        day, competitor, session_id = r[0], r[1], r[2]
        key = (day, competitor)
        kps = _parse_json_field(r[3])
        highlights = _parse_json_field(r[4])
        item = {
            "day": day, "competitor": competitor, "session_id": session_id,
            "products": [p["name"] for p in kps if isinstance(p, dict)],
            "highlights": highlights,
            "strategy": r[5] or "",
        }
        if key not in seen:
            seen[key] = []
        seen[key].append(item)

    # 按竞品聚合
    competitors = sorted(set(v[0]["competitor"] for v in seen.values()))
    days = sorted(set(v[0]["day"] for v in seen.values()), reverse=True)

    # trend[day][comp] = [item1, item2, ...]（每天每竞品可能多条）
    trend_by_day = {}
    for d in days:
        trend_by_day[d] = {}
        for comp in competitors:
            key = (d, comp)
            trend_by_day[d][comp] = seen.get(key, [])

    return {"days": days, "competitors": competitors, "trend": trend_by_day}


def export_market_overview():
    """
    市场大盘横向对比：汇总所有竞品在所有日期的用户画像、产品方向、话术策略，
    输出给"竞争格局"页签使用。
    """
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT
                c.name AS competitor,
                COUNT(DISTINCT s.id) AS session_count,
                a.user_persona,
                a.key_products,
                a.strategy_summary,
                a.highlights
            FROM sessions s
            LEFT JOIN competitors c ON c.id = s.competitor_id
            LEFT JOIN analysis a ON a.session_id = s.id
            WHERE s.status = 'analyzed'
            GROUP BY c.name
            ORDER BY session_count DESC
        """).fetchall()

        # 建立 competitor_name -> id 映射
        name_to_id = {}
        for (cid, name) in conn.execute("SELECT id, name FROM competitors").fetchall():
            name_to_id[name] = cid

        # 获取每个竞品的所有日期
        dates_map = {}
        for (cid, day) in conn.execute("""
            SELECT competitor_id, date(recorded_at)
            FROM sessions WHERE status = 'analyzed'
            GROUP BY competitor_id, date(recorded_at)
            ORDER BY date(recorded_at) DESC
        """).fetchall():
            dates_map.setdefault(cid, []).append(day)

        result = []
        for r in rows:
            kps = _parse_json_field(r[3])
            highlights = _parse_json_field(r[5])
            cid = name_to_id.get(r[0])
            dates_list = sorted(dates_map.get(cid, []), reverse=True)
            result.append({
                "competitor": r[0] or "未知",
                "dates": dates_list,
                "session_count": r[1],
                "user_persona": r[2] or "",
                "key_products_summary": [p["name"] for p in kps if isinstance(p, dict)],
                "strategy_summary": r[4] or "",
                "highlights": highlights,
            })
        return result
    finally:
        conn.close()


# ── 静态页面 ──────────────────────────────────────────────────────────────────

def build_index():
    # 直接读取看板 HTML，然后用 GITHUB_TOKEN 注入替换占位符
    index_path = BOARD_HTML_SRC
    html = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    # GITHUB_TOKEN 通过环境变量传入（workflow 的 github.token，CI 运行时有值，本地为空）
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        html = html.replace("<!-- GITHUB_TOKEN_PLACEHOLDER -->",
                            f'const GITHUB_TOKEN = "{token}";')
        print(f"  Token injected (len={len(token)})")
    return html or """<!DOCTYPE html><html><body style="font-family:sans-serif;padding:40px;text-align:center">
<h2>看板文件缺失</h2><p>请确保 docs/index.html 存在。</p></body></html>"""




def main():
    print("[generate_static] 开始生成静态文件…")

    competitors = export_competitors()
    # 合并：UI编辑保存的 brand/stage 优先级高于 DB（DB不存这两个字段）
    committed = {}
    committed_path = OUTPUT_DIR / "competitors.json"
    if committed_path.exists():
        try:
            committed_list = json.loads(committed_path.read_text(encoding="utf-8"))
            committed = {c["id"]: c for c in committed_list if "id" in c}
        except Exception:
            pass
    # DB字段为主，补充 brand/stage（来自UI编辑）
    for c in competitors:
        cid = c["id"]
        if cid in committed:
            c["brand"] = committed[cid].get("brand") or c.get("brand") or ""
            c["stage"] = committed[cid].get("stage") or c.get("stage") or ""
            # monitoring 字段优先用 committed 的值，默认为 True
            c["monitoring"] = committed[cid].get("monitoring", True) if committed[cid].get("monitoring") is not None else True
        else:
            c["monitoring"] = True

    sessions = export_sessions()
    analyses = export_analyses()
    transcripts = export_transcripts()
    compare = export_compare()
    trend = export_trend()
    market = export_market_overview()

    def write_json(path, data):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✓ {path.relative_to(_ROOT)}")

    write_json(OUTPUT_DIR / "competitors.json", competitors)
    write_json(OUTPUT_DIR / "sessions.json", sessions)
    write_json(OUTPUT_DIR / "analyses.json", analyses)
    write_json(OUTPUT_DIR / "transcripts.json", transcripts)
    write_json(OUTPUT_DIR / "compare.json", compare)
    write_json(OUTPUT_DIR / "trend.json", trend)
    write_json(OUTPUT_DIR / "market.json", market)

    index_html = build_index()
    index_path = BOARD_HTML_DST
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(index_html, encoding="utf-8")
    print(f"  ✓ {index_path.relative_to(_ROOT)}")

    print(f"[generate_static] 完成！输出目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
