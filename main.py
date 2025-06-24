from fastapi import FastAPI, Request
import subprocess
import uuid
import os

app = FastAPI()

@app.post("/generate")
async def generate(request: Request):
    data = await request.json()
    
    image_url = data.get("image_url")
    length = str(data.get("length", 10))
    frame_rate = str(data.get("frame_rate", 25))
    zoom_speed = str(data.get("zoom_speed", 3))
    clip_id = data.get("id", str(uuid.uuid4()))

    input_name = f"{clip_id}.jpg"
    output_name = f"{clip_id}.mp4"

    # Download the image
    subprocess.run(["wget", "-O", input_name, image_url])

    # Create zoompan string using zoom_speed
    zoom_expr = f"zoompan=z='zoom+0.00{zoom_speed}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',fps={frame_rate},scale=1280:720"

    # Generate video using ffmpeg
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", input_name,
        "-vf", zoom_expr,
        "-t", length, "-pix_fmt", "yuv420p", output_name
    ]
    subprocess.run(cmd)

    # Optional: Upload video to transfer.sh or similar, or return local path
    return {
        "video_path": output_name,
        "id": clip_id
    }
