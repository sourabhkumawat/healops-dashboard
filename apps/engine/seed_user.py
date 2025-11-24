from database import SessionLocal, engine, Base
from models import User
from auth import get_password_hash

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
