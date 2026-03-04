#!/bin/bash
# Auto-relay: Poll Blackford bot, deploy Three Rivers bot when free
# Then poll both for transcripts

RECALL_TOKEN="0c6672b813840510595fbc7b9ec89a43a871ab58"
RECALL_BASE="https://us-west-2.recall.ai/api/v1"
BLACKFORD_BOT="7a9fbe56-231f-4aff-a09a-4d14bf7a387a"
THREE_RIVERS_MEETING="https://teams.microsoft.com/meet/2239030137332?p=s412S1xsQh8qPCaWL5"
LOG="/opt/goliath/logs/recall_relay.log"
POLL_INTERVAL=30

mkdir -p /opt/goliath/logs

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"
}

log "=== RECALL RELAY STARTED ==="
log "Monitoring Blackford bot $BLACKFORD_BOT"
log "Will deploy to Three Rivers when free"

# Phase 1: Wait for Blackford bot to finish
THREE_RIVERS_BOT=""
while true; do
    STATUS=$(curl -s -H "Authorization: Token $RECALL_TOKEN" \
        "$RECALL_BASE/bot/$BLACKFORD_BOT/" 2>/dev/null)
    
    CALL_STATUS=$(echo "$STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status_changes',[-1])[-1].get('code','unknown') if d.get('status_changes') else 'unknown')" 2>/dev/null)
    
    log "Blackford bot status: $CALL_STATUS"
    
    if [[ "$CALL_STATUS" != "in_call_recording" && "$CALL_STATUS" != "in_call_not_recording" && "$CALL_STATUS" != "joining_call" && "$CALL_STATUS" != "in_waiting_room" ]]; then
        log "Blackford bot is DONE (status: $CALL_STATUS). Deploying Three Rivers bot NOW!"
        
        # Deploy Three Rivers bot
        DEPLOY_RESULT=$(curl -s -X POST "$RECALL_BASE/bot/" \
            -H "Authorization: Token $RECALL_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{
                \"meeting_url\": \"$THREE_RIVERS_MEETING\",
                \"bot_name\": \"Aaron Gonzalez\",
                \"transcription_options\": {
                    \"provider\": \"default\"
                },
                \"recording_mode\": \"speaker_view\"
            }" 2>/dev/null)
        
        THREE_RIVERS_BOT=$(echo "$DEPLOY_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id','FAILED'))" 2>/dev/null)
        
        if [[ "$THREE_RIVERS_BOT" != "FAILED" && -n "$THREE_RIVERS_BOT" ]]; then
            log "✅ Three Rivers bot deployed! Bot ID: $THREE_RIVERS_BOT"
            echo "$THREE_RIVERS_BOT" > /opt/goliath/logs/three_rivers_bot_id.txt
        else
            log "❌ Three Rivers deployment FAILED: $DEPLOY_RESULT"
            echo "$DEPLOY_RESULT" > /opt/goliath/logs/three_rivers_deploy_error.txt
        fi
        break
    fi
    
    sleep $POLL_INTERVAL
done

# Phase 2: Save Blackford transcript
log "--- Fetching Blackford transcript ---"
BF_TRANSCRIPT=$(curl -s -H "Authorization: Token $RECALL_TOKEN" \
    "$RECALL_BASE/bot/$BLACKFORD_BOT/transcript/" 2>/dev/null)
    
if [[ -n "$BF_TRANSCRIPT" && "$BF_TRANSCRIPT" != "[]" ]]; then
    mkdir -p /opt/goliath/projects/blackford/transcripts
    echo "$BF_TRANSCRIPT" > /opt/goliath/projects/blackford/transcripts/2026-03-02-constraints-raw.json
    log "✅ Blackford transcript saved"
else
    log "⚠️ Blackford transcript not ready yet or empty"
fi

# Phase 3: If Three Rivers bot deployed, poll for it to finish
if [[ -n "$THREE_RIVERS_BOT" && "$THREE_RIVERS_BOT" != "FAILED" ]]; then
    log "--- Monitoring Three Rivers bot $THREE_RIVERS_BOT ---"
    while true; do
        STATUS=$(curl -s -H "Authorization: Token $RECALL_TOKEN" \
            "$RECALL_BASE/bot/$THREE_RIVERS_BOT/" 2>/dev/null)
        
        CALL_STATUS=$(echo "$STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status_changes',[-1])[-1].get('code','unknown') if d.get('status_changes') else 'unknown')" 2>/dev/null)
        
        log "Three Rivers bot status: $CALL_STATUS"
        
        if [[ "$CALL_STATUS" != "in_call_recording" && "$CALL_STATUS" != "in_call_not_recording" && "$CALL_STATUS" != "joining_call" && "$CALL_STATUS" != "in_waiting_room" && "$CALL_STATUS" != "unknown" ]]; then
            log "Three Rivers bot DONE. Fetching transcript..."
            
            TR_TRANSCRIPT=$(curl -s -H "Authorization: Token $RECALL_TOKEN" \
                "$RECALL_BASE/bot/$THREE_RIVERS_BOT/transcript/" 2>/dev/null)
            
            if [[ -n "$TR_TRANSCRIPT" && "$TR_TRANSCRIPT" != "[]" ]]; then
                mkdir -p /opt/goliath/projects/three-rivers/transcripts
                echo "$TR_TRANSCRIPT" > /opt/goliath/projects/three-rivers/transcripts/2026-03-02-constraints-raw.json
                log "✅ Three Rivers transcript saved"
            else
                log "⚠️ Three Rivers transcript empty or not ready"
            fi
            break
        fi
        
        sleep $POLL_INTERVAL
    done
fi

# Also re-check Blackford transcript in case it wasn't ready earlier
BF_TRANSCRIPT=$(curl -s -H "Authorization: Token $RECALL_TOKEN" \
    "$RECALL_BASE/bot/$BLACKFORD_BOT/transcript/" 2>/dev/null)
if [[ -n "$BF_TRANSCRIPT" && "$BF_TRANSCRIPT" != "[]" ]]; then
    mkdir -p /opt/goliath/projects/blackford/transcripts
    echo "$BF_TRANSCRIPT" > /opt/goliath/projects/blackford/transcripts/2026-03-02-constraints-raw.json
    log "✅ Blackford transcript (re-check) saved"
fi

log "=== RECALL RELAY COMPLETE ==="
