"""Natural Language -> SQL engine (FastAPI + SQLite).

Run:  uvicorn app:app --reload
Open: http://127.0.0.1:8000
"""
import os
import re
import sqlite3
import time
from pathlib import Path

import requests
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

BASE = Path(__file__).parent
DB_PATH = BASE / "shop.db"
MAX_ROWS = 5000          # hard cap on rows sent to the browser
QUERY_TIMEOUT_S = 5      # abort runaway queries


# ---------- config ----------
def load_env():
    env_file = BASE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()


def get_provider() -> str:
    p = os.getenv("LLM_PROVIDER", "").lower()
    if p:
        return p
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if os.getenv("GROQ_API_KEY"):
        return "groq"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "offline"


# ---------- errors ----------
class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message, self.status = message, status


app = FastAPI(title="NL to SQL Engine")


@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError):
    return JSONResponse({"error": exc.message}, status_code=exc.status)


@app.on_event("startup")
def ensure_db():
    if not DB_PATH.exists():
        import seed
        seed.main()


# ---------- database helpers ----------
def ro_conn() -> sqlite3.Connection:
    """Read-only connection: even if validation is bypassed, writes are impossible."""
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def schema_ddl() -> str:
    with ro_conn() as c:
        rows = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return "\n".join(r[0] for r in rows)


def table_names() -> list[str]:
    with ro_conn() as c:
        return [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def friendly_db_error(e: Exception) -> str:
    m = str(e)
    if (r := re.search(r"no such table: (\S+)", m)):
        return (f"The query uses a table called '{r.group(1)}', which doesn't exist. "
                f"Available tables: {', '.join(table_names())}.")
    if (r := re.search(r"no such column: (\S+)", m)):
        return f"The query uses a column called '{r.group(1)}', which doesn't exist. Check the Schema panel for column names."
    if "ambiguous column name" in m:
        return "A column name is ambiguous (it exists in more than one table). Prefix it with the table name, e.g. users.name."
    if "syntax error" in m or "incomplete input" in m:
        return "The SQL has a syntax error. Please check the query and try again."
    if "interrupted" in m:
        return f"The query took longer than {QUERY_TIMEOUT_S} seconds and was stopped. Try a narrower question."
    if "attempt to write a readonly database" in m:
        return "Only read queries are permitted."
    return f"The database could not run this query: {m}"


# ---------- safety ----------
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|attach|detach|pragma|vacuum|reindex)\b", re.I)
MUTATION_INTENT = re.compile(
    r"^\s*(please\s+|can you\s+|could you\s+|i want to\s+|i need to\s+)?"
    r"(delete|drop|insert|update|remove|truncate|alter|erase|wipe|modify|change)\b", re.I)
READ_ONLY_MSG = "Only read queries (SELECT) are permitted. This request would change the data, so it was not executed."


def validate_sql(sql: str) -> str:
    s = sql.strip().rstrip(";").strip()
    if not s:
        raise ApiError("There is no SQL to run. Describe the data you need first.")
    if ";" in s:
        raise ApiError("Only a single SQL statement can be run at a time.")
    if FORBIDDEN.search(s):
        raise ApiError(READ_ONLY_MSG)
    if not re.match(r"^(select|with)\b", s, re.I):
        raise ApiError("This doesn't look like a valid SELECT query. A query must start with SELECT (or WITH).")
    return s


# ---------- SQL generation ----------
SYSTEM_PROMPT = """You are an expert SQLite analyst. Convert the user's request into ONE SQLite SELECT query.

Rules:
- Use ONLY the tables and columns in the schema below.
- Output ONLY the raw SQL. No explanation, no markdown, no code fences.
- The query must be read-only. If the user asks to insert, update, delete, drop, alter or otherwise change data, output exactly: MUTATION
- If the request is vague, make the most reasonable interpretation and still output a query.
- If the request cannot be answered from this schema at all, output exactly: UNANSWERABLE
- Dates are stored as TEXT in YYYY-MM-DD. Use SQLite date functions, e.g. date('now', '-30 days').
- Give computed columns clear aliases (e.g. total_spend).

Schema:
{schema}
"""


def call_llm(provider: str, question: str) -> str:
    system = SYSTEM_PROMPT.format(schema=schema_ddl())
    try:
        if provider == "gemini":
            model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": os.environ["GEMINI_API_KEY"]},
                json={"system_instruction": {"parts": [{"text": system}]},
                      "contents": [{"parts": [{"text": question}]}],
                      "generationConfig": {"temperature": 0}},
                timeout=30)
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        if provider == "groq":
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
                json={"model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"), "temperature": 0,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": question}]},
                timeout=30)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        if provider == "anthropic":
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                json={"model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"), "max_tokens": 600,
                      "temperature": 0, "system": system,
                      "messages": [{"role": "user", "content": question}]},
                timeout=30)
            r.raise_for_status()
            return r.json()["content"][0]["text"]
    except KeyError:
        raise ApiError(f"API key for '{provider}' is missing. Check your .env file.", 500)
    except requests.HTTPError as e:
        code = e.response.status_code
        if code == 429:
            raise ApiError("The AI service is rate-limited (free tier). Wait 30 seconds and try again.", 429)
        if code >= 500:
            raise ApiError("The AI service is busy right now. Try again in a few seconds.", 503)
        raise ApiError(f"The AI service returned error {code}: {e.response.text[:200]}", 502)
    except requests.RequestException as e:
        raise ApiError(f"Could not reach the AI service ({type(e).__name__}). Check your internet connection.", 502)
    raise ApiError(f"Unknown LLM_PROVIDER '{provider}'. Use gemini, groq or anthropic.", 500)


def clean_sql(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:sql)?\s*|\s*```$", "", text, flags=re.I).strip()
    return text


def offline_sql(q: str) -> str | None:
    """Tiny rule-based fallback so the app runs without an API key (demo only)."""
    ql = q.lower()
    if (m := re.search(r"top\s+(\d+)", ql)) and re.search(r"customer|user", ql) and re.search(r"spen|revenue|total|order", ql):
        return ("SELECT u.user_id, u.name, ROUND(SUM(o.order_total), 2) AS total_spend\n"
                "FROM users u\nJOIN orders o ON o.user_id = u.user_id\n"
                f"GROUP BY u.user_id, u.name\nORDER BY total_spend DESC\nLIMIT {int(m.group(1))}")
    if "out of stock" in ql or "out-of-stock" in ql:
        if re.search(r"how many|count|number", ql):
            return "SELECT COUNT(*) AS out_of_stock_products FROM products WHERE stock = 0"
        return "SELECT * FROM products WHERE stock = 0"
    if (m := re.search(r"last\s+(\d+)\s+days", ql)) and "order" in ql:
        return f"SELECT * FROM orders WHERE order_date >= date('now', '-{int(m.group(1))} days') ORDER BY order_date DESC"
    for t in table_names():
        if re.search(rf"\b{t}\b|\b{t.rstrip('s')}s?\b", ql) and re.search(r"list|show|all|display|get", ql):
            return f"SELECT * FROM {t}"
    return None


class GenerateIn(BaseModel):
    question: str


class ExecuteIn(BaseModel):
    sql: str


@app.post("/api/generate")
def generate(body: GenerateIn):
    q = body.question.strip()
    if len(q) < 4:
        raise ApiError("Please describe the data you need, e.g. \"Show all orders from the last 30 days\".")
    if MUTATION_INTENT.search(q):
        raise ApiError(READ_ONLY_MSG)

    provider = get_provider()
    if provider == "offline":
        sql = offline_sql(q)
        if not sql:
            raise ApiError("Offline demo mode (no API key set) only understands simple requests. "
                           "Add GEMINI_API_KEY, GROQ_API_KEY or ANTHROPIC_API_KEY to your .env file for full AI support.")
        note = "Generated by built-in rules (offline demo mode, no AI key configured)."
    else:
        raw = clean_sql(call_llm(provider, q))
        if raw.upper().startswith("MUTATION"):
            raise ApiError(READ_ONLY_MSG)
        if raw.upper().startswith("UNANSWERABLE"):
            raise ApiError("That request can't be answered from this database. Check the Schema panel to see what data is available.")
        sql = raw
        note = f"Generated by {provider}. Review the SQL below before running."
    return {"sql": sql, "note": note, "provider": provider}


@app.post("/api/execute")
def execute(body: ExecuteIn):
    sql = validate_sql(body.sql)
    start = time.time()
    try:
        conn = ro_conn()
        conn.set_progress_handler(lambda: 1 if time.time() - start > QUERY_TIMEOUT_S else 0, 100000)
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS + 1)
        conn.close()
    except sqlite3.Error as e:
        raise ApiError(friendly_db_error(e))
    truncated = len(rows) > MAX_ROWS
    rows = [list(r) for r in rows[:MAX_ROWS]]
    return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated,
            "elapsed_ms": round((time.time() - start) * 1000, 1)}


@app.get("/api/schema")
def schema():
    out = []
    with ro_conn() as c:
        for t in table_names():
            cols = [{"name": r[1], "type": r[2], "pk": bool(r[5])} for r in c.execute(f"PRAGMA table_info({t})")]
            out.append({"table": t, "rows": c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], "columns": cols})
    return {"tables": out, "provider": get_provider()}


@app.get("/")
def index():
    return FileResponse(BASE / "index.html")