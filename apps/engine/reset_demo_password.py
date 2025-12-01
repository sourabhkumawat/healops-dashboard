"""
Reset demo user password with the new bcrypt truncation fix
"""
from dotenv import load_dotenv
load_dotenv()

from database import SessionLocal
from models import User
from auth import get_password_hash

db = SessionLocal()

email = "demo@healops.ai"
new_password = "demo123"

print("=" * 60)
print("Resetting demo user password...")
print("=" * 60)

user = db.query(User).filter(User.email == email).first()

if user:
    # Update password with new hash (using truncation)
    user.hashed_password = get_password_hash(new_password)
    db.commit()
    print(f"✓ Password reset for: {email}")
    print(f"  New password: {new_password}")
    print("=" * 60)
else:
    print(f"✗ User not found: {email}")
    print("  Run setup_demo_user.py first")
    print("=" * 60)

db.close()
