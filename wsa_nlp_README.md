# wsa_nlp.db — NLP-результаты по корпусу WSA

Слим-БД с результатами трёх задач, отработанных скриптом `nlp_pipeline.py` поверх `wsa_data.db` 26.05.2026.

## Что внутри

| Таблица         | Строк      | Колонки                                                                                  |
|-----------------|-----------:|------------------------------------------------------------------------------------------|
| `msg_sentiment` |    408 057 | `channel_username, msg_id, label, score`                                                 |
| `msg_emotion`   |    408 057 | `channel_username, msg_id, label, score`                                                 |
| `msg_entities`  |  3 298 181 | `channel_username, msg_id, text, label, start_char, end_char`                            |
| `msg_ner_done`  |    408 057 | `channel_username, msg_id` (трекинг, какие сообщения NER уже видел)                      |

Ключ во всех таблицах: `(channel_username, msg_id)` — джойнится 1-в-1 с `messages` из `wsa_data.db` (отсутствует только ~1939 сообщений с `text IS NULL` или `length(text) <= 10`, их пайплайн пропускает).

### Метки

- `msg_sentiment.label` ∈ {`neutral`, `positive`, `negative`}
  модель `blanchefort/rubert-base-cased-sentiment-rusentiment`
- `msg_emotion.label` ∈ {`no_emotion`, `joy`, `sadness`, `surprise`, `fear`, `anger`}
  модель `cointegrated/rubert-tiny2-cedr-emotion-detection`
- `msg_entities.label` ∈ {`PER`, `ORG`, `LOC`} (плюс редкие из spaCy)
  модель `spaCy ru_core_news_lg` (3.8.0), активные компоненты `tok2vec + ner`

`score` — softmax-confidence предсказанного класса (transformer-задачи).

## Как подключиться

### Вариант 1 — ATTACH к существующей `wsa_data.db` (рекомендую)

```python
import sqlite3
conn = sqlite3.connect("wsa_data.db")
conn.execute("ATTACH DATABASE 'wsa_nlp.db' AS nlp")

# проверка
print(conn.execute("SELECT COUNT(*) FROM nlp.msg_sentiment").fetchone())

# джойн с messages
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

В pandas:

```python
import pandas as pd, sqlite3
conn = sqlite3.connect("wsa_data.db")
conn.execute("ATTACH DATABASE 'wsa_nlp.db' AS nlp")

df_sent = pd.read_sql("SELECT * FROM nlp.msg_sentiment", conn)
df_emo  = pd.read_sql("SELECT * FROM nlp.msg_emotion",   conn)
df_ent  = pd.read_sql("SELECT * FROM nlp.msg_entities",  conn)
```

### Вариант 2 — открыть `wsa_nlp.db` напрямую

Если нужны только сами NLP-результаты без текстов сообщений:

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

## Полезные запросы

```sql
-- Распределение сентимента
SELECT label, COUNT(*) FROM nlp.msg_sentiment GROUP BY label;
-- → neutral 388,176 | negative 11,447 | positive 8,434

-- Распределение эмоций
SELECT label, COUNT(*) FROM nlp.msg_emotion GROUP BY label;
-- → no_emotion 375,069 | anger 15,469 | joy 10,120 | surprise 4,007 | fear 1,861 | sadness 1,531

-- Топ-сущностей по типу
SELECT label, text, COUNT(*) c
FROM nlp.msg_entities
GROUP BY label, text
ORDER BY label, c DESC;

-- Сентимент по каналам
SELECT m.channel_username,
       AVG(CASE s.label WHEN 'positive' THEN 1 WHEN 'negative' THEN -1 ELSE 0 END) AS polarity
FROM messages m
JOIN nlp.msg_sentiment s USING (channel_username, msg_id)
GROUP BY m.channel_username
ORDER BY polarity DESC;
```

## Индексы

Уже созданы — джойны и фильтры быстрые:

- `msg_sentiment(channel_username, msg_id)` — PK
- `msg_emotion(channel_username, msg_id)` — PK
- `msg_entities(channel_username, msg_id)` — idx
- `msg_entities(text)` — idx (поиск по конкретному имени/топониму)
- `msg_entities(label)` — idx
- `msg_ner_done(channel_username, msg_id)` — PK

## Воспроизведение

```
python nlp_pipeline.py --db wsa_data.db --tasks sentiment emotion ner \
    --batch-size 128 --n-process 4 --limit 0
```

Resume-safe: пропускает уже обработанные сообщения по соответствующим таблицам. На RTX 5060 Ti + 16 CPU: sentiment ~19 мин, emotion ~3 мин, NER ~30 мин.

## Проверка целостности

```
sha256: e44b4a1d38eb3ffe...  (полный хеш см. в сопроводительном сообщении)
size:   469,671,936 bytes
```
