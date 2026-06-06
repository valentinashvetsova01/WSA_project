# Coordinated Propaganda Networks in Russian-Language Political Telegram

A network and content analysis of the Russian-language political Telegram ecosystem.
We collect a six-month corpus of public channels, reconstruct who amplifies whom,
detect signs of coordinated inauthentic behaviour, and contrast the language, emotion,
and framing of the opposing camps.

**Course:** Web and Social Media Search and Analysis · 2025 / 2026
**Authors:** Maksim Okulov (network & coordination) · Valentina Shvetsova (content & framing)

---

## Research questions

1. **Communities** — What communities exist in the network, and do they match political alignment?
2. **Coordination** — Are there measurable signs of coordinated, inauthentic behaviour?
3. **Content** — How do tone, emotion, topics, and entity framing differ between camps?
4. **Bridges** — Which channels, if any, connect otherwise separate bubbles?

---

## Corpus at a glance

| Metric | Value |
|---|---|
| Seed channels | **167** (89 pro · 55 anti · 12 mixed · 11 neutral) |
| Scrape status | 148 completed · 19 partial (short preview history) |
| Messages | **409,996** |
| URLs extracted | **743,445** |
| Collection period | 2025-11-23 → 2026-05-23 (≈ 6 months) |
| Collection method | Public `t.me/s/<channel>` web previews — no Telegram API, no account |

Labels (`lean`, `subcategory`) were assigned **by hand**, based on the institutional
type and origin of each channel (state media, Kremlin pool, pro-war "voenkory", and
provladimir propagandists → `pro`; independent / "foreign agent" outlets, opposition,
and Ukrainian channels → `anti`). The hand labels are **only used for validation** —
the network layout is driven purely by forwarding behaviour, and the pro/anti split
falls out on its own (community structure agrees with the labels at NMI ≈ 0.36).

---

## Project layout

```
WSA_project/
├── WSA_analysis_v3.ipynb        ★ Main submission notebook (full analysis, 122 cells)
│
├── Deliverables
│   ├── WSA_Report.docx              Formal written report
│   ├── WSA_Presentation.pptx        Presentation deck (15 slides)
│   └── WSA_Speech.pdf               Per-slide speaker script (~10 min)
│
├── Data
│   ├── wsa_data.db                  SQLite — channels, messages, urls (≈ 737 MB)
│   ├── wsa_nlp.db                   SQLite — NLP results (≈ 458 MB, see wsa_nlp_README.md)
│   └── wsa_seed_channels.csv        167 channels with manual lean / subcategory labels
│
├── Pipeline (source)
│   ├── wsa_scraper.py               t.me/s scraper (proxy rotation, parallel, WAL)
│   ├── nlp_pipeline.py              Sentiment + emotion + NER over the corpus
│   └── network_builder.py           Standalone graph builder (mirrors notebook logic)
│
├── Document generators
│   ├── build_report.js              → WSA_Report.docx   (Node, docx)
│   ├── build_deck.js                → WSA_Presentation.pptx (Node, pptxgenjs)
│   └── build_script.py              → WSA_Speech.pdf    (Python, reportlab)
│
├── outputs/                      Figures (.png), Gephi graphs (.graphml), cached data (.pkl)
│
├── Config / docs
│   ├── requirements.txt             Python dependencies
│   ├── proxies.txt                  Local proxy pool (127.0.0.1:60000-60019)
│   ├── WSA_project_plan.md          Original project plan
│   └── README.md                    This file
│
└── .venv/ · node_modules/       Environments (regenerable; not tracked)
```

---

## Work split

| Member | Focus | Notebook sections |
|---|---|---|
| **Maksim Okulov** | Scraping, preprocessing, network analysis, coordination detection | Data + Network + Coordination |
| **Valentina Shvetsova** | NLP content analysis, sentiment / emotion / entities / topics, framing | Content |
| **Joint** | Report, presentation, speaker script | Deliverables |

---

## Quickstart

```bash
# 1. Python environment
python -m venv .venv
.venv\Scripts\activate          # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt

# 2. Open the analysis
jupyter notebook WSA_analysis_v3.ipynb
```

The notebook reads `wsa_data.db` and attaches `wsa_nlp.db` for the content sections,
so no recomputation is required to reproduce the figures.

---

## Reproducing the pipeline

```bash
# Collect the corpus (resume-safe; uses proxies.txt)
python wsa_scraper.py

# Run NLP over all messages — writes results into wsa_nlp.db
python nlp_pipeline.py --db wsa_data.db --tasks sentiment emotion ner --batch-size 128
```

See `wsa_nlp_README.md` for the NLP schema, models, label sets, and example queries.

---

## Database schema (`wsa_data.db`)

| Table | Rows | Key columns |
|---|---:|---|
| `channels` | 167 | `username, display_name, lean, subcategory, subscribers, tgstat_rank, scrape_status` |
| `messages` | 409,996 | `channel_username, msg_id, timestamp, text, views, forwarded_from, reply_to_url, has_*` |
| `urls` | 743,445 | `channel_username, msg_id, url, domain` |

Top shared domains: `t.me` (385K), `max.ru` (117K), `vk.com` (12K), `youtube.com` (11K), `bit.ly` (11K).
NLP result tables (`msg_sentiment`, `msg_emotion`, `msg_entities`) are stored separately in
**`wsa_nlp.db`** — see its dedicated README.

---

## Methods

**Network (3 graphs).**
- *Graph A — Forwarding:* directed; an edge means one channel forwarded another's post.
- *Graph B — URL co-sharing:* undirected; two channels post the same link (latent coordination).
- *Graph C — Temporal co-posting:* the same link shared within 30 minutes (synchronised publishing).

Analysis: PageRank, betweenness, clustering coefficient, degree assortativity, power-law fit,
and comparison against Erdős–Rényi (random) and Barabási–Albert (scale-free) null models.
Community detection with Louvain, Infomap, and k-clique, validated against manual labels (NMI).

**Coordination.** A composite coordination score per channel pair (forwarding, shared URLs,
timing, near-duplicate text), with MinHash/LSH near-duplicate detection (Jaccard ≥ 0.85).

**Content.** RuBERT sentiment and emotion classifiers, spaCy `ru_core_news_lg` NER and
entity co-occurrence networks, BERTopic and TF-IDF themes, and an LLM-as-judge pass that
flags propaganda techniques. See `wsa_nlp_README.md` for exact models.

---

## Methodological note — mimicry detection

Seed-list construction surfaced **three channels that disguise their alignment**, confirmed
by manual content verification rather than self-presentation:

1. `@warfakes` - framed as a fact-checker, but the content is state-aligned counter-propaganda.
2. `@Rezident_ua` - poses as Ukrainian commentary; in fact a Russian information operation.
3. `@legitimniy` - same pattern (≈ 1M subscribers).

Two further instructive cases were labelled `mixed`: `@strelkovii` (Girkin - anti-Putin from
the ultra-right) and `@ksbchk` (Sobchak - systemic-liberal / controlled opposition). These
motivated a **two-pass labelling** approach: content verification overriding channel
self-presentation or directory categorisation.
