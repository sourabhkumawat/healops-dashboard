"""
Authentication Controller - Handles user authentication and profile management.
"""
from fastapi import HTTPException, status, Response, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import timedelta
import json

from src.database.database import get_db
from src.database.models import User
from src.auth import verify_password, get_password_hash, create_access_token, verify_token
from src.services.email.service import send_test_email
from src.api.controllers.base import get_user_id_from_request


class UserUpdateRequest(BaseModel):
    name: Optional[str] = None
    organization_name: Optional[str] = None


class TestEmailRequest(BaseModel):
    recipient_email: str


class RegisterRequest(BaseModel):
    email: str
    password: str


def get_current_user(request: Request, db: Session = Depends(get_db)):
    """Get current user from JWT token"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise credentials_exception
    
    token = auth_header.replace("Bearer ", "").strip()
    email = verify_token(token, credentials_exception)
    
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
    
    return user


class AuthController:
    """Controller for authentication and user management."""
    
    @staticmethod
    def register(user_data: RegisterRequest, db: Session):
        """Register a new user."""
        email = user_data.email
        password = user_data.password
        
        if not email or not password:
            raise HTTPException(status_code=400, detail="Email and password required")
            
        existing_user = db.query(User).filter(User.email == email).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="Email already registered")
            
        hashed_password = get_password_hash(password)
        new_user = User(email=email, hashed_password=hashed_password)
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        return {"message": "User created successfully"}
    
    @staticmethod
    def login(form_data: OAuth2PasswordRequestForm, db: Session):
        """Authenticate user and return access token."""
        user = db.query(User).filter(User.email == form_data.username).first()
        if not user or not verify_password(form_data.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        access_token_expires = timedelta(minutes=30)
        access_token = create_access_token(
            data={"sub": user.email}, expires_delta=access_token_expires
        )
        # Return token in both body (standard OAuth2) and Authorization header (for convenience)
        return Response(
            content=json.dumps({"access_token": access_token, "token_type": "bearer"}),
            media_type="application/json",
            headers={"Authorization": f"Bearer {access_token}"}
        )
    
    @staticmethod
    def get_me(current_user: User):
        """Get current user information."""
        return {
            "id": current_user.id,
            "email": current_user.email,
            "role": current_user.role,
            "name": current_user.name,
            "organization_name": current_user.organization_name,
            "created_at": current_user.created_at.isoformat() if current_user.created_at else None
        }
    
    @staticmethod
    def update_me(update: UserUpdateRequest, current_user: User, db: Session):
        """Update current user information."""
        if update.name is not None:
            current_user.name = update.name
        if update.organization_name is not None:
            current_user.organization_name = update.organization_name
        
        db.commit()
        db.refresh(current_user)
        
        return {
            "id": current_user.id,
            "email": current_user.email,
            "role": current_user.role,
            "name": current_user.name,
            "organization_name": current_user.organization_name,
            "created_at": current_user.created_at.isoformat() if current_user.created_at else None
        }
    
    @staticmethod
    def test_email(request_data: TestEmailRequest):
        """Test email functionality by sending a test email."""
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
