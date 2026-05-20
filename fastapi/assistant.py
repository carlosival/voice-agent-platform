import torch
import numpy as np
import pyaudio
import base64
import io
import wave
from openai import OpenAI

# 1. Configuration
client = OpenAI(base_url="http://localhost:8091/v1", api_key="token-not-needed")
MODEL_NAME = "Qwen/Qwen3-Omni-30B-A3B-Instruct"
RATE = 16000
CHUNK = 512  # Small chunks for low latency
SILENCE_LIMIT = 1.0  # Seconds of silence before "committing" speech

# 2. Initialize Silero VAD
model, utils = torch.hub.load(repo_or_dir='snickersberg/silero-vad', model='silero_vad', force_reload=False)
(get_speech_timestamps, save_audio, read_audio, VADIterator, collect_chunks) = utils

def run_vad_pipeline():
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True, frames_per_buffer=CHUNK)
    
    print(">>> RTX 5090 Assistant Ready. Start speaking...")
    
    audio_buffer = []
    is_speaking = False
    silence_start = None

    try:
        while True:
            data = stream.read(CHUNK, exception_on_overflow=False)
            audio_buffer.append(data)
            
            # Convert chunk to tensor for VAD
            audio_int16 = np.frombuffer(data, np.int16)
            audio_float32 = audio_int16.astype(np.float32) / 32768.0
            
            # Get speech probability
            speech_prob = model(torch.from_numpy(audio_float32), RATE).item()

            if speech_prob > 0.6:  # High probability of speech
                if not is_speaking:
                    print("Listening...")
                is_speaking = True
                silence_start = None 
            elif is_speaking:
                if silence_start is None:
                    silence_start = torch.cuda.Event(enable_timing=True) # Optional: Use 5090 events for timing
                    import time
                    silence_start = time.time()
                
                # If silent for long enough, trigger the LLM
                if time.time() - silence_start > SILENCE_LIMIT:
                    print("Processing...")
                    process_and_respond(audio_buffer)
                    # Reset
                    audio_buffer = []
                    is_speaking = False
                    silence_start = None
            else:
                # Keep a small "pre-roll" buffer so we don't cut off the start of sentences
                if len(audio_buffer) > 20: 
                    audio_buffer.pop(0)

    except KeyboardInterrupt:
        stream.stop_stream()
        stream.close()
        p.terminate()

def process_and_respond(frames):
    # Convert frames to Base64 WAV
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(b''.join(frames))
    
    audio_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

    # Send to vLLM-Omni
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "Respond naturally to this voice input."},
            {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "wav"}}
        ]}],
        modalities=["text", "audio"],
        audio={"voice": "alloy", "format": "wav"}
    )

    # Play result
    if response.choices[0].message.audio:
        play_audio(response.choices[0].message.audio.data)

def play_audio(data_b64):
    audio_data = base64.b64decode(data_b64)
    p = pyaudio.PyAudio()
    # Qwen3-Omni usually outputs 24kHz
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=24000, output=True)
    stream.write(audio_data)
    stream.close()
    p.terminate()

if __name__ == "__main__":
    run_vad_pipeline()