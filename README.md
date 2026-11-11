# Ask Your Database: Natural Language to SQL Engine

Type a question in plain English, see the generated SQL, run it against a real SQLite database, and view results in a table. Everything is on one page.

## Features
- Plain-English input → generated SQL shown before anything runs
- **Editable SQL** before execution (bonus)
- Live execution against a seeded SQLite database (4 tables, 1000+ rows)
- Results table with dynamic columns, "Showing N results", pagination (10/25/50/100 rows per page) and scrolling
- Query history for the session; click an entry to reload the question, SQL and results
- **CSV export** and **schema explorer** sidebar (bonus)
- Edge cases handled: mutation blocking, missing table/column, empty results, empty or too-short input, vague prompts, syntax errors, runaway queries

## Run locally

Requirements: Python 3.10+

```bash
# 1. (optional) create a virtual environment
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. install dependencies
pip install -r requirements.txt

# 3. add an AI key (free Gemini key: https://aistudio.google.com/apikey)
cp .env.example .env              # Windows: copy .env.example .env
#    open .env and paste your key after GEMINI_API_KEY=

# 4. create and seed the database
python seed.py

# 5. start the app
uvicorn app:app --reload
```

Open http://127.0.0.1:8000

> If `shop.db` does not exist, the app seeds it automatically on startup.
> With no API key, the app runs in **offline demo mode** using simple built-in rules, which is enough for the sample prompts. Add a key for full natural-language support.

## Technologies and why
| Tech | Why |
|---|---|
| **Python + FastAPI** | Small, fast, automatic request validation, easy to read |
| **SQLite** | Zero setup, a real SQL engine, and read-only mode is built in |
| **Gemini / Groq / Claude API (pick one)** | Converts English to SQL. Called via plain HTTP, so there are no heavy SDKs and the provider is swappable with one env variable |
| **Vanilla HTML/CSS/JS** | No build step; one file; easy to run and review |

## Database schema (e-commerce)
```
users(user_id PK, name, email UNIQUE, city, signup_date)
products(product_id PK, name, category, price, stock)
orders(order_id PK, user_id FK→users, order_date, status, order_total)
order_items(item_id PK, order_id FK→orders, product_id FK→products, quantity, unit_price)
```
Seeded rows: 80 users, 60 products (9 out of stock), 250 orders (about 30% in the last 30 days) and about 600 order items.

**Why this schema:** it is a classic one-to-many and many-to-many design (users → orders → items ← products). It naturally supports the lookups, date filters, counts and "top customers by spend" aggregations the task asks for. Indexes sit on the foreign keys and `order_date`.

## How it works
1. `POST /api/generate`: the user's question plus the live database schema go to the LLM with strict rules (one SELECT only, no markdown, output `MUTATION` for write requests). The generated SQL is returned and displayed.
2. `POST /api/execute`: the SQL is validated, then run on a **read-only** SQLite connection. Columns come from the actual result.
3. `GET /api/schema`: feeds the schema explorer.

### Safety (defence in depth)
1. Intent check on the English input (e.g. "delete all users" is refused before any AI call)
2. The prompt tells the LLM to return `MUTATION` for write requests
3. SQL validation: a single statement, must start with `SELECT`/`WITH`, and any `INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/ATTACH/PRAGMA…` is rejected
4. SQLite opened with `mode=ro`, so even a bypassed check cannot write
5. 5-second query timeout and a 5000-row cap

### Friendly errors
Raw database exceptions are translated to readable messages, for example *"The query uses a table called 'customers', which doesn't exist. Available tables: …"*.

## Assumptions and trade-offs
- Dates are stored as `TEXT` (`YYYY-MM-DD`), SQLite's convention; the LLM is told to use `date('now', …)`.
- Pagination is done in the browser (results are capped at 5000 rows). Server-side pagination would suit much larger data.
- The word-based keyword check may reject a read query that contains words like "update" inside a string. This is a deliberate safe-side choice, backed by the read-only connection.
- LLM output is never trusted: it goes through the same validation as manual SQL.
- Query history lives in browser memory, per session, as the task requires.

## Query speed and caching (bonus)
For repeated or similar queries I would add two cache layers. **(1) A question→SQL cache:** normalize the question (lowercase, trim, collapse spaces) and use it as the key, so repeated questions skip the slow, paid LLM call. A semantic cache using embeddings could match paraphrases such as "show users" and "list all users". **(2) A result cache:** key it on the normalized SQL plus a database version, with a short TTL, in an in-memory LRU or Redis, so identical queries skip execution. I'd also add indexes on frequently filtered or joined columns (already done for foreign keys and `order_date`) and check slow queries with `EXPLAIN QUERY PLAN`. **Trade-offs:** cached results can go stale after data changes (fixed with TTLs or invalidating on writes); memory grows (bounded by LRU size); semantic matching can return the wrong SQL for a similar-looking but different question; and caching adds complexity to the code.

## Project structure
```
app.py            FastAPI backend (generation, safety, execution, schema)
seed.py           Creates and seeds shop.db
index.html        Single-page UI
requirements.txt  Python dependencies
.env.example      API key template
```
