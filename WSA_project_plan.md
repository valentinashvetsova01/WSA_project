# WSA Project Plan: Coordinated Propaganda Networks in Russian Telegram

## 0. Objective

Исследовать экосистему русскоязычных политических Telegram-каналов: обнаружить координированные кластеры, проанализировать их сетевую структуру и контент, выявить «мостовые» каналы и паттерны распространения информации.

**Research questions:**
1. Какие сообщества (communities) существуют в сети русскоязычных политических каналов?
2. Есть ли признаки координированного поведения (одновременные публикации, одинаковые URL, массовые пересылки)?
3. Как различается контент (тональность, темы, упоминаемые сущности) между кластерами?
4. Какие каналы играют роль «мостов» между информационными пузырями?

---

## 1. Data Collection

### 1.1 Поиск каналов (seed list)
- **TGStat.com** — поиск по ключевым словам: `политика`, `новости`, `Россия`, `Украина`, `война`, `мобилизация`
- Категории: про-Кремль, анти-Кремль, нейтральные/новостные, военкоры, оппозиция
- Цель: **40–60 каналов** с >5K подписчиков
- Ручная первичная разметка: каждому каналу присваиваем категорию (pro/anti/neutral) на основе описания — это ground truth для валидации community detection

### 1.2 Сбор данных через Telethon
```
Для каждого канала собираем:
├── messages (text, date, views, forwards_count)
├── forwards (source_channel_id → target_channel_id)
├── shared URLs (извлекаем из текста)
├── media metadata (photo/video/document)
└── channel metadata (title, description, subscribers_count, creation_date)
```
- **Временной диапазон:** 3–6 месяцев (достаточно для анализа, реалистично по объёму)
- **Ожидаемый объём:** ~500K–2M сообщений
- Сохранение: SQLite или PostgreSQL (структурированные данные) + JSON backup

### 1.3 Preprocessing
- Очистка текста: удаление emoji, ссылок (сохраняя их отдельно), спецсимволов
- Дедупликация: exact match + near-duplicate detection (MinHash)
- Фильтрация: удаление каналов с <100 сообщений за период
- Извлечение URL: нормализация (убираем utm-параметры, раскрываем short links)

---

## 2. Network Analysis

> Покрывает лекции: **Graph Theory, Complex Networks, Metrics for SNA, Community Detection**

### 2.1 Построение графов

**Граф A — Forwarding Network (основной)**
- Directed weighted graph
- Nodes = каналы
- Edge (A → B) = канал B переслал сообщение из канала A
- Weight = количество пересылок за период
- Это прямой показатель информационного потока

**Граф B — URL Co-sharing Network**
- Undirected weighted graph
- Nodes = каналы
- Edge (A, B) = оба канала поделились одним и тем же URL
- Weight = количество общих URL (или TF-IDF cosine similarity по вектору URL)
- Показывает скрытую координацию: каналы могут не пересылать друг друга, но ссылаться на одни источники

**Граф C — Temporal Co-posting Network (🌟 wow factor)**
- Undirected weighted graph
- Edge (A, B) = каналы публикуют на одну тему в узком временном окне (< 30 мин)
- Выявляет координацию, даже если нет прямых пересылок и общих URL
- Метод: для каждой пары каналов считаем temporal correlation публикаций

### 2.2 Метрики SNA (для каждого графа)

**Node-level:**
- **Degree centrality** (in/out для directed) — кто больше всех пересылает / кого пересылают
- **Betweenness centrality** — «мосты» между кластерами
- **Eigenvector centrality** — влиятельность (связан с другими влиятельными)
- **PageRank** — кто главный источник информации
- **Clustering coefficient** — насколько соседи канала связаны между собой

**Graph-level:**
- Density, diameter, average path length
- Degree distribution → проверка на power law (scale-free network?)
- Reciprocity (в directed графе) — взаимные пересылки
- Assortativity — связываются ли каналы с похожими по размеру

**🌟 Wow factor:** сравнить метрики с теоретическими моделями (Erdős–Rényi, Barabási–Albert) — показать, что реальная сеть не случайна и имеет свойства complex network (small-world, scale-free).

### 2.3 Community Detection

- **Louvain algorithm** — основной метод, максимизация modularity
- **Infomap** — альтернативный метод (information-theoretic), для сравнения
- Сравнить обнаруженные communities с нашей ручной разметкой (pro/anti/neutral)
- Метрики качества: **Modularity score**, **NMI** (Normalized Mutual Information) между detected communities и ground truth labels

**🌟 Wow factor:** применить **overlapping community detection** (например, DEMON или BigCLAM) — показать, что некоторые каналы принадлежат нескольким сообществам одновременно (мостовые каналы).

### 2.4 Координация (CIB Detection)

- Для каждой пары каналов в одном community — посчитать **coordination score**:
  - `coord_score = α * forwarding_similarity + β * url_similarity + γ * temporal_similarity`
- Пары с аномально высоким score → кандидаты в координированные
- Визуализация: heatmap coordination scores внутри и между кластерами

---

## 3. Content Analysis

> Покрывает лекции: **Sentiment Analysis, NER, Linking, Disambiguation**

### 3.1 Sentiment Analysis

- **Модель:** `sismetanin/rubert-ru-sentiment-rusentiment` (fine-tuned RuBERT)
- Классификация: positive / negative / neutral
- Агрегация по каналу → средний sentiment score
- Агрегация по community → sentiment profile кластера
- **Temporal analysis:** sentiment timeline → как менялась тональность вокруг ключевых событий

**🌟 Wow factor:** aspect-based sentiment — не просто «позитивно/негативно», а sentiment к конкретным сущностям (Путин, Зеленский, НАТО, мобилизация). Реализация: NER → извлечение предложений с сущностью → sentiment этих предложений.

### 3.2 Emotion Analysis

- **Модель:** `cointegrated/rubert-tiny2-cedr-emotion-detection`
- Эмоции: joy, sadness, anger, fear, surprise, disgust
- Emotion profiles по кластерам → radar charts
- Сравнение: про-Кремль каналы используют больше anger/fear? Оппозиция — sadness?

### 3.3 Named Entity Recognition + Linking + Disambiguation

- **NER:** SpaCy `ru_core_news_lg` или DeepPavlov NER
- Извлечение: PERSON, ORG, GPE (geopolitical entity), EVENT
- **Entity Linking:** связывание упоминаний с Wikidata ID
  - «Путин», «ВВП», «президент» → Q7747 (Vladimir Putin)
  - Используем simple string matching + context-based disambiguation
- **Entity Disambiguation:** разрешение неоднозначностей
  - «Вагнер» → музыкальная группа Wagner? Группа Вагнера (ЧВК)? → контекст решает
- **Entity co-occurrence network:** граф, где ноды = сущности, рёбра = совместное упоминание в одном сообщении → какие сущности связаны в нарративе каждого кластера

**🌟 Wow factor:** сравнить entity networks между кластерами — про-Кремль каналы строят один нарратив (связи между сущностями), оппозиция — другой. Визуализация этого различия очень эффектна.

### 3.4 Topic Modeling

- **BERTopic** с RuBERT embeddings
- Автоматическое обнаружение тем в корпусе
- Topic distribution по кластерам → какие темы доминируют где
- Temporal topic evolution → какие темы появляются/исчезают

### 3.5 LLM-as-Judge (🌟 wow factor)

- На подвыборке (~500–1000 сообщений) используем GPT-4o / Claude для тонкой разметки:
  - Propaganda techniques (appeal to fear, ad hominem, whataboutism, loaded language...)
  - Framing: victim / aggressor / neutral framing
- Используем как дополнительный content analysis, а не как замену основным моделям
- Показываем inter-annotator agreement между LLM и (опционально) ручной разметкой

---

## 4. Visualization

> Самая весомая часть оценки: 3.5 из 8 баллов (Results + Visualization)

### 4.1 Network Visualizations
- **Основной граф:** Gephi (force-directed layout, ForceAtlas2)
  - Размер ноды = eigenvector centrality или subscribers
  - Цвет = detected community
  - Толщина ребра = forwarding weight
  - Экспорт как интерактивный HTML (Sigma.js plugin)
- **Ego networks** ключевых каналов — отдельные визуализации для top-5 по betweenness

### 4.2 Content Visualizations
- **Sentiment timeline:** линейный график sentiment по кластерам + вертикальные линии на ключевых событиях
- **Emotion radar charts:** по кластеру, наложение для сравнения
- **Word clouds:** по кластеру (после TF-IDF взвешивания, не просто частотность)
- **Entity co-occurrence networks:** отдельный граф для каждого кластера
- **Topic heatmap:** topics × clusters, цвет = доля темы в кластере

### 4.3 Coordination Visualizations
- **Heatmap:** coordination score между парами каналов
- **Temporal heatmap:** ось X = время (дни), ось Y = каналы, цвет = posting activity → визуально видны «волны» координированных публикаций
- **Sankey diagram:** информационный поток между кластерами

### 4.4 Summary Dashboard (🌟 wow factor)
- Plotly Dash или Streamlit: интерактивный дашборд со всеми визуализациями
- Фильтры по времени, по кластеру, по теме
- Это не обязательно, но сильно впечатляет на презентации

---

## 5. Распределение работы

### Участник A
- Data collection pipeline (Telethon скрипты)
- Network construction (графы A, B, C)
- SNA metrics + community detection
- Network visualizations (Gephi)

### Участник B
- Content analysis pipeline (sentiment, emotion, NER, topic modeling)
- LLM-as-judge разметка
- Entity linking + disambiguation
- Content visualizations (matplotlib/plotly)

### Совместно
- Определение seed list каналов
- Coordination analysis (пересечение network + content)
- Написание отчёта
- Подготовка презентации + dashboard

---

## 6. Timeline (примерный)

| Неделя | Задача |
|--------|--------|
| 1–2 | Seed list каналов, настройка Telethon, начало сбора данных |
| 3 | Preprocessing, построение графов, первичный EDA |
| 4–5 | Network analysis: метрики, community detection, координация |
| 5–6 | Content analysis: sentiment, emotion, NER, topic modeling |
| 7 | LLM-as-judge, entity linking, aspect-based sentiment |
| 8 | Визуализации, dashboard |
| 9 | Написание отчёта |
| 10 | Презентация |

---

## 7. Mapping на курс (для отчёта и презентации)

| Лекция курса | Что покрываем в проекте |
|---|---|
| Internet & Web Technologies | Telegram Bot API, Telethon, URL extraction & normalization |
| Graph Theory | Directed/undirected графы, weighted edges, paths, components |
| Social Media | Telegram как платформа, каналы vs группы, forwarding mechanism |
| Complex Networks | Power law degree distribution, small-world properties, scale-free test |
| Metrics for SNA | Degree, betweenness, eigenvector, PageRank, clustering coefficient, density |
| Community Detection | Louvain, Infomap, overlapping (DEMON), modularity, NMI validation |
| Sentiment Analysis | RuBERT sentiment, aspect-based sentiment, temporal sentiment |
| NER | SpaCy/DeepPavlov NER, entity types (PER/ORG/GPE/EVENT) |
| Linking & Disambiguation | Entity linking к Wikidata, контекстная disambiguation |

---

## 8. Tech Stack

```
Data:        Telethon, SQLite, TGStat
Network:     NetworkX, python-igraph, Gephi
NLP:         HuggingFace Transformers, SpaCy, BERTopic
LLM:         OpenAI API / Anthropic API (for LLM-as-judge)
Viz:         matplotlib, seaborn, Plotly, Pyvis, Gephi (Sigma.js export)
Dashboard:   Streamlit или Plotly Dash (опционально)
Report:      LaTeX или Google Docs
Presentation: PowerPoint (~15 мин)
```

---

## 9. Deliverables

1. **Отчёт** — Objective, Data, Models, Results (по структуре курса)
2. **Данные** — собранный корпус + построенные графы
3. **Код** — GitHub repo с README
4. **Презентация** — PowerPoint, ~15 минут, каждый представляет свою часть
5. **Google Drive папка** — `WSA_surname1_surname2`, расшарить на:
   - marco.viviani@unimib.it
   - d.mancino1@campus.unimib.it
   - m.braga@campus.unimib.it
