import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

os.environ["OPENAI_API_URL"] = "http://mock.test/v1"
os.environ["OPENAI_API_TOKEN"] = "tok"

from nisa_tracker import app, db

fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
db.DB_PATH = path
db.init_db()
isin, _ = next(iter(db.FUNDS.items()))
db.update_prices(
    [
        {"isin": isin, "date": "2024-01-31", "nav": 22000},
        {"isin": isin, "date": "2025-01-31", "nav": 24000},
        {"isin": isin, "date": "2026-01-31", "nav": 26000},
    ]
)
db.add_purchase("2024-01-01", isin, "NISA (つみたて)", 100_000, 22_000)
db.add_purchase("2025-01-01", isin, "NISA (成長)", 200_000, 24_000)
db.add_purchase("2026-01-01", isin, "特定/一般", 50_000, 26_000)

client = app.app.test_client()
sys.stdout.write(client.get("/", headers={"Accept-Language": "ja-JP"}).get_data(as_text=True))
sys.stdout.flush()