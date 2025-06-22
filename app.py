import cv2
from flask import Flask, render_template, request, send_file, jsonify, Response
from werkzeug.utils import secure_filename
from pathlib import Path
import uuid
import io
import tempfile
import torch
import numpy as np
from torchvision.ops import nms
import os
import subprocess
import datetime

app = Flask(__name__)

global width, height
width = 1920
height = 1080

def blur_region(image, output, ksize=(51, 51)):
    image = cv2.resize(image, (width, height))
    if output is None:
        return image
    if isinstance(output, tuple):
        if len(output) >= 1:
            detection_boxes = output[0]
            if hasattr(detection_boxes, 'tolist'):
                box = detection_boxes.tolist()
                print(f"Detection boxes shape: {detection_boxes.shape}, converted to: {len(box)} detections")
            else:
                print(f"Warning: detection_boxes has no tolist method: {type(detection_boxes)}")
                return image
        else:
            return image
    else:
        try:
            box = output[0].tolist()
        except:
            return image
    try:
        if not isinstance(box, list) or len(box) == 0:
            print(f"Warning: box is not a valid list: {type(box)}, length: {len(box) if isinstance(box, list) else 'N/A'}")
            return image
        if not isinstance(box[0], list):
            print(f"Warning: Expected list of detection boxes, got: {type(box[0])}")
            return image
        for i in range(len(box)):
            if len(box[i]) != 4:
                print(f"Warning: Detection {i} has {len(box[i])} coordinates, expected 4")
                continue
            for j in range(4):
                box[i][j] = int(box[i][j])
        for i in range(len(box)):
            if output[2][i] >= 0.1:
                x_min, y_min, x_max, y_max = box[i]
                x_min = max(0, min(x_min, image.shape[1]))
                y_min = max(0, min(y_min, image.shape[0]))
                x_max = max(0, min(x_max, image.shape[1]))
                y_max = max(0, min(y_max, image.shape[0]))
                if x_max <= x_min or y_max <= y_min:
                    continue
                roi = image[y_min:y_max, x_min:x_max]
                blurred_roi = cv2.GaussianBlur(roi, ksize, 0)
                image[y_min:y_max, x_min:x_max] = blurred_roi
    except Exception as e:
        print(f"Error in blur_region: {e}")
        print(f"Output type: {type(output)}")
        if isinstance(output, tuple):
            print(f"Output tuple length: {len(output)}")
            for i, item in enumerate(output):
                print(f"Output[{i}] type: {type(item)}, shape: {getattr(item, 'shape', 'N/A')}")
    return image

def run_model(lp_model, frame):
    resized_frame = cv2.resize(frame, (width, height))
    input_tensor = torch.from_numpy(resized_frame).permute(2, 0, 1)
    with torch.no_grad():
        detections = lp_model(input_tensor)
    return detections

def process_video(video_path):
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Could not open video file: {video_path}")
        model_path1 = "/mnt/c/Users/naiya/blurify/blurify-devsite/components/models/ego_blur_lp.jit"
        model_path2 = "/mnt/c/Users/naiya/blurify/blurify-devsite/components/models/ego_blur_face.jit"

        audio_file = "audio.mp3"
        ffmpeg_extract_audio_cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-q:a", "0", "-map", "a", audio_file
        ]
        subprocess.run(ffmpeg_extract_audio_cmd, check=True)
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_num = 0
        frames = []
        detections = None
        lp_model = torch.jit.load(model_path1)
        face_model = torch.jit.load(model_path2)
        lp_model.eval()
        face_model.eval()
        
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_num % 4 == 0:
                detections1 = run_model(lp_model, frame)
                detections2 = run_model(face_model, frame)
                print(detections1)
                print(f"Processed frame {frame_num}")
            if detections1 is not None or detections2 is not None:
                frame = blur_region(frame, detections1)
                frame = blur_region(frame, detections2)

            frame_num += 1
            frames.append(frame)
        cap.release()
        print(f"Extracted {frame_num} frames")
        out = cv2.VideoWriter(
            "temp_video.mp4",
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps,
            (width, height),
        )
        for frame in frames:
            out.write(frame)
        out.release()
        output_file = "output_video.mp4"
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", "temp_video.mp4", "-i", audio_file,
            "-c:v", "copy", "-c:a", "aac", "-shortest", output_file
        ]
        subprocess.run(ffmpeg_cmd, check=True)
        return output_file
    except Exception as e:
        print(f"Error processing video: {str(e)}")
        raise

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/how-it-works')
def how_it_works():
    return render_template('how_it_works.html')

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html')

@app.route('/support')
def support():
    return render_template('support.html')

def cleanup_temp_files():
    temp_files = ["temp_video.mp4", "audio.mp3", "output_video.mp4"]
    for filename in os.listdir('.'):
        if filename.startswith('temp_upload_') and filename.endswith('.mp4'):
            temp_files.append(filename)
    cleaned_files = []
    for temp_file in temp_files:
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
                cleaned_files.append(temp_file)
                print(f"Cleaned up temporary file: {temp_file}")
        except Exception as e:
            print(f"Warning: Could not remove temporary file {temp_file}: {e}")
    return cleaned_files

@app.route('/process_video', methods=['POST'])
def process_video_route():
    temp_video_path = None
    try:
        print("=== Video processing request received ===")
        print(f"Request method: {request.method}")
        print(f"Request files: {list(request.files.keys())}")
        print(f"Request headers: {dict(request.headers)}")
        print(f"Request URL: {request.url}")
        if 'video' not in request.files:
            print("Error: No video file in request.files")
            return jsonify({'error': 'No video file provided'}), 400
        video_file = request.files['video']
        print(f"Video file: {video_file.filename}, Content-Type: {video_file.content_type}")
        if video_file.filename == '':
            print("Error: Empty filename")
            return jsonify({'error': 'No video file selected'}), 400
        temp_video_path = f"temp_upload_{uuid.uuid4()}.mp4"
        print(f"Saving uploaded file to: {temp_video_path}")
        video_file.save(temp_video_path)
        print("Starting video processing...")
        output_path = process_video(temp_video_path)
        print(f"Video processing complete. Output: {output_path}")
        with open(output_path, 'rb') as f:
            video_data = f.read()
        print(f"Read processed video data: {len(video_data)} bytes")
        video_id = str(uuid.uuid4())
        if not hasattr(app, 'video_cache'):
            app.video_cache = {}
        app.video_cache[video_id] = {
            'data': video_data,
            'filename': f'processed_{video_file.filename}',
            'temp_files': [temp_video_path, output_path]
        }
        print(f"Video cached with ID: {video_id}")
        print("=== Video processing request completed successfully ===")
        return jsonify({
            'success': True,
            'video_id': video_id,
            'message': 'Video processed successfully'
        })
    except Exception as e:
        print(f"=== Error in video processing: {str(e)} ===")
        import traceback
        traceback.print_exc()
        if temp_video_path and os.path.exists(temp_video_path):
            try:
                os.remove(temp_video_path)
                print(f"Cleaned up temp file on error: {temp_video_path}")
            except:
                pass
        return jsonify({'error': f'Processing failed: {str(e)}'}), 500

@app.route('/download/<video_id>')
def download_file(video_id):
    if not hasattr(app, 'video_cache'):
        app.video_cache = {}
    if video_id not in app.video_cache:
        return jsonify({'error': 'Video not found'}), 404
    video_info = app.video_cache[video_id]
    video_data = video_info['data']
    filename = video_info['filename']
    temp_files = video_info.get('temp_files', [])
    video_stream = io.BytesIO(video_data)
    video_stream.seek(0)
    del app.video_cache[video_id]
    print("Cleaning up temporary files after successful download...")
    for temp_file in temp_files:
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
                print(f"Cleaned up temporary file: {temp_file}")
        except Exception as e:
            print(f"Warning: Could not remove temporary file {temp_file}: {e}")
    cleanup_temp_files()
    return send_file(
        video_stream,
        mimetype='video/mp4',
        as_attachment=True,
        download_name=filename
    )

@app.route('/test')
def test():
    return jsonify({
        'status': 'ok',
        'message': 'Server is working',
        'timestamp': str(datetime.datetime.now()),
        'routes': [str(rule) for rule in app.url_map.iter_rules()]
    })

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy', 'message': 'Server is running'})

@app.route('/main.js')
def serve_main_js():
    return send_file('static/js/main.js', mimetype='application/javascript')

@app.route('/favicon.ico')
def favicon():
    return '', 204

# Ensure cleanup of temporary files on server shutdown

if __name__ == '__main__':
    print(f"PyTorch version: {torch.__version__}")
    print(f"CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"CUDA device: {torch.cuda.get_device_name(0)}")
    app.run(debug=True, host='0.0.0.0', port=5000)
