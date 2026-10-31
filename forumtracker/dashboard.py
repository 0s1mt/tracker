"""Builds the master dashboard: one static HTML page bundling everything
this tracker currently knows (forum health/activity, incidents, alerts,
identity cross-matches, member-directory cross-matches) with a client-side
unified search box over all of it - no server, no network calls at view
time, just the current `data/` state embedded as JSON in the page.

Deliberately NOT wired into the scheduled pipeline.yml auto-commit: this
page embeds the full incidents/alerts history (megabytes, growing every
cycle), and committing a multi-MB diff every 30 minutes forever is a
different sustainability trade-off than the small `reports/latest_cycle.json`
summary that workflow already commits. Regenerate on demand with
`python -m forumtracker.cli --build-dashboard` instead.

**Screenshot evidence is a manual, session-scoped layer on top of this.**
`data/screenshots/*.png` (348MB, ~1978 files as of 2026-11-01) is far too
much to embed in one static page (16MB artifact limit) or upload file-by-file
in bulk - there is no batch-upload path, each one is a separate action. So
rather than pretend to cover "all incidents", `reports/dashboard_screenshots.json`
is a small, honestly-partial {incident_id: asset_url} map of whichever
screenshots have actually been uploaded to the currently-published artifact's
asset store (see the Artifact tool's `upload_asset` action) - grown in
batches over time, e.g. starting with the top alerts shown on the dashboard.
The asset URLs it contains (`/_blob/<id>`) only resolve inside THAT specific
published artifact - republishing to a different artifact URL invalidates them.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .member_directory import MEMBER_DIRECTORY_POOL
from .sources.browser_forum import BROWSER_FORUM_POOL
from .sources.underground_forums import FORUM_POOL

TEMPLATE_PATH = Path(__file__).parent / "templates" / "dashboard.html"
SCREENSHOT_MAP_PATH = Path(__file__).parent.parent / "reports" / "dashboard_screenshots.json"


def _screenshot_map(path: Path = SCREENSHOT_MAP_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _forum_health(health_db: Path) -> dict:
    if not health_db.exists():
        return {}
    con = sqlite3.connect(health_db)
    try:
        rows = con.execute(
            "SELECT name, homepage, status, consecutive_failures, last_checked_at, last_http_status FROM forum_health"
        ).fetchall()
        return {r[0]: {"homepage": r[1], "status": r[2], "fails": r[3], "checked_at": r[4], "http": r[5]} for r in rows}
    finally:
        con.close()


def _forum_stats_history(stats_db: Path) -> dict:
    if not stats_db.exists():
        return {}
    con = sqlite3.connect(stats_db)
    try:
        rows = con.execute(
            "SELECT name, captured_at, threads, messages, members FROM forum_stats ORDER BY name, captured_at"
        ).fetchall()
        history: dict[str, list[dict]] = {}
        for name, captured_at, threads, messages, members in rows:
            history.setdefault(name, []).append(
                {"t": captured_at, "threads": threads, "messages": messages, "members": members}
            )
        return history
    finally:
        con.close()


def _member_directory(member_db: Path) -> tuple[dict, int, list[dict]]:
    if not member_db.exists():
        return {}, 0, []
    con = sqlite3.connect(member_db)
    try:
        counts = dict(con.execute(
            "SELECT forum, COUNT(*) FROM members GROUP BY forum ORDER BY COUNT(*) DESC"
        ).fetchall())
        total = con.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        cross_rows = con.execute(
            """
            SELECT username_norm, GROUP_CONCAT(DISTINCT display_name), GROUP_CONCAT(DISTINCT forum)
            FROM members GROUP BY username_norm HAVING COUNT(DISTINCT forum) >= 2
            ORDER BY COUNT(DISTINCT forum) DESC, username_norm
            """
        ).fetchall()
        cross = [{"username": d.split(",")[0], "forums": sorted(f.split(","))} for _, d, f in cross_rows]
        return counts, total, cross
    finally:
        con.close()


def _identity(identity_db: Path) -> tuple[int, dict, list[dict]]:
    if not identity_db.exists():
        return 0, {}, []
    con = sqlite3.connect(identity_db)
    try:
        total = con.execute("SELECT COUNT(DISTINCT username_norm) FROM username_forums").fetchone()[0]
        by_forum = dict(con.execute(
            "SELECT forum, COUNT(DISTINCT username_norm) FROM username_forums GROUP BY forum "
            "ORDER BY COUNT(DISTINCT username_norm) DESC"
        ).fetchall())
        match_rows = con.execute(
            "SELECT username_norm, first_matched_at FROM cross_matches ORDER BY first_matched_at DESC"
        ).fetchall()
        matches = []
        for username_norm, first_matched_at in match_rows:
            forums = [r[0] for r in con.execute(
                "SELECT DISTINCT forum FROM username_forums WHERE username_norm = ? ORDER BY forum", (username_norm,)
            ).fetchall()]
            display = con.execute(
                "SELECT DISTINCT display_name FROM username_forums WHERE username_norm = ? LIMIT 1", (username_norm,)
            ).fetchone()
            matches.append({
                "username": display[0] if display else username_norm,
                "forums": forums,
                "first_matched_at": first_matched_at,
            })
        return total, by_forum, matches
    finally:
        con.close()


def _incidents(feed_path: Path) -> list[dict]:
    if not feed_path.exists():
        return []
    out = []
    with feed_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                inc = json.loads(line)
            except json.JSONDecodeError:
                continue
            source = inc.get("source") or {}
            actor = inc.get("threat_actor") or {}
            victim = inc.get("victim") or {}
            evidence = inc.get("evidence") or {}
            out.append({
                "id": inc.get("incident_id"),
                "t": inc.get("title"),
                "a": actor.get("name"),
                "src": source.get("name"),
                "cat": inc.get("category"),
                "d": inc.get("detected_at"),
                "u": evidence.get("post_url"),
                "tags": inc.get("tags") or [],
                "cc": victim.get("country_code"),
            })
    return out


# Role/placeholder actor names that would otherwise dominate a "top actors"
# ranking without being a real identity - same list identity_correlation.py
# already excludes from cross-forum matching, for the same reason.
_GENERIC_ACTOR_NAMES = {
    "unknown", "admin", "administrator", "mod", "moderator", "system",
    "deleted", "guest", "anonymous", "support", "staff", "bot",
}


def _top_actors(incidents: list[dict], limit: int = 15) -> list[dict]:
    counts: dict[str, int] = {}
    for inc in incidents:
        name = (inc.get("a") or "").strip()
        if not name or name.lower() in _GENERIC_ACTOR_NAMES:
            continue
        counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"name": name, "count": count} for name, count in ranked]


def _top_countries(incidents: list[dict], limit: int = 12) -> list[dict]:
    counts: dict[str, int] = {}
    for inc in incidents:
        cc = inc.get("cc")
        if not cc:
            continue
        counts[cc] = counts.get(cc, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"code": code, "count": count} for code, count in ranked]


def _alerts(feed_path: Path) -> list[dict]:
    if not feed_path.exists():
        return []
    out = []
    with feed_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                a = json.loads(line)
            except json.JSONDecodeError:
                continue
            rich = a.get("rich") or {}
            out.append({
                "id": a.get("incident_id"),
                "t": rich.get("target_title"),
                "a": rich.get("threat_actor"),
                "src": rich.get("intel_source"),
                "sev": rich.get("severity"),
                "risk": rich.get("risk_score"),
                "pop": rich.get("popularity_score"),
                "replies": rich.get("reply_count"),
                "entities": rich.get("matched_entities") or [],
                "d": rich.get("detection_date"),
                "u": rich.get("source_link"),
            })
    return out


def build_dashboard_data(data_dir: Path) -> dict:
    """Assembles the full dashboard data bundle from the current data/
    state - real numbers only, no placeholders. Safe to call any time;
    every sub-store is optional (a fresh checkout with no data/ yet just
    gets empty sections rather than an error).
    """
    health = _forum_health(data_dir / "health.sqlite3")
    history = _forum_stats_history(data_dir / "forum_stats.sqlite3")
    mdir_counts, mdir_total, mdir_cross = _member_directory(data_dir / "member_directory.sqlite3")
    id_total, id_by_forum, id_matches = _identity(data_dir / "identity.sqlite3")
    incidents = _incidents(data_dir / "incidents.jsonl")
    alerts = _alerts(data_dir / "alerts.jsonl")
    # alerts.jsonl carries no victim info of its own - join back to the
    # matching incident (same incident_id) so the dashboard's country
    # search/filter also covers the alert list, not just raw incidents.
    incident_cc_by_id = {i["id"]: i["cc"] for i in incidents if i.get("cc")}
    for a in alerts:
        if a.get("id") in incident_cc_by_id:
            a["cc"] = incident_cc_by_id[a["id"]]

    all_forum_names = list(FORUM_POOL.keys()) + list(BROWSER_FORUM_POOL.keys())
    forums = []
    for name in all_forum_names:
        cfg = FORUM_POOL.get(name) or BROWSER_FORUM_POOL.get(name) or {}
        homepage = cfg.get("homepage") or cfg.get("base_url")
        h = health.get(name, {})
        forum_history = history.get(name, [])
        forums.append({
            "name": name,
            "homepage": homepage,
            "status": h.get("status", "unknown"),
            "fails": h.get("fails", 0),
            "checked_at": h.get("checked_at"),
            "member_dir": name in MEMBER_DIRECTORY_POOL,
            "latest": forum_history[-1] if forum_history else None,
            "history": forum_history,
            "incident_count": sum(1 for i in incidents if i["src"] == f"forum:{name}"),
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kpi": {
            "forums_monitored": len(all_forum_names),
            "forums_up": sum(1 for f in forums if f["status"] == "up"),
            "forums_dead": sum(1 for f in forums if f["status"] not in ("up", "unknown")),
            "total_incidents": len(incidents),
            "total_alerts": len(alerts),
            "identity_total_users": id_total,
            "identity_cross_forum": len(id_matches),
            "member_directory_total": mdir_total,
            "member_directory_cross_forum": len(mdir_cross),
        },
        "forums": forums,
        "identity_by_forum": id_by_forum,
        "identity_matches": id_matches,
        "member_directory_by_forum": mdir_counts,
        "member_directory_cross_forum": mdir_cross,
        "incidents": incidents,
        "alerts": sorted(alerts, key=lambda a: (a.get("pop") or 0), reverse=True),
        "top_actors": _top_actors(incidents),
        "top_countries": _top_countries(incidents),
        "screenshots": _screenshot_map(),
    }


def render_dashboard(data_dir: Path) -> str:
    bundle = build_dashboard_data(data_dir)
    data_json = json.dumps(bundle, ensure_ascii=False).replace("</script>", "<\\/script>")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("__DATA_JSON__", data_json)


def build_dashboard(data_dir: Path, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_dashboard(data_dir), encoding="utf-8")
    return out_path
