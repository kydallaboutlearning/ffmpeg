from fastapi import FastAPI, Request
import subprocess
import uuid
import os

app = FastAPI()

@app.post("/generate")
async def generate(request: Request):
    data = await request.json()
    image_url = data["image_url"]
    duration = str(data.get("duration", 10))

    input_name = f"{uuid.uuid4()}.jpg"
    output_name = f"{uuid.uuid4()}.mp4"

    subprocess.run(["wget", "-O", input_name, image_url], check=True)

    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", input_name,
        "-vf", "zoompan=z='zoom+0.001':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',fps=25,scale=1280:720",
        "-t", duration, "-pix_fmt", "yuv420p", output_name
    ]
    subprocess.run(cmd, check=True)

    # Optional: Upload to transfer.sh or your own S3 bucket
    # For now, just return the file path
    return {"video_file": output_name}
