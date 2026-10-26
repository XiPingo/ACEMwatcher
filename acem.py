#!/usr/bin/env python3
"""Monitor the ACEM PhD notice list and notify one mailbox on changes."""

from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import ssl
import sys
import time
from dataclasses import dataclass, asdict
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from lxml import html as lxml_html
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


URL = "https://acem.sjtu.edu.cn/acem2021/phd/notices.html"
GS_URL = "https://www.gs.sjtu.edu.cn/"
YZW_URL = "https://yzb.sjtu.edu.cn/"
SITES = (
    (URL, "ACEM 博士通知", None),
    (GS_URL, "研究生院通知公告", "/html/body/div[5]/div[1]/div/div/div[1]"),
    (YZW_URL, "研究生招生网博士招生", "/html/body/div[5]/div[1]/div/div/div[1]"),
    (YZW_URL, "研究生招生网硕士招生", "/html/body/div[5]/div[1]/div/div/div[2]"),
)
SNAPSHOT_FILE = Path(os.getenv("SNAPSHOT_FILE", "acem_snapshot.json"))
RECIPIENT = os.getenv("EMAIL_TO", "xiangxi@smail.nju.edu.cn").strip()
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass(frozen=True)
class Notice:
    title: str
    url: str
    date: str = ""
    source: str = "ACEM PhD admissions"


def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _parse_dated_link(text: str) -> tuple[str, str]:
    text = " ".join(text.split())
    match = re.match(
        r"^(20\d{2}(?:[./-]\d{1,2}){0,2}|\d{1,2}(?:[./]\d{1,2})?(?:[./]\d{4})?|\d{1,2}\s+20\d{2}(?:[./]\d{1,2})?)"
        r"(?=\s+|[-|：:])\s*[-|：:]?\s*(.+)$",
        text,
    )
    return (match.group(1), match.group(2).strip()) if match else ("", text)


def _merge_dated_link_parts(title_date: str, row_date: str) -> str:
    """Combine a date prefix in a title with a date shown in its list row."""
    if not title_date:
        return row_date
    if not row_date:
        return title_date

    title_parts = re.findall(r"\d+", title_date)
    row_parts = re.findall(r"\d+", row_date)
    # These two sites put the year or year/month in the title and the
    # remaining month/day (or day) in a sibling element.
    if title_parts and len(title_parts[0]) == 4 and len(title_parts) < 3:
        if not row_parts or len(row_parts) > 3 or len(row_parts[0]) == 4:
            return title_date
        parts = title_parts + row_parts
        if len(parts) >= 2:
            parts[1:] = [part.zfill(2) for part in parts[1:]]
            return ".".join(parts)
    return title_date


def _row_date(row_text: str, link_text: str) -> str:
    """Extract the date rendered beside a link, excluding the link title."""
    row_text = " ".join(row_text.split())
    link_text = " ".join(link_text.split())
    remainder = row_text.replace(link_text, "", 1).strip()
    match = re.search(r"(?<!\d)(\d{1,2}(?:[./]\d{1,2})?(?:[./]\d{4})?)(?!\d)", remainder)
    return match.group(1) if match else ""


def _xpath_notices(document: str, url: str, xpath: str, source: str) -> list[Notice]:
    tree = lxml_html.fromstring(document)
    sections = tree.xpath(xpath)
    if not sections:
        raise RuntimeError(f"Could not find the {source} section ({xpath})")

    notices: list[Notice] = []
    seen: set[str] = set()
    for link in sections[0].xpath(".//a[@href]"):
        text = " ".join(" ".join(link.itertext()).split()) or (link.get("title") or "").strip()
        if not text:
            continue
        href = urljoin(url, (link.get("href") or "").strip())
        if not href or href.startswith(("javascript:", "mailto:")) or href in seen:
            continue
        seen.add(href)
        row_date = ""
        parent = link.getparent()
        while parent is not None and parent is not sections[0]:
            row_text = " ".join(" ".join(parent.itertext()).split())
            if row_text != text:
                row_date = _row_date(row_text, text)
                if row_date:
                    break
            parent = parent.getparent()
        text_date, title = _parse_dated_link(text)
        date = _merge_dated_link_parts(text_date, row_date)
        notices.append(Notice(title=title, url=href, date=date, source=source))
    if not notices:
        raise RuntimeError(f"The {source} section was empty")
    return notices


def fetch_site_notices(
    url: str, timeout: int = 30, *, xpath: str | None = None, source: str | None = None
) -> list[Notice]:
    response = build_session().get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    source = source or url
    if xpath:
        return _xpath_notices(response.text, url, xpath, source)
    if url != URL:
        raise ValueError(f"Unsupported notice URL: {url}")
    soup = BeautifulSoup(response.text, "lxml")

    heading = soup.find(
        lambda tag: tag.name in {"h1", "h2", "h3"}
        and "公告信息" in tag.get_text(" ", strip=True)
    )
    if heading is None:
        raise RuntimeError("Could not find the ACEM notice heading")

    notice_list = heading.find_next("ul", class_=lambda value: value and "list-date" in value)
    if notice_list is None:
        raise RuntimeError("Could not find the ACEM notice list")

    notices: list[Notice] = []
    for item in notice_list.find_all("li", recursive=False):
        link = item.find("a", href=True)
        if link is None:
            continue
        title = link.get_text(" ", strip=True) or link.get("title", "").strip()
        href = urljoin(url, link["href"].strip())
        date_node = item.find(class_=lambda value: value and "time" in value)
        date = date_node.get_text(" ", strip=True) if date_node else ""
        if title and href:
            notices.append(Notice(title=title, url=href, date=date, source="ACEM PhD admissions"))

    if not notices:
        raise RuntimeError("The ACEM notice list was empty")
    return notices


def fetch_notices(url: str | None = None, timeout: int = 30) -> list[Notice]:
    sites = [site for site in SITES if url is None or site[0] == url]
    if url is not None and not sites:
        raise ValueError(f"Unsupported notice URL: {url}")
    notices: list[Notice] = []
    for site_url, source, xpath in sites:
        notices.extend(fetch_site_notices(site_url, timeout, xpath=xpath, source=source))
    return notices


def load_snapshot(path: Path = SNAPSHOT_FILE) -> list[Notice]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data: Any = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    if isinstance(data, dict):
        data = data.get("notices", [])
    if not isinstance(data, list):
        return []

    notices: list[Notice] = []
    for item in data:
        if isinstance(item, dict) and item.get("title") and item.get("url"):
            notices.append(
                Notice(
                    title=str(item["title"]),
                    url=str(item["url"]),
                    date=str(item.get("date", "")),
                    source=str(item.get("source", "ACEM PhD admissions")),
                )
            )
    return notices


def save_snapshot(notices: list[Notice], path: Path = SNAPSHOT_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            {"version": 2, "notices": [asdict(notice) for notice in notices]},
            handle,
            ensure_ascii=False,
            indent=2,
        )
        handle.write("\n")


def is_legacy_snapshot(path: Path = SNAPSHOT_FILE) -> bool:
    """Return whether *path* was written by the pre-initial-email format."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            data: Any = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return isinstance(data, list)


def diff_notices(old: list[Notice], new: list[Notice]) -> dict[str, list[Any]]:
    old_by_url = {notice.url: notice for notice in old}
    new_by_url = {notice.url: notice for notice in new}
    added = [notice for url, notice in new_by_url.items() if url not in old_by_url]
    removed = [notice for url, notice in old_by_url.items() if url not in new_by_url]
    changed = [
        (old_by_url[url], notice)
        for url, notice in new_by_url.items()
        if url in old_by_url and old_by_url[url] != notice
    ]
    return {"added": added, "removed": removed, "changed": changed}


def format_diff(diff: dict[str, list[Any]]) -> str:
    lines = [f"SJTU研究生信息页面发生变化（{time.strftime('%Y-%m-%d %H:%M:%S')}）", ""]
    for notice in diff["added"]:
        lines.append(f"新增：[{notice.source}] {notice.title}")
        lines.append(f"日期：{notice.date}" if notice.date else "")
        lines.append(f"链接：{notice.url}")
        lines.append("")
    for notice in diff["removed"]:
        lines.append(f"移除：[{notice.source}] {notice.title}")
        lines.append(f"链接：{notice.url}")
        lines.append("")
    for old, new in diff["changed"]:
        lines.append(f"更新：[{new.source}] {old.title} -> {new.title}")
        if new.date:
            lines.append(f"日期：{new.date}")
        lines.append(f"链接：{new.url}")
        lines.append("")
    lines.extend([f"通知公告：首页 {GS_URL}", f"招生栏目：首页 {YZW_URL}"])
    return "\n".join(lines).strip() + "\n"


def send_email(body: str) -> None:
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASS", "").strip()
    sender = os.getenv("EMAIL_FROM", smtp_user).strip()
    host = os.getenv("SMTP_HOST", "").strip() or "smtp.qq.com"
    port = int(os.getenv("SMTP_PORT", "").strip() or "465")
    if not smtp_user or not smtp_pass or not sender:
        raise RuntimeError("SMTP_USER, SMTP_PASS and EMAIL_FROM must be configured")
    if not RECIPIENT:
        raise RuntimeError("EMAIL_TO must not be empty")

    message = MIMEText(body, "plain", "utf-8")
    # An address must stay an address, rather than an encoded-word header.
    message["From"] = sender
    message["To"] = RECIPIENT
    message["Subject"] = Header("[SJTU] 研究生通知更新", "utf-8")
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    payload = message.as_string()
    context = ssl.create_default_context()

    for attempt in range(1, 4):
        smtp = None
        stage = "connect"
        try:
            transport = "STARTTLS" if port == 587 else "SSL/TLS"
            print(f"SMTP attempt {attempt}/3: {host}:{port} ({transport}), connect", flush=True)
            if port == 587:
                smtp = smtplib.SMTP(host, port, timeout=30)
                stage = "STARTTLS"
                smtp.starttls(context=context)
            else:
                smtp = smtplib.SMTP_SSL(host, port, timeout=30, context=context)
            stage = "greeting"
            print(f"SMTP stage: {stage}", flush=True)
            smtp.ehlo_or_helo_if_needed()
            stage = "login"
            print(f"SMTP stage: {stage}", flush=True)
            smtp.login(smtp_user, smtp_pass)
            stage = "send"
            print(f"SMTP stage: {stage}", flush=True)
            smtp.sendmail(sender, [RECIPIENT], payload)
        except OSError as exc:
            detail = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, smtplib.SMTPAuthenticationError):
                raise RuntimeError(
                    f"SMTP login failed at {host}:{port}: {detail}. "
                    "For QQ Mail, enable SMTP and use the SMTP authorization code "
                    "as SMTP_PASS, not the QQ account password."
                ) from exc
            transient = isinstance(
                exc, (smtplib.SMTPServerDisconnected, ConnectionError, TimeoutError)
            ) or (
                isinstance(exc, smtplib.SMTPResponseException)
                and 400 <= exc.smtp_code < 500
            )
            if stage != "send" and transient and attempt < 3:
                # Release the failed connection before waiting to reconnect.
                if smtp is not None:
                    smtp.close()
                    smtp = None
                delay = 2 ** attempt
                print(
                    f"SMTP {stage} failed: {detail}; retrying in {delay}s",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay)
                continue
            hint = (
                " Delivery status is unknown after a connection loss; "
                "the snapshot will not be updated."
                if stage == "send" and isinstance(
                    exc, (smtplib.SMTPServerDisconnected, ConnectionError, TimeoutError)
                )
                else ""
            )
            raise RuntimeError(
                f"SMTP {stage} failed at {host}:{port} "
                f"(attempt {attempt}/3): {detail}.{hint}"
            ) from exc
        else:
            # Acceptance by sendmail() is success even if QUIT subsequently fails.
            print("SMTP server accepted the message.", flush=True)
            try:
                smtp.quit()
            except OSError as exc:
                print(
                    f"SMTP QUIT failed after message acceptance: {type(exc).__name__}",
                    file=sys.stderr,
                )
            return
        finally:
            if smtp is not None:
                smtp.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print changes without sending mail")
    args = parser.parse_args()

    try:
        new_notices = fetch_notices()
        old_notices = load_snapshot()
        # A legacy list snapshot was created before initial notifications were
        # enabled. Treat it as an unnotified baseline so the first upgraded run
        # sends the current notices once.
        if is_legacy_snapshot():
            diff = {"added": new_notices, "removed": [], "changed": []}
        else:
            diff = diff_notices(old_notices, new_notices)
        has_changes = any(diff.values())

        if not has_changes:
            print("No ACEM notice changes detected.")
            return 0

        body = format_diff(diff)
        if args.dry_run:
            print(body, end="")
            return 0

        send_email(body)
        print(f"Notice update sent to {RECIPIENT}.")
        save_snapshot(new_notices)
        return 0
    except Exception as exc:
        print(f"ACEMwatcher failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
