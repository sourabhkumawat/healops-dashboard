from typing import Optional
from pydantic import BaseModel, EmailStr

class UserBase(BaseModel):
    email: EmailStr

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    name: Optional[str] = None
    organization_name: Optional[str] = None

class UserResponse(UserBase):
    id: int
    role: str
    name: Optional[str] = None
    organization_name: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TestEmailRequest(BaseModel):
    recipient_email: str
