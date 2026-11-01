"""Offline tests for src/notify.py — no network, no real email.

Run:  python -m tests.test_notify
      (or) PYTHONPATH=. python tests/test_notify.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import notify  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REAL_MD = notify._read(os.path.join(ROOT, "reports", "latest.md"))
REAL_PLAN = notify._json(os.path.join(ROOT, "reports", "fix_plan.json"))
REAL_HIST = notify._json(os.path.join(ROOT, "fixes_history.json")) or []

FAILS = []


def check(name, cond, extra=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {extra}")
        FAILS.append(name)


# ---------------------------------------------------------------- real report
def test_real_report():
    print("real report digest (data-independent)")
    subj, text, html = notify.build_digest(REAL_MD, REAL_PLAN, REAL_HIST, "success")
    m = notify._DATE_RE.search(REAL_MD)
    if m:
        check("subject has report date", m.group(1) in subj, subj)
    check("subject is success", subj.startswith("✅"), subj)
    check("has movement table", "LAST 28 DAYS" in text)
    move = notify.parse_movement(REAL_MD)
    if move.get("Clicks"):
        check("clicks parsed", move["Clicks"]["current"] in text, move.get("Clicks"))
    else:
        print("  skip clicks (no movement table in report)")
    check("no markdown bold leaked", "**" not in text, [l for l in text.splitlines() if "**" in l])
    check("no markdown bold in html", "**" not in html)
    n = len((REAL_PLAN or {}).get("fixes") or [])
    check("fix plan count ok", f"Fix plan        {n} page" in text, n)
    check("html escaped ok", "<table" in html and "</html>" in html)
    check("links present", "Dashboard" in text and "Actions" in text)


# -------------------------------------------------------------- empty report
def test_empty_report():
    print("empty / missing report")
    subj, text, html = notify.build_digest("", None, None, "success")
    check("no crash, subject has run", "run" in subj, subj)
    check("report missing flagged", "❌ missing" in text)
    check("fix plan 0", "0 pages" in text)
    check("no movement fallback", "no movement data" in html)
    check("no PRs line", "  PRs             " not in text)


def test_failure_status():
    print("failure status")
    subj, text, html = notify.build_digest(REAL_MD, REAL_PLAN, REAL_HIST, "failure")
    check("failure subject", subj.startswith("❌") and "FAILURE" in subj, subj)
    check("failure banner", "did not finish cleanly" in text)
    check("failure html marker", "run status: <b>failure</b>" in html)


def test_open_pr():
    print("open PR rendering")
    hist = [{"pr": 7, "status": "open", "files": ["a", "b"], "direction": "pending"},
            {"pr": 8, "status": "merged", "files": ["c"], "direction": "improved",
             "merged_at": "2026-10-01T00:00:00Z"}]
    subj, text, html = notify.build_digest(REAL_MD, REAL_PLAN, hist, "success")
    check("open pr shown", "#7 open (2 files)" in text, text)
    check("merged pr shown", "#8 merged (improved)" in text)
    check("merged date in html", "2026-10-01" in html)


def test_html_escaping():
    print("html escaping (XSS)")
    md = (
        "# 🩺 SEO Doctor Report — 2026-01-01\n"
        "- Technical flags: **2** across **1** issue type\n"
        "## 🧱 Technical Issues\n"
        "| Issue Type | Pages |\n|---|---|\n"
        "| <script>alert(1)</script> | 1 |\n"
    )
    subj, text, html = notify.build_digest(md, {"fixes": []}, [], "success")
    check("script tag escaped", "<script>alert(1)</script>" not in html, html[:200])
    check("escaped entity present", "&lt;script&gt;" in html)
    check("tech row singular", "1 page" in text)


# ------------------------------------------------------------------ smtp cfg
ENV_KEYS = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "MAIL_TO", "MAIL_FROM", "JOB_STATUS"]


def _isolate_env():
    saved = {k: os.environ.pop(k, None) for k in ENV_KEYS}
    return saved


def _restore(saved):
    for k in ENV_KEYS:
        os.environ.pop(k, None)
        if saved[k] is not None:
            os.environ[k] = saved[k]


def test_smtp_config():
    print("smtp config")
    saved = _isolate_env()
    try:
        cfg = notify.smtp_config()
        check("unconfigured by default", cfg["configured"] is False)
        check("default port 465", cfg["port"] == 465)

        os.environ.update({"SMTP_HOST": "smtp.example.com", "SMTP_USER": "u@example.com",
                           "SMTP_PASS": "secret", "MAIL_TO": "a@x.com, b@y.com",
                           "SMTP_PORT": "not-a-port"})
        cfg = notify.smtp_config()
        check("configured when all set", cfg["configured"] is True)
        check("bad port falls back 465", cfg["port"] == 465)
        check("recipients split", cfg["to"] == ["a@x.com", "b@y.com"], cfg["to"])
        check("from defaults to user", cfg["frm"] == "u@example.com")

        os.environ["MAIL_FROM"] = "bot@example.com"
        check("MAIL_FROM overrides", notify.smtp_config()["frm"] == "bot@example.com")

        os.environ["SMTP_PASS"] = ""
        check("missing pass = unconfigured", notify.smtp_config()["configured"] is False)
    finally:
        _restore(saved)


def test_main_unconfigured():
    print("main() unconfigured")
    saved = _isolate_env()
    try:
        check("exit 0 when not configured", notify.main() == 0)
    finally:
        _restore(saved)


class FakeSMTP:
    """Stand-in for smtplib.SMTP / SMTP_SSL."""
    instances = []
    fail_login = False
    fail_send = False

    def __init__(self, host, port, timeout=30, context=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.context = context
        self.started_tls = False
        self.logged_in = False
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        return None

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, pwd):
        if FakeSMTP.fail_login:
            raise RuntimeError("bad credentials")
        self.logged_in = (user, pwd)

    def send_message(self, msg):
        if FakeSMTP.fail_send:
            raise RuntimeError("connection dropped")
        self.sent.append(msg)


def _configure_env():
    os.environ.update({"SMTP_HOST": "smtp.example.com", "SMTP_PORT": "465",
                       "SMTP_USER": "u@example.com", "SMTP_PASS": "pw",
                       "MAIL_TO": "to@example.com"})


def test_send_mocked():
    print("send() with mocked smtplib")
    saved = _isolate_env()
    real = (notify.smtplib.SMTP_SSL, notify.smtplib.SMTP)
    try:
        _configure_env()
        FakeSMTP.instances = []
        FakeSMTP.fail_login = FakeSMTP.fail_send = False
        notify.smtplib.SMTP_SSL = FakeSMTP
        notify.smtplib.SMTP = FakeSMTP

        ok = notify.send("subj", "text body", "<b>html</b>")
        check("send returns True", ok is True)
        inst = FakeSMTP.instances[-1]
        check("used implicit TLS on 465", inst.port == 465)
        check("logged in", inst.logged_in == ("u@example.com", "pw"), inst.logged_in)
        msg = inst.sent[-1]
        check("one message sent", len(inst.sent) == 1)
        check("subject set", msg["Subject"] == "subj")
        check("to set", msg["To"] == "to@example.com")
        check("multipart/alternative", msg.is_multipart())
        check("has html part", any(p.get_content_type() == "text/html" for p in msg.walk()))

        os.environ["SMTP_PORT"] = "587"
        FakeSMTP.instances = []
        notify.send("s2", "t2")
        inst = FakeSMTP.instances[-1]
        check("used STARTTLS on 587", inst.port == 587 and inst.started_tls is True)
    finally:
        notify.smtplib.SMTP_SSL, notify.smtplib.SMTP = real
        _restore(saved)


def test_main_send_paths():
    print("main() with mocked smtplib")
    saved = _isolate_env()
    real = (notify.smtplib.SMTP_SSL, notify.smtplib.SMTP)
    real_paths = (notify.REPORT_PATH, notify.PLAN_PATH, notify.HISTORY_PATH)
    try:
        _configure_env()
        notify.REPORT_PATH = os.path.join(ROOT, "reports", "latest.md")
        notify.PLAN_PATH = os.path.join(ROOT, "reports", "fix_plan.json")
        notify.HISTORY_PATH = os.path.join(ROOT, "fixes_history.json")
        notify.smtplib.SMTP_SSL = FakeSMTP
        notify.smtplib.SMTP = FakeSMTP

        FakeSMTP.instances = []
        FakeSMTP.fail_login = FakeSMTP.fail_send = False
        check("main exit 0 on success", notify.main() == 0)
        check("main sent a mail", len(FakeSMTP.instances[-1].sent) == 1)

        FakeSMTP.fail_send = True
        check("main exit 1 when SMTP fails", notify.main() == 1)

        FakeSMTP.fail_send = False
        FakeSMTP.fail_login = True
        check("main exit 1 on bad login", notify.main() == 1)

        FakeSMTP.fail_login = False
        notify.REPORT_PATH = os.path.join(ROOT, "reports", "__nope__.md")
        FakeSMTP.instances = []
        check("missing report still exits 0", notify.main() == 0)
        body = FakeSMTP.instances[-1].sent[-1].get_body(preferencelist=("plain",)).get_content()
        check("missing report flagged in mail", "missing" in body)
    finally:
        notify.smtplib.SMTP_SSL, notify.smtplib.SMTP = real
        notify.REPORT_PATH, notify.PLAN_PATH, notify.HISTORY_PATH = real_paths
        _restore(saved)


def test_print_mode():
    print("--print mode (issue comment fallback)")
    saved = _isolate_env()
    real_paths = (notify.REPORT_PATH, notify.PLAN_PATH, notify.HISTORY_PATH)
    real_argv = sys.argv
    try:
        notify.REPORT_PATH = os.path.join(ROOT, "reports", "latest.md")
        notify.PLAN_PATH = os.path.join(ROOT, "reports", "fix_plan.json")
        notify.HISTORY_PATH = os.path.join(ROOT, "fixes_history.json")
        sys.argv = ["notify", "--print"]
        rc = notify.main()
        check("--print exits 0 even with SMTP unset", rc == 0, rc)
    finally:
        notify.REPORT_PATH, notify.PLAN_PATH, notify.HISTORY_PATH = real_paths
        sys.argv = real_argv
        _restore(saved)


def test_parse_edge_cases():
    print("parser edge cases")
    check("empty md parses empty", notify.parse_executive("") == {})
    check("empty movement", notify.parse_movement("") == {})
    check("empty technical", notify.parse_technical("") == [])
    md = ("## 🧱 Technical Issues\n| Issue Type | Pages |\n|---|---|\n"
          "| TITLE_LENGTH | 84 |\n| META_LENGTH | 38 |\n")
    check("technical rows parsed", notify.parse_technical(md) ==
          [("TITLE_LENGTH", "84"), ("META_LENGTH", "38")], notify.parse_technical(md))
    check("bold stripped from exec",
          "**" not in str(notify.parse_executive("- Technical flags: **140** across **6** types\n")))


if __name__ == "__main__":
    for fn in (test_real_report, test_empty_report, test_failure_status, test_open_pr,
               test_html_escaping, test_smtp_config, test_main_unconfigured,
               test_send_mocked, test_main_send_paths, test_print_mode,
               test_parse_edge_cases):
        fn()
    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)} check(s): {FAILS}")
        sys.exit(1)
    print("ALL NOTIFY TESTS PASSED")
