import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("CLAUDE_API_KEY")

client = anthropic.Anthropic(
    api_key=api_key,
)

message = client.messages.create(
    # model="claude-sonnet-4-5-20250929",
    model="claude-haiku-4-5-20251001",
    max_tokens=1024,
    temperature=0.0,
    messages=[
        {
            "content": "Hello, world",
            "role": "user",
        }
    ],
)
print(message.content)
