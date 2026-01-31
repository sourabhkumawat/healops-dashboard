import sys
from pathlib import Path

# Load .env before importing database (which reads DATABASE_URL)
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.database import SessionLocal, engine, Base
from src.database.models import ApiKey
from src.integrations import generate_api_key

Base.metadata.create_all(bind=engine)
db = SessionLocal()

USER_ID = 4
full_key, key_hash, key_prefix = generate_api_key()

api_key = ApiKey(
    user_id=USER_ID,
    key_hash=key_hash,
    key_prefix=key_prefix,
    name="temp-script-key",
    scopes=["logs:write", "metrics:write"],
)
db.add(api_key)
db.commit()
db.refresh(api_key)

print(f"API Key: {full_key}")
print(f"Key Prefix: {key_prefix}")
print(f"User ID: {USER_ID}")

db.close()
