#!/usr/bin/env python3
"""Test login endpoint locally and on production"""
import requests
import sys

def test_login(base_url, email, password):
    """Test login endpoint"""
    url = f"{base_url}/auth/login"
    
    print(f"\n{'='*60}")
    print(f"Testing: {base_url}")
    print(f"Email: {email}")
    print(f"{'='*60}")
    
    try:
        # OAuth2PasswordRequestForm expects form data
        response = requests.post(
            url,
            data={
                "username": email,
                "password": password
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded"
            },
            timeout=10
        )
        
        print(f"Status Code: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        
        try:
            response_data = response.json()
            print(f"Response Body: {response_data}")
            
            if response.status_code == 200:
                if "access_token" in response_data:
                    print("✅ LOGIN SUCCESSFUL")
                    print(f"Token Type: {response_data.get('token_type')}")
                    print(f"Access Token (first 50 chars): {response_data.get('access_token', '')[:50]}...")
                    return True
                else:
                    print("❌ LOGIN FAILED: No access_token in response")
                    return False
            else:
                print(f"❌ LOGIN FAILED: {response_data.get('detail', 'Unknown error')}")
                return False
                
        except ValueError:
            print(f"Response Text: {response.text}")
            if response.status_code == 200:
                print("✅ LOGIN SUCCESSFUL (non-JSON response)")
                return True
            else:
                print(f"❌ LOGIN FAILED: Status {response.status_code}")
                return False
                
    except requests.exceptions.ConnectionError:
        print(f"❌ CONNECTION ERROR: Could not connect to {base_url}")
        print("   Make sure the server is running")
        return False
    except requests.exceptions.Timeout:
        print(f"❌ TIMEOUT: Request to {base_url} timed out")
        return False
    except Exception as e:
        print(f"❌ ERROR: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    email = "demo@healops.ai"
    password = "demo123"
    
    results = []
    
    # Test local
    print("\n" + "="*60)
    print("TESTING LOCAL SERVER")
    print("="*60)
    local_result = test_login("http://localhost:8000", email, password)
    results.append(("Local", local_result))
    
    # Test production
    print("\n" + "="*60)
    print("TESTING PRODUCTION SERVER")
    print("="*60)
    prod_result = test_login("https://engine.healops.ai", email, password)
    results.append(("Production", prod_result))
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for env, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{env:15} {status}")
    
    # Exit with error code if any test failed
    if not all(result for _, result in results):
        sys.exit(1)
