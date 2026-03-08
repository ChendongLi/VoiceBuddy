#!/bin/bash
# claude-telegram.sh — Run Claude Code and report back to Telegram

TASK="$1"
PROJECT_DIR="/Users/chendongli/Projects/VoiceBuddy"
OUTPUT_FILE="/tmp/voicebuddy_claude_output.txt"

cd "$PROJECT_DIR" || exit 1

openclaw message send --channel telegram \
  --message "🚀 *VoiceBuddy* task started:
\`$TASK\`"

# Run Claude Code in background
claude --headless \
  --dangerously-skip-permissions \
  --verbose \
  -p "$TASK" 2>&1 | tee "$OUTPUT_FILE"

# Send a progress ping every 30 seconds
TICK=0
while kill -0 $CLAUDE_PID 2>/dev/null; do
  sleep 30
  TICK=$((TICK + 1))
  LAST=$(tail -3 "$OUTPUT_FILE" | tr '\n' ' ')
  openclaw message send --channel telegram \
    --message "⏳ Still running (${TICK}×30s)...
$LAST"
done

wait $CLAUDE_PID
EXIT_CODE=$?

RESULT=$(tail -60 "$OUTPUT_FILE")

STATUS="✅ Done"
[ $EXIT_CODE -ne 0 ] && STATUS="❌ Failed (exit $EXIT_CODE)"

openclaw message send --channel telegram \
  --message "$STATUS — *VoiceBuddy*

$RESULT"
```

---

## Practical Example Flow
```
You (Telegram):  "VoiceBuddy: fix the audio buffer overflow in the
                  ElevenLabs streaming handler"

Bot (immediate): 🚀 VoiceBuddy — Starting task:
                 `fix the audio buffer overflow in the ElevenLabs streaming handler`

Bot (30s later): ⏳ Still running (1×30s)...
                 Reading voice_stream.py... Identified issue in chunk_size

Bot (2min):     ✅ Done — VoiceBuddy
                Modified voice_stream.py line 47
                Changed chunk_size from 1024 to 4096
                Added overflow guard in _on_audio_data()
                Tests: 3 passed, 0 failed
