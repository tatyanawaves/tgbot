"""Quick test script to check if OpenRouter API key works."""
import requests
import config

url = "https://openrouter.ai/api/v1/chat/completions"

headers = {
    "Authorization": f"Bearer {config.LLM_API_KEY}",
    "HTTP-Referer": "https://github.com/test",
    "X-Title": "Test",
    "Content-Type": "application/json"
}

data = {
    "model": "arcee-ai/trinity-large-preview:free",
    "messages": [
        {"role": "user", "content": "Скажи привет одним словом."}
    ]
}

print(f"Using API key: {config.LLM_API_KEY[:10]}...{config.LLM_API_KEY[-5:]}")
print(f"Key length: {len(config.LLM_API_KEY)}")
print()

response = requests.post(url, headers=headers, json=data, timeout=30)
print(f"Status: {response.status_code}")
print(f"Response: {response.text}")
