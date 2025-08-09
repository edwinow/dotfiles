#!/usr/bin/env python3
import requests
import os
import json

api_key = os.environ.get("OPENAI_API_KEY")

print("Testing GPT-5 with correct parameters...")

# Try without temperature and with max_completion_tokens
response = requests.post(
    "https://api.openai.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    json={
        "model": "gpt-5",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant. Reply with a JSON object."},
            {"role": "user", "content": 'Should tests be added after fixing a bug? Reply as JSON: {"answer": "yes or no", "reason": "brief"}'}
        ],
        "max_completion_tokens": 100
    }
)

print(f"Status: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    print(f"✅ GPT-5 Response:\n{content}")
    
    # Check token usage
    usage = data.get("usage", {})
    print(f"\nTokens used: {usage.get('total_tokens', 'N/A')}")
else:
    print(f"❌ Error: {response.json()}")