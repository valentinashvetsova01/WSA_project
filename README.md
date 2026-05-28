# WSA Project — Coordinated Propaganda Networks in Russian Telegram

## Структура проекта

```
WSA_project/
├── WSA_project_plan.md            # Оригинальный план проекта
├── WSA_analysis.ipynb             # ★ Главный ноутбук-сабмишен (Member A + Member B)
├── WSA_analysis.py                # jupytext-paired source (можно править в редакторе)
│
├── wsa_seed_channels.csv          # 167 каналов с ручной разметкой (lean/subcategory)
├── wsa_data.db                    # SQLite БД, 410K сообщений за 6 месяцев (720 MB)
│
├── wsa_scraper.py                 # [done] Скрейпер t.me/s (proxies + parallel + WAL)
├── nlp_pipeline.py                # [for Member B] sentiment + emotion + NER
├── network_builder.py             # [auxiliary] standalone версия графов (можно игнорить, в ноутбуке всё есть)
│
├── proxies.txt                    # Прокси (локалка 127.0.0.1:60000-60019)
├── scraper.log                    # Лог последнего прогона скрейпера
│
├── outputs/                       # Будет наполнен после прогона ноутбука
│   ├── *.png                      # Графики
│   ├── *.graphml                  # Для Gephi
│   └── *.parquet                  # Cached intermediate data
│
└── .venv/                         # Python окружение (torch, transformers, spacy, networkx, и т.д.)
```

## Распределение работы

| Участник | Секции | Файлы |
|---|---|---|
| **Member A** | 1.3 Preprocessing, 2.x Network analysis | `WSA_analysis.ipynb` cells 1.3.x — 2.4.x |
| **Member B** | 3.x Content analysis, контентная виз | `WSA_analysis.ipynb` cells 3.x (скелеты) + `nlp_pipeline.py` |
| **Совместно** | Network viz в Gephi, отчёт, презентация | external (Gephi), отчёт |

## Quickstart

```bash
cd ~/Desktop/WSA_project
source .venv/bin/activate
jupyter notebook WSA_analysis.ipynb
```

## Для Member B (как стартовать с NLP)

`nlp_pipeline.py` уже умеет sentiment + emotion + NER на готовой БД:

```bash
source .venv/bin/activate
# Запустит sentiment + emotion + NER на всех 410K сообщений (~30-60 минут)
python nlp_pipeline.py --db wsa_data.db --tasks sentiment emotion ner --batch-size 32

# Результаты появятся в новых таблицах SQLite: msg_sentiment, msg_emotion, msg_entities
```

После этого в ноутбуке секции 3.x подхватывают данные из этих таблиц. Скелеты ячеек (TODO) — место где допиливать:
- Aggregations по каналу / community
- Aspect-based sentiment (sentiment к конкретным entities типа Путин, Зеленский, НАТО)
- Entity co-occurrence networks per cluster
- BERTopic — потребует `pip install bertopic` (тяжёлая зависимость, но рабочая)
- LLM-as-judge — потребует OpenAI/Anthropic API key

## Технические детали сбора

- **Период:** 2025-11-23 → 2026-05-23 (6 месяцев)
- **Каналы:** 167 (148 completed + 19 partial из-за коротких preview-историй)
- **Сообщения:** ~410K
- **Метод:** парсинг публичных `t.me/s/<channel>` страниц (без Telethon, без аккаунтов)
- **Schema:**
  - `channels` — метадата + скрейп-статус
  - `messages` — текст, timestamp, views, forwarded_from, reply, media flags
  - `urls` — все URL'ы из сообщений с доменами

## Методологически важные находки

В процессе seed list construction выявлено **3 случая мимикрии**, подтверждённых ручной верификацией контента (через WebFetch):

1. `@warfakes` — позиционируется как fact-checker, но контент = state-aligned counter-propaganda
2. `@Rezident_ua` — мимикрирует под украинский комментарий, реально = русская ИО
3. `@legitimniy` — то же самое (1M подписчиков)

Также:
- `@strelkovii` (Гиркин) — `mixed` lean: анти-Путин с ультра-правых, методологически ценный кейс
- `@ksbchk` (Собчак) — `mixed` lean: системный либерал / controlled opposition

Эти кейсы — отдельный нарратив для секции Methods в отчёте: «mimicry detection во время seed list construction мотивировало two-pass labeling с верификацией контента вместо channel self-presentation или directory categorization».
