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

# Setup folders
CLIP_DIR = "static/clips"
FINAL_DIR = "static/final"
os.makedirs(CLIP_DIR, exist_ok=True)
os.makedirs(FINAL_DIR, exist_ok=True)

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
        zoom_speed = float(data.get("zoom_speed", 0.001))  # very subtle zoom

        if not image_url:
            raise HTTPException(status_code=400, detail="Missing image_url")

        timestamp = datetime.now().isoformat().replace(":", "-").replace(".", "-")
        input_image = f"{CLIP_DIR}/{timestamp}.jpg"
        output_video = f"{CLIP_DIR}/{timestamp}.mp4"

        # Download image
        subprocess.run(["curl", "-L", image_url, "-o", input_image], check=True)

        if not os.path.exists(input_image) or os.path.getsize(input_image) < 1024:
            raise HTTPException(status_code=422, detail="Invalid image or download failed")

        # Final zoom filter: upscale for clean zoom, then animate zoompan, then pad for TikTok/reels format
        zoom_expr = (
    f"scale=8000:-1,"  # upscale for clarity
    f"zoompan=z='min(zoom+{zoom_speed},1.5)':x='if(gte(zoom,1.5),x,x+1)':y='y':d=1,"  # smooth zoom
    f"scale=720:1280:force_original_aspect_ratio=decrease,"  # scale to fit without cropping
    f"pad=720:1280:(ow-iw)/2:(oh-ih)/2:black"  # pad to vertical TikTok/Reels size
)':x='if(gte(zoom,1.5),x,x+1)':y='y':d=1,"  # smooth zoom
    f"scale=720:1280:force_original_aspect_ratio=decrease,"  # scale to fit without cropping
    f"pad=720:1280:(ow-iw)/2:(oh-ih)/2:black"  # pad to vertical TikTok/reels size
),x,x+1)':y='y':d=1,"  # smooth zoom
    f"scale=720:1280:force_original_aspect_ratio=decrease,"  # scale to fit vertical
    f"pad=720:1280:(ow-iw)/2:(oh-ih)/2:black"  # pad to fill TikTok/Reels format
)':x='if(gte(zoom,1.5),x,x+1)':y='y':d=1,"  # zoom and pan
    f"scale=720:1280:force_original_aspect_ratio=decrease,"  # scale to fit without cropping
    f"pad=720:1280:(ow-iw)/2:(oh-ih)/2:black"  # pad to vertical TikTok/reels size
)':x='if(gte(zoom,1.5),x,x+1)':y='y':d=1,"  # zoom and pan
    f"scale=720:1280:force_original_aspect_ratio=decrease,"  # scale to fit 720x1280 without crop
    f"pad=720:1280:(ow-iw)/2:(oh-ih)/2:black"  # pad if needed to fill frame
)':x='if(gte(zoom,1.5),x,x+1)':y='y':d=1,"  # zoom and pan
            f"scale=720:-1,pad=720:1280:(ow-iw)/2:(oh-ih)/2:black"  # downscale + pad to TikTok format
        )

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", input_image,
            "-vf", zoom_expr,
            "-t", str(duration),
            "-r", str(frame_rate),
            "-pix_fmt", "yuv420p",
            output_video
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(result.stderr.decode())

        if not os.path.exists(output_video) or os.path.getsize(output_video) == 0:
            raise HTTPException(status_code=500, detail="Video generation failed")

        background_tasks.add_task(delete_files, [input_image, output_video], delay=3600)

        return {
            "clip_path": output_video,
            "public_url": f"/static/clips/{os.path.basename(output_video)}"
        }

    except subprocess.CalledProcessError as err:
        raise HTTPException(status_code=500, detail=f"Subprocess error: {err}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/join-clips")
async def join_clips(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
        clips = data.get("clips")
        audio_url = data.get("audio_url")
        captions_file = data.get("captions_file")

        if not clips or not isinstance(clips, list) or len(clips) < 1:
            raise HTTPException(status_code=400, detail="Invalid or missing clips list")

        timestamp = datetime.now().isoformat().replace(":", "-").replace(".", "-")
        concat_txt = f"{FINAL_DIR}/concat_{timestamp}.txt"
        joined_video = f"{FINAL_DIR}/joined_{timestamp}.mp4"
        final_video = f"{FINAL_DIR}/final_{timestamp}.mp4"
        temp_audio = f"{FINAL_DIR}/audio_{timestamp}.mp3"

        with open(concat_txt, "w") as f:
            for clip in clips:
                if not os.path.exists(clip):
                    raise HTTPException(status_code=404, detail=f"Clip not found: {clip}")
                f.write(f"file '{clip}'\n")

        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_txt, "-c", "copy", joined_video], check=True)

        if audio_url:
            subprocess.run(["curl", "-L", audio_url, "-o", temp_audio], check=True)
            if not os.path.exists(temp_audio):
                raise HTTPException(status_code=500, detail="Audio download failed")

            subprocess.run([
                "ffmpeg", "-y", "-i", joined_video, "-i", temp_audio,
                "-shortest", "-c:v", "copy", "-c:a", "aac", final_video
            ], check=True)
        else:
            shutil.copy(joined_video, final_video)

        if captions_file and os.path.exists(captions_file):
            subtitled_video = final_video.replace(".mp4", "_subtitled.mp4")
            subprocess.run([
                "ffmpeg", "-y", "-i", final_video,
                "-vf", f"subtitles={captions_file}",
                "-c:a", "copy", subtitled_video
            ], check=True)
            final_video = subtitled_video

        if not os.path.exists(final_video):
            raise HTTPException(status_code=500, detail="Final rendering failed")

        background_tasks.add_task(delete_files, [concat_txt, joined_video, temp_audio, *clips], delay=3600)

        return {"video_url": f"/static/final/{os.path.basename(final_video)}"}

    except subprocess.CalledProcessError as err:
        raise HTTPException(status_code=500, detail=f"FFmpeg error: {err}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
