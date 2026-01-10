import sys
from pathlib import Path
# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.database import SessionLocal, engine, Base
from src.database.models import User
from src.auth.auth import get_password_hash

# Create tables
Base.metadata.create_all(bind=engine)

db = SessionLocal()

email = "admin@healops.ai"
password = "admin"

existing_user = db.query(User).filter(User.email == email).first()
if not existing_user:
    hashed_password = get_password_hash(password)
    user = User(email=email, hashed_password=hashed_password)
    db.add(user)
    db.commit()
    print(f"User {email} created successfully.")
else:
    print(f"User {email} already exists.")

db.close()
