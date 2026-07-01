


# Stream audio file from remote host and play it
ssh root@192.168.1.2 "pct exec 503 -- cat /root/voice-agent/test_output.wav" | aplay - 

# Check ngrok tunnel show your static domain as an active tunnel
curl http://localhost:4040/api/tunnels | python3 -m json.tool

# Check what piper models speaches-ai publishes
curl -s "https://huggingface.co/api/models?search=piper&author=speaches-ai" | python3 -m json.tool | grep '"id"'

# List available models STT and TTS
curl http://localhost:8000/v1/models | python3 -m json.tool


# Generate TTS audio
curl http://localhost:8000/v1/audio/speech   -H "Content-Type: application/json"   -d '{"model":"speaches-ai/piper-es_ES-sharvard-medium","input":"Esta es una prueba con piper usando CPU","voice":"sharvard"}'   --output test_output.wav

# Record a quick test audio or use any wav file STT
curl http://localhost:8000/v1/audio/transcriptions \
  -F file=@test_transcribe.wav \
  -F model=Systran/faster-whisper-large-v3

# Lists loaded models — works on Ollama AND vLLM
curl http://localhost:11434/v1/models | python3 -m json.tool

# Works for both Ollama and vLLM — just change the URL and model name
curl http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.1:8b",
    "messages": [{"role": "user", "content": "Say hello in one sentence"}],
    "max_tokens": 50
  }' | python3 -m json.tool

# Check FastAPI health private
curl http://localhost:8080/health/ | python3 -m json.tool

# Check FastAPI health public
curl https://epiphanic-marriageable-keely.ngrok-free.dev/health/ | python3 -m json.tool


# Check WebSocket connection curl --version >= 7.86.0
curl -v \
     --max-time 3 \
     --no-buffer \
     --http1.1 \
     -H "Connection: Upgrade" \
     -H "Upgrade: websocket" \
     -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
     -H "Sec-WebSocket-Version: 13" \
     -H "Origin: http://localhost:8080" \
     http://localhost:8080/ws/ 2>&1


# LLM tool calling test

curl https://api.groq.com/openai/v1/chat/completions \
  -H "Authorization: Bearer $GROQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-3.3-70b-versatile",
    "temperature": 0,
    "stream": true,
    "messages": [
      {"role": "system", "content": "Tu eres un asistente de voz en español."},
      {"role": "user", "content": "Adios"}
    ],
    "tools": [{
      "type": "function",
      "function": {
        "name": "end_conversation",
        "description": "Call this to end the conversation when the user says goodbye.",
        "parameters": {"type": "object", "properties": {}, "required": []}
  }'"tool_choice": "auto"
{"id":"chatcmpl-b740e70c-4a78-42b0-a2f6-9d6435d4f449","object":"chat.completion","created":1778762393,"model":"llama-3.3-70b-versatile","choices":[{"index":0,"message":{"role":"assistant","tool_calls":[{"id":"fcc4jmv4d","type":"function","function":{"name":"end_conversation","arguments":"null"}}]},"logprobs":null,"finish_reason":"tool_calls"}],"usage":{"queue_time":0.153111877,"prompt_tokens":232,"prompt_time":0.01400981,"completion_tokens":9,"completion_time":0.025112427,"total_tokens":241,"total_time":0.039122237},"usage_breakdown":null,"system_fingerprint":"fp_d42c28f9ce","x_groq":{"id":"req_01krk80a57e6w9dv4z1qk0szms","seed":683720658},"service_tier":"on_demand"}
