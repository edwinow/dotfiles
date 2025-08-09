#!/usr/bin/env python3
import requests
import os

api_key = os.environ.get("OPENAI_API_KEY")
headers = {"Authorization": f"Bearer {api_key}"}

response = requests.get("https://api.openai.com/v1/models", headers=headers)
models = response.json()

print("Available GPT/O1 models:")
for model in models.get("data", []):
    model_id = model["id"]
    if "gpt" in model_id.lower() or "o1" in model_id:
        print(f"  - {model_id}")

print("\nTrying gpt-5:")
test_response = requests.post(
    "https://api.openai.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    json={
        "model": "gpt-5",
        "messages": [{"role": "user", "content": "test"}],
        "max_tokens": 10
    }
)
print(f"Status: {test_response.status_code}")
if test_response.status_code != 200:
    print(f"Error: {test_response.json().get('error', {}).get('message', 'Unknown error')}")