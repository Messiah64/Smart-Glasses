import asyncio
from bleak import BleakClient, BleakError
import numpy as np
import queue
import threading
from pydub import AudioSegment
import os
from PIL import Image
import io
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

ADDRESS = "64:E8:33:51:D1:5D"  # Replace with your ESP32's BLE address
AUDIO_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
IMAGE_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a9"

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # 16 bits = 2 bytes
CHANNELS = 1
RECORD_TIME = 3  # seconds

audio_queue = queue.Queue()
image_queue = queue.Queue()

# Create the audio and image directories if they don't exist
for directory in ["Audio", "Image"]:
    if not os.path.exists(directory):
        os.makedirs(directory)

def save_audio(audio_data, index):
    audio_array = np.frombuffer(audio_data, dtype=np.int16)
    audio_segment = AudioSegment(
        audio_array.tobytes(),
        frame_rate=SAMPLE_RATE,
        sample_width=SAMPLE_WIDTH,
        channels=CHANNELS
    )
    file_path = os.path.join("Audio", f"audio_{index}.mp3")
    audio_segment.export(file_path, format="mp3")
    logger.info(f"Audio saved as {file_path}")

def save_image(image_data, index):
    try:
        logger.debug(f"First 20 bytes of image data: {image_data[:20].hex()}")
        
        if not image_data.startswith(b'\xFF\xD8'):
            logger.error("Image data does not start with JPEG signature")
            return

        image = Image.open(io.BytesIO(image_data))
        file_path = os.path.join("Image", f"image_{index}.jpg")
        image.save(file_path)
        logger.info(f"Image saved as {file_path}")
    except Exception as e:
        logger.error(f"Error saving image: {e}")
        raw_path = os.path.join("Image", f"raw_image_data_{index}.bin")
        with open(raw_path, 'wb') as f:
            f.write(image_data)
        logger.info(f"Raw image data saved as {raw_path}")

def audio_worker():
    counter = 0
    while True:
        audio_data = audio_queue.get()
        if audio_data is None:
            break
        save_audio(audio_data, counter)
        audio_queue.task_done()
        counter += 1

def image_worker():
    counter = 0
    while True:
        image_data = image_queue.get()
        if image_data is None:
            break
        save_image(image_data, counter)
        image_queue.task_done()
        counter += 1

async def connect_ble(address, max_retries=3, retry_interval=5):
    for attempt in range(max_retries):
        try:
            client = BleakClient(address)
            await client.connect(timeout=15.0)
            logger.info(f"Connected to {address}")
            return client
        except asyncio.TimeoutError:
            logger.warning(f"Connection attempt {attempt + 1} timed out. Retrying in {retry_interval} seconds...")
            await asyncio.sleep(retry_interval)
        except BleakError as e:
            logger.error(f"Error connecting to device: {e}")
            logger.info(f"Retrying in {retry_interval} seconds...")
            await asyncio.sleep(retry_interval)
    
    logger.error(f"Failed to connect after {max_retries} attempts.")
    return None

async def main():
    client = await connect_ble(ADDRESS)
    if not client:
        logger.error("Unable to connect to the device. Please check the following:")
        logger.error("1. Ensure the ESP32 is powered on and in range.")
        logger.error("2. Verify the BLE address is correct.")
        logger.error("3. Check for any interference or connectivity issues.")
        return

    try:
        audio_buffer = bytearray()
        image_buffer = bytearray()
        expected_audio_size = SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS * RECORD_TIME

        def audio_notification_handler(sender, data):
            nonlocal audio_buffer
            audio_buffer.extend(data)
            
            if len(audio_buffer) >= expected_audio_size:
                audio_queue.put(bytes(audio_buffer[:expected_audio_size]))
                audio_buffer = audio_buffer[expected_audio_size:]
                logger.debug("Audio data received and queued")

        def image_notification_handler(sender, data):
            nonlocal image_buffer
            image_buffer.extend(data)
            
            if len(image_buffer) > 0 and data[-2:] == b'\xff\xd9':  # JPEG end marker
                logger.debug(f"Image data size: {len(image_buffer)} bytes")
                image_queue.put(bytes(image_buffer))
                image_buffer = bytearray()
                logger.debug("Image data received and queued")

        await client.start_notify(AUDIO_CHAR_UUID, audio_notification_handler)
        await client.start_notify(IMAGE_CHAR_UUID, image_notification_handler)
        
        # Start the worker threads
        audio_thread = threading.Thread(target=audio_worker)
        image_thread = threading.Thread(target=image_worker)
        audio_thread.start()
        image_thread.start()

        # Keep the connection alive
        while True:
            if not client.is_connected:
                logger.warning("Connection lost. Attempting to reconnect...")
                client = await connect_ble(ADDRESS)
                if not client:
                    logger.error("Reconnection failed. Exiting...")
                    break
            await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"An error occurred: {e}")
    finally:
        if client and client.is_connected:
            await client.disconnect()
        audio_queue.put(None)
        image_queue.put(None)
        audio_thread.join()
        image_thread.join()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopping...")
