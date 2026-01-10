import sys
from pathlib import Path
# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.database import SessionLocal, engine, Base
from src.database.models import User, ApiKey
from src.auth.auth import get_password_hash
from src.integrations import generate_api_key

# Create tables
Base.metadata.create_all(bind=engine)

db = SessionLocal()

email = "ech@spacesos.com "
password = "spacesos"

# Check if user exists
existing_user = db.query(User).filter(User.email == email).first()
if existing_user:
    print(f"User {email} already exists.")
    user = existing_user
else:
    hashed_password = get_password_hash(password)
    user = User(email=email, hashed_password=hashed_password)
    db.add(user)
    db.commit()
    db.refresh(user)
    print(f"User {email} created successfully.")

# Generate API key for the user
full_key, key_hash, key_prefix = generate_api_key()

api_key = ApiKey(
    user_id=user.id,
    key_hash=key_hash,
    key_prefix=key_prefix,
    name="bot-api-key",
    scopes=["logs:write", "metrics:write"]
)

db.add(api_key)
db.commit()
db.refresh(api_key)

print(f"\nAPI Key generated successfully!")
print(f"API Key: {full_key}")
print(f"Key Prefix: {key_prefix}")
print(f"User ID: {user.id}")

db.close()








