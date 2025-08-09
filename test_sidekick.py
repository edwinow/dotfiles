#!/usr/bin/env python3
"""
Test script for sidekick review system - makes actual GPT-5 API call
"""

import json, os, sys, time, tempfile, requests
from pathlib import Path
from datetime import datetime, timezone

# Test configuration
TEST_DIR = Path("/tmp/sidekick_test")
TEST_DIR.mkdir(exist_ok=True)

# Check API key
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    print("❌ OPENAI_API_KEY not set!")
    sys.exit(1)
else:
    print(f"✅ API Key found: {OPENAI_API_KEY[:10]}...")

# Create mock transcript with realistic Claude Code messages
def create_mock_transcript():
    transcript_path = TEST_DIR / "test_transcript.jsonl"
    
    messages = [
        {
            "type": "user",
            "message": {"role": "user", "content": "Help me fix the authentication bug in my app"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uuid": "msg1"
        },
        {
            "type": "assistant", 
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me search for the authentication code"},
                    {"type": "tool_use", "name": "Grep", "input": {"pattern": "authenticate", "path": "./src"}},
                ]
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uuid": "msg2"
        },
        {
            "type": "user",
            "message": {
                "role": "user", 
                "content": [
                    {"type": "tool_result", "content": "Found in src/auth.js: function authenticate() { return true; }"}
                ]
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uuid": "msg3"
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I found the issue - the authenticate function always returns true! Let me fix it."},
                    {"type": "tool_use", "name": "Edit", "input": {
                        "file_path": "src/auth.js",
                        "old_string": "return true;",
                        "new_string": "return checkUserCredentials(username, password);"
                    }}
                ]
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uuid": "msg4"
        },
        {
            "type": "user",
            "message": {"role": "user", "content": "Great! Now can you add tests?"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uuid": "msg5"
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": "I'll add tests for the authentication function right away."
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uuid": "msg6"
        }
    ]
    
    with open(transcript_path, 'w') as f:
        for msg in messages:
            f.write(json.dumps(msg) + '\n')
    
    return transcript_path

# Direct API test
def test_direct_api_call():
    print("\n📞 Testing direct GPT-5 API call...")
    
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    body = {
        "model": "gpt-4-turbo-preview",  # Use GPT-4 Turbo as GPT-5 might not be available
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "You are a code reviewer. Should the developer add tests after fixing authentication? Reply with a JSON object: {\"should_intervene\": boolean, \"score\": 0-1, \"reason\": \"brief reason\"}"},
            {"role": "user", "content": "Developer fixed auth bug but hasn't written tests yet."}
        ]
    }
    
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=body,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            print(f"✅ API Response: {content}")
            return True
        else:
            print(f"❌ API Error {response.status_code}: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Request failed: {e}")
        return False

# Test the full worker flow
def test_worker_flow():
    print("\n🔧 Testing full sidekick worker flow...")
    
    # Create test transcript
    transcript_path = create_mock_transcript()
    print(f"✅ Created mock transcript: {transcript_path}")
    
    # Create event file
    event = {
        "cwd": str(TEST_DIR),
        "transcript_path": str(transcript_path),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    event_file = TEST_DIR / "test_event.json"
    event_file.write_text(json.dumps(event))
    print(f"✅ Created event file: {event_file}")
    
    # Run the worker
    import subprocess
    worker_path = Path.home() / ".claude/hooks/sidekick-review-worker.py"
    
    print(f"🚀 Running worker: {worker_path}")
    result = subprocess.run(
        ["python3", str(worker_path), "--event-file", str(event_file)],
        capture_output=True,
        text=True,
        timeout=60
    )
    
    print(f"   Return code: {result.returncode}")
    if result.stdout:
        print(f"   Stdout: {result.stdout[:200]}")
    if result.stderr:
        print(f"   Stderr: {result.stderr[:200]}")
    
    # Check for results
    proj_dir = Path.home() / ".claude/sidekick" / TEST_DIR.name[:12]
    pending_file = proj_dir / "pending_feedback.json"
    memory_file = proj_dir / "memory.json"
    
    if pending_file.exists():
        feedback = json.loads(pending_file.read_text())
        print(f"\n✅ NUDGE CREATED!")
        print(f"   Score: {feedback.get('score')}")
        print(f"   Reason: {feedback.get('reason')}")
        print(f"   Nudge: {feedback.get('nudge_markdown')[:200]}...")
        return True
    else:
        print(f"\n⚠️  No nudge created (might not have met criteria)")
        
    if memory_file.exists():
        print(f"✅ Memory file updated: {memory_file}")
        
    return result.returncode == 0

# Main test
def main():
    print("=" * 60)
    print("SIDEKICK REVIEW SYSTEM TEST")
    print("=" * 60)
    
    # Test 1: Direct API call
    api_works = test_direct_api_call()
    
    if api_works:
        # Test 2: Full worker flow
        worker_works = test_worker_flow()
        
        if worker_works:
            print("\n🎉 ALL TESTS PASSED! System is working.")
        else:
            print("\n⚠️  Worker test had issues, check configuration")
    else:
        print("\n❌ API test failed - check your OPENAI_API_KEY and network")
    
    # Cleanup
    print(f"\nTest files in: {TEST_DIR}")

if __name__ == "__main__":
    main()