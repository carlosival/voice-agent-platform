from typing import Callable
from aiortc import MediaStreamTrack, RTCDataChannel

# INPUT of CREATE PEER Connection
class PeerDependencies:
    audio_handler: Callable[[MediaStreamTrack], None] = None
    video_handler: Callable[[MediaStreamTrack], None] = None
    datachannel_handler: Callable[[RTCDataChannel], None] = None
    on_connected_fully: Callable[[], None] = None
    on_track: Callable[[MediaStreamTrack], None] = None
    on_ice_state_change: Callable[[str], None] = None
    on_connection_state_change: Callable[[str], None] = None
    on_terminated: Callable[[], None] = None

# OUTPUT CREATE PEER Connection
class PeerSession:
    def __init__(self):
        self.pc = None
        self.tasks = set()

    def add_task(self, task):
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
    def set_pc(self, pc):
        self.pc = pc
        