import sys
import signal
import time
from meet_bot import MeetBot

def main():
    job_id = sys.argv[1]
    meet_url = sys.argv[2]

    bot = MeetBot(job_id=job_id, meet_url=meet_url)

    # Trap FastAPI's process.terminate() cleanly
    def handle_sigterm(signum, frame):
        print(f"[Worker:{job_id}] Caught kill signal {signum}. Propagating graceful stop...")
        bot.stop()
        
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    bot.run()

if __name__ == "__main__":
    main()