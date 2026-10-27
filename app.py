"""
Flask Web Application for Driver Drowsiness Detection System
Provides a real-time web dashboard with live video streaming, telemetry, and browser-based audio alerts.
Light Theme Edition with High-Performance Smooth Streaming.
"""

import os
import time
import threading
from datetime import datetime
import cv2
from flask import Flask, render_template, Response, jsonify, request, send_file
from simple_detection import SimpleDrowsinessDetector

try:
    from pygrabber.dshow_graph import FilterGraph
    HAS_PYGRABBER = True
except ImportError:
    HAS_PYGRABBER = False

app = Flask(__name__)

class VideoStreamManager:
    def __init__(self):
        self.cap = None
        self.detector = None
        self.is_running = False
        self.lock = threading.Lock()
        self.frame_condition = threading.Condition()
        self.latest_frame_jpeg = None
        self.frame_id = 0
        self.camera_index = 0
        self.use_dshow = True
        self.mirror_view = True
        self.smoothed_ear = 0.3
        self.latest_state = {
            "active": False,
            "ear": 0.3,
            "eyes_status": "WAITING",
            "drowsiness": 0.0,
            "level": "NORMAL",
            "closed_frames": 0,
            "alert_triggered": False,
            "alert_level": "normal",
            "fps": 0,
            "sound_alert": None,
            "camera_index": 0,
            "mirror": True
        }
        self.thread = None
        self.fps = 0
        self.ear_history = []
        self.max_history = 50

    def get_available_cameras(self):
        """Returns detected camera devices"""
        devices = []
        if HAS_PYGRABBER:
            try:
                graph = FilterGraph()
                names = graph.get_input_devices()
                for idx, name in enumerate(names):
                    devices.append({"id": idx, "name": name})
            except Exception as e:
                print(f"[WARN] Failed listing cameras via pygrabber: {e}")
        
        if not devices:
            devices = [
                {"id": 0, "name": "USB Video Device (Laptop Camera)"},
                {"id": 1, "name": "Camera 1"},
                {"id": 2, "name": "Camera 2"}
            ]
        return devices

    def start(self, camera_index=None):
        with self.lock:
            if camera_index is not None:
                self.camera_index = int(camera_index)

            if self.is_running:
                return True
            
            print(f"[INFO] Opening camera {self.camera_index} with DirectShow backend...")
            self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
            if not self.cap.isOpened():
                print(f"[WARN] Failed with DSHOW, trying default backend on camera {self.camera_index}...")
                self.cap = cv2.VideoCapture(self.camera_index)

            if not self.cap.isOpened():
                print(f"[ERROR] Could not open camera {self.camera_index}")
                return False

            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if not self.detector:
                self.detector = SimpleDrowsinessDetector()

            self.is_running = True
            self.ear_history = []
            self.smoothed_ear = 0.3
            self.thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.thread.start()
            print(f"[INFO] Video stream and detection active on camera {self.camera_index}.")
            return True

    def stop(self):
        with self.lock:
            self.is_running = False
            if self.cap:
                self.cap.release()
                self.cap = None
            with self.frame_condition:
                self.latest_frame_jpeg = None
                self.frame_id += 1
                self.frame_condition.notify_all()
            self.latest_state["active"] = False
            self.latest_state["eyes_status"] = "STOPPED"
            print("[INFO] Video stream stopped.")

    def _capture_loop(self):
        frame_count = 0
        fps_start = time.time()
        sound_trigger_cooldown = 0

        while self.is_running:
            loop_start = time.time()
            if not self.cap or not self.cap.isOpened():
                break

            ret, frame = self.cap.read()
            if not ret or frame is None:
                time.sleep(0.02)
                continue

            # Mirror view for natural driver preview
            if self.mirror_view:
                frame = cv2.flip(frame, 1)

            # Process frame with drowsiness detector
            processed_frame, state = self.detector.process_frame(frame)

            # Smooth EAR with exponential moving average to eliminate jitter
            raw_ear = state.get("ear", 0.3)
            self.smoothed_ear = round(0.4 * raw_ear + 0.6 * self.smoothed_ear, 3)

            # Compute FPS
            frame_count += 1
            if time.time() - fps_start >= 1.0:
                self.fps = round(frame_count / (time.time() - fps_start), 1)
                frame_count = 0
                fps_start = time.time()

            # Encode frame to JPEG with optimal speed/quality balance
            ret, buffer = cv2.imencode('.jpg', processed_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ret:
                frame_bytes = buffer.tobytes()
                with self.frame_condition:
                    self.latest_frame_jpeg = frame_bytes
                    self.frame_id += 1
                    self.frame_condition.notify_all()

                # Instant sound alert trigger for danger state (no lag, no 2-second bottleneck)
                level = state.get("level", "NORMAL")
                sound_to_emit = level.lower() if level != "NORMAL" else None

                with self.lock:
                    self.ear_history.append({
                        "t": datetime.now().strftime("%H:%M:%S"),
                        "ear": self.smoothed_ear
                    })
                    if len(self.ear_history) > self.max_history:
                        self.ear_history.pop(0)

                    self.latest_state = {
                        "active": True,
                        "ear": self.smoothed_ear,
                        "raw_ear": round(raw_ear, 3),
                        "eyes_status": state.get("eyes_status", "OPEN"),
                        "drowsiness": round(state.get("drowsiness", 0.0), 1),
                        "level": level,
                        "closed_frames": state.get("closed_frames", 0),
                        "alert_triggered": state.get("alert_triggered", False),
                        "fps": self.fps,
                        "sound_alert": sound_to_emit,
                        "camera_index": self.camera_index,
                        "mirror": self.mirror_view
                    }

            # Frame rate cap to maintain stability
            if self.detector:
                target_frame_time = 1.0 / self.detector.max_fps
                elapsed = time.time() - loop_start
                if elapsed < target_frame_time:
                    time.sleep(target_frame_time - elapsed)

        if self.cap:
            self.cap.release()
            self.cap = None

    def wait_for_new_frame(self, last_id, timeout=0.08):
        """Paced frame retrieval to guarantee zero duplicate network frames and smooth playback"""
        with self.frame_condition:
            if self.frame_id == last_id and self.is_running:
                self.frame_condition.wait(timeout=timeout)
            return self.latest_frame_jpeg, self.frame_id

    def get_state(self):
        with self.lock:
            state = dict(self.latest_state)
            state["ear_threshold"] = self.detector.EAR_THRESHOLD if self.detector else 0.20
            state["alert_frames"] = self.detector.ALERT_FRAMES if self.detector else 15
            state["critical_frames"] = self.detector.CRITICAL_FRAMES if self.detector else 45
            state["history"] = list(self.ear_history)
            if self.detector:
                stats = self.detector.get_session_stats()
                state["stats"] = {
                    "duration_minutes": round(stats.get("duration_minutes", 0), 1),
                    "total_frames": stats.get("total_frames", 0),
                    "total_alerts": stats.get("total_alerts", 0),
                    "avg_ear": round(float(stats.get("avg_ear", 0)), 3),
                    "csv_file": stats.get("csv_file", "")
                }
            else:
                state["stats"] = None
            return state

    def update_settings(self, ear_threshold=None, alert_frames=None, critical_frames=None):
        with self.lock:
            if not self.detector:
                return False
            if ear_threshold is not None:
                self.detector.EAR_THRESHOLD = float(ear_threshold)
            if alert_frames is not None:
                self.detector.ALERT_FRAMES = int(alert_frames)
            if critical_frames is not None:
                self.detector.CRITICAL_FRAMES = int(critical_frames)
            return True


stream_manager = VideoStreamManager()

@app.route("/")
def index():
    return render_template("index.html")

def generate_frames():
    """Generator function that yields only new frames, preventing stutter and network choke"""
    last_id = -1
    while True:
        frame_bytes, current_id = stream_manager.wait_for_new_frame(last_id, timeout=0.06)
        if frame_bytes and current_id != last_id:
            last_id = current_id
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        elif not stream_manager.is_running:
            time.sleep(0.05)

@app.route("/video_feed")
def video_feed():
    if not stream_manager.is_running:
        stream_manager.start()
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/api/cameras")
def api_cameras():
    return jsonify(stream_manager.get_available_cameras())

@app.route("/api/status")
def api_status():
    return jsonify(stream_manager.get_state())

@app.route("/api/control", methods=["POST"])
def api_control():
    data = request.get_json() or {}
    action = data.get("action")
    
    if action == "start":
        camera_index = data.get("camera_index", stream_manager.camera_index)
        success = stream_manager.start(camera_index=camera_index)
        return jsonify({"success": success, "active": stream_manager.is_running})
    elif action == "stop":
        stream_manager.stop()
        return jsonify({"success": True, "active": False})
    elif action == "switch_camera":
        camera_index = data.get("camera_index", 0)
        stream_manager.stop()
        time.sleep(0.3)
        success = stream_manager.start(camera_index=camera_index)
        return jsonify({"success": success, "active": stream_manager.is_running})
    elif action == "toggle_mirror":
        stream_manager.mirror_view = not stream_manager.mirror_view
        return jsonify({"success": True, "mirror": stream_manager.mirror_view})
    elif action == "update_settings":
        success = stream_manager.update_settings(
            ear_threshold=data.get("ear_threshold"),
            alert_frames=data.get("alert_frames"),
            critical_frames=data.get("critical_frames")
        )
        return jsonify({"success": success})
    
    return jsonify({"error": "Unknown action"}), 400

@app.route("/api/send-alert", methods=["POST"])
def send_alert_api():
    """API endpoint to dispatch emergency sleep/faint email alerts"""
    data = request.get_json() or {}
    to_email = data.get("to_email", "driverdrowsiness001@gmail.com").strip()
    level = data.get("level", "CRITICAL")
    drowsiness = data.get("drowsiness", 100)
    closed_seconds = data.get("closed_seconds", 2.0)
    incident_type = data.get("incident_type", "DRIVER_ASLEEP_OR_FAINTED")
    timestamp = data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587").strip())
    sender_email = os.getenv("SENDER_EMAIL", "driverdrowsiness001@gmail.com").strip()
    sender_password = os.getenv("SENDER_PASSWORD", "jrbllxdsihhnzpow").strip()

    email_sent = False
    status_message = ""

    if sender_email and sender_password:
        try:
            import smtplib
            from email.message import EmailMessage
            msg = EmailMessage()
            msg["Subject"] = f"🚨 SOS ALERT: Driver {level} - Sleep/Faint Detected"
            msg["From"] = sender_email
            msg["To"] = to_email
            content = (
                "══════════════════════════════════════════════════\n"
                "   🚨 DRIVER DROWSINESS & SAFETY EMERGENCY ALERT\n"
                "══════════════════════════════════════════════════\n\n"
                f"Status: {level}\n"
                f"Incident: {incident_type}\n"
                f"Detection Time: {timestamp}\n"
                f"Eyes Closed Duration: {closed_seconds:.1f} seconds\n"
                f"Drowsiness Risk: {drowsiness}%\n\n"
                "⚠️ WARNING:\n"
                "The driver appears to have fallen asleep or lost consciousness.\n"
                "Immediate contact or intervention is strongly recommended.\n\n"
                "══════════════════════════════════════════════════\n"
                "Automated safety alert from Driver Drowsiness Alert System"
            )
            msg.set_content(content)
            with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
                server.starttls()
                server.login(sender_email, sender_password)
                server.send_message(msg)
            email_sent = True
            status_message = f"Emergency alert email successfully dispatched to {to_email}"
        except Exception as e:
            status_message = f"SMTP error while sending to {to_email}: {str(e)}"
    else:
        status_message = (
            f"Emergency alert registered for {to_email}. "
            "To send live emails via Gmail, configure SENDER_EMAIL and SENDER_PASSWORD in environment variables."
        )

    return jsonify({
        "success": True,
        "email_sent": email_sent,
        "recipient": to_email,
        "message": status_message,
        "timestamp": timestamp,
        "incident": incident_type
    })

@app.route("/api/download_csv")
def download_csv():
    state = stream_manager.get_state()
    csv_file = state.get("stats", {}).get("csv_file") if state.get("stats") else None
    if csv_file and os.path.exists(csv_file):
        return send_file(csv_file, as_attachment=True)
    
    files = [f for f in os.listdir(".") if f.startswith("drowsiness_log_") and f.endswith(".csv")]
    if files:
        latest = max(files, key=os.path.getmtime)
        return send_file(latest, as_attachment=True)
    
    return jsonify({"error": "No CSV log found"}), 404

if __name__ == "__main__":
    # Start laptop camera automatically using DirectShow
    stream_manager.start(camera_index=0)
    print("\n" + "=" * 65)
    print(" [ONLINE] DRIVER DROWSINESS ALERT SYSTEM - WEB SERVER RUNNING")
    print(" Physical Laptop Webcam Selected via DirectShow")
    print(" Open your browser and visit: http://127.0.0.1:5000")
    print("=" * 65 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
