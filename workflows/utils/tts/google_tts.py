# After — lazy initialization
_tts_client = None

def get_tts_client():
    global _tts_client
    if _tts_client is None:
        _tts_client = texttospeech.TextToSpeechAsyncClient()
    return _tts_client


async def _request_generator(text: str) -> AsyncGenerator[texttospeech.StreamingSynthesizeRequest, None]:
    """
    gRPC bidirectional streaming requires an input generator.
    The first message MUST contain the configuration parameters.
    Subsequent messages can feed raw text strings over time.
    """
    # 1. First message: Setup configurations
    config = texttospeech.StreamingSynthesizeConfig(
        voice=texttospeech.VoiceSelectionParams(
            language_code=TTS_LANGUAGE,
            name=TTS_VOICE,
            model_name=TTS_MODEL
        ),
        streaming_audio_config=texttospeech.StreamingAudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16, # Raw PCM
            sample_rate_hertz=SAMPLE_RATE
        )
    )
    yield texttospeech.StreamingSynthesizeRequest(streaming_config=config)

    # 2. Second message: Pass the text block or text chunk to synthesize
    # If feeding from a live LLM, you can loop and yield multiple text blocks here.
    input_text = texttospeech.StreamingText(
        text=text,
        prompt="Read aloud in a warm, welcoming tone."
    )
    yield texttospeech.StreamingSynthesizeRequest(input_text=input_text)




async def call_tts_stream_google(
    text: str,
    debug: bool = False,                                        
    debug_path: str = "./static/tts_debug"  
) -> AsyncGenerator[bytes, None]:
    """
    Streams raw PCM audio back in perfect 20ms WebRTC frames using 
    Google Cloud TTS's native bidirectional StreamingSynthesize gRPC pipeline.
    """
    buffer = b""
    debug_pcm = bytearray() if debug else None

    if debug:
        Path(debug_path).parent.mkdir(parents=True, exist_ok=True)
        print(f"\n── Google gRPC StreamingSynthesize Active ──")

    try:

        tts_client = get_tts_client()

        # Create the input gRPC stream sequence
        requests_stream = _request_generator(text)

        # Establish the bidirectional stream with Google
        response_stream = await tts_client.streaming_synthesize(requests_stream)

        # Process the fragments Google returns on the fly
        async for response in response_stream:
            # Check if there are valid audio bytes in this chunk
            chunk = response.audio_content
            if not chunk:
                continue

            buffer += chunk

            # Sift through the buffer to extract exact 20ms blocks
            while len(buffer) >= BYTES_PER_FRAME:
                frame = buffer[:BYTES_PER_FRAME]
                buffer = buffer[BYTES_PER_FRAME:]
                
                if debug:
                    debug_pcm.extend(frame)
                yield frame

        # Final Flush: Pad any remaining leftovers to keep the audio frame aligned
        if len(buffer) > 0:
            padding = BYTES_PER_FRAME - len(buffer)
            final_frame = buffer + (b"\x00" * padding)
            if debug:
                debug_pcm.extend(final_frame)
            yield final_frame

    except Exception as e:
        logger.error(f"Error during Google Cloud TTS Streaming: {e}")
        raise

    # --- Background Debug Task ---
    if debug and debug_pcm:
        unique_path = os.path.join(debug_path, f"tts_{uuid.uuid4().hex}.wav")
        Path(debug_path).mkdir(parents=True, exist_ok=True)
        
        asyncio.create_task(
            asyncio.to_thread(save_debug_wav, unique_path, debug_pcm, SAMPLE_RATE)
        )





async def call_tts_stream_google(
    http_client: httpx.AsyncClient,
    text: str,
    chunk_size: int = 4096, 
    debug: bool = False,                                        
    debug_path: str = "./static/tts_debug"  
) -> AsyncGenerator[bytes, None]:
    """
    Streams raw PCM audio chunks back in perfect 20ms WebRTC frames.
    Google Cloud Text-to-Speech does not support standard bidirectional HTTP streaming 
    for text inputs (synthesize_streaming is only available for long text via specific gRPC setups).
    However, we achieve identical internal streaming behavior by chunking Google's fast response 
    directly into your fixed 20ms (1920 byte) windows.
    """
    buffer = b""
    debug_pcm = bytearray() if debug else None

    if debug:
        Path(debug_path).parent.mkdir(parents=True, exist_ok=True)

    # Fetch the full raw PCM bytes payload asynchronously
    raw_audio = await call_tts(text)
    
    # We read through the payload in incremental chunks to simulate the stream 
    # and feed your WebRTC buffer smoothly without modification downstream.
    for i in range(0, len(raw_audio), chunk_size):
        chunk = raw_audio[i:i + chunk_size]
        buffer += chunk

        # Yield chunks as soon as we have enough for a 20ms frame
        while len(buffer) >= BYTES_PER_FRAME:
            frame = buffer[:BYTES_PER_FRAME]
            buffer = buffer[BYTES_PER_FRAME:]
            
            if debug:
                debug_pcm.extend(frame)
            yield frame
            # Short yield pause to match network processing cadences if required
            await asyncio.sleep(0.001) 

    # Final Flush: If data is left over, pad it with silence to complete the frame
    if len(buffer) > 0:
        padding = BYTES_PER_FRAME - len(buffer)
        final_frame = buffer + (b"\x00" * padding)
        if debug:
            debug_pcm.extend(final_frame)
        yield final_frame

    # --- THE INDEPENDENT TASK ---
    if debug and debug_pcm:
        unique_path = os.path.join(debug_path, f"tts_{uuid.uuid4().hex}.wav")
        Path(debug_path).mkdir(parents=True, exist_ok=True)
        
        asyncio.create_task(
            asyncio.to_thread(save_debug_wav, unique_path, debug_pcm, SAMPLE_RATE)
        )