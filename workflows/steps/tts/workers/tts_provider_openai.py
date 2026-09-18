import logging
import httpx
import asyncio
from workflows.steps.outputs import AudioConverter, get_tts_provider_format, get_output_format
from workflows.steps.tts.utils import call_tts_stream, build_payload
from workflows.steps.outputs.audio_output import IAudioOutput

import logging 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
    
# 3. DEFINE THE SYNTHESIS WORKER
async def tts_worker(
                    http_client: httpx.AsyncClient, 
                    output_track, 
                    tts_queue, 
                    provider_name: str,
                    model:str,
                    voice:str,
                    language:str,
                    api_key:str,
                    base_url:str
                    ):
        
        converter = AudioConverter(
                            input_format=get_tts_provider_format(provider_name, model),
                            output_format=get_output_format(output_track),
                        )
        while True:
            try:
                text = await tts_queue.get()
                
                if text is None:
                    # End of the CONTINUOUS audio stream.
                    pcm = converter.flush()

                    if pcm.size:
                        await output_track.write(pcm)

                    break
                    
                    
                
                # get a payload  
                payload = build_payload(provider_name, text, model, voice)  
                
                
                async for chunk_bytes in call_tts_stream(text=text, payload=payload,api_key=api_key, base_url=base_url,http_client=http_client):
                    # I don't really need to convert the bytes to an AudioFrame
                    # I can just add the bytes directly to the output track
                    # But I need to make sure the format is correct
                    # The format is PCM_S16LE at 24000 Hz
                    # The output track is PCM_S16LE at 8000 Hz
                    # So I need to convert the format
                    #source_chunk = AudioFrame.from_bytes(chunk_bytes, format=TWILIO_FORMAT)

                    destination_chunk = converter.convert(chunk_bytes)
                    
                    if destination_chunk.size:
                        await output_track.write(destination_chunk)

                        
            except asyncio.CancelledError:
                # Still vital for barge-in!
                logger.debug("TTS Worker: Cancelled (Barge-in).", exc_info=True)
                raise 
            except Exception as e:
                logger.error(f"TTS Worker Error: {e}",exc_info=True)

    
