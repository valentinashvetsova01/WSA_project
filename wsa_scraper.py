"""
WSA Telegram Scraper (v2: proxies + parallelism)

Reads seed channels from CSV, scrapes public t.me/s/<channel> previews,
saves messages, forwards, URLs, and media metadata to SQLite.

Usage:
    pip install requests beautifulsoup4 tqdm
    python wsa_scraper.py --csv wsa_seed_channels.csv --db wsa_data.db \
        --start-date 2025-11-23 --proxies proxies.txt --workers 5

Resume: re-running picks up where the last run stopped per channel.
Proxies file: one proxy URL per line, e.g. http://user:pass@host:port
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from random import uniform
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm


USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
]
BASE_URL = "https://t.me/s/{channel}"
REQUEST_DELAY = (0.4, 1.2)  # per-worker delay; proxies let us push harder
MAX_RETRIES = 5
TIMEOUT = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS channels (
    username                  TEXT PRIMARY KEY,
    display_name              TEXT,
    lean                      TEXT,
    subcategory               TEXT,
    subscribers               INTEGER,
    tgstat_rank               INTEGER,
    earliest_scraped_msg_id   INTEGER,
    earliest_scraped_date     TEXT,
    latest_scraped_msg_id     INTEGER,
    latest_scraped_date       TEXT,
    scrape_status             TEXT,
    scrape_started_at         TEXT,
    scrape_completed_at       TEXT,
    messages_count            INTEGER DEFAULT 0,
    errors_count              INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    channel_username    TEXT,
    msg_id              INTEGER,
    timestamp           TEXT,
    text                TEXT,
    views               INTEGER,
    forwarded_from      TEXT,
    forwarded_from_url  TEXT,
    reply_to_url        TEXT,
    has_photo           INTEGER DEFAULT 0,
    has_video           INTEGER DEFAULT 0,
    has_document        INTEGER DEFAULT 0,
    has_poll            INTEGER DEFAULT 0,
    is_sticker          INTEGER DEFAULT 0,
    PRIMARY KEY (channel_username, msg_id)
);

CREATE TABLE IF NOT EXISTS urls (
    channel_username   TEXT,
    msg_id             INTEGER,
    url                TEXT,
    domain             TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_ts          ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_forwarded   ON messages(forwarded_from);
CREATE INDEX IF NOT EXISTS idx_urls_domain          ON urls(domain);
CREATE INDEX IF NOT EXISTS idx_urls_msg             ON urls(channel_username, msg_id);
"""


@dataclass
class ParsedMessage:
    channel_username: str
    msg_id: int
    timestamp: str
    text: Optional[str]
    views: Optional[int]
    forwarded_from: Optional[str]
    forwarded_from_url: Optional[str]
    reply_to_url: Optional[str]
    has_photo: bool
    has_video: bool
    has_document: bool
    has_poll: bool
    is_sticker: bool
    urls: list[str] = field(default_factory=list)


class ProxyPool:
    """Thread-safe round-robin proxy selector."""
    def __init__(self, proxies: list[str]):
        self.proxies = proxies
        self._idx = 0
        self._lock = threading.Lock()
        self._ua_idx = 0

    def __bool__(self) -> bool:
        return bool(self.proxies)

    def get(self) -> Optional[dict]:
        if not self.proxies:
            return None
        with self._lock:
            p = self.proxies[self._idx % len(self.proxies)]
            self._idx += 1
        return {"http": p, "https": p}

    def get_user_agent(self) -> str:
        with self._lock:
            ua = USER_AGENTS[self._ua_idx % len(USER_AGENTS)]
            self._ua_idx += 1
        return ua


def load_proxies(path: Optional[Path]) -> list[str]:
    if not path:
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def init_db(db_path: Path) -> None:
    """Create tables and indexes; uses its own connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def open_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def load_channels_from_csv(csv_path: Path) -> list[dict]:
    out: list[dict] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            username = (row.get("username") or "").strip().lstrip("@")
            confidence = (row.get("username_confidence") or "").strip()
            if not username or confidence == "unknown":
                continue
            subs_raw = (row.get("subscribers") or "").strip()
            rank_raw = (row.get("tgstat_rank") or "").strip()
            out.append({
                "display_name": row["display_name"],
                "username": username,
                "lean": row.get("lean", ""),
                "subcategory": row.get("subcategory", ""),
                "subscribers": int(subs_raw) if subs_raw.isdigit() else None,
                "tgstat_rank": int(rank_raw) if rank_raw.isdigit() else None,
            })
    return out


_VIEWS_RE = re.compile(r"^([\d.]+)\s*([KMB]?)$", re.IGNORECASE)


def parse_views(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = _VIEWS_RE.match(s.strip().replace(" ", ""))
    if not m:
        return None
    num, suffix = m.groups()
    try:
        v = float(num)
    except ValueError:
        return None
    mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix.upper()]
    return int(v * mult)


_FWD_USERNAME_RE = re.compile(r"^https?://t\.me/([^/?#]+)")


def parse_iso_utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def extract_message(msg_elem, channel_username: str) -> Optional[ParsedMessage]:
    data_post = msg_elem.get("data-post", "")
    if "/" not in data_post:
        return None
    _, msg_id_str = data_post.rsplit("/", 1)
    try:
        msg_id = int(msg_id_str)
    except ValueError:
        return None

    time_elem = msg_elem.select_one("time[datetime]")
    if not time_elem:
        return None
    timestamp = time_elem.get("datetime", "")
    if not timestamp:
        return None

    text_elem = msg_elem.select_one(".tgme_widget_message_text")
    text = text_elem.get_text("\n", strip=True) if text_elem else None

    views_elem = msg_elem.select_one(".tgme_widget_message_views")
    views = parse_views(views_elem.get_text(strip=True)) if views_elem else None

    fwd_elem = msg_elem.select_one(".tgme_widget_message_forwarded_from_name")
    forwarded_from = None
    forwarded_from_url = None
    if fwd_elem and fwd_elem.has_attr("href"):
        forwarded_from_url = fwd_elem["href"]
        m = _FWD_USERNAME_RE.match(forwarded_from_url)
        if m:
            forwarded_from = m.group(1)

    reply_elem = msg_elem.select_one("a.tgme_widget_message_reply")
    reply_to_url = reply_elem["href"] if reply_elem and reply_elem.has_attr("href") else None

    has_photo = bool(msg_elem.select_one(".tgme_widget_message_photo_wrap, .tgme_widget_message_photo"))
    has_video = bool(msg_elem.select_one(".tgme_widget_message_video_player, .tgme_widget_message_video"))
    has_document = bool(msg_elem.select_one(".tgme_widget_message_document"))
    has_poll = bool(msg_elem.select_one(".tgme_widget_message_poll"))
    is_sticker = bool(msg_elem.select_one(".tgme_widget_message_sticker_wrap, .tgme_widget_message_sticker"))

    urls: list[str] = []
    if text_elem:
        for link in text_elem.select("a[href]"):
            href = link.get("href", "")
            if href.startswith("http"):
                urls.append(href)

    return ParsedMessage(
        channel_username=channel_username, msg_id=msg_id, timestamp=timestamp,
        text=text, views=views,
        forwarded_from=forwarded_from, forwarded_from_url=forwarded_from_url,
        reply_to_url=reply_to_url,
        has_photo=has_photo, has_video=has_video, has_document=has_document,
        has_poll=has_poll, is_sticker=is_sticker,
        urls=urls,
    )


def fetch_page(channel: str, before: Optional[int],
               proxy_pool: Optional[ProxyPool]) -> Optional[str]:
    url = BASE_URL.format(channel=channel)
    if before:
        url += f"?before={before}"
    for attempt in range(MAX_RETRIES):
        proxies = proxy_pool.get() if proxy_pool else None
        ua = proxy_pool.get_user_agent() if proxy_pool else USER_AGENTS[0]
        headers = {"User-Agent": ua, "Accept-Language": "ru,en;q=0.9"}
        try:
            resp = requests.get(url, headers=headers, proxies=proxies, timeout=TIMEOUT)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code == 404:
                logger.warning(f"{channel}: 404 (channel not found or private)")
                return None
            if resp.status_code == 429:
                wait = 5 * (2 ** attempt)
                logger.warning(f"{channel}: 429; sleeping {wait}s")
                time.sleep(wait)
                continue
            logger.warning(f"{channel}: HTTP {resp.status_code} before={before}")
        except (requests.RequestException, requests.Timeout) as e:
            wait = 2 ** attempt
            logger.warning(f"{channel}: {e}; retry in {wait}s")
            time.sleep(wait)
    return None


def scrape_channel(channel: dict, conn: sqlite3.Connection,
                   cutoff_date: datetime,
                   proxy_pool: Optional[ProxyPool],
                   max_pages: int = 100_000) -> int:
    """Scrape one channel from newest down to cutoff_date. Returns new messages inserted."""
    username = channel["username"]
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO channels (username, display_name, lean, subcategory, subscribers, tgstat_rank,
                              scrape_status, scrape_started_at)
        VALUES (?, ?, ?, ?, ?, ?, 'in_progress', ?)
        ON CONFLICT(username) DO UPDATE SET
            display_name=excluded.display_name,
            lean=excluded.lean,
            subcategory=excluded.subcategory,
            subscribers=excluded.subscribers,
            tgstat_rank=excluded.tgstat_rank,
            scrape_status='in_progress',
            scrape_started_at=excluded.scrape_started_at
        """,
        (username, channel["display_name"], channel["lean"], channel["subcategory"],
         channel["subscribers"], channel["tgstat_rank"],
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    cursor.execute(
        "SELECT earliest_scraped_msg_id FROM channels WHERE username = ?", (username,)
    )
    row = cursor.fetchone()
    before: Optional[int] = row[0] if row and row[0] else None

    new_count = 0
    pages = 0
    finished = False

    while pages < max_pages and not finished:
        html = fetch_page(username, before, proxy_pool)
        if html is None:
            break

        soup = BeautifulSoup(html, "html.parser")
        msg_elems = soup.select(".tgme_widget_message_wrap .tgme_widget_message")
        if not msg_elems:
            break

        page_msgs: list[ParsedMessage] = []
        for elem in msg_elems:
            pm = extract_message(elem, username)
            if pm:
                page_msgs.append(pm)
        if not page_msgs:
            break

        page_inserted = 0
        for pm in page_msgs:
            # Date filter: skip messages older than cutoff
            try:
                msg_dt = parse_iso_utc(pm.timestamp)
                if msg_dt < cutoff_date:
                    continue
            except ValueError:
                pass

            cursor.execute(
                """
                INSERT OR IGNORE INTO messages
                (channel_username, msg_id, timestamp, text, views, forwarded_from, forwarded_from_url,
                 reply_to_url, has_photo, has_video, has_document, has_poll, is_sticker)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (pm.channel_username, pm.msg_id, pm.timestamp, pm.text, pm.views,
                 pm.forwarded_from, pm.forwarded_from_url, pm.reply_to_url,
                 int(pm.has_photo), int(pm.has_video), int(pm.has_document),
                 int(pm.has_poll), int(pm.is_sticker)),
            )
            if cursor.rowcount > 0:
                new_count += 1
                page_inserted += 1
                for url in pm.urls:
                    domain = urlparse(url).netloc
                    cursor.execute(
                        "INSERT INTO urls (channel_username, msg_id, url, domain) VALUES (?, ?, ?, ?)",
                        (pm.channel_username, pm.msg_id, url, domain),
                    )

        min_id = min(pm.msg_id for pm in page_msgs)
        max_id = max(pm.msg_id for pm in page_msgs)
        earliest_ts = min(pm.timestamp for pm in page_msgs)
        latest_ts = max(pm.timestamp for pm in page_msgs)

        try:
            earliest_dt = parse_iso_utc(earliest_ts)
            if earliest_dt < cutoff_date:
                finished = True
        except ValueError:
            pass

        cursor.execute(
            """
            UPDATE channels
            SET earliest_scraped_msg_id = ?,
                earliest_scraped_date   = ?,
                latest_scraped_msg_id   = COALESCE(MAX(latest_scraped_msg_id, ?), ?),
                latest_scraped_date     = COALESCE(MAX(latest_scraped_date, ?), ?),
                messages_count          = messages_count + ?
            WHERE username = ?
            """,
            (min_id, earliest_ts, max_id, max_id, latest_ts, latest_ts, page_inserted, username),
        )
        conn.commit()

        if before == min_id:
            break
        before = min_id
        pages += 1
        time.sleep(uniform(*REQUEST_DELAY))

    cursor.execute(
        "UPDATE channels SET scrape_status = ?, scrape_completed_at = ? WHERE username = ?",
        ("completed" if finished else "partial",
         datetime.now(timezone.utc).isoformat(), username),
    )
    conn.commit()
    return new_count


def scrape_channel_worker(channel: dict, db_path: Path, cutoff_date: datetime,
                          proxy_pool: Optional[ProxyPool]) -> tuple[str, int]:
    conn = open_conn(db_path)
    try:
        n = scrape_channel(channel, conn, cutoff_date, proxy_pool)
        return channel["username"], n
    finally:
        conn.close()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", type=Path, required=True, help="Seed channels CSV path")
    p.add_argument("--db", type=Path, required=True, help="Output SQLite DB path")
    p.add_argument("--start-date", required=True, help="Cutoff YYYY-MM-DD (collect newer than this)")
    p.add_argument("--channels", nargs="*", default=None, help="Optional subset of usernames")
    p.add_argument("--limit", type=int, default=None, help="Limit number of channels (test)")
    p.add_argument("--proxies", type=Path, default=None, help="Path to proxies.txt")
    p.add_argument("--workers", type=int, default=1, help="Parallel channel workers (default 1)")
    args = p.parse_args(argv)

    cutoff = datetime.fromisoformat(args.start_date).replace(tzinfo=timezone.utc)

    channels = load_channels_from_csv(args.csv)
    if args.channels:
        wanted = {u.lstrip("@") for u in args.channels}
        channels = [c for c in channels if c["username"] in wanted]
    if args.limit:
        channels = channels[: args.limit]
    if not channels:
        logger.error("No channels loaded (check CSV and filters)")
        return 1

    proxies_list = load_proxies(args.proxies)
    proxy_pool = ProxyPool(proxies_list) if proxies_list else None
    logger.info(
        f"{len(channels)} channels | cutoff: {cutoff.isoformat()} | "
        f"proxies: {len(proxies_list)} | workers: {args.workers}"
    )

    init_db(args.db)

    total_new = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(scrape_channel_worker, ch, args.db, cutoff, proxy_pool): ch
            for ch in channels
        }
        pbar = tqdm(total=len(channels), desc="Channels", unit="ch")
        for future in as_completed(futures):
            ch = futures[future]
            try:
                username, n = future.result()
                tqdm.write(f"  {username:<30} +{n} new")
                total_new += n
            except Exception as e:
                logger.exception(f"FAILED {ch['username']}: {e}")
                err_conn = open_conn(args.db)
                err_conn.execute(
                    "UPDATE channels SET scrape_status = 'error', errors_count = errors_count + 1 WHERE username = ?",
                    (ch["username"],),
                )
                err_conn.commit()
                err_conn.close()
            pbar.update(1)
        pbar.close()

    logger.info(f"Done. Total new messages inserted: {total_new:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
