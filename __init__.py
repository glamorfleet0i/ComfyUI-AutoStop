import logging
import os
import subprocess
import threading
import time

from aiohttp import web

from main import cleanup_temp
import server


TIMEOUT_SEC = int(os.environ.get("COMFYUI_AUTOSTOP_TIMEOUT_SEC", "900"))
WARNING_TIMES_SEC_REMAINING = [300, 120, 60, 30, 15, 10, 5, 4, 3, 2, 1]

class ThreadSafeTimer:
    def __init__(self):
        self._timer_value = 0
        self._timer_lock = threading.Lock()

    def get_timer(self):
        with self._timer_lock:
            return self._timer_value

    def increment_timer(self):
        with self._timer_lock:
            self._timer_value += 1

    def reset_timer(self):
        with self._timer_lock:
            self._timer_value = 0
        
timer = ThreadSafeTimer()

def inactivity_checker():
    while True:
        timer_elapsed_sec = timer.get_timer()
        q = server.PromptServer.instance.prompt_queue
        if q.get_tasks_remaining() > 0:
            if timer_elapsed_sec > 0:
                logging.info(f"[AutoStop] New prompt queued, the timer has been reset and paused. It will resume after all prompts have been executed.")
            timer.reset_timer()
        else:
            timer.increment_timer()
            
            timer_sec_remaining = TIMEOUT_SEC - timer_elapsed_sec
            if timer_sec_remaining in WARNING_TIMES_SEC_REMAINING:
                logging.info(f"[AutoStop] Warning: The ComfyUI server will stop in {timer_sec_remaining // 60 if timer_sec_remaining >= 60 else timer_sec_remaining} {'minute(s)' if timer_sec_remaining >= 60 else 'second(s)'} due to inactivity. Submit any prompt to keep it running.")
                
            if timer_elapsed_sec > TIMEOUT_SEC:
                stop_server()
        
        time.sleep(1)

def stop_server():
    try:
        logging.info(f"[AutoStop] Stopping the ComfyUI server...")
        cleanup_temp()
        
        runpod_pod_id = os.environ.get("RUNPOD_POD_ID")
        if runpod_pod_id:
            logging.info("[AutoStop] Detected that ComfyUI is running on Runpod. Stopping the pod.")
            result = subprocess.run(["runpodctl", "stop", "pod", runpod_pod_id])
            if result.returncode != 0:
                logging.error(f"[AutoStop] Failed to stop the pod!")
            else:
                logging.info(f"[AutoStop] Successfully sent the stop request.")

        time.sleep(5) # Give a slight buffer just in case
        os._exit(status=0)
    except Exception as e:
        logging.error(f"[AutoStop] An error occurred while trying to gracefully stop the server. Will force exit now.")
        logging.exception(e)
        os._exit(status=1)

logging.info(f"[AutoStop] Initializing ComfyUI AutoStop...")
prompt_server = server.PromptServer.instance
app = prompt_server.app
routes = prompt_server.routes

@routes.get("/autostop/time-left")
async def get_timer(request):
    return web.json_response({"time_left": TIMEOUT_SEC - timer.get_timer()})

@routes.post("/autostop/keep-alive")
async def keep_alive(request):
    timer.reset_timer()
    return web.json_response({"time_left": TIMEOUT_SEC - timer.get_timer()})

@routes.post("/autostop/stop-now")
async def stop_now(request):
    logging.info(f"[AutoStop] Received a request to stop the ComfyUI server now.")
    threading.Thread(target=stop_server).start()
    return web.json_response({"status": "ok"})

threading.Thread(target=inactivity_checker, daemon=True).start()
logging.info(f"[AutoStop] ComfyUI AutoStop is enabled with a timeout of {TIMEOUT_SEC} second(s). Submit any prompt to reset the timer.")

NODE_CLASS_MAPPINGS = {}
