import os
import sys
from datetime import datetime
from fastapi.testclient import TestClient

# Add apps/engine to path
sys.path.append(os.path.join(os.getcwd(), "apps/engine"))

# Use local SQLite for testing
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(os.getcwd(), 'apps/engine/test.db')}"

from database import SessionLocal, engine, Base
from models import Integration
from crypto_utils import encrypt_token
from integrations.github_integration import GithubIntegration
import main

def test_token_decryption():
    print("Testing token decryption...")
    
    # Setup DB
    db = SessionLocal()
    
    try:
        # Create dummy integration
        original_token = "ghp_test_token_12345"
        encrypted = encrypt_token(original_token)
        
        # Check if user 1 exists, if not create dummy user? 
        # Integration requires user_id. Assuming user 1 exists or FK constraint might fail if enforced.
        # But let's try.
        
        integration = Integration(
            user_id=1,
            provider="GITHUB",
            name="Test GitHub",
            status="ACTIVE",
            access_token=encrypted,
            last_verified=datetime.utcnow()
        )
        db.add(integration)
        db.commit()
        db.refresh(integration)
        
        print(f"Created integration {integration.id} with encrypted token.")
        
        # Test GithubIntegration
        gh = GithubIntegration(integration_id=integration.id)
        
        if gh.access_token == original_token:
            print("SUCCESS: Token decrypted correctly.")
        else:
            print(f"FAILURE: Token mismatch. Expected {original_token}, got {gh.access_token}")
            
        # Cleanup
        db.delete(integration)
        db.commit()
        
    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        db.close()

def test_authorize_endpoint():
    print("\nTesting authorize endpoint...")
    
    client = TestClient(main.app)
    
    # Patch GITHUB_CLIENT_ID - REMOVED to test real env
    # main.GITHUB_CLIENT_ID = "test_client_id"
    
    print(f"Checking Client ID: {main.GITHUB_CLIENT_ID}")
    if not main.GITHUB_CLIENT_ID:
        print("FAILURE: GITHUB_CLIENT_ID not found in environment/app.")
        return
    
    response = client.get("/integrations/github/authorize", follow_redirects=False)
    
    if response.status_code == 307:
        location = response.headers['location']
        if f"client_id={main.GITHUB_CLIENT_ID}" in location and "scope=repo" in location:
            print(f"SUCCESS: Redirected correctly to {location}")
        else:
            print(f"FAILURE: Redirect URL incorrect: {location}")
    else:
        print(f"FAILURE: Expected 307, got {response.status_code}")

if __name__ == "__main__":
    test_token_decryption()
    test_authorize_endpoint()
