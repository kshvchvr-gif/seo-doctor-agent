"""Email digest for the SEO Doctor pipeline.

Purpose: a human should be able to tell at a glance that the pipeline is alive.
One short email after every weekly run (success *or* failure) is the heartbeat —
if the email does not arrive on Sunday, something is wrong.

Design notes:
  * stdlib only (smtplib / email) — no new dependency to vet.
  * Never fails the workflow when email is simply not configured yet.
  * If email IS configured but sending fails, we exit non-zero on purpose so the
    broken notification is visible instead of silently swallowed.

Env:
  SMTP_HOST   e.g. smtp.gmail.com
  SMTP_PORT   465 (implicit TLS) or 587 (STARTTLS); default 465
  SMTP_USER   login (usually the sending address)
  SMTP_PASS   app password / SMTP password
  MAIL_TO     one or more recipients, comma-separated
  MAIL_FROM   optional From header (defaults to SMTP_USER)
  JOB_STATUS  "success" / "failure" / "cancelled" (passed from the workflow)
  DASHBOARD_URL, REPO_URL  optional links in the mail
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPORT_PATH = os.environ.get("REPORT_PATH", os.path.join(REPO_ROOT, "reports", "latest.md"))
PLAN_PATH = os.environ.get("FIX_PLAN_PATH", os.path.join(REPO_ROOT, "reports", "fix_plan.json"))
HISTORY_PATH = os.environ.get("HISTORY_PATH", os.path.join(REPO_ROOT, "fixes_history.json"))

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://kshvchvr-gif.github.io/seo-doctor-agent/")
REPO_URL = os.environ.get("REPO_URL", "https://github.com/kshvchvr-gif/seo-doctor-agent")
SITE_REPO = os.environ.get("SITE_REPO", "kshvchvr-gif/krishnbhakti-web")


# --------------------------------------------------------------------------- io
def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------- parsing
_EXEC_RE = re.compile(r"^-\s+([^:]+):\s*\*\*(.+?)\*\*(.*)$", re.M)
_DATE_RE = re.compile(r"^#\s*.*?SEO Doctor Report\s*[—\-]\s*(\d{4}-\d{2}-\d{2})", re.M)


def parse_executive(md: str) -> dict:
    out = {}
    for label, value, tail in _EXEC_RE.findall(md):
        out[label.strip()] = re.sub(r"\*\*", "", value + tail).strip()
    return out


def parse_movement(md: str) -> dict:
    """First metric table under the 28-day window (current / previous / change)."""
    m = re.search(r"###\s*Window:\s*last 28d.*?(?=^###\s|\Z)", md, re.M | re.S)
    block = m.group(0) if m else md
    out = {}
    for name, cur, prev, chg in re.findall(
        r"^\|\s*(Clicks|Impressions|CTR|Avg position|GA4 sessions)\s*\|"
        r"\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$",
        block, re.M,
    ):
        out[name] = {"current": cur.strip(), "previous": prev.strip(), "change": chg.strip()}
    return out


def parse_technical(md: str) -> list:
    m = re.search(r"##\s*🧱\s*Technical Issues.*?\n(.*?)(?:\n##\s|\Z)", md, re.S)
    if not m:
        return []
    rows = []
    for line in m.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] and not set(cells[0]) <= set("-: "):
            if cells[0].lower().startswith("issue type"):
                continue
            rows.append((cells[0], cells[1]))
    return rows


def build_digest(md: str, plan, history, status: str = "success"):
    date = (_DATE_RE.search(md).group(1) if _DATE_RE.search(md) else "")
    execu = parse_executive(md)
    move = parse_movement(md)
    tech = parse_technical(md)

    plan_count = len((plan or {}).get("fixes") or [])
    open_prs = [e for e in (history or []) if e.get("status") == "open"]
    merged = [e for e in (history or []) if e.get("status") == "merged"]

    ok = status == "success"
    subject = (
        f"✅ SEO Doctor {date or 'run'} — {plan_count} fix(es) ready" if ok
        else f"❌ SEO Doctor {status.upper()} — {date or 'run'}"
    )

    def chg(name):
        v = move.get(name)
        if not v:
            return ""
        c = v["change"]
        return f"  ({c})" if c else ""

    lines = [
        f"SEO Doctor weekly — {date or 'run'}",
        f"Run status: {status} {'✅' if ok else '❌'}",
        "",
        "📊 LAST 28 DAYS (vs previous 28)",
    ]
    for name in ("Clicks", "Impressions", "CTR", "Avg position", "GA4 sessions"):
        v = move.get(name)
        if v:
            lines.append(f"  {name:<14}{v['current']}{chg(name)}")
    if not move and execu:
        for k in ("Total clicks (last 90d)", "Total impressions (last 90d)", "Overall CTR"):
            if k in execu:
                lines.append(f"  {k:<32}{execu[k]}")
    lines += [
        "",
        "🚦 PIPELINE",
        f"  Report          {'✅ generated' if md else '❌ missing'}",
        f"  Fix plan        {plan_count} page{'s' if plan_count != 1 else ''}",
        "  Auto-fix        runs right after this email → PR opens/updates",
    ]
    pr_bits = [f"#{e.get('pr')} open ({len(e.get('files') or [])} files)" for e in open_prs]
    pr_bits += [f"#{e.get('pr')} merged ({e.get('direction') or 'pending'})" for e in merged]
    if pr_bits:
        lines.append("  PRs             " + " · ".join(pr_bits))
    lines.append("")

    if tech:
        total = execu.get("Technical flags", "")
        lines.append(f"🔧 TOP TECHNICAL FLAGS{(' (' + total + ')') if total else ''}")
        for name, pages in tech[:6]:
            n = pages.strip()
            lines.append(f"  {name:<24}{n} {'page' if n == '1' else 'pages'}")
        lines.append("")

    if not ok:
        lines += [
            "⚠️ The weekly job did not finish cleanly. Open the run log:",
            f"  {REPO_URL}/actions",
            "  (any report shown above may be from the previous run)",
            "",
        ]

    lines += [
        "🔗 Links",
        f"  Dashboard : {DASHBOARD_URL}",
        f"  Actions   : {REPO_URL}/actions",
        f"  Report    : {REPO_URL}/issues?q=label%3Aseo-report",
        f"  Site PRs  : https://github.com/{SITE_REPO}/pulls",
        "",
        "— Yeh email har Sunday aati hai. Na aaye to Actions page check karo.",
    ]
    text = "\n".join(lines)

    # ---- simple HTML version
    def row(k, v):
        return (f'<tr><td style="padding:4px 12px 4px 0;color:#555">{_html.escape(k)}</td>'
                f'<td style="padding:4px 0;font-weight:600">{_html.escape(str(v))}</td></tr>')

    mv_rows = "".join(
        row(n, f"{move[n]['current']} {move[n]['change']}".strip()) for n in
        ("Clicks", "Impressions", "CTR", "Avg position", "GA4 sessions") if n in move
    )
    tech_rows = "".join(row(n, f"{p} {'page' if p == '1' else 'pages'}") for n, p in tech[:6])
    pr_rows = "".join(
        row(f"Open PR #{e.get('pr')}", f"{len(e.get('files') or [])} file(s) — awaiting review")
        for e in open_prs
    )
    ver_rows = "".join(
        row(f"PR #{e.get('pr')} ({str(e.get('merged_at'))[:10]})", e.get("direction") or "pending")
        for e in merged
    )
    head = ("✅" if ok else "❌")
    htmldoc = f"""<!doctype html><html><body style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#222;max-width:640px;margin:0 auto;padding:16px">
<h2 style="margin:0 0 4px">{head} SEO Doctor — {_html.escape(date or 'run')}</h2>
<p style="margin:0 0 16px;color:#666">Weekly digest · run status: <b>{_html.escape(status)}</b></p>
<h3 style="margin:16px 0 6px;font-size:15px">📊 Last 28 days (vs previous 28)</h3>
<table style="border-collapse:collapse;font-size:14px">{mv_rows or '<tr><td style="color:#888">no movement data</td></tr>'}</table>
<h3 style="margin:16px 0 6px;font-size:15px">🚦 Pipeline</h3>
<table style="border-collapse:collapse;font-size:14px">
{row('Report', 'generated' if md else 'missing')}
{row('Fix plan', f'{plan_count} page(s)')}
{pr_rows}{ver_rows}
</table>
<h3 style="margin:16px 0 6px;font-size:15px">🔧 Top technical flags</h3>
<table style="border-collapse:collapse;font-size:14px">{tech_rows or '<tr><td style="color:#888">none</td></tr>'}</table>
<p style="margin:20px 0 6px;font-size:14px">
<a href="{_html.escape(DASHBOARD_URL)}">Dashboard</a> ·
<a href="{_html.escape(REPO_URL)}/actions">Actions</a> ·
<a href="{_html.escape(REPO_URL)}/issues?q=label%3Aseo-report">Report</a> ·
<a href="https://github.com/{_html.escape(SITE_REPO)}/pulls">Site PRs</a>
</p>
<p style="margin:0;color:#999;font-size:12px">Yeh email har Sunday aati hai. Na aaye to Actions page check karo.</p>
</body></html>"""
    return subject, text, htmldoc


# ----------------------------------------------------------------------- send
def smtp_config():
    host = (os.environ.get("SMTP_HOST") or "").strip()
    user = (os.environ.get("SMTP_USER") or "").strip()
    pwd = os.environ.get("SMTP_PASS") or ""
    to = [x.strip() for x in (os.environ.get("MAIL_TO") or "").split(",") if x.strip()]
    try:
        port = int((os.environ.get("SMTP_PORT") or "465").strip())
    except ValueError:
        port = 465
    frm = (os.environ.get("MAIL_FROM") or "").strip() or user
    return {"host": host, "port": port, "user": user, "pwd": pwd,
            "to": to, "frm": frm, "configured": bool(host and user and pwd and to)}


def send(subject: str, text: str, html_body: str = "", cfg=None) -> bool:
    cfg = cfg or smtp_config()
    if not cfg["configured"]:
        print("Email not configured — set SMTP_HOST, SMTP_USER, SMTP_PASS, MAIL_TO. Skipping.")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["frm"]
    msg["To"] = ", ".join(cfg["to"])
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    ctx = ssl.create_default_context()
    if cfg["port"] == 465:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=ctx, timeout=30) as s:
            s.login(cfg["user"], cfg["pwd"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.ehlo()
            s.login(cfg["user"], cfg["pwd"])
            s.send_message(msg)
    print(f"Email sent to {', '.join(cfg['to'])}")
    return True


def main() -> int:
    status = (os.environ.get("JOB_STATUS") or "success").strip().lower()
    md = _read(REPORT_PATH)
    plan = _json(PLAN_PATH)
    history = _json(HISTORY_PATH) or []
    subject, text, html_body = build_digest(md, plan, history, status)

    # --print: emit the text digest only (used to post it as an issue comment,
    # which is what makes GitHub email the watchers). Never sends mail.
    if "--print" in sys.argv[1:]:
        print(text)
        return 0

    cfg = smtp_config()
    if not cfg["configured"]:
        print("Email not configured — set SMTP_HOST, SMTP_USER, SMTP_PASS, MAIL_TO. Skipping.")
        print("(Workflow stays green on purpose until email is set up.)")
        return 0
    try:
        send(subject, text, html_body, cfg)
        return 0
    except Exception as exc:  # noqa: BLE001 — surface a broken notifier loudly
        print(f"ERROR sending email: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
