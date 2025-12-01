"""
Setup demo user and generate API key
"""
from dotenv import load_dotenv
load_dotenv()

from database import SessionLocal, engine, Base
from models import User, ApiKey
from auth import get_password_hash
from integrations import generate_api_key

# Create tables
Base.metadata.create_all(bind=engine)

db = SessionLocal()

# Demo user credentials
email = "demo@healops.ai"
password = "demo123"

print("=" * 60)
print("Setting up demo user...")
print("=" * 60)

# Create or get user
existing_user = db.query(User).filter(User.email == email).first()
if not existing_user:
    hashed_password = get_password_hash(password)
    user = User(email=email, hashed_password=hashed_password, role="admin")
    db.add(user)
    db.commit()
    db.refresh(user)
    print(f"✓ User created: {email}")
    user_id = user.id
else:
    print(f"✓ User already exists: {email}")
    user_id = existing_user.id

# Generate API key
full_key, key_hash, key_prefix = generate_api_key()

api_key = ApiKey(
    user_id=user_id,
    key_hash=key_hash,
    key_prefix=key_prefix,
    name="Demo API Key",
    scopes=["logs:write", "metrics:write"]
)

db.add(api_key)
db.commit()
db.refresh(api_key)

print("\n" + "=" * 60)
print("DEMO USER CREDENTIALS")
print("=" * 60)
print(f"Email:    {email}")
print(f"Password: {password}")
print("\n" + "=" * 60)
print("API KEY (save this - shown only once!)")
print("=" * 60)
print(f"{full_key}")
print("\n" + "=" * 60)
print(f"Key Prefix: {key_prefix}")
print(f"Created at: {api_key.created_at}")
print("=" * 60)

db.close()
