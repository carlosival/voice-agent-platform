PRE_ROLL_LEN = 50
MAX_UTTERANCE_LEN = 1000 # 1000frames * 20ms = 20min; Hold the max length of the audio conversation

QUEUE_BACKPRESSURE_MAXSIZE = 10

# Tunable thresholds in real time
START_SECS   = 0.2   # confirmed speech after this long
CONFIRM_TURN_SEC = 0.2   # Explore turn detection
STOP_SECS    = 0.6   # confirmed absolute silence after this long
FRAME_SECS   = 0.032  # 20ms per aiortc frame
INACTIVITY_SECS = 60 # 60 seconds At this point the agent will ask the user still there?
VAD_CHUNK_SAMPLES = 512 # 16KHz F32SLE 512 Samples are 32ms or 0.032s and 2048 bytes 

START_FRAMES = round(START_SECS / FRAME_SECS)   # 10 frames

'''
Amount of VAD_CHUNK_SAMPLES in x sec.
'''
STOP_FRAMES  = round(STOP_SECS  / FRAME_SECS)   # 40 frames
INACTIVITY_FRAMES = round(INACTIVITY_SECS / FRAME_SECS) # 60 seconds At this point the agent will ask the user still there?
CONFIRM_TURN = round(CONFIRM_TURN_SEC / FRAME_SECS)
INACTIVITY_STOP = 2 * INACTIVITY_FRAMES
MAX_ASK_USER = 1



SAMPLE_RATE     = 16_000
VAD_WINDOW_SIZE      = 50          # frames per VAD call  (~1 s @ 20 ms/frame)
VAD_SILENCE_WINDOWS_LIMIT = 3      # silent windows after speech → flush utterance
VAD_THRESHOLD   = 0.5
VAD_STRIDE    = 10  # call VAD every 20 frames -> call every 400ms
