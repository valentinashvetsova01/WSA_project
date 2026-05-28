"""
WSA NLP Pipeline

Runs sentiment, emotion, and NER over collected messages. Writes results
back to SQLite so downstream analysis (visualization, aspect-based sentiment)
can query directly.

Models used (downloaded on first run):
  - Sentiment: blanchefort/rubert-base-cased-sentiment-rusentiment (3-way pos/neg/neu)
  - Emotion:   cointegrated/rubert-tiny2-cedr-emotion-detection (6 emotions)
  - NER:       SpaCy ru_core_news_lg (PERSON / ORG / GPE / EVENT etc.)

Usage:
  python nlp_pipeline.py --db wsa_data.db --tasks sentiment emotion ner --batch-size 32 --limit 0
  # --limit 0 = process all messages
  # tasks can be any subset of: sentiment, emotion, ner

Resume-safe: skips messages already processed for each task.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


SCHEMA_ADDITIONS = """
CREATE TABLE IF NOT EXISTS msg_sentiment (
    channel_username TEXT,
    msg_id           INTEGER,
    label            TEXT,           -- 'positive' / 'negative' / 'neutral'
    score            REAL,           -- confidence of predicted class
    PRIMARY KEY (channel_username, msg_id)
);

CREATE TABLE IF NOT EXISTS msg_emotion (
    channel_username TEXT,
    msg_id           INTEGER,
    label            TEXT,           -- joy/sadness/anger/fear/surprise/no_emotion
    score            REAL,
    PRIMARY KEY (channel_username, msg_id)
);

CREATE TABLE IF NOT EXISTS msg_entities (
    channel_username TEXT,
    msg_id           INTEGER,
    text             TEXT,
    label            TEXT,           -- PER / ORG / LOC / MISC
    start_char       INTEGER,
    end_char         INTEGER
);

CREATE INDEX IF NOT EXISTS idx_ent_text ON msg_entities(text);
CREATE INDEX IF NOT EXISTS idx_ent_label ON msg_entities(label);
"""


SENTIMENT_MODEL = "blanchefort/rubert-base-cased-sentiment-rusentiment"
EMOTION_MODEL = "cointegrated/rubert-tiny2-cedr-emotion-detection"
SPACY_MODEL = "ru_core_news_lg"


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_ADDITIONS)
    conn.commit()


def load_messages(conn: sqlite3.Connection,
                  task: str,
                  limit: int = 0) -> list[tuple[str, int, str]]:
    """Load messages NOT yet processed for the given task."""
    task_table = {
        "sentiment": "msg_sentiment",
        "emotion":   "msg_emotion",
        "ner":       "msg_entities",
    }[task]

    if task == "ner":
        # NER doesn't have a 1-row-per-message contract; use a tracking table
        conn.execute("CREATE TABLE IF NOT EXISTS msg_ner_done (channel_username TEXT, msg_id INTEGER, PRIMARY KEY (channel_username, msg_id))")
        sql = """
          SELECT m.channel_username, m.msg_id, m.text
          FROM messages m
          LEFT JOIN msg_ner_done d
            ON d.channel_username = m.channel_username AND d.msg_id = m.msg_id
          WHERE m.text IS NOT NULL AND length(m.text) > 10 AND d.msg_id IS NULL
        """
    else:
        sql = f"""
          SELECT m.channel_username, m.msg_id, m.text
          FROM messages m
          LEFT JOIN {task_table} t
            ON t.channel_username = m.channel_username AND t.msg_id = m.msg_id
          WHERE m.text IS NOT NULL AND length(m.text) > 10 AND t.msg_id IS NULL
        """
    if limit > 0:
        sql += f" LIMIT {limit}"
    return conn.execute(sql).fetchall()


def run_sentiment(conn: sqlite3.Connection, batch_size: int, limit: int) -> int:
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification

    logger.info(f"Loading sentiment model: {SENTIMENT_MODEL}")
    tok = AutoTokenizer.from_pretrained(SENTIMENT_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(SENTIMENT_MODEL)
    device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device).eval()
    logger.info(f"Device: {device}")

    # Label mapping for blanchefort/rubert-base-cased-sentiment-rusentiment
    # id2label per model card: 0=NEUTRAL, 1=POSITIVE, 2=NEGATIVE
    id2label = {0: "neutral", 1: "positive", 2: "negative"}

    rows = load_messages(conn, "sentiment", limit)
    logger.info(f"Pending sentiment rows: {len(rows):,}")

    n_done = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [r[2][:512] for r in batch]  # truncate to model max
        inputs = tok(texts, padding=True, truncation=True, max_length=512, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            logits = model(**inputs).logits
            probs = torch.softmax(logits, dim=-1)
            best = torch.argmax(probs, dim=-1)
            scores = probs.gather(1, best.unsqueeze(1)).squeeze(1)

        for (ch, mid, _txt), pred, sc in zip(batch, best.tolist(), scores.tolist()):
            conn.execute(
                "INSERT OR REPLACE INTO msg_sentiment (channel_username, msg_id, label, score) VALUES (?, ?, ?, ?)",
                (ch, mid, id2label.get(int(pred), "unknown"), float(sc)),
            )
        conn.commit()
        n_done += len(batch)
        if (i // batch_size) % 20 == 0:
            logger.info(f"  sentiment progress: {n_done:,}/{len(rows):,}")
    return n_done


def run_emotion(conn: sqlite3.Connection, batch_size: int, limit: int) -> int:
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification

    logger.info(f"Loading emotion model: {EMOTION_MODEL}")
    tok = AutoTokenizer.from_pretrained(EMOTION_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(EMOTION_MODEL)
    device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device).eval()

    # cointegrated/rubert-tiny2-cedr-emotion-detection labels
    id2label = model.config.id2label if model.config.id2label else {
        0: "no_emotion", 1: "joy", 2: "sadness", 3: "surprise", 4: "fear", 5: "anger"
    }

    rows = load_messages(conn, "emotion", limit)
    logger.info(f"Pending emotion rows: {len(rows):,}")

    n_done = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [r[2][:512] for r in batch]
        inputs = tok(texts, padding=True, truncation=True, max_length=512, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            logits = model(**inputs).logits
            probs = torch.softmax(logits, dim=-1)
            best = torch.argmax(probs, dim=-1)
            scores = probs.gather(1, best.unsqueeze(1)).squeeze(1)

        for (ch, mid, _txt), pred, sc in zip(batch, best.tolist(), scores.tolist()):
            conn.execute(
                "INSERT OR REPLACE INTO msg_emotion (channel_username, msg_id, label, score) VALUES (?, ?, ?, ?)",
                (ch, mid, id2label.get(int(pred), str(pred)), float(sc)),
            )
        conn.commit()
        n_done += len(batch)
        if (i // batch_size) % 20 == 0:
            logger.info(f"  emotion progress: {n_done:,}/{len(rows):,}")
    return n_done


def run_ner(conn: sqlite3.Connection, batch_size: int, limit: int) -> int:
    import spacy

    logger.info(f"Loading SpaCy model: {SPACY_MODEL}")
    try:
        nlp = spacy.load(SPACY_MODEL)
    except OSError:
        logger.error(f"SpaCy model '{SPACY_MODEL}' not installed. Run:\n  python -m spacy download {SPACY_MODEL}")
        return 0

    rows = load_messages(conn, "ner", limit)
    logger.info(f"Pending NER rows: {len(rows):,}")

    n_done = 0
    # spaCy benefits from nlp.pipe for batching
    texts = [r[2][:5000] for r in rows]  # cap text length
    for i, doc in enumerate(nlp.pipe(texts, batch_size=batch_size)):
        ch, mid, _ = rows[i]
        for ent in doc.ents:
            conn.execute(
                "INSERT INTO msg_entities (channel_username, msg_id, text, label, start_char, end_char) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ch, mid, ent.text, ent.label_, ent.start_char, ent.end_char),
            )
        conn.execute(
            "INSERT OR IGNORE INTO msg_ner_done (channel_username, msg_id) VALUES (?, ?)",
            (ch, mid),
        )
        n_done += 1
        if n_done % 500 == 0:
            conn.commit()
            logger.info(f"  NER progress: {n_done:,}/{len(rows):,}")
    conn.commit()
    return n_done


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--tasks", nargs="+", choices=["sentiment", "emotion", "ner"], required=True)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--limit", type=int, default=0, help="0 = no limit")
    args = p.parse_args(argv)

    conn = sqlite3.connect(str(args.db), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)

    for task in args.tasks:
        logger.info(f"=== {task.upper()} ===")
        if task == "sentiment":
            n = run_sentiment(conn, args.batch_size, args.limit)
        elif task == "emotion":
            n = run_emotion(conn, args.batch_size, args.limit)
        elif task == "ner":
            n = run_ner(conn, args.batch_size, args.limit)
        else:
            continue
        logger.info(f"{task}: processed {n:,} messages")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
