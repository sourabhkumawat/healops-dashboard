from datetime import timedelta
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.models.models import User
from app.schemas.user import UserCreate, UserResponse, Token, UserUpdate, TestEmailRequest
from app.core.security import get_password_hash, verify_password, create_access_token, verify_token, ACCESS_TOKEN_EXPIRE_MINUTES
from fastapi.security import OAuth2PasswordBearer

router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    email = verify_token(token, credentials_exception)
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
    return user

@router.post("/register", response_model=dict)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.email == user_in.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_password = get_password_hash(user_in.password)
    new_user = User(email=user_in.email, hashed_password=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "User created successfully"}

@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user

@router.put("/me", response_model=UserResponse)
def update_me(
    update: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if update.name is not None:
        current_user.name = update.name
    if update.organization_name is not None:
        current_user.organization_name = update.organization_name

    db.commit()
    db.refresh(current_user)
    return current_user

@router.post("/test-email")
def test_email_endpoint(
    request_data: TestEmailRequest,
    current_user: User = Depends(get_current_user)
):
    # This assumes email_service is available in pythonpath or moved
    # For now we will try to import it from the root if not moved yet
    # Or ideally, move email_service to app/services/
    try:
        from email_service import send_test_email
    except ImportError:
        # Fallback if file is still in root
        import sys
        sys.path.append("apps/engine")
        from email_service import send_test_email

    try:
        success = send_test_email(
            recipient_email=request_data.recipient_email,
            subject="🧪 HealOps SMTP Test - Email Service Verification"
        )

        if success:
            return {
                "status": "success",
                "message": f"Test email sent successfully to {request_data.recipient_email}",
                "recipient": request_data.recipient_email
            }
        else:
            return {
                "status": "error",
                "message": "Failed to send test email. Please check SMTP configuration.",
                "recipient": request_data.recipient_email
            }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error sending test email: {str(e)}"
        )
