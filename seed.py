"""Creates and seeds shop.db (SQLite) with a realistic e-commerce schema.

Tables: users, products, orders, order_items  (all with 50+ rows)
Run:  python seed.py
"""
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "shop.db"

SCHEMA = """
CREATE TABLE users (
    user_id     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    city        TEXT NOT NULL,
    signup_date TEXT NOT NULL            -- YYYY-MM-DD
);

CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    category   TEXT NOT NULL,
    price      REAL NOT NULL CHECK (price > 0),
    stock      INTEGER NOT NULL CHECK (stock >= 0)
);

CREATE TABLE orders (
    order_id    INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(user_id),
    order_date  TEXT NOT NULL,           -- YYYY-MM-DD
    status      TEXT NOT NULL,           -- placed / shipped / delivered / cancelled
    order_total REAL NOT NULL DEFAULT 0
);

CREATE TABLE order_items (
    item_id    INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(order_id),
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity   INTEGER NOT NULL CHECK (quantity > 0),
    unit_price REAL NOT NULL
);

CREATE INDEX idx_orders_user  ON orders(user_id);
CREATE INDEX idx_orders_date  ON orders(order_date);
CREATE INDEX idx_items_order  ON order_items(order_id);
CREATE INDEX idx_items_prod   ON order_items(product_id);
"""

FIRST = ["Aarav", "Priya", "Rohan", "Ananya", "Vikram", "Sneha", "Arjun", "Kavya", "Rahul", "Meera",
         "Karthik", "Divya", "Aditya", "Neha", "Siddharth", "Pooja", "Manish", "Ishita", "Nikhil", "Riya",
         "Varun", "Shreya", "Harsh", "Tanvi", "Deepak", "Lakshmi", "Sandeep", "Anjali", "Gaurav", "Swati"]
LAST = ["Sharma", "Patel", "Reddy", "Iyer", "Nair", "Gupta", "Singh", "Kumar", "Das", "Mehta",
        "Joshi", "Rao", "Verma", "Chopra", "Bose", "Menon", "Pillai", "Shetty", "Kapoor", "Naidu"]
CITIES = ["Bengaluru", "Mumbai", "Delhi", "Chennai", "Hyderabad", "Pune", "Kolkata", "Kochi", "Jaipur", "Ahmedabad"]

# category -> (price range, item names)
CATALOG = {
    "Electronics": ((999, 45000), ["Wireless Mouse", "Bluetooth Speaker", "Noise Cancelling Headphones", "Smart Watch",
                                   "Power Bank 20000mAh", "USB-C Hub", "Mechanical Keyboard", "Webcam HD",
                                   "Portable SSD 1TB", "Tablet 10 inch"]),
    "Books": ((199, 1200), ["Python Crash Course", "Clean Code", "Atomic Habits", "Deep Work", "The Pragmatic Programmer",
                            "Data Structures in C", "Designing Data Apps", "Sapiens", "Zero to One", "Algorithms Unlocked"]),
    "Clothing": ((399, 4500), ["Cotton T-Shirt", "Denim Jeans", "Hoodie", "Running Shoes", "Formal Shirt",
                               "Track Pants", "Winter Jacket", "Leather Belt", "Sports Socks Pack", "Baseball Cap"]),
    "Home & Kitchen": ((299, 8000), ["Non-stick Pan", "Electric Kettle", "Mixer Grinder", "Water Bottle", "Lunch Box",
                                     "Table Lamp", "Bedsheet Set", "Air Fryer", "Knife Set", "Storage Containers"]),
    "Sports": ((299, 6000), ["Yoga Mat", "Dumbbell Set", "Cricket Bat", "Football", "Badminton Racket",
                             "Skipping Rope", "Resistance Bands", "Gym Bag", "Cycling Gloves", "Shuttlecock Tube"]),
    "Stationery": ((49, 900), ["Notebook A4", "Gel Pen Pack", "Desk Organizer", "Sticky Notes", "Highlighter Set",
                               "Geometry Box", "Planner 2026", "Whiteboard Markers", "File Folder", "Scientific Calculator"]),
}
STATUSES = ["placed", "shipped", "delivered", "delivered", "delivered", "cancelled"]


def main():
    random.seed(42)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    today = date.today()

    # users (80)
    users, used = [], set()
    while len(users) < 80:
        f, l = random.choice(FIRST), random.choice(LAST)
        if (f, l) in used:
            continue
        used.add((f, l))
        uid = len(users) + 1
        signup = today - timedelta(days=random.randint(30, 900))
        users.append((uid, f"{f} {l}", f"{f}.{l}{uid}@example.com".lower(),
                      random.choice(CITIES), signup.isoformat()))
    conn.executemany("INSERT INTO users VALUES (?,?,?,?,?)", users)

    # products (60) - some deliberately out of stock
    products, pid = [], 1
    for cat, ((lo, hi), names) in CATALOG.items():
        for n in names:
            price = round(random.uniform(lo, hi), 2)
            products.append([pid, n, cat, price, random.randint(5, 200)])
            pid += 1
    for p in random.sample(products, 9):
        p[4] = 0
    conn.executemany("INSERT INTO products VALUES (?,?,?,?,?)", products)
    price_of = {p[0]: p[3] for p in products}

    # orders (250) + order_items; ~30% of orders fall in the last 30 days
    item_id = 1
    for oid in range(1, 251):
        days_ago = random.randint(0, 30) if random.random() < 0.3 else random.randint(31, 365)
        odate = (today - timedelta(days=days_ago)).isoformat()
        uid = random.randint(1, 80)
        conn.execute("INSERT INTO orders VALUES (?,?,?,?,0)", (oid, uid, odate, random.choice(STATUSES)))
        total = 0.0
        for prod in random.sample(range(1, 61), random.randint(1, 4)):
            qty = random.randint(1, 3)
            total += qty * price_of[prod]
            conn.execute("INSERT INTO order_items VALUES (?,?,?,?,?)", (item_id, oid, prod, qty, price_of[prod]))
            item_id += 1
        conn.execute("UPDATE orders SET order_total=? WHERE order_id=?", (round(total, 2), oid))

    conn.commit()
    for t in ("users", "products", "orders", "order_items"):
        print(f"{t:12s} {conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]} rows")
    conn.close()
    print(f"Database created at {DB_PATH}")


if __name__ == "__main__":
    main()
