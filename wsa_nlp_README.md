# wsa_nlp.db — NLP results for the WSA corpus

A slim SQLite database with the output of three NLP tasks run by `nlp_pipeline.py`
over `wsa_data.db`. It is kept separate from the main corpus database so the heavy
result tables can be attached on demand without bloating `wsa_data.db`.

## Contents

| Table           | Rows        | Columns                                                                  |
|-----------------|------------:|--------------------------------------------------------------------------|
| `msg_sentiment` |    408,057  | `channel_username, msg_id, label, score`                                 |
| `msg_emotion`   |    408,057  | `channel_username, msg_id, label, score`                                 |
| `msg_entities`  |  3,298,181  | `channel_username, msg_id, text, label, start_char, end_char`            |
| `msg_ner_done`  |    408,057  | `channel_username, msg_id` (tracks which messages NER has already seen)  |

The key in every table is `(channel_username, msg_id)`, which joins 1-to-1 with
`messages` in `wsa_data.db`. About 1,900 messages are absent because the pipeline
skips rows where `text IS NULL` or `length(text) <= 10`.

## Labels and models

- **`msg_sentiment.label`** ∈ {`neutral`, `positive`, `negative`}
  model: `blanchefort/rubert-base-cased-sentiment-rusentiment`
- **`msg_emotion.label`** ∈ {`no_emotion`, `joy`, `sadness`, `surprise`, `fear`, `anger`}
  model: `cointegrated/rubert-tiny2-cedr-emotion-detection`
- **`msg_entities.label`** ∈ {`LOC`, `ORG`, `PER`} (plus rare spaCy types)
  model: spaCy `ru_core_news_lg` (3.8.0), active components `tok2vec + ner`

`score` is the softmax confidence of the predicted class (transformer tasks only).

## How to connect

### Option 1 — ATTACH to the existing `wsa_data.db` (recommended)

```python
import sqlite3
conn = sqlite3.connect("wsa_data.db")
conn.execute("ATTACH DATABASE 'wsa_nlp.db' AS nlp")

# sanity check
print(conn.execute("SELECT COUNT(*) FROM nlp.msg_sentiment").fetchone())

# join with messages
rows = conn.execute("""
    SELECT m.channel_username, m.msg_id, m.text, s.label, s.score
    FROM messages m
    JOIN nlp.msg_sentiment s
      ON s.channel_username = m.channel_username
     AND s.msg_id = m.msg_id
    WHERE s.label = 'negative'
    ORDER BY s.score DESC
    LIMIT 10
""").fetchall()
```

With pandas:

```python
import pandas as pd, sqlite3
conn = sqlite3.connect("wsa_data.db")
conn.execute("ATTACH DATABASE 'wsa_nlp.db' AS nlp")

df_sent = pd.read_sql("SELECT * FROM nlp.msg_sentiment", conn)
df_emo  = pd.read_sql("SELECT * FROM nlp.msg_emotion",   conn)
df_ent  = pd.read_sql("SELECT * FROM nlp.msg_entities",  conn)
```

### Option 2 — open `wsa_nlp.db` directly

If you only need the NLP results without message text:

```python
conn = sqlite3.connect("wsa_nlp.db")
top_entities = conn.execute("""
    SELECT text, label, COUNT(*) c
    FROM msg_entities
    GROUP BY text, label
    ORDER BY c DESC
    LIMIT 50
""").fetchall()
```

## Useful queries

```sql
-- Sentiment distribution
SELECT label, COUNT(*) FROM nlp.msg_sentiment GROUP BY label;
-- → neutral 388,176 | negative 11,447 | positive 8,434

-- Emotion distribution
SELECT label, COUNT(*) FROM nlp.msg_emotion GROUP BY label;
-- → no_emotion 375,069 | anger 15,469 | joy 10,120 | surprise 4,007 | fear 1,861 | sadness 1,531

-- Entity counts by type
SELECT label, COUNT(*) FROM nlp.msg_entities GROUP BY label;
-- → LOC 1,440,809 | ORG 1,050,609 | PER 806,763

-- Top entities per type
SELECT label, text, COUNT(*) c
FROM nlp.msg_entities
GROUP BY label, text
ORDER BY label, c DESC;

-- Sentiment polarity by channel
SELECT m.channel_username,
       AVG(CASE s.label WHEN 'positive' THEN 1 WHEN 'negative' THEN -1 ELSE 0 END) AS polarity
FROM messages m
JOIN nlp.msg_sentiment s USING (channel_username, msg_id)
GROUP BY m.channel_username
ORDER BY polarity DESC;
```

## Indexes

Joins and filters are fast — these indexes already exist:

- `msg_sentiment(channel_username, msg_id)` — PK
- `msg_emotion(channel_username, msg_id)` — PK
- `msg_entities(channel_username, msg_id)` — index
- `msg_entities(text)` — index (look up a specific name / place)
- `msg_entities(label)` — index
- `msg_ner_done(channel_username, msg_id)` — PK

## Reproduction

```bash
python nlp_pipeline.py --db wsa_data.db --tasks sentiment emotion ner \
    --batch-size 128 --n-process 4 --limit 0
```

Resume-safe: messages already processed are skipped via the per-task tables.
Reference timing (RTX 5060 Ti + 16 CPU): sentiment ≈ 19 min, emotion ≈ 3 min, NER ≈ 30 min.

## File

- size: 469,671,936 bytes (≈ 458 MB)
