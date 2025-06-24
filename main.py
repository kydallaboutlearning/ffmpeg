from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime
import subprocess
import uuid
import os
import time

app = FastAPI()

# Serve static files (e.g. generated videos)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Cleanup task: delete image and video after a delay (e.g. 1 hour)
def delete_file_after_delay(image_path, video_path, delay=3600):
    time.sleep(delay)
    try:
        if os.path.exists(image_path):
            os.remove(image_path)
        if os.path.exists(video_path):
            os.remove(video_path)
        print(f"Deleted files: {image_path}, {video_path}")
    except Exception as e:
        print(f"Cleanup error: {e}")

@app.post("/generate")
async def generate(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()

    image_url = data.get("image_url")
    length = str(data.get("length", 10))
    frame_rate = str(data.get("frame_rate", 25))
    zoom_speed = str(data.get("zoom_speed", 3))

    # Create safe ID using timestamp
    timestamp = datetime.now().isoformat()
    safe_id = timestamp.replace(":", "-").replace(".", "-")
    input_image = f"{safe_id}.jpg"
    output_video = f"static/{safe_id}.mp4"

    # Download image
    subprocess.run(["wget", "-O", input_image, image_url])

    # ffmpeg zoom effect
    zoom_expr = f"zoompan=z='zoom+0.00{zoom_speed}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',fps={frame_rate},scale=1280:720"

    # Generate video
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", input_image,
        "-vf", zoom_expr,
        "-t", length, "-pix_fmt", "yuv420p", output_video
    ]
    subprocess.run(cmd)

    # Schedule file cleanup
    background_tasks.add_task(delete_file_after_delay, input_image, output_video, delay=3600)

    # Return video URL
    return {
        "video_url": f"https://image-to-video-api-qkjd.onrender.com/static/{safe_id}.mp4",
        "expires_in": 3600,
        "id": safe_id
    }
