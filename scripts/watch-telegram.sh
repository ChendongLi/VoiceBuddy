#!/bin/bash
LOG_FILE="/tmp/voicebuddy_live.log"
BUFFER=""
BUFFER_LINES=0
MAX_LINES=10
TARGET="8552547829"

# Strip ANSI escape codes
strip_ansi() {
  sed 's/\x1b\[[0-9;]*[mGKHFJABCDsuhl]//g; s/\x1b\][^\x07]*\x07//g; s/\r//g' | tr -s ' \n'
}

echo "👀 Watching Claude Code output → Telegram"

openclaw message send --channel telegram --target $TARGET \
  --message "👀 *VoiceBuddy* live monitor started"

tail -f "$LOG_FILE" | strip_ansi | while IFS= read -r line; do
  # Skip empty lines
  [ -z "$(echo "$line" | tr -d ' ')" ] && continue

  BUFFER="$BUFFER
$line"
  BUFFER_LINES=$((BUFFER_LINES + 1))

  if [ $BUFFER_LINES -ge $MAX_LINES ]; then
    openclaw message send --channel telegram --target $TARGET \
      --message "📟 *VoiceBuddy*:
\`\`\`
$BUFFER
\`\`\`"
    BUFFER=""
    BUFFER_LINES=0
  fi
done
