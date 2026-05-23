
Direction state machine in WebRCT 
Audio Channel        
mic off    →  recvonly 
mic on    →  sendrecv    

Video Channel
For video there's no "receive" side (server doesn't send video back), so:
camera off  →  inactive
camera on   →  sendonly  (or sendrecv if server ever sends video)