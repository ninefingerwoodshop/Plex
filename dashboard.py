# Plex Media Stack - Web Dashboard
# Local web UI showing library health, stats, and management tools

import json
import threading
import time
from flask import Flask, render_template_string, jsonify, request
from config import PLEX, SONARR, RADARR

app = Flask(__name__)

# Try to use SocketIO for real-time updates
try:
    from flask_socketio import SocketIO, emit
    socketio = SocketIO(app, cors_allowed_origins="*")
    HAS_SOCKETIO = True
except ImportError:
    socketio = None
    HAS_SOCKETIO = False


def background_updater():
    """Push real-time updates to connected clients."""
    if not HAS_SOCKETIO:
        return
    while True:
        time.sleep(15)
        try:
            # Check Plex sessions
            from api import plex_get
            sessions = plex_get("/status/sessions")
            active = sessions.get("MediaContainer", {}).get("Metadata", [])
            session_data = []
            for s in active:
                session_data.append({
                    "user": s.get("User", {}).get("title", "?"),
                    "title": s.get("title", "?"),
                    "grandparent": s.get("grandparentTitle", ""),
                    "type": s.get("type", ""),
                    "state": s.get("Player", {}).get("state", "?"),
                    "progress": s.get("viewOffset", 0),
                    "duration": s.get("duration", 0),
                    "player": s.get("Player", {}).get("title", "?"),
                    "transcode": s.get("TranscodeSession", {}).get("videoDecision", "direct") if s.get("TranscodeSession") else "direct",
                })
            socketio.emit("sessions", {"sessions": session_data})

            # Check NZBGet status
            import requests as req
            from config import NZBGET
            r = req.post(f"{NZBGET['url']}/jsonrpc",
                         json={"method": "status", "params": []}, timeout=3)
            nzb = r.json().get("result", {})
            socketio.emit("nzbget", {
                "speed": round(nzb.get("DownloadRate", 0) / 1024 / 1024, 1),
                "standby": nzb.get("ServerStandBy", True),
            })
        except Exception:
            pass

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Plex Health Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #e0e0e0;
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(135deg, #e5a00d 0%, #cc7b19 100%);
            color: #000;
            padding: 16px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 { font-size: 20px; font-weight: 700; }
        .header .subtitle { opacity: 0.7; font-size: 12px; }
        .live-bar {
            background: #0d1b2a;
            padding: 8px 20px;
            display: flex;
            gap: 16px;
            font-size: 12px;
            color: #888;
            border-bottom: 1px solid #0f3460;
            overflow-x: auto;
        }
        .live-bar .live-item { white-space: nowrap; }
        .live-bar .live-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }
        .live-bar .live-dot.green { background: #27ae60; }
        .live-bar .live-dot.orange { background: #e5a00d; }
        .live-bar .live-dot.red { background: #c0392b; }
        .nav {
            background: #16213e;
            padding: 8px 20px;
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            border-bottom: 1px solid #0f3460;
            overflow-x: auto;
        }
        .nav button {
            background: #0f3460;
            color: #e0e0e0;
            border: none;
            padding: 7px 12px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
            transition: all 0.2s;
            white-space: nowrap;
        }
        .nav button:hover { background: #e5a00d; color: #000; }
        .nav button.active { background: #e5a00d; color: #000; font-weight: 600; }
        .container { padding: 16px 20px; max-width: 1400px; margin: 0 auto; }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 12px;
            margin-bottom: 16px;
        }
        .card {
            background: #16213e;
            border-radius: 10px;
            padding: 16px;
            border: 1px solid #0f3460;
        }
        .card h3 {
            color: #e5a00d;
            margin-bottom: 10px;
            font-size: 14px;
        }
        .stat-value {
            font-size: 32px;
            font-weight: 700;
            color: #fff;
        }
        .stat-label { font-size: 12px; color: #888; margin-top: 4px; }
        .table-card { grid-column: 1 / -1; overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th {
            text-align: left;
            padding: 6px 10px;
            border-bottom: 2px solid #0f3460;
            color: #e5a00d;
            font-size: 12px;
            white-space: nowrap;
        }
        td {
            padding: 6px 10px;
            border-bottom: 1px solid #0f3460;
            font-size: 12px;
        }
        tr:hover { background: rgba(229, 160, 13, 0.05); }
        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
        }
        .badge-red { background: #c0392b; color: #fff; }
        .badge-yellow { background: #f39c12; color: #000; }
        .badge-green { background: #27ae60; color: #fff; }
        .badge-blue { background: #2980b9; color: #fff; }
        .badge-gray { background: #555; color: #fff; }
        .bar-container {
            background: #0f3460;
            border-radius: 4px;
            height: 20px;
            overflow: hidden;
            margin-top: 6px;
        }
        .bar-fill {
            height: 100%;
            border-radius: 4px;
            transition: width 0.5s;
        }
        .loading {
            text-align: center;
            padding: 40px;
            color: #888;
        }
        .btn {
            background: #e5a00d;
            color: #000;
            border: none;
            padding: 8px 14px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            font-size: 12px;
            margin-top: 8px;
        }
        .btn:hover { background: #cc7b19; }
        .btn:disabled { background: #555; color: #888; cursor: not-allowed; }
        #content { min-height: 400px; }
        .session-card {
            background: #0f3460;
            border-radius: 8px;
            padding: 12px;
            margin: 4px 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .session-card .title { font-weight: 600; }
        .session-card .meta { font-size: 11px; color: #888; }
        @media (max-width: 768px) {
            .header { padding: 12px 16px; flex-direction: column; text-align: center; gap: 8px; }
            .header h1 { font-size: 18px; }
            .nav { padding: 8px 12px; gap: 4px; justify-content: center; }
            .nav button { padding: 6px 10px; font-size: 11px; }
            .container { padding: 12px; }
            .grid { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; }
            .card { padding: 12px; }
            .stat-value { font-size: 24px; }
            .table-card { padding: 8px; }
            td, th { padding: 4px 6px; font-size: 11px; }
            .live-bar { padding: 6px 12px; font-size: 11px; }
        }
        @media (max-width: 480px) {
            .grid { grid-template-columns: 1fr 1fr; }
            .table-card { grid-column: 1 / -1; }
        }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>Plex Health Dashboard</h1>
            <div class="subtitle">Library Health & Sync Report</div>
        </div>
        <div style="text-align:right">
            <div style="font-size:13px">Plex v{{ plex_version }}</div>
            <div style="font-size:11px;color:#333">{{ movie_count }} movies | {{ show_count }} shows</div>
        </div>
    </div>
    <div class="live-bar" id="live-bar">
        <div class="live-item"><span class="live-dot green"></span> Plex Online</div>
        <div class="live-item" id="live-sessions">No active streams</div>
        <div class="live-item" id="live-nzbget">NZBGet: idle</div>
    </div>
    <div class="nav">
        <button class="active" onclick="loadPage('overview')">Overview</button>
        <button onclick="loadPage('sync')">Sync Audit</button>
        <button onclick="loadPage('quality')">Quality</button>
        <button onclick="loadPage('episodes')">Episodes</button>
        <button onclick="loadPage('stale')">Stale Quality</button>
        <button onclick="loadPage('duplicates')">Duplicates</button>
        <button onclick="loadPage('stats')">Stats</button>
        <button onclick="loadPage('collections')">Collections</button>
        <button onclick="loadPage('storage')">Storage</button>
        <button onclick="loadPage('nzbget')">NZBGet</button>
        <button onclick="loadPage('pick')">Movie Pick</button>
    </div>
    <div class="container" id="content">
        <div class="loading">Loading...</div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
    <script>
        let currentPage = 'overview';

        async function loadPage(page) {
            currentPage = page;
            document.querySelectorAll('.nav button').forEach(b => b.classList.remove('active'));
            event.target.classList.add('active');
            document.getElementById('content').innerHTML = '<div class="loading">Loading...</div>';

            try {
                const resp = await fetch('/api/' + page);
                const data = await resp.json();
                renderPage(page, data);
            } catch(e) {
                document.getElementById('content').innerHTML = '<div class="loading">Error loading data</div>';
            }
        }

        function renderPage(page, data) {
            const el = document.getElementById('content');
            switch(page) {
                case 'overview': el.innerHTML = renderOverview(data); break;
                case 'sync': el.innerHTML = renderSync(data); break;
                case 'quality': el.innerHTML = renderQuality(data); break;
                case 'episodes': el.innerHTML = renderEpisodes(data); break;
                case 'stale': el.innerHTML = renderStale(data); break;
                case 'duplicates': el.innerHTML = renderDuplicates(data); break;
                case 'stats': el.innerHTML = renderStats(data); break;
                case 'collections': el.innerHTML = renderCollections(data); break;
                case 'storage': el.innerHTML = renderStorage(data); break;
                case 'nzbget': el.innerHTML = renderNzbget(data); break;
                case 'pick': el.innerHTML = renderPick(data); break;
            }
        }

        function renderOverview(d) {
            return `
                <div class="grid">
                    <div class="card">
                        <h3>Movies</h3>
                        <div class="stat-value">${d.movies}</div>
                        <div class="stat-label">${d.movies_watched} watched (${Math.round(100*d.movies_watched/d.movies)}%)</div>
                    </div>
                    <div class="card">
                        <h3>TV Shows</h3>
                        <div class="stat-value">${d.shows}</div>
                        <div class="stat-label">Across ${d.show_drives} drives</div>
                    </div>
                    <div class="card">
                        <h3>Storage</h3>
                        <div class="stat-value">${d.total_tb} TB</div>
                        <div class="stat-label">${d.total_gb} GB total</div>
                    </div>
                    <div class="card">
                        <h3>Radarr</h3>
                        <div class="stat-value">${d.radarr_movies}</div>
                        <div class="stat-label">movies tracked</div>
                    </div>
                    <div class="card">
                        <h3>Sonarr</h3>
                        <div class="stat-value">${d.sonarr_series}</div>
                        <div class="stat-label">series tracked</div>
                    </div>
                    <div class="card">
                        <h3>Health Issues</h3>
                        <div class="stat-value">${d.issues}</div>
                        <div class="stat-label">${d.no_subs} missing subs, ${d.low_res} low-res</div>
                    </div>
                </div>
                <div class="grid">
                    <div class="card">
                        <h3>Quality Breakdown</h3>
                        ${Object.entries(d.quality || {}).map(([k,v]) =>
                            '<div style="display:flex;justify-content:space-between;margin:4px 0">' +
                            '<span>' + k + '</span><span>' + v + '</span></div>'
                        ).join('')}
                    </div>
                    <div class="card">
                        <h3>Top Genres</h3>
                        ${(d.top_genres || []).map(g =>
                            '<div style="display:flex;justify-content:space-between;margin:4px 0">' +
                            '<span>' + g[0] + '</span><span>' + g[1] + '</span></div>'
                        ).join('')}
                    </div>
                    <div class="card">
                        <h3>Services</h3>
                        <div style="margin:4px 0">Plex <span class="badge badge-green">RUNNING</span></div>
                        <div style="margin:4px 0">Sonarr <span class="badge badge-green">RUNNING</span></div>
                        <div style="margin:4px 0">Radarr <span class="badge badge-green">RUNNING</span></div>
                        <div style="margin:4px 0">NZBGet <span class="badge badge-green">RUNNING</span></div>
                    </div>
                </div>
                <div class="grid">
                    <div class="card table-card">
                        <h3>Quick Actions</h3>
                        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
                            <button class="btn" data-label="Scan Movies" onclick="triggerAction('scan-movies')">Scan Movies</button>
                            <button class="btn" data-label="Scan TV" onclick="triggerAction('scan-tv')">Scan TV</button>
                            <button class="btn" data-label="Upgrade Stale" onclick="triggerAction('upgrade-stale')">Upgrade CAM/TS</button>
                            <button class="btn" data-label="Fix Posters" onclick="triggerAction('refresh-posters')">Fix Posters</button>
                        </div>
                    </div>
                </div>`;
        }

        function renderSync(d) {
            let html = '<div class="grid">';
            html += `<div class="card"><h3>Movies</h3>
                <div>${d.plex_movies} in Plex | ${d.radarr_movies} in Radarr</div>
                <div style="margin-top:8px">${d.in_radarr_not_plex.length} in Radarr but not Plex</div>
                <div>${d.in_plex_not_radarr.length} in Plex but not Radarr</div></div>`;
            html += `<div class="card"><h3>TV Shows</h3>
                <div>${d.plex_shows} in Plex | ${d.sonarr_series} in Sonarr</div>
                <div style="margin-top:8px">${d.in_sonarr_not_plex.length} in Sonarr but not Plex</div>
                <div>${d.in_plex_not_sonarr.length} in Plex but not Sonarr</div></div>`;
            html += '</div>';

            if (d.in_radarr_not_plex.length) {
                html += '<div class="card table-card"><h3>In Radarr but NOT in Plex</h3><table><tr><th>Title</th><th>Year</th><th>Status</th></tr>';
                d.in_radarr_not_plex.forEach(m => {
                    let badge = m.hasFile ? '<span class="badge badge-yellow">Has File</span>' :
                        m.monitored ? '<span class="badge badge-blue">Monitored</span>' :
                        '<span class="badge badge-gray">Unmonitored</span>';
                    html += '<tr><td>' + m.title + '</td><td>' + m.year + '</td><td>' + badge + '</td></tr>';
                });
                html += '</table></div>';
            }
            if (d.in_plex_not_radarr.length) {
                html += '<div class="card table-card"><h3>In Plex but NOT in Radarr</h3><table><tr><th>Title</th><th>Year</th></tr>';
                d.in_plex_not_radarr.forEach(m => {
                    html += '<tr><td>' + m.title + '</td><td>' + m.year + '</td></tr>';
                });
                html += '</table></div>';
            }
            return html;
        }

        function renderQuality(d) {
            let html = '<div class="grid">';
            html += `<div class="card"><h3>Low Resolution</h3><div class="stat-value">${d.low_resolution.length}</div>
                <div class="stat-label">movies below 1080p</div></div>`;
            html += `<div class="card"><h3>Low Bitrate</h3><div class="stat-value">${d.low_bitrate.length}</div>
                <div class="stat-label">movies under 3 Mbps</div></div>`;
            html += `<div class="card"><h3>No Subtitles</h3><div class="stat-value">${d.no_subtitles.length}</div>
                <div class="stat-label">movies without subs</div></div>`;
            html += `<div class="card"><h3>Missing Metadata</h3><div class="stat-value">${d.missing_summary.length + d.missing_poster.length}</div>
                <div class="stat-label">${d.missing_summary.length} no summary, ${d.missing_poster.length} no poster</div></div>`;
            html += '</div>';

            if (d.low_resolution.length) {
                html += '<div class="card table-card"><h3>Below 1080p</h3><table><tr><th>Movie</th></tr>';
                d.low_resolution.forEach(m => { html += '<tr><td>' + m + '</td></tr>'; });
                html += '</table></div>';
            }
            return html;
        }

        function renderEpisodes(d) {
            let html = '<div class="grid">';
            html += `<div class="card"><h3>Shows with Gaps</h3><div class="stat-value">${d.shows_with_gaps}</div></div>`;
            html += `<div class="card"><h3>Missing Seasons</h3><div class="stat-value">${d.fully_missing_seasons.length}</div></div>`;
            html += `<div class="card"><h3>Incomplete Seasons</h3><div class="stat-value">${d.incomplete_seasons.length}</div></div>`;
            html += '</div>';

            if (d.fully_missing_seasons.length) {
                html += '<div class="card table-card"><h3>Fully Missing Seasons</h3><table><tr><th>Show / Season</th></tr>';
                d.fully_missing_seasons.forEach(s => { html += '<tr><td>' + s + '</td></tr>'; });
                html += '</table></div>';
            }
            if (d.incomplete_seasons.length) {
                html += '<div class="card table-card"><h3>Incomplete Seasons</h3><table><tr><th>Show / Season</th></tr>';
                d.incomplete_seasons.forEach(s => { html += '<tr><td>' + s + '</td></tr>'; });
                html += '</table></div>';
            }
            return html;
        }

        function renderStale(d) {
            let html = '<div class="grid">';
            html += `<div class="card"><h3>CAM Copies</h3><div class="stat-value" style="color:#c0392b">${d.cam.length}</div></div>`;
            html += `<div class="card"><h3>TELESYNC</h3><div class="stat-value" style="color:#f39c12">${d.telesync.length}</div></div>`;
            html += `<div class="card"><h3>DVD Quality</h3><div class="stat-value">${d.dvd.length}</div></div>`;
            html += `<div class="card"><h3>Sub-720p</h3><div class="stat-value">${d.low_quality.length}</div></div>`;
            html += '</div>';

            let all_stale = [...d.cam, ...d.telesync, ...d.dvd];
            if (all_stale.length) {
                html += '<div class="card table-card"><h3>Needs Upgrade</h3><table><tr><th>Title</th><th>Quality</th><th>Resolution</th></tr>';
                all_stale.forEach(m => {
                    let badge = m.rank <= 1 ? 'badge-red' : m.rank <= 3 ? 'badge-yellow' : 'badge-gray';
                    html += `<tr><td>${m.label}</td><td><span class="badge ${badge}">${m.quality}</span></td><td>${m.height}p</td></tr>`;
                });
                html += '</table></div>';
            }
            return html;
        }

        function renderDuplicates(d) {
            if (!d.duplicates || d.duplicates.length === 0) {
                return '<div class="card"><h3>No Duplicates Found</h3><p>All movies are unique.</p></div>';
            }
            let html = `<div class="card"><h3>Potential Space Savings</h3>
                <div class="stat-value">${(d.wasted_mb / 1024).toFixed(1)} GB</div></div>`;
            html += '<div class="card table-card"><h3>Duplicate Movies</h3>';
            html += '<p style="margin:8px 0;color:#888">Movies with multiple copies on disk</p>';
            html += '<div>' + JSON.stringify(d.duplicates).substring(0, 200) + '...</div></div>';
            return html;
        }

        function renderStats(d) {
            let html = '<div class="grid">';
            html += `<div class="card"><h3>Total Movies</h3><div class="stat-value">${d.total}</div></div>`;
            html += `<div class="card"><h3>Watched</h3><div class="stat-value">${d.watched}</div>
                <div class="stat-label">${Math.round(100*d.watched/d.total)}% of library</div></div>`;
            html += `<div class="card"><h3>Unwatched</h3><div class="stat-value">${d.unwatched}</div></div>`;
            html += `<div class="card"><h3>Library Size</h3><div class="stat-value">${d.total_gb} GB</div></div>`;
            html += '</div>';

            html += '<div class="grid"><div class="card"><h3>Quality Distribution</h3>';
            Object.entries(d.quality || {}).forEach(([k,v]) => {
                let pct = Math.round(100 * v / d.total);
                let color = k === '4K' ? '#e5a00d' : k === '1080p' ? '#27ae60' : k === '720p' ? '#2980b9' : '#c0392b';
                html += `<div style="margin:6px 0"><span>${k}: ${v} (${pct}%)</span>
                    <div class="bar-container"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div></div>`;
            });
            html += '</div>';

            html += '<div class="card"><h3>Genre Breakdown</h3>';
            Object.entries(d.genres || {}).sort((a,b) => b[1]-a[1]).slice(0, 10).forEach(([k,v]) => {
                let pct = Math.round(100 * v / d.total);
                html += `<div style="display:flex;justify-content:space-between;margin:4px 0">
                    <span>${k}</span><span>${v}</span></div>`;
            });
            html += '</div></div>';
            return html;
        }

        function renderCollections(d) {
            let html = `<div class="card"><h3>Collections</h3>
                <div class="stat-value">${d.total}</div>
                <div class="stat-label">${d.existing} existing in Plex</div></div>`;
            html += '<div class="card table-card"><h3>All Collections</h3><table><tr><th>Name</th><th>Movies</th></tr>';
            (d.collections || []).forEach(c => {
                html += `<tr><td>${c.name}</td><td>${c.count}</td></tr>`;
            });
            html += '</table></div>';
            return html;
        }

        function renderStorage(d) {
            let html = '<div class="grid">';
            html += `<div class="card"><h3>Total Storage</h3><div class="stat-value">${d.total_tb} TB</div>
                <div class="stat-label">${d.used_gb} GB used / ${d.free_gb} GB free</div></div>`;
            html += `<div class="card"><h3>Overall Usage</h3><div class="stat-value">${d.pct_used}%</div></div>`;
            html += `<div class="card"><h3>Movies Until Full</h3><div class="stat-value">${d.movies_until_full}</div>
                <div class="stat-label">at ${d.avg_movie_gb} GB avg</div></div>`;
            html += '</div>';
            html += '<div class="card table-card"><h3>Drive Breakdown</h3><table>';
            html += '<tr><th>Drive</th><th>Total</th><th>Used</th><th>Free</th><th>Usage</th><th>Type</th></tr>';
            (d.drives || []).forEach(dr => {
                let color = dr.pct > 90 ? '#c0392b' : dr.pct > 80 ? '#f39c12' : '#27ae60';
                html += `<tr><td>${dr.path}</td><td>${dr.total} GB</td><td>${dr.used} GB</td><td>${dr.free} GB</td>`;
                html += `<td><div class="bar-container" style="width:120px;display:inline-block;vertical-align:middle">`;
                html += `<div class="bar-fill" style="width:${dr.pct}%;background:${color}"></div></div> ${dr.pct}%</td>`;
                html += `<td>${dr.type}</td></tr>`;
            });
            html += '</table></div>';
            return html;
        }

        function renderNzbget(d) {
            let html = '<div class="grid">';
            let status = d.ServerStandBy ? 'IDLE' : 'DOWNLOADING';
            let badge = d.ServerStandBy ? 'badge-gray' : 'badge-green';
            html += `<div class="card"><h3>Status</h3><div class="stat-value"><span class="badge ${badge}">${status}</span></div></div>`;
            html += `<div class="card"><h3>Download Speed</h3><div class="stat-value">${d.speed_mbps} MB/s</div></div>`;
            html += `<div class="card"><h3>Downloaded Today</h3><div class="stat-value">${d.day_gb} GB</div></div>`;
            html += `<div class="card"><h3>Downloaded This Month</h3><div class="stat-value">${d.month_gb} GB</div></div>`;
            html += `<div class="card"><h3>Total Downloaded</h3><div class="stat-value">${d.total_gb} GB</div></div>`;
            html += `<div class="card"><h3>Free Disk Space</h3><div class="stat-value">${d.free_disk_gb} GB</div></div>`;
            html += '</div>';
            if (d.queue && d.queue.length > 0) {
                html += '<div class="card table-card"><h3>Download Queue</h3><table>';
                html += '<tr><th>Name</th><th>Size</th><th>Status</th></tr>';
                d.queue.forEach(q => {
                    html += `<tr><td>${q.name}</td><td>${q.size_mb} MB</td><td>${q.status}</td></tr>`;
                });
                html += '</table></div>';
            }
            return html;
        }

        function renderPick(d) {
            let html = '<div style="margin-bottom:16px">';
            html += '<button class="btn" onclick="loadPage(\'pick\')">Re-roll</button> ';
            html += '</div><div class="grid">';
            (d.picks || []).forEach((m, i) => {
                html += `<div class="card"><h3>${m.title} (${m.year})</h3>`;
                html += `<div style="margin:8px 0">Rating: <strong>${m.rating.toFixed(1)}</strong> | ${m.genres.join(', ')} | ${m.runtime} min</div>`;
                html += `<div style="color:#aaa;font-size:13px">${m.summary}...</div></div>`;
            });
            html += '</div>';
            return html;
        }

        async function triggerAction(action) {
            event.target.disabled = true;
            event.target.textContent = 'Working...';
            try {
                const resp = await fetch('/api/action/' + action, {method: 'POST'});
                const data = await resp.json();
                alert(data.message || 'Done!');
            } catch(e) {
                alert('Error: ' + e.message);
            }
            event.target.disabled = false;
            event.target.textContent = event.target.dataset.label || 'Done';
        }

        // Real-time WebSocket updates
        try {
            const socket = io();
            socket.on('sessions', function(data) {
                const el = document.getElementById('live-sessions');
                if (data.sessions.length === 0) {
                    el.textContent = 'No active streams';
                } else {
                    let parts = data.sessions.map(s => {
                        let title = s.grandparent ? s.grandparent + ' - ' + s.title : s.title;
                        let pct = s.duration ? Math.round(100 * s.progress / s.duration) : 0;
                        return s.user + ': ' + title + ' (' + pct + '%)';
                    });
                    el.innerHTML = '<span class="live-dot orange"></span> ' + parts.join(' | ');
                }
            });
            socket.on('nzbget', function(data) {
                const el = document.getElementById('live-nzbget');
                if (data.standby) {
                    el.textContent = 'NZBGet: idle';
                } else {
                    el.innerHTML = '<span class="live-dot green"></span> NZBGet: ' + data.speed + ' MB/s';
                }
            });
        } catch(e) {
            console.log('WebSocket not available, using polling');
        }

        // Load overview on page load
        window.onload = () => loadPage('overview');
    </script>
</body>
</html>
"""


@app.route("/")
def index():
    from api import plex_get, get_plex_movies, get_plex_shows
    try:
        identity = plex_get("/identity")
        version = identity.get("MediaContainer", {}).get("version", "?")
    except Exception:
        version = "?"

    movies = get_plex_movies()
    shows = get_plex_shows()

    return render_template_string(
        DASHBOARD_HTML,
        plex_version=version,
        movie_count=len(movies),
        show_count=len(shows),
    )


@app.route("/api/overview")
def api_overview():
    from api import get_plex_movies, get_plex_shows, get_radarr_movies, get_sonarr_series
    from collections import Counter

    movies = get_plex_movies()
    shows = get_plex_shows()
    radarr = get_radarr_movies()
    sonarr = get_sonarr_series()

    watched = sum(1 for m in movies if m.get("viewCount", 0) > 0 or m.get("lastViewedAt"))
    total_size = sum(
        part.get("size", 0)
        for m in movies for media in m.get("Media", []) for part in media.get("Part", [])
    )

    resolutions = Counter()
    no_subs = 0
    for m in movies:
        for media in m.get("Media", []):
            h = media.get("height", 0)
            if h >= 2160: resolutions["4K"] += 1
            elif h >= 1080: resolutions["1080p"] += 1
            elif h >= 720: resolutions["720p"] += 1
            elif h > 0: resolutions["SD"] += 1

            has_sub = False
            for part in media.get("Part", []):
                for stream in part.get("Stream", []):
                    if stream.get("streamType") == 3:
                        has_sub = True
                        break
            if not has_sub:
                no_subs += 1
            break

    low_res = resolutions.get("SD", 0) + resolutions.get("720p", 0)
    genres = Counter()
    for m in movies:
        for g in m.get("Genre", []):
            genres[g.get("tag", "")] += 1

    total_gb = round(total_size / (1024 ** 3), 1)

    return jsonify({
        "movies": len(movies),
        "movies_watched": watched,
        "shows": len(shows),
        "show_drives": 7,
        "total_gb": total_gb,
        "total_tb": round(total_gb / 1024, 2),
        "radarr_movies": len(radarr),
        "sonarr_series": len(sonarr),
        "issues": no_subs + low_res,
        "no_subs": no_subs,
        "low_res": low_res,
        "quality": dict(resolutions),
        "top_genres": genres.most_common(8),
    })


@app.route("/api/sync")
def api_sync():
    from sync_audit import movie_sync_audit, tv_sync_audit
    import io, contextlib
    # Capture print output but get return values
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        movie_result = movie_sync_audit()
        tv_result = tv_sync_audit()

    return jsonify({
        "plex_movies": movie_result["plex_count"],
        "radarr_movies": movie_result["radarr_count"],
        "in_radarr_not_plex": movie_result["in_radarr_not_plex"],
        "in_plex_not_radarr": movie_result["in_plex_not_radarr"],
        "plex_shows": tv_result["plex_count"],
        "sonarr_series": tv_result["sonarr_count"],
        "in_sonarr_not_plex": tv_result["in_sonarr_not_plex"],
        "in_plex_not_sonarr": tv_result["in_plex_not_sonarr"],
    })


@app.route("/api/quality")
def api_quality():
    from quality_scan import scan_movie_quality
    import io, contextlib
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        result = scan_movie_quality()
    return jsonify(result)


@app.route("/api/episodes")
def api_episodes():
    from missing_episodes import missing_episodes_report
    import io, contextlib
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        result = missing_episodes_report()
    return jsonify(result)


@app.route("/api/stale")
def api_stale():
    from stale_quality import stale_quality_report
    import io, contextlib
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        result = stale_quality_report()
    return jsonify(result)


@app.route("/api/duplicates")
def api_duplicates():
    from duplicates import find_duplicates
    import io, contextlib
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        result = find_duplicates()

    # Serialize duplicates for JSON
    dups = []
    for key, copies in result.get("duplicates", {}).items():
        dups.append({"key": key, "copies": copies})

    return jsonify({
        "duplicates": dups,
        "wasted_mb": result.get("wasted_mb", 0),
    })


@app.route("/api/stats")
def api_stats():
    from watch_stats import movie_stats
    import io, contextlib
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        result = movie_stats()
    return jsonify(result)


@app.route("/api/collections")
def api_collections():
    from collections_builder import build_collections, get_existing_collections
    import io, contextlib
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        result = build_collections(dry_run=True)

    existing = get_existing_collections()
    collections = [{"name": k, "count": len(v)} for k, v in result.items()]

    return jsonify({
        "total": len(collections),
        "existing": len(existing),
        "collections": sorted(collections, key=lambda x: -x["count"]),
    })


@app.route("/api/storage")
def api_storage():
    from storage import get_drive_info
    from api import get_plex_movies, get_radarr_movies
    import io, contextlib

    drives = get_drive_info()
    movies = get_plex_movies()
    radarr = get_radarr_movies()

    total_size = sum(
        part.get("size", 0)
        for m in movies for media in m.get("Media", []) for part in media.get("Part", [])
    )

    drive_list = []
    total_gb = 0
    used_gb = 0
    free_gb = 0
    for d, info in sorted(drives.items()):
        if info.get("error"):
            continue
        drive_list.append({
            "path": info["path"],
            "total": info["total_gb"],
            "used": info["used_gb"],
            "free": info["free_gb"],
            "pct": info["pct_used"],
            "type": info["type"],
        })
        total_gb += info["total_gb"]
        used_gb += info["used_gb"]
        free_gb += info["free_gb"]

    avg_movie = round(total_size / (1024 ** 3) / len(movies), 1) if movies else 0
    movies_left = int(free_gb / avg_movie) if avg_movie else 0

    return jsonify({
        "drives": drive_list,
        "total_gb": round(total_gb, 1),
        "total_tb": round(total_gb / 1024, 2),
        "used_gb": round(used_gb, 1),
        "free_gb": round(free_gb, 1),
        "pct_used": round(100 * used_gb / total_gb, 1) if total_gb else 0,
        "avg_movie_gb": avg_movie,
        "movies_until_full": movies_left,
    })


@app.route("/api/nzbget")
def api_nzbget():
    import requests as req
    from config import NZBGET
    try:
        r = req.post(
            f"{NZBGET['url']}/jsonrpc",
            json={"method": "status", "params": []},
            timeout=5,
        )
        status = r.json().get("result", {})

        # Get queue
        rq = req.post(
            f"{NZBGET['url']}/jsonrpc",
            json={"method": "listgroups", "params": []},
            timeout=5,
        )
        groups = rq.json().get("result", [])
        queue = []
        for g in groups:
            queue.append({
                "name": g.get("NZBName", "?"),
                "size_mb": round(g.get("FileSizeMB", 0)),
                "status": g.get("Status", "?"),
            })

        return jsonify({
            "ServerStandBy": status.get("ServerStandBy", True),
            "speed_mbps": round(status.get("DownloadRate", 0) / 1024 / 1024, 1),
            "day_gb": round(status.get("DaySizeMB", 0) / 1024, 1),
            "month_gb": round(status.get("MonthSizeMB", 0) / 1024, 1),
            "total_gb": round(status.get("DownloadedSizeMB", 0) / 1024, 1),
            "free_disk_gb": round(status.get("FreeDiskSpaceMB", 0) / 1024, 1),
            "queue": queue,
        })
    except Exception as e:
        return jsonify({"error": str(e), "ServerStandBy": True, "speed_mbps": 0,
                        "day_gb": 0, "month_gb": 0, "total_gb": 0, "free_disk_gb": 0, "queue": []})


@app.route("/api/pick")
def api_pick():
    from movie_picker import pick_movie
    picks = pick_movie(unwatched_only=True, count=3)
    return jsonify({"picks": picks})


@app.route("/api/action/<action>", methods=["POST"])
def api_action(action):
    import io, contextlib

    if action == "scan-movies":
        import requests as req
        req.get(f"{PLEX['url']}/library/sections/{PLEX['movie_section']}/refresh",
                params={"X-Plex-Token": PLEX["token"]})
        return jsonify({"message": "Movie library scan triggered!"})

    elif action == "scan-tv":
        import requests as req
        req.get(f"{PLEX['url']}/library/sections/{PLEX['tv_section']}/refresh",
                params={"X-Plex-Token": PLEX["token"]})
        return jsonify({"message": "TV library scan triggered!"})

    elif action == "upgrade-stale":
        from api import get_radarr_movies, radarr_post
        movies = get_radarr_movies()
        ids = []
        for m in movies:
            if not m.get("hasFile"):
                continue
            q = m.get("movieFile", {}).get("quality", {}).get("quality", {}).get("name", "").lower()
            if any(x in q for x in ["cam", "telesync", "telecine"]):
                ids.append(m["id"])
        if ids:
            radarr_post("/command", {"name": "MoviesSearch", "movieIds": ids})
        return jsonify({"message": f"Triggered upgrade search for {len(ids)} movies"})

    elif action == "refresh-posters":
        from poster_upgrade import find_bad_posters, refresh_metadata
        issues = find_bad_posters()
        count = 0
        for item in issues[:20]:
            if refresh_metadata(item["ratingKey"]):
                count += 1
        return jsonify({"message": f"Refreshed metadata for {count} items"})

    return jsonify({"message": "Unknown action"}), 400


if __name__ == "__main__":
    print("Starting Plex Health Dashboard on http://localhost:5050")
    if HAS_SOCKETIO:
        # Start background updater
        updater = threading.Thread(target=background_updater, daemon=True)
        updater.start()
        print("  Real-time WebSocket updates: enabled")
        socketio.run(app, host="0.0.0.0", port=5050, debug=False, allow_unsafe_werkzeug=True)
    else:
        print("  Real-time updates: disabled (pip install flask-socketio)")
        app.run(host="0.0.0.0", port=5050, debug=False)
