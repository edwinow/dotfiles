#!/usr/bin/env python3
import requests
import os
import json

api_key = os.environ.get("OPENAI_API_KEY")

print("Testing GPT-5 with high reasoning and 10000 max tokens...")

response = requests.post(
    "https://api.openai.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    json={
        "model": "gpt-5",
        "messages": [
            {"role": "system", "content": "You are an expert code reviewer. Analyze code changes and provide actionable feedback. Always respond with valid JSON."},
            {"role": "user", "content": """The developer fixed an authentication bug by changing 'return true' to 'return checkUserCredentials(username, password)' but hasn't written any tests yet. 

Should you intervene? Reply as JSON with this exact structure:
{
  "should_intervene": boolean,
  "score": number between 0 and 1,
  "reason": "brief explanation under 120 chars",
  "nudge_markdown": "actionable advice under 700 chars",
  "commands": ["list", "of", "commands"],
  "memory_update": "optional summary under 300 chars"
}"""}
        ],
        "max_completion_tokens": 10000,
        "reasoning_effort": "high"
    },
    timeout=60
)

print(f"Status: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    print(f"\n✅ GPT-5 Response:")
    print(content)
    
    # Check if it's valid JSON
    try:
        parsed = json.loads(content) if content else None
        if parsed:
            print(f"\n📊 Parsed successfully:")
            print(f"  - Should intervene: {parsed.get('should_intervene')}")
            print(f"  - Score: {parsed.get('score')}")
            print(f"  - Reason: {parsed.get('reason')}")
    except:
        print("\n⚠️  Response is not valid JSON")
    
    # Check token usage
    usage = data.get("usage", {})
    print(f"\nTokens used: {usage.get('total_tokens', 'N/A')}")
    print(f"Completion tokens: {usage.get('completion_tokens', 'N/A')}")
else:
    error = response.json()
    print(f"❌ Error: {json.dumps(error, indent=2)}")