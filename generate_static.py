#!/usr/bin/env python3
"""
静态数据生成脚本：从 data.db 导出完整 JSON，供 GitHub Pages 直接读取（无后端）。
输出到 docs/data/ 目录。
"""
import json
import sys
from pathlib import Path
from datetime import datetime

_ROOT = Path(__file__).parent
DB_PATH = _ROOT / "storage" / "data.db"
OUTPUT_DIR = _ROOT / "docs" / "data"
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
    rows = conn.execute(
        "SELECT id, name, douyin_id, url, created_at FROM competitors ORDER BY id"
    ).fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "douyin_id": r[2] or "", "url": r[3] or "", "created_at": r[4]} for r in rows]


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
    html = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>竞品直播间监控看板</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --blue: #1677ff; --blue-dark: #0958d9;
  --green: #52c41a; --orange: #fa8c16; --red: #f5222d;
  --bg: #f0f2f5; --card-bg: #fff;
  --border: #e8e8e8; --text: #1a1a1a;
  --text-2: #666; --text-3: #999;
}
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }
a { color: var(--blue); text-decoration: none; }
a:hover { color: var(--blue-dark); }

/* Header */
header { background: var(--card-bg); border-bottom: 1px solid var(--border); padding: 14px 24px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
header h1 { font-size: 18px; font-weight: 700; }
.header-right { margin-left: auto; display: flex; align-items: center; gap: 10px; }
.container { max-width: 1440px; margin: 0 auto; padding: 20px 24px; }

/* Stats */
.stats-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; margin-bottom: 20px; }
.stat-card { background: var(--card-bg); border-radius: 10px; padding: 16px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
.stat-num { font-size: 26px; font-weight: 700; color: var(--blue); }
.stat-num.g { color: var(--green); }
.stat-num.o { color: var(--orange); }
.stat-label { font-size: 12px; color: var(--text-2); margin-top: 4px; }

/* Tabs */
.tabs { display: flex; gap: 4px; margin-bottom: 20px; flex-wrap: wrap; }
.tab { padding: 8px 18px; border-radius: 6px; cursor: pointer; font-size: 14px; color: var(--text-2); border: 1px solid var(--border); background: var(--card-bg); transition: all .15s; white-space: nowrap; }
.tab:hover { border-color: var(--blue); color: var(--blue); }
.tab.active { background: var(--blue); color: #fff; border-color: var(--blue); }

/* Filters */
.filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; background: var(--card-bg); padding: 12px 16px; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
.filters label { font-size: 13px; color: var(--text-2); font-weight: 500; white-space: nowrap; }
.filters select, .filters input { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; outline: none; }
.filters select:focus, .filters input:focus { border-color: var(--blue); box-shadow: 0 0 0 2px rgba(22,119,255,.12); }
.filters input[type="search"] { min-width: 160px; }
.export-btns { margin-left: auto; display: flex; gap: 8px; flex-wrap: wrap; }

/* Buttons */
.btn { display: inline-flex; align-items: center; gap: 4px; padding: 6px 14px; border: 1px solid var(--border); border-radius: 6px; background: var(--card-bg); cursor: pointer; font-size: 13px; color: var(--text); transition: all .15s; white-space: nowrap; }
.btn:hover { border-color: var(--blue); color: var(--blue); }
.btn.pri { background: var(--blue); color: #fff; border-color: var(--blue); }
.btn.pri:hover { background: var(--blue-dark); }
.btn-sm { padding: 3px 10px; font-size: 12px; }

/* Cards */
.card { background: var(--card-bg); border-radius: 10px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.06); margin-bottom: 16px; }
.card-title { font-size: 15px; font-weight: 600; margin-bottom: 14px; color: #333; }

/* Compare grid */
.compare-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; }
.competitor-card { background: var(--card-bg); border-radius: 10px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.06); border-top: 3px solid var(--blue); transition: box-shadow .15s; }
.competitor-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,.1); }
.competitor-name { font-size: 16px; font-weight: 700; margin-bottom: 2px; }
.competitor-meta { font-size: 11px; color: var(--text-3); margin-bottom: 12px; }
.section-label { font-size: 11px; color: var(--text-3); text-transform: uppercase; letter-spacing: .6px; margin: 14px 0 6px; border-bottom: 1px solid var(--border); padding-bottom: 4px; }

/* Product */
.product-item { border: 1px solid #eee; border-radius: 6px; padding: 10px 12px; margin-bottom: 8px; }
.product-name { font-weight: 600; font-size: 14px; }
.product-price { color: var(--red); font-size: 13px; font-weight: 600; margin: 3px 0; }
.product-meta { display: flex; flex-wrap: wrap; gap: 4px; margin: 4px 0; }
.badge { display: inline-block; background: #f0f5ff; color: var(--blue); border-radius: 4px; padding: 1px 7px; font-size: 12px; }
.badge.g { background: #f6ffed; color: var(--green); }
.badge.o { background: #fff7e6; color: var(--orange); }
.badge.r { background: #fff1f0; color: var(--red); }
.product-points { font-size: 12px; color: #666; line-height: 1.6; margin-top: 4px; }

/* Highlights */
.highlight-item { font-size: 13px; color: var(--text-2); padding: 5px 0; line-height: 1.5; border-bottom: 1px dashed #f0f0f0; }
.highlight-item:last-child { border-bottom: none; }
.persona-text, .strategy-text { font-size: 13px; color: #444; line-height: 1.7; }
.strategy-text { background: #fafafa; padding: 10px 12px; border-radius: 6px; }

/* Sessions table */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { text-align: left; padding: 10px 12px; background: #fafafa; border-bottom: 2px solid #f0f0f0; color: var(--text-2); font-weight: 500; font-size: 13px; white-space: nowrap; }
td { padding: 10px 12px; border-bottom: 1px solid #f5f5f5; vertical-align: top; }
tr:hover td { background: #fafafa; }
.badge-status { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: 500; }
.s-analyzed { background: #f6ffed; color: var(--green); }
.s-transcribed { background: #e6f7ff; color: var(--blue); }
.s-recorded { background: #fff7e6; color: var(--orange); }

/* Detail panel */
.detail-panel { display: none; background: var(--card-bg); border-radius: 10px; box-shadow: 0 4px 20px rgba(0,0,0,.1); padding: 24px; margin-bottom: 16px; animation: slideDown .2s ease; }
.detail-panel.open { display: block; }
@keyframes slideDown { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: translateY(0); } }
.detail-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; flex-wrap: wrap; gap: 10px; }
.detail-title { font-size: 18px; font-weight: 700; }
.detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
@media (max-width: 800px) { .detail-grid { grid-template-columns: 1fr; } }

/* Scripts */
.script-item { background: #fafafa; border-radius: 6px; padding: 10px 12px; margin-bottom: 8px; border-left: 3px solid var(--blue); }
.script-type { font-size: 11px; color: var(--blue); font-weight: 600; margin-bottom: 4px; }
.script-content { font-size: 13px; color: #333; line-height: 1.6; }
.script-ts { font-size: 11px; color: var(--text-3); margin-top: 4px; }

/* Transcript */
.transcript-box { background: #fafafa; border-radius: 8px; padding: 16px; font-size: 13px; line-height: 1.8; color: #444; max-height: 500px; overflow-y: auto; white-space: pre-wrap; font-family: ui-monospace, monospace; }

/* Scripts archive table */
.archive-link { color: var(--blue); font-size: 13px; word-break: break-all; }
.archive-link:hover { text-decoration: underline; }

/* Trend table */
.trend-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.trend-table th { background: #fafafa; padding: 10px 12px; text-align: left; border-bottom: 2px solid #f0f0f0; color: var(--text-2); font-weight: 500; white-space: nowrap; }
.trend-table td { padding: 10px 12px; border-bottom: 1px solid #f5f5f5; vertical-align: top; }
.trend-table tr:hover td { background: #fafafa; }
.change-new { background: #f6ffed; color: var(--green); border-radius: 4px; padding: 1px 6px; font-size: 12px; font-weight: 600; }
.change-gone { background: #fff1f0; color: var(--red); border-radius: 4px; padding: 1px 6px; font-size: 12px; font-weight: 600; }

/* Market overview */
.overview-card { background: var(--card-bg); border-radius: 10px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.06); border-top: 3px solid var(--blue); margin-bottom: 16px; }
.overview-comp { font-size: 16px; font-weight: 700; margin-bottom: 6px; }
.overview-meta { font-size: 12px; color: var(--text-3); margin-bottom: 12px; }

/* Empty / Loading */
.empty, .loading { text-align: center; padding: 50px 20px; color: var(--text-3); font-size: 14px; }
.err-panel { background: #fff1f0; border: 1px solid #ffccc7; border-radius: 8px; padding: 16px; margin: 8px 0; font-size: 13px; color: var(--red); }

/* Footer */
footer { text-align: center; padding: 24px; font-size: 12px; color: var(--text-3); }

/* Responsive */
@media (max-width: 640px) {
  .container { padding: 12px; }
  .compare-grid { grid-template-columns: 1fr; }
  .header-right { margin-left: 0; width: 100%; justify-content: flex-end; }
}
</style>
</head>
<body>

<header>
  <h1>竞品直播间监控看板</h1>
  <div class="header-right">
    <span id="lastUpdate" style="font-size:12px;color:var(--text-3)"></span>
    <button class="btn" onclick="location.reload()">刷新</button>
  </div>
</header>

<div class="container">

  <!-- 统计概览 -->
  <div class="stats-bar" id="statsBar">
    <div class="stat-card"><div class="stat-num" id="sComp">-</div><div class="stat-label">竞品账号</div></div>
    <div class="stat-card"><div class="stat-num g" id="sSess">-</div><div class="stat-label">采集场次</div></div>
    <div class="stat-card"><div class="stat-num" id="sAnalyzed">-</div><div class="stat-label">已分析</div></div>
    <div class="stat-card"><div class="stat-num o" id="sDays">-</div><div class="stat-label">覆盖天数</div></div>
  </div>

  <!-- 标签页 -->
  <div class="tabs">
    <div class="tab active" id="tab-btn-compare" onclick="switchTab('compare')">首页·今日概览</div>
    <div class="tab" id="tab-btn-sessions" onclick="switchTab('sessions')">话术文本库</div>
    <div class="tab" id="tab-btn-trend" onclick="switchTab('trend')">竞品动态</div>
    <div class="tab" id="tab-btn-market" onclick="switchTab('market')">竞争格局</div>
    <div class="tab" id="tab-btn-about" onclick="switchTab('about')">说明</div>
  </div>

  <!-- ── 首页·今日概览 ─────────────────────────────────────── -->
  <div id="tab-compare">
    <div class="filters">
      <label>日期</label>
      <select id="fDate" onchange="renderCompare()"><option value="">全部日期</option></select>
      <label>竞品</label>
      <select id="fComp" onchange="renderCompare()"><option value="">全部</option></select>
      <label>🔍</label>
      <input type="search" id="fSearch" placeholder="搜索…" oninput="renderCompare()">
      <div class="export-btns">
        <button class="btn" onclick="exportJSON()">📥 JSON</button>
        <button class="btn" onclick="exportCSV()">📥 CSV</button>
      </div>
    </div>
    <div id="compareContent"><div class="loading">加载中…</div></div>
  </div>

  <!-- ── 话术文本库 ───────────────────────────────────────── -->
  <div id="tab-sessions" style="display:none">
    <div class="filters">
      <label>账号</label>
      <select id="fSessComp" onchange="renderSessions()"><option value="">全部</option></select>
      <label>日期</label>
      <select id="fSessDate" onchange="renderSessions()"><option value="">全部</option></select>
      <label>状态</label>
      <select id="fSessStatus" onchange="renderSessions()">
        <option value="">全部</option>
        <option value="analyzed">已分析</option>
        <option value="transcribed">已转写</option>
        <option value="recorded">已录制</option>
      </select>
      <label>🔍</label>
      <input type="search" id="fSessSearch" placeholder="搜索账号…" oninput="renderSessions()">
    </div>
    <div id="sessionsContent"><div class="loading">加载中…</div></div>
    <div id="detailPanel" class="detail-panel"></div>
  </div>

  <!-- ── 竞品动态 ────────────────────────────────────────── -->
  <div id="tab-trend" style="display:none">
    <div class="filters">
      <label>竞品</label>
      <select id="fTrendComp" onchange="renderTrend()"><option value="">全部</option></select>
      <label>日期范围</label>
      <select id="fTrendRange" onchange="renderTrend()">
        <option value="all">全部日期</option>
        <option value="7">最近7天</option>
        <option value="30">最近30天</option>
      </select>
    </div>
    <div id="trendContent"><div class="loading">加载中…</div></div>
  </div>

  <!-- ── 竞争格局 ────────────────────────────────────────── -->
  <div id="tab-market" style="display:none">
    <div id="marketContent"><div class="loading">加载中…</div></div>
  </div>

  <!-- ── 说明 ─────────────────────────────────────────────── -->
  <div id="tab-about" style="display:none">
    <div class="card">
      <div class="card-title">数据说明</div>
      <p style="font-size:13px;color:#666;line-height:1.8;margin-bottom:16px">
        本看板展示竞品抖音直播间的每日监控数据，由 GitHub Actions 每天北京时间 11:00 自动采集更新。
      </p>
      <div class="card-title" style="margin-top:20px">字段说明</div>
      <ul style="font-size:13px;color:#666;line-height:2.2;padding-left:20px">
        <li><b>主推品</b>：直播间重点推广的课程产品，含价格、上课时间、课程节数、赠品等</li>
        <li><b>话术策略</b>：主播的核心转化逻辑和痛点设计</li>
        <li><b>用户画像</b>：目标家长群体的特征描述</li>
        <li><b>Highlights</b>：值得关注的差异化亮点</li>
        <li><b>竞品动态</b>：对比前后场次，发现产品方向和话术策略的变化</li>
        <li><b>竞争格局</b>：各竞品整体策略对比和用户画像差异</li>
      </ul>
    </div>
  </div>
</div>

<footer>
  数据来源：抖音直播间自动录制转写 · <a href="https://github.com/wangziyan617-sudo/live-monitor-" target="_blank">GitHub</a>
</footer>

<script>
// ─── 全局数据 ─────────────────────────────────────────────────────────────────
let ALL = {
  competitors: [], sessions: [], analyses: [], transcripts: [],
  compare: { dates: [], latest_day: "", competitors_on_day: [] },
  trend: { days: [], competitors: [], trend: {} },
  market: [],
};
let currentTab = "compare";

const STATUS_LABEL = { recorded: "已录制", transcribed: "已转写", analyzed: "已分析" };
const STATUS_CLASS = { recorded: "s-recorded", transcribed: "s-transcribed", analyzed: "s-analyzed" };

// ─── 初始化 ───────────────────────────────────────────────────────────────────
async function init() {
  let errMsg = "";
  try {
    const [comp, sess, analy, trans, compData, trend, market] = await Promise.all([
      fetch("data/competitors.json").then(r => { if (!r.ok) throw new Error("competitors.json 加载失败"); return r.json(); }),
      fetch("data/sessions.json").then(r => { if (!r.ok) throw new Error("sessions.json 加载失败"); return r.json(); }),
      fetch("data/analyses.json").then(r => { if (!r.ok) throw new Error("analyses.json 加载失败"); return r.json(); }),
      fetch("data/transcripts.json").then(r => { if (!r.ok) throw new Error("transcripts.json 加载失败"); return r.json(); }),
      fetch("data/compare.json").then(r => { if (!r.ok) throw new Error("compare.json 加载失败"); return r.json(); }),
      fetch("data/trend.json").then(r => { if (!r.ok) throw new Error("trend.json 加载失败"); return r.json(); }),
      fetch("data/market.json").then(r => { if (!r.ok) throw new Error("market.json 加载失败"); return r.json(); }),
    ]);
    ALL = { competitors: comp, sessions: sess, analyses: analy, transcripts: trans,
            compare: compData, trend, market };
  } catch (e) {
    errMsg = "数据加载失败: " + e.message;
    document.querySelectorAll("[id$='Content'], #compareContent, #sessionsContent, #trendContent, #marketContent")
      .forEach(el => { el.innerHTML = '<div class="err-panel">' + escHtml(errMsg) + '</div>'; });
    console.error(e);
    return;
  }

  // 统计
  const analyzed = ALL.sessions.filter(s => s.status === "analyzed").length;
  const days = new Set(ALL.sessions.map(s => s.recorded_at.slice(0, 10))).size;
  document.getElementById("sComp").textContent = ALL.competitors.length;
  document.getElementById("sSess").textContent = ALL.sessions.length;
  document.getElementById("sAnalyzed").textContent = analyzed;
  document.getElementById("sDays").textContent = days;
  document.getElementById("lastUpdate").textContent = "数据更新: " + new Date().toLocaleString("zh-CN");

  // 日期选择器（首页）
  const dates = ALL.compare.dates;
  const fDate = document.getElementById("fDate");
  dates.forEach(d => {
    const o = document.createElement("option"); o.value = d; o.textContent = d;
    if (d === ALL.compare.latest_day) o.selected = true;
    fDate.appendChild(o);
  });

  // 竞品选择器（首页）
  ALL.competitors.forEach(c => {
    ["fComp", "fSessComp", "fTrendComp"].forEach(id => {
      const sel = document.getElementById(id);
      if (!sel) return;
      if (![...sel.options].find(o => o.value === String(c.id))) {
        const o = document.createElement("option"); o.value = c.id; o.textContent = c.name;
        sel.appendChild(o);
      }
    });
  });

  // 话术库日期选择器
  const allDates = [...new Set(ALL.sessions.map(s => s.recorded_at.slice(0, 10)))].sort().reverse();
  const fSessDate = document.getElementById("fSessDate");
  allDates.forEach(d => {
    const o = document.createElement("option"); o.value = d; o.textContent = d; fSessDate.appendChild(o);
  });

  renderCompare();
}

// ─── 标签切换 ─────────────────────────────────────────────────────────────────
function switchTab(tab) {
  currentTab = tab;
  const tabs = ["compare", "sessions", "trend", "market", "about"];
  tabs.forEach(t => {
    const btn = document.getElementById("tab-btn-" + t);
    if (btn) btn.classList.toggle("active", t === tab);
    const content = document.getElementById("tab-" + t);
    if (content) content.style.display = t === tab ? "" : "none";
  });
  if (tab === "sessions") renderSessions();
  if (tab === "trend") renderTrend();
  if (tab === "market") renderMarket();
}

// ─── 首页·今日概览 ─────────────────────────────────────────────────────────────
function renderCompare() {
  const el = document.getElementById("compareContent");
  const date = document.getElementById("fDate").value;
  const compId = parseInt(document.getElementById("fComp").value) || 0;
  const kw = document.getElementById("fSearch").value.trim().toLowerCase();

  let sessions = ALL.sessions.filter(s => s.status === "analyzed");
  if (date) sessions = sessions.filter(s => s.recorded_at.startsWith(date));
  if (compId) sessions = sessions.filter(s => s.competitor_id === compId);
  if (kw) sessions = sessions.filter(s =>
    s.competitor.includes(kw) ||
    (ALL.analyses.find(a => a.session_id === s.id)?.key_products.some(p => p.name?.includes(kw)))
  );

  if (!sessions.length) { el.innerHTML = '<div class="empty">暂无匹配数据</div>'; return; }

  el.innerHTML = `<div class="compare-grid">${sessions.map(s => buildCard(s)).join("")}</div>`;
}

function buildCard(s) {
  const a = ALL.analyses.find(a => a.session_id === s.id);
  if (!a) return "";
  const time = fmtTime(s.recorded_at);
  const products = renderProducts(a.key_products);

  // 话术类型统计
  const groups = groupScriptsByType(a.sales_scripts || []);
  const scriptSummary = groups.map(function(g) {
    var label = SCRIPT_TYPE_LABELS[g.type] || g.type;
    return '<span class="badge" style="margin:2px;cursor:pointer" onclick="openDetailAndShowScripts(' + s.id + ')">' + escHtml(label) + '×' + g.scripts.length + '</span>';
  }).join("") || '<span style="color:var(--text-3);font-size:12px">暂无</span>';

  // 转写预览（前120字）
  var t = ALL.transcripts.find(function(x) { return x.session_id === s.id; });
  var transcriptPreview = "";
  if (t && t.full_text) {
    var firstLine = t.full_text.split("\n").filter(function(l) { return l.trim(); })[0] || "";
    var previewText = t.full_text.replace(/^\[[^\]]+\]\s*/gm, "").replace(/\n/g, " ").slice(0, 100);
    transcriptPreview = '<div style="margin-top:10px;font-size:12px;color:var(--text-3);font-style:italic;line-height:1.5">' + escHtml(previewText) + (t.full_text.length > 100 ? "…" : "") + '</div>';
  }

  const highlights = a.highlights.map(h => '<div class="highlight-item">• ' + escHtml(h) + '</div>').join("") ||
    '<div style="color:var(--text-3);font-size:13px">暂无</div>';
  return '<div class="competitor-card">' +
    '<div class="competitor-name">' + escHtml(s.competitor) + '</div>' +
    '<div class="competitor-meta">' + time + ' · ' + (s.duration ? Math.round(s.duration/60) + '分钟' : '—') + '</div>' +
    '<div class="section-label">主推品</div>' + products +
    '<div class="section-label">话术片段（按类型）</div>' +
    '<div style="margin-bottom:4px">' + scriptSummary + '</div>' +
    transcriptPreview +
    '<div class="section-label">用户画像</div><div class="persona-text">' + escHtml(a.user_persona || "暂无") + '</div>' +
    '<div class="section-label">话术策略</div><div class="strategy-text">' + escHtml(a.strategy_summary || "暂无") + '</div>' +
    '<div class="section-label">Highlights</div>' + highlights +
    '<div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">' +
    '<button class="btn btn-sm" onclick="openDetail(' + s.id + ')">查看详情 ▸</button>' +
    '<button class="btn btn-sm" onclick="openDetail(' + s.id + ');showDetailTab(\'transcript\')">完整转写</button>' +
    '</div>' +
    '</div>';
}

function renderProducts(kps) {
  if (!kps || !kps.length) return '<div style="color:var(--text-3);font-size:13px">暂无</div>';
  return kps.map(p => {
    const meta = [];
    if (p.price) meta.push(`<span class="badge r">¥${escHtml(p.price)}</span>`);
    const sps = p.selling_points || [];
    // 提取课程节数、上课时间、赠品
    const match = t => sps.some(s => s.includes(t));
    if (match("节")) {
      const m = sps.find(s => /\d+节/.test(s)); if (m) meta.push(`<span class="badge g">${escHtml(m)}</span>`);
    }
    if (match("周") || match("课时") || match("分钟") || match("小时")) {
      const m = sps.find(s => /\d+[\u4e00-\u9fa5]*([周课分钟时天])/.test(s)); if (m) meta.push(`<span class="badge">${escHtml(m)}</span>`);
    }
    if (match("赠") || match("礼品") || match("资料")) {
      const m = sps.find(s => s.includes("赠") || s.includes("礼品") || s.includes("资料")); if (m) meta.push(`<span class="badge o">${escHtml(m)}</span>`);
    }
    return `
      <div class="product-item">
        <div class="product-name">${escHtml(p.name || "")}</div>
        ${meta.length ? `<div class="product-meta">${meta.join("")}</div>` : ""}
        ${sps.length ? `<div class="product-points">${sps.map(sp => escHtml(sp)).join(" · ")}</div>` : ""}
      </div>
    `;
  }).join("");
}

// ─── 话术文本库 ────────────────────────────────────────────────────────────────
function renderSessions() {
  const el = document.getElementById("sessionsContent");
  const compId = parseInt(document.getElementById("fSessComp").value) || 0;
  const date = document.getElementById("fSessDate").value;
  const status = document.getElementById("fSessStatus").value;
  const kw = document.getElementById("fSessSearch").value.trim().toLowerCase();

  let rows = ALL.sessions.slice();
  if (compId) rows = rows.filter(function(s) { return s.competitor_id === compId; });
  if (date) rows = rows.filter(function(s) { return s.recorded_at.startsWith(date); });
  if (status) rows = rows.filter(function(s) { return s.status === status; });
  if (kw) rows = rows.filter(function(s) { return s.competitor.toLowerCase().indexOf(kw) !== -1; });

  if (!rows.length) { el.innerHTML = '<div class="empty">暂无记录</div>'; return; }

  const today = new Date().toISOString().slice(0, 10);
  var tbodyHtml = '';
  rows.forEach(function(s) {
    var t = ALL.transcripts.find(function(x) { return x.session_id === s.id; });
    var textPreview = '<span style="color:var(--text-3);font-size:12px">—</span>';
    if (t && t.full_text) {
      var cleanText = t.full_text.replace(/^\[[^\]]+\]\s*/gm, "").replace(/\n+/g, " ").trim();
      var preview = cleanText.slice(0, 100);
      textPreview = '<span style="font-size:12px;color:#666;cursor:pointer" onclick="openDetail(' + s.id + ');showDetailTab(\'transcript\')">' + escHtml(preview) + (cleanText.length > 100 ? "…" : "") + '</span>';
    }
    var day = s.recorded_at.slice(0, 10);
    var isToday = day === today;
    tbodyHtml += '<tr>' +
      '<td><b>' + escHtml(s.competitor) + '</b></td>' +
      '<td>' + (isToday ? "<b>" : "") + day + (isToday ? " 📺" : "") + '</td>' +
      '<td>' + s.recorded_at.slice(11, 16) + '</td>' +
      '<td>' + (s.duration ? Math.round(s.duration / 60) + "分钟" : "—") + '</td>' +
      '<td><span class="badge-status ' + (STATUS_CLASS[s.status] || "") + '">' + (STATUS_LABEL[s.status] || s.status) + '</span></td>' +
      '<td style="max-width:280px">' + textPreview + '</td>' +
      '<td>' + (s.status === "analyzed" ? '<button class="btn btn-sm pri" onclick="openDetail(' + s.id + ')">分析详情</button>' : "—") + '</td>' +
      '</tr>';
  });

  el.innerHTML = '<div class="card">' +
    '<div class="card-title">共 ' + rows.length + ' 条记录（点击文本预览可打开完整转写）</div>' +
    '<div class="table-wrap"><table>' +
    '<thead><tr><th>账号</th><th>日期</th><th>时间</th><th>时长</th><th>状态</th><th>转写预览</th><th>操作</th></tr></thead>' +
    '<tbody>' + tbodyHtml + '</tbody></table></div></div>';
}

// ─── 竞品动态 ─────────────────────────────────────────────────────────────────
function renderTrend() {
  const el = document.getElementById("trendContent");
  const compId = parseInt(document.getElementById("fTrendComp").value) || 0;
  const range = document.getElementById("fTrendRange").value;

  const { days, trend } = ALL.trend;
  const allCompNames = days.length ? Object.keys(trend[days[0]] || {}) : [];
  const competitors = compId
    ? ALL.competitors.filter(c => c.id === compId).map(c => c.name)
    : allCompNames;

  let filteredDays = days;
  if (range !== "all") {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - parseInt(range));
    filteredDays = days.filter(d => d >= cutoff.toISOString().slice(0, 10));
  }

  if (!filteredDays.length || !competitors.length) {
    el.innerHTML = '<div class="empty">暂无动态数据<br><br><span style="color:var(--text-3)">积累多天数据后将自动展示各竞品推品和话术变化趋势。</span></div>'; return;
  }

  let html = `<div class="card" style="margin-bottom:12px"><div class="table-wrap"><table class="trend-table">
    <thead><tr><th>时间</th><th>竞品</th><th>场次</th><th>主推品</th><th>关注亮点</th></tr></thead><tbody>`;

  for (const day of filteredDays) {
    const dayData = trend[day] || {};
    for (const comp of competitors) {
      const items = dayData[comp] || [];
      if (!items.length) continue;

      items.forEach(function(item, idx) {
        const s = ALL.sessions.find(function(x) { return x.id === item.session_id; });
        const time = s ? s.recorded_at.slice(11, 16) : "";
        const products = item.products || [];
        const highlights = item.highlights || [];
        const notable = highlights.filter(function(h) { return /首次|明确|新|强|突|变化|强调|提出|对比|差异/.test(h); });
        html += "<tr>" +
          "<td style='white-space:nowrap;color:var(--text-3);font-size:12px'>" + day + " " + time + "</td>" +
          "<td><b>" + escHtml(comp) + "</b></td>" +
          "<td style='color:var(--text-3);font-size:12px'>" + (items.length > 1 ? "第" + (idx+1) + "场" : "—") + "</td>" +
          "<td style='font-size:13px'>" + (products.length ? products.map(function(p) { return "<div>" + escHtml(p) + "</div>"; }).join("") : "<span style='color:var(--text-3)'>暂无</span>") + "</td>" +
          "<td>" + (notable.length ? notable.map(function(c) { return "<span class='change-new' style='display:block;margin-bottom:3px'>• " + escHtml(c) + "</span>"; }).join("") : "<span style='color:var(--text-3);font-size:12px'>—</span>") + "</td>" +
          "</tr>";
      });

      // 同日多场：追加变化分析行
      if (items.length > 1) {
        const allHighlights = items.flatMap(function(i) { return i.highlights || []; });
        const newProducts = allHighlights.filter(function(h) { return /首次|明确|新|提出|强调|突出|变化/.test(h); });
        html += "<tr style='background:#fafafa'>" +
          "<td colspan='2' style='font-size:12px;color:var(--text-3);font-style:italic'>同日变化分析</td>" +
          "<td colspan='3' style='font-size:12px;color:var(--text-2)'>" +
          (newProducts.length ? newProducts.map(function(c) { return "<span class='change-new' style='margin-right:4px'>变化: " + escHtml(c) + "</span>"; }).join("") : "<span style='color:var(--text-3)'>多场主推品无明显差异</span>") +
          "</td></tr>";
      }
    }
  }

  html += "</tbody></table></div></div>";
  el.innerHTML = html;
}

// ─── 竞争格局 ─────────────────────────────────────────────────────────────────
function renderMarket() {
  const el = document.getElementById("marketContent");
  const { market } = ALL;
  if (!market.length) { el.innerHTML = '<div class="empty">暂无市场数据</div>'; return; }

  el.innerHTML = `
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:16px">
      ${market.map(m => `
        <div class="overview-card">
          <div class="overview-comp">${escHtml(m.competitor)}</div>
          <div class="overview-meta">采集 ${m.session_count} 场 · ${m.dates.join(" / ") || "—"}</div>

          <div class="section-label">目标用户画像</div>
          <div class="persona-text" style="margin-bottom:12px">${escHtml(m.user_persona || "暂无")}</div>

          <div class="section-label">主推课程方向</div>
          ${m.key_products_summary.length
            ? m.key_products_summary.map(p => `<span class="badge" style="margin:2px">${escHtml(p)}</span>`).join("")
            : '<div style="color:var(--text-3);font-size:13px">暂无</div>'}
          <div style="margin-top:10px;font-size:13px;color:#666">${escHtml(m.strategy_summary || "暂无策略")}</div>

          <div class="section-label">核心亮点</div>
          ${m.highlights.length
            ? m.highlights.map(h => `<div style="font-size:13px;color:var(--text-2);padding:3px 0;border-bottom:1px dashed #f0f0f0">• ${escHtml(h)}</div>`).join("")
            : '<div style="color:var(--text-3);font-size:13px">暂无</div>'}
        </div>
      `).join("")}
    </div>
  `;
}

// ─── 详情面板 ─────────────────────────────────────────────────────────────────

// 话术类型中文映射和顺序
var SCRIPT_TYPE_ORDER = [
  "暖场破冰", "开场白", "痛点/需求唤醒", "痛点需求唤醒",
  "价值塑造", "产品介绍", "效果承诺",
  "信任背书", "社会认同", "比喻说服",
  "促销逼单", "服务保障", "催单话术",
  "结尾收束", "FAQ答疑",
];
var SCRIPT_TYPE_LABELS = {
  "痛点/需求唤醒": "痛点唤醒", "痛点需求唤醒": "痛点唤醒",
  "暖场破冰": "暖场破冰", "开场白": "开场白",
  "价值塑造": "价值塑造", "产品介绍": "产品介绍",
  "效果承诺": "效果承诺", "信任背书": "信任背书",
  "社会认同": "社会认同", "比喻说服": "比喻说服",
  "促销逼单": "促销逼单", "服务保障": "服务保障",
  "催单话术": "催单话术", "结尾收束": "结尾收束",
  "FAQ答疑": "FAQ答疑",
};

function groupScriptsByType(scripts) {
  var groups = {};
  scripts.forEach(function(sc) {
    var t = sc.type || "其他";
    if (!groups[t]) groups[t] = [];
    groups[t].push(sc);
  });
  var ordered = [];
  SCRIPT_TYPE_ORDER.forEach(function(t) {
    if (groups[t]) { ordered.push({ type: t, scripts: groups[t] }); delete groups[t]; }
  });
  Object.keys(groups).forEach(function(t) { ordered.push({ type: t, scripts: groups[t] }); });
  return ordered;
}

// 按产品分组展示话术（优先用 key_products 里的 scripts）
function renderProductScripts(kps) {
  if (!kps || !kps.length) return "";
  var html = "";
  kps.forEach(function(p, pi) {
    var scripts = p.scripts || [];
    var sps = p.selling_points || [];
    var price = p.price;
    // 判断是否有产品级话术
    var hasScripts = scripts.length > 0;
    html += '<div style="margin-bottom:20px;border:1px solid #eee;border-radius:8px;overflow:hidden">';
    // 产品标题栏
    var headerBg = pi === 0 ? "#1677ff" : pi === 1 ? "#52c41a" : "#fa8c16";
    html += '<div style="background:' + headerBg + ';color:#fff;padding:8px 14px;font-weight:600;font-size:14px">';
    html += escHtml(p.name || "产品" + (pi + 1));
    if (price) html += ' <span style="font-size:13px;opacity:0.9">· ' + escHtml(price) + '</span>';
    if (sps.length) html += ' <span style="font-size:12px;opacity:0.8">· ' + sps.slice(0,3).map(function(s){return escHtml(s);}).join(" · ") + '</span>';
    html += '</div>';
    // 卖点（全部）
    if (sps.length) {
      html += '<div style="padding:8px 14px;background:#fafafa;border-bottom:1px solid #f0f0f0">';
      sps.forEach(function(sp) {
        html += '<span class="badge" style="margin:2px">' + escHtml(sp) + '</span>';
      });
      html += '</div>';
    }
    // 话术片段
    if (hasScripts) {
      var groups = groupScriptsByType(scripts);
      groups.forEach(function(g) {
        var label = SCRIPT_TYPE_LABELS[g.type] || g.type;
        var badgeColor = g.type.indexOf("痛点") !== -1 ? "o" :
                         g.type.indexOf("逼单") !== -1 || g.type.indexOf("催单") !== -1 ? "r" :
                         g.type.indexOf("价值") !== -1 || g.type.indexOf("产品") !== -1 ? "g" : "";
        html += '<div style="padding:8px 14px;border-bottom:1px solid #f5f5f5">';
        html += '<div style="font-size:12px;color:#888;margin-bottom:6px;font-weight:600">' + escHtml(label) + ' ×' + g.scripts.length + '</div>';
        g.scripts.forEach(function(sc) {
          html += '<div style="font-size:13px;color:#333;line-height:1.6;margin-bottom:6px">';
          if (sc.timestamp) html += '<span class="badge" style="margin-right:6px;flex-shrink:0">' + escHtml(sc.timestamp) + '</span>';
          html += escHtml(sc.content || "") + '</div>';
        });
        html += '</div>';
      });
    } else {
      html += '<div style="padding:12px 14px;color:#bbb;font-size:13px">暂无话术片段</div>';
    }
    html += '</div>';
  });
  return html;
}

function renderScriptGroups(scripts) {
  if (!scripts || !scripts.length) return '<div style="color:var(--text-3);font-size:13px">暂无话术片段</div>';
  var groups = groupScriptsByType(scripts);
  var html = '';
  groups.forEach(function(g) {
    var label = SCRIPT_TYPE_LABELS[g.type] || g.type;
    var badgeColor = g.type.indexOf("痛点") !== -1 ? "o" :
                     g.type.indexOf("逼单") !== -1 || g.type.indexOf("催单") !== -1 ? "r" :
                     g.type.indexOf("价值") !== -1 || g.type.indexOf("产品") !== -1 ? "g" : "";
    html += '<div style="margin-bottom:16px">';
    html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid #eee">';
    html += '<span class="badge ' + badgeColor + '" style="font-size:13px;font-weight:600;padding:2px 10px">' + escHtml(label) + '</span>';
    html += '<span style="font-size:12px;color:var(--text-3)">' + g.scripts.length + '条</span>';
    html += '</div>';
    g.scripts.forEach(function(sc) {
      html += '<div class="script-item" style="margin-bottom:6px">';
      if (sc.timestamp) {
        html += '<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:4px">';
        html += '<span class="badge" style="font-size:11px;flex-shrink:0">' + escHtml(sc.timestamp) + '</span>';
        html += '</div>';
      }
      html += '<div class="script-content">' + escHtml(sc.content || "") + '</div>';
      html += '</div>';
    });
    html += '</div>';
  });
  return html;
}

function renderTranscript(text) {
  if (!text) return '<div class="empty">暂无转写文本</div>';
  var tocHtml = "";
  var lines = text.split("\n");
  var entries = [];
  lines.forEach(function(line) {
    var m = line.match(/^\[(\d{2}:\d{2})\s*-->\s*(\d{2}:\d{2})\]\s*(.+)/);
    if (m) {
      entries.push({ start: m[1], end: m[2], content: m[3].trim() });
    }
  });
  if (entries.length === 0) {
    return '<div class="transcript-box">' + escHtml(text) + '</div>';
  }
  entries.forEach(function(e, idx) {
    tocHtml += '<a href="#tseg' + idx + '" style="display:inline-block;background:#f0f5ff;color:var(--blue);border-radius:4px;padding:1px 8px;font-size:12px;margin:2px;text-decoration:none">' + escHtml(e.start) + '</a>';
  });
  var bodyHtml = '<div style="margin-bottom:16px;padding:10px;background:#fafafa;border-radius:6px;font-size:12px;color:var(--text-2)">时间导航：' + tocHtml + '</div>';
  bodyHtml += '<div class="transcript-box">';
  entries.forEach(function(e, idx) {
    bodyHtml += '<div id="tseg' + idx + '" style="margin-bottom:8px;line-height:1.7">';
    bodyHtml += '<span style="color:var(--blue);font-weight:600;flex-shrink:0;margin-right:8px">' + escHtml(e.start) + '</span>';
    bodyHtml += '<span style="color:#333">' + escHtml(e.content) + '</span>';
    bodyHtml += '</div>';
  });
  bodyHtml += '</div>';
  return bodyHtml;
}

function openDetail(sessionId) {
  var panel = document.getElementById("detailPanel");
  var s = ALL.sessions.find(function(x) { return x.id === sessionId; });
  var a = ALL.analyses.find(function(x) { return x.session_id === sessionId; });
  var t = ALL.transcripts.find(function(x) { return x.session_id === sessionId; });
  if (!a) { panel.innerHTML = '<div class="err-panel">暂无分析数据</div>'; panel.classList.add("open"); return; }

  // 判断是否已有按产品分组的话术（新格式）或旧的扁平话术
  var hasProductScripts = (a.key_products || []).some(function(p) { return p.scripts && p.scripts.length; });
  var productScriptsHtml = renderProductScripts(a.key_products || []);

  // 兜底：旧格式话术（按类型分组）
  var fallbackScripts = renderScriptGroups(a.sales_scripts || []);

  var highlights = a.highlights.map(function(h) { return '<div class="highlight-item">• ' + escHtml(h) + '</div>'; }).join("");
  var transcriptHtml = t ? renderTranscript(t.full_text) : '<div class="empty">暂无转写文本</div>';

  // 下载话术：生成结构化文本
  function makeScriptDownload() {
    var kps = a.key_products || [];
    var flat = a.sales_scripts || [];
    var lines = [];
    lines.push("账号: " + (s.competitor || ""));
    lines.push("日期: " + (s.recorded_at || "").slice(0, 10));
    lines.push("时长: " + (s.duration ? Math.round(s.duration/60) + "分钟" : "—"));
    lines.push("═".repeat(50));
    lines.push("");
    if (kps.length) {
      lines.push("【主推品】");
      kps.forEach(function(p, pi) {
        lines.push("  " + (pi+1) + ". " + (p.name||"") + (p.price ? "  ¥" + p.price : ""));
        if (p.selling_points && p.selling_points.length) {
          lines.push("     卖点: " + p.selling_points.join("；"));
        }
        if (p.scripts && p.scripts.length) {
          lines.push("     话术片段:");
          p.scripts.forEach(function(sc) {
            lines.push("       [" + (sc.timestamp||"") + "] " + (sc.type||"") + ": " + (sc.content||""));
          });
        }
      });
      lines.push("");
    }
    if (flat.length) {
      lines.push("【全部话术片段（按类型）】");
      var groups = groupScriptsByType(flat);
      groups.forEach(function(g) {
        lines.push("  ◆ " + g.type + " ×" + g.scripts.length);
        g.scripts.forEach(function(sc) {
          lines.push("    [" + (sc.timestamp||"") + "] " + (sc.content||""));
        });
      });
      lines.push("");
    }
    if (a.user_persona) { lines.push("【用户画像】"); lines.push(a.user_persona); lines.push(""); }
    if (a.strategy_summary) { lines.push("【话术策略】"); lines.push(a.strategy_summary); lines.push(""); }
    if (a.highlights && a.highlights.length) { lines.push("【Highlights】"); a.highlights.forEach(function(h){ lines.push("• " + h); }); }
    return lines.join("\n");
  }

  var downloadTxtContent = makeScriptDownload();
  var downloadBtn = '<button class="btn" onclick="downloadScriptText(' + sessionId + ')">📥 下载话术文本</button>';
  var downloadJsonBtn = '<button class="btn" onclick="downloadScriptJson(' + sessionId + ')">📥 下载JSON</button>';

  panel.innerHTML = '<div class="detail-header">' +
    '<div class="detail-title">' + escHtml(s.competitor) + ' · ' + fmtDate(s.recorded_at) + ' · ' + (s.duration ? Math.round(s.duration/60) + '分钟' : '') + '</div>' +
    '<div style="display:flex;gap:8px">' +
    downloadTxtContent ? downloadBtn : '' +
    downloadBtn +
    downloadJsonBtn +
    '<button class="btn" onclick="closeDetail()">关闭</button>' +
    '</div></div>' +
    '<div style="display:flex;gap:8px;margin-bottom:16px">' +
    '<button class="tab active" id="dt-tab-analysis" onclick="showDetailTab(\'analysis\')">' + (hasProductScripts ? "品牌·产品话术" : "分析详情") + '</button>' +
    '<button class="tab" id="dt-tab-type" onclick="showDetailTab(\'type\')">按类型</button>' +
    '<button class="tab" id="dt-tab-transcript" onclick="showDetailTab(\'transcript\')">完整转写</button>' +
    '</div>' +

    // 品牌·产品话术（新格式）
    '<div id="dt-analysis">' +
    '<div class="detail-grid">' +
    '<div>' +
    '<div class="section-label">主推品（含话术）</div>' + productScriptsHtml +
    '<div class="section-label" style="margin-top:16px">用户画像</div><div class="persona-text">' + escHtml(a.user_persona || "暂无") + '</div>' +
    '<div class="section-label" style="margin-top:16px">话术策略</div><div class="strategy-text">' + escHtml(a.strategy_summary || "暂无") + '</div>' +
    '<div class="section-label" style="margin-top:16px">Highlights</div>' + (highlights || '<div style="color:var(--text-3)">暂无</div>') +
    '</div>' +
    '<div id="dt-type-view">' +
    '<div class="section-label">全部话术（按类型）</div>' + fallbackScripts +
    '</div>' +
    '</div></div>' +
    '<div id="dt-transcript" style="display:none">' + transcriptHtml + '</div>';

  // 存储下载内容
  panel.dataset.downloadText = downloadTxtContent;
  panel.dataset.downloadJson = JSON.stringify({session: s, analysis: a, transcript: t ? {full_text: t.full_text} : null}, null, 2);

  panel.classList.add("open");
  panel.scrollIntoView({ behavior: "smooth" });
}

function downloadScriptText(sessionId) {
  var panel = document.getElementById("detailPanel");
  var s = ALL.sessions.find(function(x) { return x.id === sessionId; });
  var text = panel.dataset.downloadText || "";
  if (!text) return;
  var blob = new Blob(["\uFEFF" + text], {type:"text/plain;charset=utf-8"});
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "话术_" + (s ? s.competitor + "_" + s.recorded_at.slice(0,10) : sessionId) + ".txt";
  a.click();
  URL.revokeObjectURL(a.href);
}

function downloadScriptJson(sessionId) {
  var panel = document.getElementById("detailPanel");
  var s = ALL.sessions.find(function(x) { return x.id === sessionId; });
  var json = panel.dataset.downloadJson || "{}";
  var blob = new Blob([json], {type:"application/json"});
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "分析_" + (s ? s.competitor + "_" + s.recorded_at.slice(0,10) : sessionId) + ".json";
  a.click();
  URL.revokeObjectURL(a.href);
}

function closeDetail() {
  document.getElementById("detailPanel").classList.remove("open");
}

function showDetailTab(tab) {
  document.getElementById("dt-tab-analysis") && document.getElementById("dt-tab-analysis").classList.toggle("active", tab === "analysis");
  document.getElementById("dt-tab-type") && document.getElementById("dt-tab-type").classList.toggle("active", tab === "type");
  document.getElementById("dt-tab-transcript") && document.getElementById("dt-tab-transcript").classList.toggle("active", tab === "transcript");
  var analysisDiv = document.getElementById("dt-analysis");
  var typeDiv = document.getElementById("dt-type-view");
  if (analysisDiv) analysisDiv.style.display = (tab === "analysis") ? "" : "none";
  if (typeDiv) typeDiv.style.display = (tab === "type") ? "" : "none";
  document.getElementById("dt-transcript").style.display = tab === "transcript" ? "" : "none";
  // 当切到"按类型"时，同时显示主推品的话术
  if (tab === "type" && document.getElementById("dt-analysis")) {
    var analysisDiv2 = document.getElementById("dt-analysis");
    if (analysisDiv2) {
      // 隐藏主推品部分，只显示按类型
      var prodSection = analysisDiv2.querySelector(".detail-grid");
      if (prodSection) {
        var children = prodSection.children;
        if (children[0]) children[0].style.display = "none";
        if (children[1]) children[1].style.display = "";
      }
    }
  }
}

function openDetailAndShowScripts(sessionId) {
  openDetail(sessionId);
}

// ─── 导出 ─────────────────────────────────────────────────────────────────────
function exportJSON() {
  const date = document.getElementById("fDate").value;
  const compId = parseInt(document.getElementById("fComp").value) || 0;
  let sessions = ALL.sessions.filter(s => s.status === "analyzed");
  if (date) sessions = sessions.filter(s => s.recorded_at.startsWith(date));
  if (compId) sessions = sessions.filter(s => s.competitor_id === compId);
  const data = sessions.map(s => ({ session: s, analysis: ALL.analyses.find(a => a.session_id === s.id) || null }));
  downloadBlob(new Blob([JSON.stringify(data, null, 2)], {type:"application/json"}), `竞品直播_横向对比.json`);
}

function exportCSV() {
  const date = document.getElementById("fDate").value;
  let sessions = ALL.sessions.filter(s => s.status === "analyzed");
  if (date) sessions = sessions.filter(s => s.recorded_at.startsWith(date));
  const header = ["账号","日期","时长(分钟)","主推品","卖点","用户画像","话术策略","Highlights"];
  const rows = sessions.map(s => {
    const a = ALL.analyses.find(x => x.session_id === s.id);
    return [
      s.competitor, s.recorded_at.slice(0,10),
      s.duration ? Math.round(s.duration/60) : "",
      (a?.key_products||[]).map(p=>p.name).join("; "),
      (a?.key_products||[]).flatMap(p=>p.selling_points||[]).join("; "),
      a?.user_persona || "",
      a?.strategy_summary || "",
      (a?.highlights||[]).join("; "),
    ];
  });
  const BOM = "\uFEFF";
  const csv = [header, ...rows].map(r => r.map(c => { var v = String(c||""); return String.fromCharCode(34)+v.replace(/"/g, String.fromCharCode(34,34))+String.fromCharCode(34); }).join(",")).join("\n");
  downloadBlob(new Blob([BOM + csv], {type:"text/csv;charset=utf-8"}), `竞品直播_横向对比.csv`);
}

function downloadBlob(blob, filename) {
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = filename; a.click(); URL.revokeObjectURL(a.href);
}

// ─── 工具 ─────────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/\n/g,"<br>");
}
function fmtDate(dt) { return new Date(dt).toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}); }
function fmtTime(dt) { return new Date(dt).toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}); }

// ─── 启动 ─────────────────────────────────────────────────────────────────────
init().catch(e => { console.error(e); });
</script>
</body>
</html>
"""
    return html


# ── 主函数 ─────────────────────────────────────────────────────────────────────

def main():
    print("[generate_static] 开始生成静态文件…")

    competitors = export_competitors()
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
    index_path = _ROOT / "docs" / "index.html"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(index_html, encoding="utf-8")
    print(f"  ✓ {index_path.relative_to(_ROOT)}")

    print(f"[generate_static] 完成！输出目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
