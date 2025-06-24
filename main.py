from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from typing import List
from datetime import datetime
import subprocess
import uuid
import os
import time
import shutil

app = FastAPI()

# Ensure required directories
os.makedirs("static/clips", exist_ok=True)
os.makedirs("static/final", exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

def delete_files(paths: List[str], delay=3600):
    time.sleep(delay)
    for path in paths:
        try:
            if os.path.exists(path):
                os.remove(path)
                print(f"Deleted {path}")
        except Exception as e:
            print(f"Error deleting {path}: {e}")

@app.post("/generate-clip")
async def generate_clip(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
        image_url = data.get("image_url")
        duration = float(data.get("length", 5))
        frame_rate = int(data.get("frame_rate", 25))
        zoom_speed = float(data.get("zoom_speed", 0.003))
        frame_count = int(duration * frame_rate)

        if not image_url:
            raise HTTPException(status_code=400, detail="Missing image_url")

        timestamp = datetime.now().isoformat().replace(":", "-").replace(".", "-")
        input_image = f"static/clips/{timestamp}.png"
        output_video = f"static/clips/{timestamp}.mp4"

        download = subprocess.run(["curl", "-L", image_url, "-o", input_image])
        if download.returncode != 0 or not os.path.exists(input_image) or os.path.getsize(input_image) < 1000:
            raise HTTPException(status_code=422, detail="Image download failed or file invalid")

        # TikTok-style vertical formatting with animated zoom
        zoom_expr = (
            f"zoompan=z='if(lte(zoom,1.0),1.2,zoom+{zoom_speed})':d=1:s={frame_count}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',fps={frame_rate},"
            f"scale=w=720:h=-1,pad=720:1280:(ow-iw)/2:(oh-ih)/2:black"
        )

        cmd = [
            "ffmpeg", "-y", "-i", input_image,
            "-vf", zoom_expr,
            "-pix_fmt", "yuv420p", output_video
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(result.stderr.decode())

        if not os.path.exists(output_video) or os.path.getsize(output_video) == 0:
            raise HTTPException(status_code=500, detail="Video generation failed")

        background_tasks.add_task(delete_files, [input_image, output_video], delay=3600)

        return {"clip_path": output_video, "public_url": f"https://image-to-video-api-qkjd.onrender.com/static/clips/{os.path.basename(output_video)}"}

    except HTTPException as http_err:
        raise http_err
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/join-clips")
async def join_clips(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
        clips = data.get("clips")
        audio_url = data.get("audio_url")
        captions_file = data.get("captions_file")  # Optional

        if not clips or not isinstance(clips, list) or len(clips) < 2:
            raise HTTPException(status_code=400, detail="Invalid or missing clips list")

        timestamp = datetime.now().isoformat().replace(":", "-").replace(".", "-")
        concat_list_path = f"static/final/concat_{timestamp}.txt"
        joined_output = f"static/final/joined_{timestamp}.mp4"
        final_output = f"static/final/final_{timestamp}.mp4"
        temp_audio = f"static/final/audio_{timestamp}.mp3"

        # Create concat file
        with open(concat_list_path, "w") as f:
            for clip_path in clips:
                if not os.path.exists(clip_path):
                    raise HTTPException(status_code=404, detail=f"Clip not found: {clip_path}")
                f.write(f"file '{clip_path}'\n")

        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path, "-c", "copy", joined_output])

        if not os.path.exists(joined_output):
            raise HTTPException(status_code=500, detail="Failed to join clips")

        # Download audio
        if audio_url:
            subprocess.run(["curl", "-L", audio_url, "-o", temp_audio])
            if not os.path.exists(temp_audio):
                raise HTTPException(status_code=500, detail="Audio download failed")

            subprocess.run([
                "ffmpeg", "-y", "-i", joined_output, "-i", temp_audio,
                "-shortest", "-c:v", "copy", "-c:a", "aac", final_output
            ])
        else:
            shutil.copy(joined_output, final_output)

        # Add subtitles if provided
        if captions_file and os.path.exists(captions_file):
            subtitled_output = final_output.replace(".mp4", "_subtitled.mp4")
            subprocess.run([
                "ffmpeg", "-y", "-i", final_output,
                "-vf", f"subtitles={captions_file}", "-c:a", "copy", subtitled_output
            ])
            final_output = subtitled_output

        if not os.path.exists(final_output):
            raise HTTPException(status_code=500, detail="Final video rendering failed")

        background_tasks.add_task(delete_files, [concat_list_path, joined_output, temp_audio, *clips], delay=3600)

        return {"video_url": f"/static/final/{os.path.basename(final_output)}"}

    except HTTPException as http_err:
        raise http_err
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))