import os
import cv2
import base64
from moviepy.editor import AudioFileClip
from pydub import AudioSegment
from openai import OpenAI
import concurrent.futures

MODEL = "gpt-4o"
client = OpenAI(api_key="API KEY")


FRAMES_FOLDER = "Image"
AUDIO_FOLDER = "Audio"
OUTPUT_AUDIO_PATH = "Audio/combined_audio.mp3"

def combine_audio_clips(folder_path, output_path):
    audio_files = sorted([os.path.join(folder_path, f) for f in os.listdir(folder_path) if f.endswith('.mp3')])
    combined = AudioSegment.empty()
    for audio_file in audio_files:
        audio_segment = AudioSegment.from_mp3(audio_file)
        combined += audio_segment
    combined.export(output_path, format="mp3")
    print(f"Combined audio saved to {output_path}")

def encode_image_to_base64(image_path):
    with open(image_path, 'rb') as image_file:
        image_data = image_file.read()
    return base64.b64encode(image_data).decode("utf-8")

def process_images(folder_path):
    image_files = sorted(os.listdir(folder_path))
    with concurrent.futures.ThreadPoolExecutor() as executor:
        base64_images = list(executor.map(encode_image_to_base64, [os.path.join(folder_path, img) for img in image_files]))
    print(f"Processed {len(base64_images)} images from {folder_path}")
    return base64_images

combine_audio_clips(AUDIO_FOLDER, OUTPUT_AUDIO_PATH)

base64Frames = process_images(FRAMES_FOLDER)

with open(OUTPUT_AUDIO_PATH, "rb") as audio_file:
    transcription = client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
    )

response = client.chat.completions.create(
    model=MODEL,
    messages=[
        {"role": "system", "content": "You're my all-knowing helpful assistant. I will give you data to better view the environment around you. Answer my question or whatever I may ask from you. Speak in a first-person manner, in a conversational style to me. Don't tell me this is audio or this is an image. Just try to analyze it, and see if you can answer any question I may have asked."},
        {"role": "user", "content": [
            "These are what I see: ",
            *map(lambda x: {"type": "image_url", "image_url": {"url": f'data:image/jpg;base64,{x}', "detail": "low"}}, base64Frames),
            {"type": "text", "text": f"This is what I hear: {transcription.text}"}
        ]},
    ],
    temperature=0.2,
)

print(response.choices[0].message.content)
