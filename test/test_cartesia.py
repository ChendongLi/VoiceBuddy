import os
import sys
from pathlib import Path

from cartesia import Cartesia
from dotenv import load_dotenv

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from audio_config import config as audio_config

load_dotenv()

api_key = os.getenv("CARTESIA_API_KEY")
voice_id = os.getenv("CARTESIA_VOICE_ID")
print(f"Using Cartesia API Key: {api_key}, Voice ID: {voice_id}")
client = Cartesia(api_key=api_key)

output_format = audio_config.get_output_format()

chunk_iter = client.tts.bytes(
    model_id="sonic-3",
    transcript="I can't wait to see what you'll create!",
    voice={
        "mode": "id",
        "id": voice_id,
    },
    output_format=output_format,
)

output_path = Path(__file__).parent.parent / "assets" / "audio" / "outputs" / "sonic.wav"
output_path.parent.mkdir(parents=True, exist_ok=True)

with open(output_path, "wb") as f:
    for chunk in chunk_iter:
        f.write(chunk)

print(f"✅ Audio saved to {output_path}")
