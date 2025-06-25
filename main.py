import os
import uuid
import httpx
import asyncio
from typing import List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

app = FastAPI(
    title="TikTok Video Generator API",
    description="API for generating and processing TikTok-style video clips using FFmpeg.",
    version="1.0.0",
)

# --- Configuration (can be moved to a separate config.py or .env) ---
TEMP_FILES_DIR = "temp_files"
TIKTOK_REELS_WIDTH = 720
TIKTOK_REELS_HEIGHT = 1280

# Ensure the temporary directory exists
os.makedirs(TEMP_FILES_DIR, exist_ok=True)

# --- Pydantic Models for Request Bodies ---

class GenerateClipRequest(BaseModel):
    """
    Request body for generating a single video clip from an image.
    """
    image_url: str = Field(..., description="URL of the image (576x1024 recommended) to use for the clip.")
    length: float = Field(..., gt=0, description="Duration of the video clip in seconds.")
    frame_rate: int = Field(25, gt=0, description="Frames per second of the output video.")
    zoom_speed: float = Field(0.003, ge=0, description="Speed of the zoom effect. Higher value means faster zoom.")
    id: Optional[str] = Field(None, description="Optional unique ID for the request. If not provided, a UUID will be generated.")

class ClipInfo(BaseModel):
    """
    Information about an existing video clip to be used in processing.
    """
    filename: str = Field(..., description="Path/filename of the existing video clip.")
    # Optional: You could add start_time and end_time here if you want to trim individual clips
    # before concatenation, but the current `concat` demuxer approach won't directly support it.
    # For trimming, you'd need to pre-process each clip individually.

class SubtitleEntry(BaseModel):
    """
    Details for a single subtitle entry.
    """
    text: str = Field(..., min_length=1, description="The text content of the subtitle.")
    start_time: float = Field(..., ge=0, description="Start time of the subtitle in seconds (relative to the final video).")
    end_time: float = Field(..., gt=0, description="End time of the subtitle in seconds (relative to the final video).")

    class Config:
        validate_assignment = True

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Ensure end_time is always greater than start_time
        def validate_times(self):
            if self.end_time <= self.start_time:
                raise ValueError("end_time must be greater than start_time")
        cls.model_post_init = validate_times


class ProcessClipsRequest(BaseModel):
    """
    Request body for joining multiple clips, adding subtitles and voice-over.
    """
    clips: List[ClipInfo] = Field(..., min_items=1, description="List of video clips to be concatenated.")
    voice_over_audio_url: Optional[str] = Field(None, description="URL of the audio file to use as a voice-over.")
    subtitles: List[SubtitleEntry] = Field([], description="List of subtitle entries to overlay on the video.")
    output_filename: Optional[str] = Field(None, description="Desired name for the output video file. If not provided, a UUID will be generated.")

# --- Helper Function to Run FFmpeg ---

async def run_ffmpeg_command(command: list[str]):
    """
    Executes an FFmpeg command asynchronously.
    Raises HTTPException if FFmpeg returns a non-zero exit code.
    """
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_message = f"FFmpeg error:\nSTDOUT:\n{stdout.decode()}\nSTDERR:\n{stderr.decode()}"
        print(error_message) # Log the full FFmpeg error for debugging
        raise HTTPException(status_code=500, detail=f"FFmpeg command failed. See logs for details.")
    
    print(f"FFmpeg command executed successfully. STDOUT: {stdout.decode()}\nSTDERR: {stderr.decode()}")


# --- Background Task for File Cleanup ---

def cleanup_file(filepath: str):
    """Removes a file from the filesystem."""
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
            print(f"Cleaned up temporary file: {filepath}")
        except Exception as e:
            print(f"Error cleaning up file {filepath}: {e}")

# --- Endpoint 1: Generate TikTok Clip from Image ---

@app.post("/generate-clip/", summary="Generate a TikTok-style video clip from an image")
async def generate_clip_endpoint(request: GenerateClipRequest, background_tasks: BackgroundTasks):
    """
    Generates a vertical video clip (suitable for TikTok/Reels) from a single image.
    The image will be zoomed and panned over the specified duration.

    - **image_url**: URL of the input image (ideally 576x1024 or similar 9:16 aspect ratio).
    - **length**: Desired duration of the output video in seconds.
    - **frame_rate**: Frames per second for the output video.
    - **zoom_speed**: Controls how fast the image zooms in. A value of 0.003 is a good starting point.
    - **id**: An optional unique identifier for the request, used in the output filename.
    """
    request_id = request.id if request.id else str(uuid.uuid4())
    image_temp_path = os.path.join(TEMP_FILES_DIR, f"input_image_{request_id}.png")
    output_video_path = os.path.join(TEMP_FILES_DIR, f"tiktok_clip_{request_id}.mp4")

    # Add files to cleanup in background
    background_tasks.add_task(cleanup_file, image_temp_path)
    background_tasks.add_task(cleanup_file, output_video_path)

    try:
        # 1. Download the image
        print(f"Downloading image from {request.image_url} to {image_temp_path}")
        async with httpx.AsyncClient() as client:
            response = await client.get(request.image_url)
            response.raise_for_status() # Raise an exception for bad status codes
            with open(image_temp_path, "wb") as f:
                f.write(response.content)
        print("Image downloaded successfully.")

        # 2. FFmpeg command for zoompan effect
        # We target a common TikTok resolution like 720x1280.
        # Since input is 576x1024 (9:16), scaling to 720x1280 (also 9:16)
        # maintains aspect ratio. The zoompan filter then applies the motion.
        
        # 'zoom' parameter in zoompan filter expressions:
        # starts at 1 and increases by zoom_speed * 'on' (frame number).
        # We use min(..., 1.5) to cap the zoom at 1.5x the original size. Adjust as needed.
        
        ffmpeg_command = [
            "-loop", "1",  # Loop the single input image indefinitely
            "-i", image_temp_path,
            "-vf",
            # Scale the input image to fill the target resolution, maintaining aspect ratio.
            # 'force_original_aspect_ratio=increase' ensures it fills without black bars
            # on the shorter dimension, so zoompan has content to work with.
            f"scale={TIKTOK_REELS_WIDTH}:{TIKTOK_REELS_HEIGHT}:force_original_aspect_ratio=increase,"
            f"zoompan=z='min(zoom+{request.zoom_speed}*on,1.5)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s='{TIKTOK_REELS_WIDTH}x{TIKTOK_REELS_HEIGHT}'",
            "-pix_fmt", "yuv420p",  # Essential for broad video player compatibility
            "-r", str(request.frame_rate),
            "-t", str(request.length),
            "-c:v", "libx264",
            "-preset", "medium",    # 'medium' offers a good balance; 'fast'/'superfast' for speed, 'slow' for quality
            "-crf", "23",            # Constant Rate Factor: 0 (lossless) to 51 (worst quality). 23 is a good default.
            "-movflags", "+faststart", # Optimizes for web streaming
            "-y", # Overwrite output file if it exists
            output_video_path
        ]

        print(f"Executing FFmpeg command for clip generation: {' '.join(ffmpeg_command)}")
        await run_ffmpeg_command(ffmpeg_command)
        print(f"Video clip generated: {output_video_path}")

        # Return the generated video file
        return FileResponse(output_video_path, media_type="video/mp4", filename=os.path.basename(output_video_path))

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Failed to download image: {e.response.status_code} - {e.response.text}")
    except Exception as e:
        print(f"Unhandled error in generate_clip_endpoint: {e}")
        raise HTTPException(status_code=500, detail=f"An internal server error occurred: {e}")


# --- Endpoint 2: Process Clips (Join, Subtitles, Voice-over) ---

@app.post("/process-clips/", summary="Join clips, add subtitles, and voice-over")
async def process_clips_endpoint(request: ProcessClipsRequest, background_tasks: BackgroundTasks):
    """
    Takes multiple video clips, concatenates them, overlays subtitles, and adds a voice-over audio track.

    - **clips**: A list of `ClipInfo` specifying the video files to join.
      (Note: These files must be accessible on the server where the API runs).
    - **voice_over_audio_url**: Optional URL to an audio file for voice-over.
    - **subtitles**: Optional list of `SubtitleEntry` objects for text overlays.
    - **output_filename**: Optional name for the final output video file.
    """
    
    # Generate unique IDs for temporary files
    process_id = str(uuid.uuid4())
    concat_list_path = os.path.join(TEMP_FILES_DIR, f"concat_list_{process_id}.txt")
    voice_over_temp_path = None
    output_video_path = os.path.join(TEMP_FILES_DIR, request.output_filename if request.output_filename else f"final_reel_{process_id}.mp4")

    # Add files to cleanup in background
    background_tasks.add_task(cleanup_file, concat_list_path)
    if voice_over_temp_path: # Will be added after download
        background_tasks.add_task(cleanup_file, voice_over_temp_path)
    background_tasks.add_task(cleanup_file, output_video_path)


    try:
        # 1. Download voice-over audio if provided
        if request.voice_over_audio_url:
            voice_over_temp_path = os.path.join(TEMP_FILES_DIR, f"voiceover_{process_id}.mp3")
            print(f"Downloading voice-over from {request.voice_over_audio_url} to {voice_over_temp_path}")
            async with httpx.AsyncClient() as client:
                response = await client.get(request.voice_over_audio_url)
                response.raise_for_status()
                with open(voice_over_temp_path, "wb") as f:
                    f.write(response.content)
            background_tasks.add_task(cleanup_file, voice_over_temp_path) # Add to cleanup list after successful download
            print("Voice-over downloaded successfully.")

        # 2. Create a concat list file for the FFmpeg concat demuxer
        # IMPORTANT: Ensure 'clips[i].filename' points to files accessible by the FFmpeg process.
        # If they are remote URLs, you need to download them here first and update filename.
        print(f"Creating concat list file: {concat_list_path}")
        with open(concat_list_path, "w") as f:
            for clip in request.clips:
                # Basic validation: ensure clip file exists (or is a valid URL if downloading)
                if not os.path.exists(clip.filename):
                    # In a real app, you'd likely download remote clips here
                    raise HTTPException(status_code=400, detail=f"Clip file not found: {clip.filename}. Please ensure all clip files are accessible on the server.")
                f.write(f"file '{clip.filename}'\n")
        print("Concat list file created.")

        # Base FFmpeg command for concatenation
        # Using concat demuxer which is efficient for joining same-format clips (stream copy)
        # If clips have different formats/resolutions, you'd need the `concat` *filter*
        # and potentially `scale` filters for each input.
        ffmpeg_inputs = ["-f", "concat", "-safe", "0", "-i", concat_list_path]
        
        # Determine video and audio mapping and filter complex
        filter_complex_parts = []
        map_options = []
        
        # Start with the concatenated video and audio streams
        # [0:v] and [0:a] refer to the video and audio from the concatenated input (concat_list_path)
        current_video_input = "[0:v]"
        current_audio_input = "[0:a]"
        input_index_counter = 1 # For voice-over audio

        # Add voice-over if present
        if voice_over_temp_path:
            ffmpeg_inputs.extend(["-i", voice_over_temp_path])
            # Mix original audio with voice-over
            filter_complex_parts.append(f"{current_audio_input}[{input_index_counter}:a]amix=inputs=2:duration=longest[mixed_audio]")
            current_audio_input = "[mixed_audio]"
            input_index_counter += 1 # Increment for next potential input

        # Add subtitles using drawtext filter
        if request.subtitles:
            # We need to apply drawtext after concatenation.
            # Each subtitle entry gets its own drawtext filter, enabled by its time range.
            # Using 'x=(w-text_w)/2' and 'y=h-50' for centered bottom text.
            for i, sub in enumerate(request.subtitles):
                # You might need to escape single quotes in subtitle text if they appear
                escaped_text = sub.text.replace("'", "'\\''")
                
                # Each drawtext filter applies to the current video input and outputs a new video stream.
                # So we chain them.
                if i == 0: # First subtitle applies to the concatenated video
                    filter_complex_parts.append(
                        f"{current_video_input}drawtext=text='{escaped_text}':x=(w-text_w)/2:y=h-50:fontsize=48:fontcolor=white:borderw=2:bordercolor=black:enable='between(t,{sub.start_time},{sub.end_time})'[v_temp{i}]"
                    )
                else: # Subsequent subtitles apply to the output of the previous drawtext
                    filter_complex_parts.append(
                        f"[v_temp{i-1}]drawtext=text='{escaped_text}':x=(w-text_w)/2:y=h-50:fontsize=48:fontcolor=white:borderw=2:bordercolor=black:enable='between(t,{sub.start_time},{sub.end_time})'[v_temp{i}]"
                    )
            current_video_input = f"[v_temp{len(request.subtitles)-1}]" # Last video output of the chain

        # Final mapping
        map_options.append(f"-map {current_video_input}")
        map_options.append(f"-map {current_audio_input}")
        
        final_command = []
        final_command.extend(ffmpeg_inputs)

        if filter_complex_parts:
            final_command.extend(["-filter_complex", ";".join(filter_complex_parts)])
        
        final_command.extend(map_options)
        
        # Output options
        final_command.extend([
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k", # Audio bitrate
            "-movflags", "+faststart",
            "-y",
            output_video_path
        ])

        print(f"Executing FFmpeg command for clip processing: {' '.join(final_command)}")
        await run_ffmpeg_command(final_command)
        print(f"Video processed: {output_video_path}")

        # Return the generated video file
        return FileResponse(output_video_path, media_type="video/mp4", filename=os.path.basename(output_video_path))

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Failed to download audio: {e.response.status_code} - {e.response.text}")
    except ValueError as e: # Catch Pydantic validation errors explicitly
        raise HTTPException(status_code=422, detail=f"Validation error: {e}")
    except HTTPException: # Re-raise FastAPI's own HTTPExceptions
        raise
    except Exception as e:
        print(f"Unhandled error in process_clips_endpoint: {e}")
        raise HTTPException(status_code=500, detail=f"An internal server error occurred: {e}")

# --- Root Endpoint ---
@app.get("/", summary="Root endpoint")
async def read_root():
    return {"message": "Welcome to the TikTok Video Generator API! Visit /docs for API documentation."}
