import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, Request, Response
from twilio.rest import Client
from twilio.twiml.voice_response import Gather, VoiceResponse

logger = logging.getLogger(__name__)
router = APIRouter()

call_sessions: dict[str, list[dict]] = {}

BASE_URL             = os.getenv("BASE_URL", "https://your-server.ngrok.io")
TWILIO_ACCOUNT_SID   = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER  = os.getenv("TWILIO_PHONE_NUMBER")

STATIC_DIR = Path("static")
STATIC_DIR.mkdir(exist_ok=True)

SYSTEM_PROMPT = (
    "You are a helpful, concise voice assistant on a phone call. "
    "Keep all responses to 1–3 short sentences. No markdown, no lists."
)



# ─── Webhooks ────────────────────────────────────────────────────────────────

@router.post("/voice/incoming")
async def incoming_call(request: Request):
    form = await request.form()
    call_sid = form.get("CallSid", "unknown")
    logger.info(f"📞 Incoming call: {call_sid}")

    call_sessions[call_sid] = []

    greeting = "Hello! I'm your AI assistant. How can I help you today?"
    audio_url = await call_tts(request, greeting)

    response = VoiceResponse()
    response.play(audio_url)
    response.redirect(f"{BASE_URL}/twilio/voice/gather?CallSid={call_sid}")
    return Response(content=str(response), media_type="text/xml")


@router.post("/voice/gather")
async def gather_speech(request: Request, CallSid: Optional[str] = Form(None)):
    call_sid = CallSid or "unknown"
    response = VoiceResponse()

    gather = Gather(
        input="speech",
        method="POST",
        speech_timeout="auto",
        speech_model="phone_call",
        enhanced=True,
        language="en-US",
    )
    gather.action = f"{BASE_URL}/twilio/voice/respond?CallSid={call_sid}"
    response.append(gather)
    response.say("I didn't catch that. Could you repeat?")
    response.redirect(f"{BASE_URL}/twilio/voice/gather?CallSid={call_sid}")
    return Response(content=str(response), media_type="text/xml")


@router.post("/voice/respond")
async def respond_to_speech(
    request: Request,
    CallSid: Optional[str] = Form(None),
    SpeechResult: Optional[str] = Form(None),
    Confidence: Optional[float] = Form(None),
):
    call_sid = CallSid or "unknown"
    user_text = SpeechResult or ""

    logger.info(f"🎤 [{call_sid}] '{user_text}' ({Confidence:.2f})" if Confidence else f"🎤 [{call_sid}] '{user_text}'")

    if not user_text:
        response = VoiceResponse()
        response.say("Sorry, I didn't understand that.")
        response.redirect(f"{BASE_URL}/twilio/voice/gather?CallSid={call_sid}")
        return Response(content=str(response), media_type="text/xml")

    history = call_sessions.get(call_sid, [])

    # LLM
    assistant_reply = await call_llm(request, user_text, history)
    logger.info(f"🤖 [{call_sid}] '{assistant_reply}'")

    # Update history
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": assistant_reply})
    call_sessions[call_sid] = history[-20:]

    # TTS
    audio_url = await call_tts(request, assistant_reply)

    response = VoiceResponse()
    response.play(audio_url)

    end_phrases = ["goodbye", "bye", "hang up", "end call", "that's all"]
    if any(p in user_text.lower() for p in end_phrases):
        farewell_url = await call_tts(request, "It was great talking with you. Goodbye!")
        response.play(farewell_url)
        response.hangup()
        call_sessions.pop(call_sid, None)
    else:
        response.redirect(f"{BASE_URL}/twilio/voice/gather?CallSid={call_sid}")

    return Response(content=str(response), media_type="text/xml")


@router.post("/voice/status")
async def call_status(request: Request):
    form = await request.form()
    call_sid = form.get("CallSid", "unknown")
    status   = form.get("CallStatus", "unknown")
    logger.info(f"📵 {call_sid} → {status}")
    if status in ("completed", "failed", "busy", "no-answer", "canceled"):
        call_sessions.pop(call_sid, None)
    return Response(content="", status_code=204)


@router.post("/voice/call")
async def make_outbound_call(request: Request):
    body = await request.json()
    to_number = body.get("to")
    if not to_number:
        return Response(content='{"error":"missing to"}', status_code=400)

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    call = client.calls.create(
        to=to_number,
        from_=TWILIO_PHONE_NUMBER,
        url=f"{BASE_URL}/twilio/voice/incoming",
        status_callback=f"{BASE_URL}/twilio/voice/status",
        status_callback_method="POST",
    )
    return {"call_sid": call.sid, "status": call.status}