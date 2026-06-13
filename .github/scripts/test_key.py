"""Quick test to verify the OpenAI API key works."""
import json
import urllib.request
import sys

api_key = "sk-pro...IEA"
print(f"Key length: {len(api_key)}")
print(f"Key prefix: {api_key[:15]}...")
print(f"Key suffix: ...{api_key[-10:]}")

req = urllib.request.Request(
    "https://api.openai.com/v1/models",
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
        model_names = [m["id"] for m in data["data"]]
        print(f"OK! {len(model_names)} models available")
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"HTTP {e.code}: {body}")
    sys.exit(1)
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
