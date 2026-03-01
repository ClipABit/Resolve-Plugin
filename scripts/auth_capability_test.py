"""
Test script to verify auth capabilities in Resolve environment.
Run this from within Resolve to check if PKCE auth flow will work.
"""
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
def test_browser_open() -> dict:
    """Test 1: Can we open the system browser?"""
    result = {"test": "browser_open", "success": False, "message": ""}
    try:
        success = webbrowser.open("https://www.google.com")
        result["success"] = success
        result["message"] = "Browser opened successfully" if success else "webbrowser.open() returned False"
    except Exception as e:
        result["message"] = f"Exception: {e}"
    return result


def test_localhost_bind() -> dict:
    """Test 2: Can we bind to 127.0.0.1?"""
    result = {"test": "localhost_bind", "success": False, "message": "", "port": None}
    
    try:
        server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        port = server.server_address[1]
        result["port"] = port
        result["message"] = f"Successfully bound to 127.0.0.1:{port}"
        result["success"] = True
        server.server_close()
        
    except Exception as e:
        result["message"] = f"Exception: {e}"
    
    return result


def run_all_tests():
    """Run all capability tests and print results."""
    print("=" * 50)
    print("Auth Capability Tests")
    print("=" * 50)
    
    tests = [test_browser_open, test_localhost_bind]
    all_passed = True
    
    for test_fn in tests:
        result = test_fn()
        status = "PASS" if result["success"] else "FAIL"
        print(f"\n[{status}] {result['test']}")
        print(f"  Message: {result['message']}")
        if result.get("port"):
            print(f"  Port: {result['port']}")
        if not result["success"]:
            all_passed = False
    
    print("\n" + "=" * 50)
    if all_passed:
        print("All tests PASSED - PKCE auth flow should work!")
    else:
        print("Some tests FAILED - may need Device Code flow instead")
    print("=" * 50)
    
    return all_passed


if __name__ == "__main__":
    run_all_tests()
