from http.server import BaseHTTPRequestHandler
import json
import os
import smtplib
from email.message import EmailMessage
from datetime import datetime

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        try:
            data = json.loads(post_data.decode('utf-8'))
        except Exception:
            data = {}

        # The emergency recipient email requested by the user
        to_email = data.get("to_email", "").strip()
        level = data.get("level", "CRITICAL")
        drowsiness = data.get("drowsiness", 100)
        closed_seconds = data.get("closed_seconds", 2.0)
        incident_type = data.get("incident_type", "DRIVER_ASLEEP_OR_FAINTED")
        timestamp = data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        if not to_email:
            to_email = "emergency-contact@example.com"

        # Dedicated sender email account
        sender_email = os.getenv("SENDER_EMAIL", "driverdrowsiness001@gmail.com").strip()
        sender_password = os.getenv("SENDER_PASSWORD", "jrbllxdsihhnzpow").strip()
        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com").strip()
        smtp_port = int(os.getenv("SMTP_PORT", "587").strip())

        email_sent = False
        status_message = ""

        if sender_email and sender_password:
            try:
                msg = EmailMessage()
                msg["Subject"] = f"🚨 SOS EMERGENCY ALERT: Driver {level} - Sleep/Faint Detected"
                msg["From"] = f"Driver Safety Monitor <{sender_email}>"
                msg["To"] = to_email
                
                content = (
                    "═══════════════════════════════════════════════════════════════\n"
                    "   🚨 DRIVER DROWSINESS & SAFETY MONITOR - EMERGENCY ALERT\n"
                    "═══════════════════════════════════════════════════════════════\n\n"
                    f"Sender: {sender_email}\n"
                    f"Recipient (Emergency Contact): {to_email}\n"
                    f"Alert Level: {level}\n"
                    f"Incident Category: {incident_type}\n"
                    f"Detection Timestamp: {timestamp}\n"
                    f"Eyes Closed Duration: {closed_seconds:.1f} seconds\n"
                    f"Drowsiness Risk: {drowsiness}%\n\n"
                    "⚠️ CRITICAL NOTICE:\n"
                    "The system detected that the driver has fallen asleep or lost consciousness.\n"
                    "Please contact or check on the driver immediately to prevent an accident.\n\n"
                    "═══════════════════════════════════════════════════════════════\n"
                    "Automated life-safety dispatch from Driver Drowsiness Alert System\n"
                    f"Dispatched via: {sender_email}"
                )
                msg.set_content(content)

                with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
                    server.starttls()
                    server.login(sender_email, sender_password)
                    server.send_message(msg)

                email_sent = True
                status_message = f"Emergency alert email successfully sent from {sender_email} to {to_email}"
            except Exception as e:
                status_message = f"SMTP error while sending from {sender_email} to {to_email}: {str(e)}"
        else:
            status_message = (
                f"Emergency alert registered from {sender_email} to {to_email}. "
                "To send live emails via Gmail, set SENDER_PASSWORD (Gmail App Password) in your Vercel or local environment variables."
            )

        response = {
            "success": True,
            "email_sent": email_sent,
            "sender": sender_email,
            "recipient": to_email,
            "message": status_message,
            "timestamp": timestamp,
            "incident": incident_type
        }

        response_bytes = json.dumps(response).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(response_bytes)
