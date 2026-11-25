import os
import base64
import traceback
import uuid
import csv
import io
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

import cv2
import numpy as np

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse, Response
from supabase import create_client


# =====================================================
# CONFIG
# =====================================================

SUPABASE_URL = "https://ebezaegdrcpssmtlotvt.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImViZXphZWdkcmNwc3NtdGxvdHZ0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg3Nzc4MTgsImV4cCI6MjEwNDM1MzgxOH0.XzcXhF1qWhSmDlWCxNXN7tEia1XiigC8hg5KnNbRgdo"

# =====================================================
# EMAIL NOTIFICATION CONFIG - EMAILJS REST API
# =====================================================
EMAILJS_SERVICE_ID = "service_h7pcpyr"
EMAILJS_TEMPLATE_ID = "template_qyvl1gd"
EMAILJS_PUBLIC_KEY = "bdU8Dx46GWog_q78b"
EMAILJS_API_URL = "https://api.emailjs.com/api/v1.0/email/send"


# =====================================================
# SUPABASE CONNECTION
# =====================================================

if not SUPABASE_KEY:
    raise RuntimeError(
        "SUPABASE_SERVICE_ROLE_KEY set karo. "
        "Windows CMD: set SUPABASE_SERVICE_ROLE_KEY=YOUR_SERVICE_ROLE_KEY"
    )

try:
    supabase = create_client(
        SUPABASE_URL,
        SUPABASE_KEY
    )
    print("✅ Supabase connected")
except Exception as error:
    print("❌ Supabase connection failed")
    print(type(error).__name__, str(error))
    raise


FEATURES = {
    "student_management": True,
    "attendance_analytics": True,
    "subject_wise_attendance": True,
    "reports_csv": True,
    "reports_pdf": True,
    "manual_attendance": True,
    "attendance_audit": True,
    "qr_fallback": False,
    "liveness_detection": False,
    "engagement_analysis": False,
}

# =====================================================
# FASTAPI
# =====================================================

app = FastAPI(
    title="AI Face Attendance",
    version="1.0.0"
)


@app.get("/api/subjects")
async def list_subjects(class_name: str | None = None):
    """List configured subjects, with attendance-derived fallback."""
    try:
        query = supabase.table("subjects").select("id,name,class_name,teacher_id").order("name")
        if class_name:
            query = query.eq("class_name", class_name)
        result = query.execute()
        return {"success": True, "subjects": result.data or []}
    except Exception:
        try:
            query = supabase.table("attendance").select("subject")
            if class_name:
                query = query.eq("class_name", class_name)
            result = query.execute()
            names = sorted({
                str(row.get("subject")).strip()
                for row in (result.data or [])
                if row.get("subject")
            })
            return {
                "success": True,
                "subjects": [{"id": None, "name": name, "class_name": class_name}
                for name in names],
                "source": "attendance"
            }
        except Exception as error:
            return {"success": False, "message": str(error)}


@app.post("/api/subjects")
async def create_subject(
    request: Request,
    name: str = Form(...),
    class_name: str = Form("")
):
    if not teacher_required(request):
        return {"success": False, "message": "Teacher login required"}

    clean_name = " ".join(name.strip().split())[:100]
    clean_class = " ".join(class_name.strip().split())[:100]

    if not clean_name:
        return {"success": False, "message": "Subject name required"}

    try:
        result = (
            supabase.table("subjects")
            .insert({"name": clean_name, "class_name": clean_class or None})
            .execute()
        )
        return {"success": True, "subject": (result.data or [None])[0]}
    except Exception as error:
        return {"success": False, "message": str(error)}


@app.get("/api/subjects/attendance")
async def subject_wise_attendance(
    class_name: str | None = None,
    subject: str | None = None,
    attendance_date: str | None = None
):
    """Subject-wise attendance report."""
    try:
        query = (
            supabase
            .table("attendance")
            .select(
                "student_id,student_name,roll_no,class_name,"
                "subject,attendance_date,status,marked_at"
            )
        )

        if class_name:
            query = query.eq("class_name", class_name)

        if subject:
            query = query.eq("subject", subject)

        if attendance_date:
            query = query.eq("attendance_date", attendance_date)

        result = query.order("attendance_date", desc=True).execute()
        rows = result.data or []

        summary = {}
        for row in rows:
            sub = row.get("subject") or "General"
            if sub not in summary:
                summary[sub] = {
                    "subject": sub,
                    "total_records": 0,
                    "present": 0,
                    "absent": 0,
                    "late": 0,
                    "attendance_percentage": 0
                }

            item = summary[sub]
            item["total_records"] += 1

            status = str(row.get("status") or "").lower()
            if status == "present":
                item["present"] += 1
            elif status == "absent":
                item["absent"] += 1
            elif status == "late":
                item["late"] += 1

        for item in summary.values():
            total = item["total_records"]
            present_like = item["present"] + item["late"]
            item["attendance_percentage"] = (
                round((present_like / total) * 100, 2) if total else 0
            )

        return {
            "success": True,
            "filters": {
                "class_name": class_name,
                "subject": subject,
                "attendance_date": attendance_date
            },
            "summary": list(summary.values()),
            "records": rows
        }

    except Exception as error:
        return {
            "success": False,
            "message": f"{type(error).__name__}: {str(error)}"
        }


@app.get("/api/features")
async def api_features():
    """Return enabled Smart Classroom modules."""
    return {
        "success": True,
        "project": "AI Smart Classroom Attendance & Engagement System",
        "features": FEATURES
    }


def send_attendance_email(student, subject, attendance_time):
    """Send attendance notification using EmailJS REST API."""
    recipient = str(student.get("email") or "").strip()
    if not recipient:
        return False, "Student email not registered."

    if not (EMAILJS_SERVICE_ID and EMAILJS_TEMPLATE_ID and EMAILJS_PUBLIC_KEY):
        return False, "EmailJS configuration is incomplete in app.py."

    try:
        name = str(student.get("name") or "Student")
        roll = str(student.get("roll_no") or "-")
        cls = str(student.get("class_name") or "-")
        date_text = attendance_time.strftime("%d %B %Y")
        time_text = attendance_time.strftime("%I:%M %p")

        template_params = {
            "to_email": recipient,
            "student_name": name,
            "roll_no": roll,
            "class_name": cls,
            "subject": str(subject or "General"),
            "date": date_text,
            "time": time_text,
            "message": (
                f"Hello {name}, your attendance has been marked PRESENT "
                f"for {subject} on {date_text} at {time_text}."
            ),
        }

        payload = {
            "service_id": EMAILJS_SERVICE_ID,
            "template_id": EMAILJS_TEMPLATE_ID,
            "user_id": EMAILJS_PUBLIC_KEY,
            "template_params": template_params,
        }

        request = urllib.request.Request(
            EMAILJS_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Origin": "http://localhost",
                "User-Agent": "AI-Smart-Classroom-Attendance/1.0",
            },
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=25) as response:
            response_text = response.read().decode("utf-8", errors="replace")
            status_code = response.getcode()

        if status_code in (200, 201) and response_text.strip().lower() in ("ok", ""):
            print(f"📧 Attendance email sent to {recipient} | EmailJS HTTP {status_code}")
            return True, "Email sent successfully through EmailJS."

        if status_code in (200, 201):
            print(f"📧 EmailJS response for {recipient}: {response_text}")
            return True, "Email sent successfully through EmailJS."

        return False, f"EmailJS HTTP {status_code}: {response_text}"

    except urllib.error.HTTPError as error:
        try:
            body = error.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(error)
        print("❌ EmailJS email failed:", error.code, body)
        return False, f"EmailJS HTTP {error.code}: {body}"

    except Exception as error:
        print("❌ Attendance email failed:", repr(error))
        return False, f"Email failed: {type(error).__name__}: {str(error)}"


@app.post("/recognize")
async def recognize_face(
    request: Request,
    image_data: str = Form(...),
    subject: str = Form("General")
):
    """Recognize a face from the camera image and mark attendance."""
    if not teacher_required(request):
        return {
            "success": False,
            "recognized": False,
            "message": "Teacher login required"
        }

    try:
        # 1. Decode camera image
        if not image_data:
            return {
                "success": False,
                "recognized": False,
                "message": "Image receive nahi hui."
            }

        if "," in image_data:
            image_data = image_data.split(",", 1)[1]

        try:
            image_bytes = base64.b64decode(image_data)
        except Exception:
            return {
                "success": False,
                "recognized": False,
                "message": "Camera image decode nahi hui."
            }

        np_array = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(np_array, cv2.IMREAD_COLOR)

        if frame is None:
            return {
                "success": False,
                "recognized": False,
                "message": "Captured image invalid hai."
            }

        # 2. Detect face in live camera image
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(80, 80)
        )

        print("🔎 Live faces detected:", len(faces))

        if len(faces) == 0:
            return {
                "success": True,
                "recognized": False,
                "message": "Live camera image me face detect nahi hua."
            }

        if len(faces) > 1:
            return {
                "success": True,
                "recognized": False,
                "message": "Ek time par sirf ek person camera ke saamne ho."
            }

        # 3. Crop live face
        x, y, w, h = faces[0]
        live_face = gray[y:y+h, x:x+w]

        if live_face.size == 0:
            return {
                "success": False,
                "recognized": False,
                "message": "Face crop nahi ho paya."
            }

        live_face = cv2.resize(live_face, (200, 200))

        # 4. Get registered students
        students_result = (
            supabase
            .table("students")
            .select("id,name,roll_no,class_name,email,face_registered")
            .eq("face_registered", True)
            .execute()
        )

        students = students_result.data or []

        if not students:
            return {
                "success": True,
                "recognized": False,
                "message": "Koi registered face available nahi hai."
            }

        # 5. Compare live face with stored face images
        try:
            cv2.face.LBPHFaceRecognizer_create
        except AttributeError:
            return {
                "success": False,
                "recognized": False,
                "message": (
                    "OpenCV face module available nahi hai. "
                    "Run: pip uninstall opencv-python -y && "
                    "pip install opencv-contrib-python"
                )
            }

        best_student = None
        best_confidence = float("inf")

        for student in students:
            student_id = str(student.get("id", "")).strip()
            if not student_id:
                continue

            file_path = f"student_id_{student_id}/face_1.jpg"

            try:
                downloaded = (
                    supabase
                    .storage
                    .from_("face_images")
                    .download(file_path)
                )

                if not downloaded:
                    continue

                stored_array = np.frombuffer(downloaded, np.uint8)
                stored_image = cv2.imdecode(stored_array, cv2.IMREAD_GRAYSCALE)

                if stored_image is None:
                    continue

                stored_faces = face_cascade.detectMultiScale(
                    stored_image,
                    scaleFactor=1.1,
                    minNeighbors=5,
                    minSize=(80, 80)
                )

                if len(stored_faces) == 0:
                    continue

                sx, sy, sw, sh = stored_faces[0]
                stored_face = stored_image[sy:sy+sh, sx:sx+sw]

                if stored_face.size == 0:
                    continue

                stored_face = cv2.resize(stored_face, (200, 200))

                recognizer = cv2.face.LBPHFaceRecognizer_create()
                recognizer.train([stored_face], np.array([1], dtype=np.int32))
                label, confidence = recognizer.predict(live_face)

                print("👤 Candidate:", student.get("name"), "| confidence:", confidence)

                if confidence < best_confidence:
                    best_confidence = confidence
                    best_student = student

            except Exception as error:
                print("⚠️ Face compare failed for", student_id, ":", repr(error))
                continue

        # 6. Confidence check
        MATCH_THRESHOLD = 85.0

        if best_student is None or best_confidence > MATCH_THRESHOLD:
            print("❌ No match. Best confidence:", best_confidence)
            return {
                "success": True,
                "recognized": False,
                "message": "Face match nahi hua. Camera ke saamne clearly dekho.",
                "confidence": (
                    None
                    if best_student is None
                    else round(float(best_confidence), 2)
                )
            }

        # 7. Mark attendance
        student_id = str(best_student["id"])
        roll_no = str(best_student["roll_no"])

        selected_subject = " ".join(str(subject or "General").strip().split())
        if not selected_subject:
            selected_subject = "General"
        selected_subject = selected_subject[:100]

        today = datetime.now(timezone.utc).date().isoformat()

        existing = (
            supabase
            .table("attendance")
            .select("id,status,attendance_date,subject")
            .eq("roll_no", roll_no)
            .eq("attendance_date", today)
            .eq("subject", selected_subject)
            .limit(1)
            .execute()
        )

        if existing.data:
            attendance = {
                "success": True,
                "already_marked": True,
                "message": "Attendance already marked today."
            }
        else:
            payload = {
                "student_id": student_id,
                "student_name": best_student.get("name"),
                "roll_no": roll_no,
                "class_name": best_student.get("class_name"),
                "attendance_date": today,
                "subject": selected_subject,
                "marked_at": datetime.now(timezone.utc).isoformat(),
                "status": "Present"
            }

            attendance_result = (
                supabase
                .table("attendance")
                .insert(payload)
                .execute()
            )

            if attendance_result.data:
                attendance = {
                    "success": True,
                    "already_marked": False,
                    "message": "Attendance marked successfully."
                }

                email_time = datetime.now()
                email_ok, email_message = send_attendance_email(
                    best_student,
                    selected_subject,
                    email_time
                )
                attendance["email_sent"] = email_ok
                attendance["email_message"] = email_message
            else:
                attendance = {
                    "success": False,
                    "already_marked": False,
                    "message": "Attendance save nahi hui."
                }

        # 8. Final response
        print(
            "✅ MATCH:",
            best_student.get("name"),
            "| roll:",
            roll_no,
            "| confidence:",
            best_confidence
        )

        return {
            "success": True,
            "recognized": True,
            "message": f"{best_student.get('name')} recognized.",
            "student": best_student,
            "confidence": round(float(best_confidence), 2),
            "attendance": attendance
        }

    except Exception as error:
        print("========== RECOGNITION ERROR ==========")
        print("ERROR TYPE:", type(error).__name__)
        print("ERROR:", repr(error))
        traceback.print_exc()
        print("========================================")
        return {
            "success": False,
            "recognized": False,
            "message": f"{type(error).__name__}: {str(error)}"
        }


# =====================================================
# FACE DETECTOR
# =====================================================

def load_face_detector():
    filename = "haarcascade_frontalface_default.xml"
    base_dir = os.path.dirname(os.path.abspath(__file__))

    paths = [
        os.path.join(base_dir, filename),
        os.path.join(cv2.data.haarcascades, filename),
        os.path.join(os.getcwd(), filename)
    ]

    for path in paths:
        if os.path.exists(path):
            try:
                detector = cv2.CascadeClassifier(path)
                if not detector.empty():
                    print("✅ FACE DETECTOR LOADED:", path)
                    return detector
            except Exception as error:
                print("⚠️ Detector error:", error)

    download_path = os.path.join(base_dir, filename)
    url = (
        "https://raw.githubusercontent.com/"
        "opencv/opencv/master/data/haarcascades/"
        "haarcascade_frontalface_default.xml"
    )

    print("⚠️ Haar Cascade XML nahi mila. Downloading automatically...")
    try:
        urllib.request.urlretrieve(url, download_path)
        detector = cv2.CascadeClassifier(download_path)
        if detector.empty():
            raise RuntimeError("XML download hua lekin OpenCV load nahi kar pa raha.")
        print("✅ FACE DETECTOR LOADED:", download_path)
        return detector
    except Exception as error:
        print("❌ FACE DETECTOR LOAD FAILED:", type(error).__name__, str(error))
        raise RuntimeError("Face detector load nahi hua. Internet ON karke app dobara start karo.")


face_cascade = load_face_detector()


# =====================================================
# AUTH LANDING PAGE
# =====================================================

@app.get("/app", response_class=HTMLResponse)
async def app_landing():
    return RedirectResponse("/login", status_code=303)


# =====================================================
# HOME PAGE (STUDENT REGISTRATION)
# =====================================================

@app.get("/", response_class=HTMLResponse)
async def home():
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Face Attendance - Student Registration</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
<style>
* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}
body {
    min-height: 100vh;
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background-color: #0b0f19;
    background-image: 
        radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.15) 0px, transparent 50%),
        radial-gradient(at 100% 100%, rgba(139, 92, 246, 0.15) 0px, transparent 50%);
    background-attachment: fixed;
    color: #f8fafc;
    line-height: 1.5;
}
.nav {
    background: rgba(15, 23, 42, 0.85);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    padding: 14px 28px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 100;
}
.brand {
    font-size: 18px;
    font-weight: 700;
    color: #fff;
    display: flex;
    align-items: center;
    gap: 10px;
    text-decoration: none;
}
.brand-badge {
    background: linear-gradient(135deg, #4f46e5, #7c3aed);
    color: white;
    width: 34px;
    height: 34px;
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
    box-shadow: 0 0 15px rgba(99, 102, 241, 0.4);
}
.nav-links {
    display: flex;
    align-items: center;
    gap: 8px;
}
.nav a {
    color: #94a3b8;
    text-decoration: none;
    font-size: 13.5px;
    font-weight: 600;
    padding: 8px 14px;
    border-radius: 9px;
    transition: all 0.2s ease;
    display: inline-flex;
    align-items: center;
    gap: 6px;
}
.nav a:hover {
    color: #f8fafc;
    background: rgba(255, 255, 255, 0.08);
}
.nav a.active {
    color: #fff;
    background: rgba(99, 102, 241, 0.2);
    border: 1px solid rgba(99, 102, 241, 0.35);
}
.container {
    width: 100%;
    max-width: 1080px;
    margin: 0 auto;
    padding: 32px 20px 60px;
}
.header-box {
    text-align: center;
    margin-bottom: 32px;
}
.header-box h1 {
    font-size: 30px;
    font-weight: 800;
    letter-spacing: -0.02em;
    color: #fff;
    margin-bottom: 8px;
}
.header-box .subtitle {
    color: #94a3b8;
    font-size: 15px;
}
.grid {
    display: grid;
    grid-template-columns: 1fr 1.1fr;
    gap: 28px;
    align-items: start;
}
.card {
    background: rgba(30, 41, 59, 0.7);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 20px;
    padding: 26px;
    box-shadow: 0 15px 35px -10px rgba(0, 0, 0, 0.5);
}
.card-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 20px;
    padding-bottom: 14px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.card-icon {
    width: 36px;
    height: 36px;
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
    background: rgba(99, 102, 241, 0.18);
    color: #818cf8;
    border: 1px solid rgba(99, 102, 241, 0.3);
}
.card-header h3 {
    font-size: 18px;
    font-weight: 700;
    color: #fff;
}
.form-group {
    margin-bottom: 16px;
}
label {
    display: block;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #94a3b8;
    margin-bottom: 6px;
}
input {
    width: 100%;
    padding: 13px 16px;
    border: 1px solid #334155;
    border-radius: 11px;
    background: #0f172a;
    color: #f8fafc;
    font-size: 14.5px;
    font-family: inherit;
    outline: none;
    transition: all 0.2s ease;
}
input:focus {
    border-color: #6366f1;
    background: #111a30;
    box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.25);
}
input::placeholder {
    color: #64748b;
}
.camera-container {
    position: relative;
    width: 100%;
    border-radius: 16px;
    overflow: hidden;
    background: #000;
    border: 2px solid rgba(99, 102, 241, 0.3);
    box-shadow: 0 10px 25px rgba(0, 0, 0, 0.6);
    aspect-ratio: 4 / 3;
    display: flex;
    align-items: center;
    justify-content: center;
}
video {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    background: #000;
}
canvas {
    display: none;
}
.scanner-overlay {
    position: absolute;
    inset: 0;
    pointer-events: none;
    display: flex;
    align-items: center;
    justify-content: center;
}
.reticle {
    width: 170px;
    height: 170px;
    border: 2px dashed rgba(99, 102, 241, 0.5);
    border-radius: 20px;
    position: relative;
}
.reticle::before, .reticle::after {
    content: '';
    position: absolute;
    width: 22px;
    height: 22px;
    border-color: #38bdf8;
    border-style: solid;
}
.reticle::before {
    top: -2px; left: -2px;
    border-width: 3px 0 0 3px;
    border-top-left-radius: 10px;
}
.reticle::after {
    bottom: -2px; right: -2px;
    border-width: 0 3px 3px 0;
    border-bottom-right-radius: 10px;
}
.btn-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-top: 16px;
}
button {
    width: 100%;
    padding: 13px 18px;
    border: none;
    border-radius: 11px;
    cursor: pointer;
    font-size: 14.5px;
    font-weight: 700;
    font-family: inherit;
    transition: all 0.2s ease;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
}
button:hover:not(:disabled) {
    transform: translateY(-1px);
    filter: brightness(1.08);
}
button:active:not(:disabled) {
    transform: translateY(0);
}
button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
    transform: none;
    filter: none;
}
#startCamera {
    background: linear-gradient(135deg, #2563eb, #3b82f6);
    color: white;
    box-shadow: 0 4px 14px rgba(37, 99, 235, 0.35);
}
#capture {
    background: linear-gradient(135deg, #059669, #10b981);
    color: white;
    box-shadow: 0 4px 14px rgba(16, 185, 129, 0.35);
}
#register {
    background: linear-gradient(135deg, #4f46e5, #7c3aed);
    color: white;
    margin-top: 8px;
    box-shadow: 0 4px 16px rgba(79, 70, 229, 0.4);
}
.status {
    color: #94a3b8;
    text-align: center;
    margin-top: 14px;
    font-size: 13.5px;
    font-weight: 500;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
}
#message {
    margin-top: 24px;
    text-align: center;
    padding: 14px 20px;
    border-radius: 12px;
    min-height: 20px;
    font-size: 14.5px;
    font-weight: 600;
    transition: all 0.3s ease;
}
#message:empty {
    display: none;
}
.success {
    background: rgba(16, 185, 129, 0.15) !important;
    border: 1px solid rgba(16, 185, 129, 0.3) !important;
    color: #6ee7b7 !important;
}
.error {
    background: rgba(239, 68, 68, 0.15) !important;
    border: 1px solid rgba(239, 68, 68, 0.3) !important;
    color: #fca5a5 !important;
}
@media (max-width: 820px) {
    .grid {
        grid-template-columns: 1fr;
    }
    .container {
        padding: 20px 16px 40px;
    }
    .nav {
        padding: 12px 18px;
    }
    .btn-grid {
        grid-template-columns: 1fr;
    }
}
</style>
</head>
<body>

<div class="nav">
    <a href="/teacher-dashboard" class="brand">
        <div class="brand-badge"><i class="fa-solid fa-robot"></i></div>
        <span>AI Face Attendance</span>
    </a>
    <div class="nav-links">
        <a href="/teacher-dashboard"><i class="fa-solid fa-chart-pie"></i> Dashboard</a>
        <a href="/take-attendance"><i class="fa-solid fa-camera"></i> Take Attendance</a>
        <a href="/login"><i class="fa-solid fa-arrow-right-to-bracket"></i> Login</a>
    </div>
</div>

<div class="container">
    <div class="header-box">
        <h1>👨‍🎓 Student Face Registration</h1>
        <div class="subtitle">Capture high-accuracy facial biometric data and save student records</div>
    </div>

    <div class="grid">
        <!-- STUDENT DETAILS -->
        <div class="card">
            <div class="card-header">
                <div class="card-icon"><i class="fa-solid fa-user-plus"></i></div>
                <h3>Student Details</h3>
            </div>

            <form id="form">
                <div class="form-group">
                    <label for="regName">Full Name</label>
                    <input id="regName" type="text" name="name" autocomplete="name" placeholder="e.g. Rahul Sharma" required>
                </div>

                <div class="form-group">
                    <label for="regRoll">Roll Number</label>
                    <input id="regRoll" type="text" name="roll_no" autocomplete="off" placeholder="e.g. CS-101" required>
                </div>

                <div class="form-group">
                    <label for="regClass">Class / Section</label>
                    <input id="regClass" type="text" name="class_name" autocomplete="off" placeholder="e.g. BCA 3rd Year" required>
                </div>

                <div class="form-group">
                    <label for="regEmail">Student Email</label>
                    <input id="regEmail" type="email" name="email" autocomplete="email" placeholder="e.g. student@gmail.com" required>
                </div>

                <button type="submit" id="register">
                    <i class="fa-solid fa-id-badge"></i> Register Student
                </button>
            </form>
        </div>

        <!-- CAMERA -->
        <div class="card">
            <div class="card-header">
                <div class="card-icon"><i class="fa-solid fa-camera-retro"></i></div>
                <h3>Face Biometric Capture</h3>
            </div>

            <div class="camera-container">
                <video id="video" autoplay playsinline></video>
                <div class="scanner-overlay">
                    <div class="reticle"></div>
                </div>
            </div>

            <canvas id="canvas"></canvas>

            <div class="btn-grid">
                <button id="startCamera" type="button">
                    <i class="fa-solid fa-video"></i> Start Camera
                </button>
                <button id="capture" type="button" disabled>
                    <i class="fa-solid fa-camera"></i> Capture Face
                </button>
            </div>

            <div class="status" id="cameraStatus">
                <i class="fa-solid fa-circle-info"></i> Camera not started
            </div>
        </div>
    </div>

    <div id="message"></div>
</div>

<script>
const form = document.getElementById("form");
const video = document.getElementById("video");
const canvas = document.getElementById("canvas");
const startCamera = document.getElementById("startCamera");
const capture = document.getElementById("capture");
const register = document.getElementById("register");
const message = document.getElementById("message");
const cameraStatus = document.getElementById("cameraStatus");

let stream = null;
let capturedImage = null;

function showMessage(text, type = "") {
    message.innerText = text;
    message.className = type;
}

startCamera.addEventListener("click", async () => {
    try {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            showMessage("Browser camera support nahi karta.", "error");
            return;
        }

        stream = await navigator.mediaDevices.getUserMedia({
            video: {
                width: { ideal: 640 },
                height: { ideal: 480 },
                facingMode: "user"
            },
            audio: false
        });

        video.srcObject = stream;
        capture.disabled = false;
        cameraStatus.innerHTML = '<span style="color:#34d399"><i class="fa-solid fa-circle-check"></i> Camera live. Face frame ke beech rakho.</span>';
        showMessage("");
    } catch(error) {
        console.error(error);
        cameraStatus.innerHTML = '<span style="color:#f87171"><i class="fa-solid fa-triangle-exclamation"></i> Camera start nahi hua.</span>';
        showMessage("Camera permission allow karo.", "error");
    }
});

capture.addEventListener("click", () => {
    if (!stream) {
        showMessage("Pehle camera start karo.", "error");
        return;
    }

    if (video.videoWidth === 0 || video.videoHeight === 0) {
        showMessage("Camera image ready nahi hai. 1-2 second wait karo.", "error");
        return;
    }

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    capturedImage = canvas.toDataURL("image/jpeg", 0.90);
    cameraStatus.innerHTML = '<span style="color:#60a5fa"><i class="fa-solid fa-camera-rotate"></i> Face photo captured successfully!</span>';
    showMessage("Photo ready. Ab Register Student dabao.", "success");
});

form.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!capturedImage) {
        showMessage("Pehle face capture karo.", "error");
        return;
    }

    register.disabled = true;
    showMessage("Student + face save ho raha hai...");

    try {
        const formData = new FormData(form);
        formData.append("face_image", capturedImage);

        const response = await fetch("/register", {
            method: "POST",
            body: formData
        });

        const data = await response.json();

        if (data.success) {
            showMessage("✅ " + data.message, "success");
            form.reset();
            capturedImage = null;
            cameraStatus.innerHTML = '<span style="color:#94a3b8"><i class="fa-solid fa-circle-check"></i> Next student ke liye ready.</span>';
            if (stream) {
                capture.disabled = false;
            }
        } else {
            showMessage("❌ " + data.message, "error");
        }
    } catch(error) {
        console.error(error);
        showMessage("❌ Server se connection nahi ho raha.", "error");
    } finally {
        register.disabled = false;
    }
});
</script>

</body>
</html>"""


# =====================================================
# REGISTER STUDENT (BACKEND API)
# =====================================================

@app.post("/register")
async def register_student(
    name: str = Form(...),
    roll_no: str = Form(...),
    class_name: str = Form(...),
    email: str = Form(...),
    face_image: str = Form(...)
):
    name = name.strip()
    roll_no = roll_no.strip()
    class_name = class_name.strip()
    email = email.strip().lower()

    if not name or not roll_no or not class_name or not email:
        return {
            "success": False,
            "message": "Sabhi fields bharna zaroori hai."
        }

    if "@" not in email or "." not in email.split("@")[-1]:
        return {
            "success": False,
            "message": "Valid student email dalo."
        }

    if not face_image:
        return {
            "success": False,
            "message": "Face photo capture karo."
        }

    try:
        existing = (
            supabase
            .table("students")
            .select("id")
            .eq("roll_no", roll_no)
            .execute()
        )

        if existing.data:
            return {
                "success": False,
                "message": "Ye Roll Number already registered hai."
            }

        if "," in face_image:
            image_data = face_image.split(",", 1)[1]
        else:
            image_data = face_image

        try:
            image_bytes = base64.b64decode(image_data)
        except Exception:
            return {
                "success": False,
                "message": "Face image decode nahi hui."
            }

        np_array = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(np_array, cv2.IMREAD_COLOR)

        if frame is None:
            return {
                "success": False,
                "message": "Captured image invalid hai."
            }

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(80, 80)
        )

        print("Detected faces:", len(faces))

        if len(faces) == 0:
            return {
                "success": False,
                "message": "Face detect nahi hua. Camera ke saamne clearly dekho."
            }

        if len(faces) > 1:
            return {
                "success": False,
                "message": "Ek time par sirf ek person camera ke saamne ho."
            }

        student = {
            "name": name,
            "roll_no": roll_no,
            "class_name": class_name,
            "email": email,
            "face_registered": False,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        result = (
            supabase
            .table("students")
            .insert(student)
            .execute()
        )

        if not result.data:
            return {
                "success": False,
                "message": "Student save nahi hua."
            }

        student_id = result.data[0]["id"]
        file_path = f"student_id_{student_id}/face_1.jpg"

        upload_result = (
            supabase
            .storage
            .from_("face_images")
            .upload(
                file_path,
                image_bytes,
                {
                    "content-type": "image/jpeg",
                    "upsert": "true"
                }
            )
        )

        print("✅ Face uploaded:", file_path)

        update_result = (
            supabase
            .table("students")
            .update({"face_registered": True})
            .eq("id", student_id)
            .execute()
        )

        return {
            "success": True,
            "message": "Student aur face successfully registered!",
            "student_id": student_id,
            "face_path": file_path
        }

    except Exception as error:
        print("========== FULL ERROR ==========")
        print("ERROR TYPE:", type(error).__name__)
        print("ERROR:", repr(error))
        traceback.print_exc()
        print("================================")
        return {
            "success": False,
            "message": f"{type(error).__name__}: {str(error)}"
        }


# =====================================================
# AUTH + TEACHER / STUDENT DASHBOARDS
# =====================================================

TEACHER_USERNAME = os.getenv("TEACHER_USERNAME", "teacher")
TEACHER_PASSWORD = os.getenv("TEACHER_PASSWORD", "teacher123")


def page_shell(title, body, extra_js=""):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} - AI Face Attendance</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
<style>
* {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}}
body {{
    min-height: 100vh;
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background-color: #0b0f19;
    background-image: 
        radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.15) 0px, transparent 50%),
        radial-gradient(at 100% 100%, rgba(139, 92, 246, 0.15) 0px, transparent 50%);
    background-attachment: fixed;
    color: #f8fafc;
    line-height: 1.5;
}}
.nav {{
    background: rgba(15, 23, 42, 0.85);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    padding: 14px 28px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 100;
}}
.brand {{
    font-size: 18px;
    font-weight: 700;
    color: #fff;
    display: flex;
    align-items: center;
    gap: 10px;
    letter-spacing: -0.01em;
    text-decoration: none;
}}
.brand-badge {{
    background: linear-gradient(135deg, #4f46e5, #7c3aed);
    color: white;
    width: 34px;
    height: 34px;
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
    box-shadow: 0 0 15px rgba(99, 102, 241, 0.4);
}}
.nav-links {{
    display: flex;
    align-items: center;
    gap: 8px;
}}
.nav a {{
    color: #94a3b8;
    text-decoration: none;
    font-size: 13.5px;
    font-weight: 600;
    padding: 8px 14px;
    border-radius: 9px;
    transition: all 0.2s ease;
    display: inline-flex;
    align-items: center;
    gap: 6px;
}}
.nav a:hover {{
    color: #f8fafc;
    background: rgba(255, 255, 255, 0.08);
}}
.nav a.logout-link {{
    color: #f87171;
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.2);
}}
.nav a.logout-link:hover {{
    background: rgba(239, 68, 68, 0.2);
    color: #fca5a5;
}}
.container {{
    max-width: 1200px;
    margin: 0 auto;
    padding: 32px 24px 60px;
}}
.header-box {{
    margin-bottom: 24px;
}}
.header-box h1 {{
    font-size: 28px;
    font-weight: 800;
    letter-spacing: -0.02em;
    color: #fff;
    margin-bottom: 4px;
}}
.muted {{
    color: #94a3b8;
    font-size: 14px;
}}
.grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    margin-bottom: 24px;
}}
.card {{
    background: rgba(30, 41, 59, 0.7);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 18px;
    padding: 24px;
    box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.4);
    margin-bottom: 24px;
    position: relative;
    overflow: hidden;
}}
.stat {{
    font-size: 32px;
    font-weight: 800;
    color: #f8fafc;
    margin-top: 8px;
    letter-spacing: -0.02em;
}}
.stat.present, .present {{
    color: #34d399;
}}
.stat.absent, .absent {{
    color: #f87171;
}}
.actions {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 14px;
    margin: 24px 0;
}}
.action {{
    display: flex;
    align-items: center;
    gap: 12px;
    text-decoration: none;
    color: white;
    background: linear-gradient(135deg, rgba(37, 99, 235, 0.85) 0%, rgba(79, 70, 229, 0.85) 100%);
    padding: 16px 20px;
    border-radius: 14px;
    font-weight: 700;
    font-size: 14px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    box-shadow: 0 4px 15px rgba(37, 99, 235, 0.25);
    transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}}
.action:hover {{
    transform: translateY(-3px);
    box-shadow: 0 8px 25px rgba(37, 99, 235, 0.4);
    filter: brightness(1.08);
}}
.action.green {{
    background: linear-gradient(135deg, rgba(5, 150, 105, 0.85) 0%, rgba(16, 185, 129, 0.85) 100%);
    box-shadow: 0 4px 15px rgba(16, 185, 129, 0.25);
}}
.action.green:hover {{
    box-shadow: 0 8px 25px rgba(16, 185, 129, 0.4);
}}
.action.purple {{
    background: linear-gradient(135deg, rgba(124, 58, 237, 0.85) 0%, rgba(139, 92, 246, 0.85) 100%);
    box-shadow: 0 4px 15px rgba(124, 58, 237, 0.25);
}}
.action.purple:hover {{
    box-shadow: 0 8px 25px rgba(124, 58, 237, 0.4);
}}
.table-responsive {{
    width: 100%;
    overflow-x: auto;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.06);
    background: rgba(15, 23, 42, 0.4);
    margin-top: 14px;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 14px;
}}
th {{
    background: rgba(15, 23, 42, 0.8);
    color: #94a3b8;
    font-weight: 700;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 14px 18px;
    text-align: left;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}}
td {{
    padding: 14px 18px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    color: #e2e8f0;
    vertical-align: middle;
}}
tr:last-child td {{
    border-bottom: none;
}}
tr:hover td {{
    background: rgba(255, 255, 255, 0.02);
}}
input, select {{
    width: 100%;
    padding: 12px 16px;
    border: 1px solid #334155;
    border-radius: 10px;
    background: #0f172a;
    color: #f8fafc;
    font-size: 14px;
    font-family: inherit;
    margin: 6px 0 14px;
    outline: none;
    transition: all 0.2s ease;
}}
input:focus, select:focus {{
    border-color: #6366f1;
    background: #111a30;
    box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.25);
}}
label {{
    display: block;
    font-size: 12px;
    font-weight: 700;
    color: #cbd5e1;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 2px;
}}
button {{
    padding: 12px 20px;
    border: none;
    border-radius: 10px;
    background: linear-gradient(135deg, #4f46e5 0%, #3b82f6 100%);
    color: white;
    font-weight: 700;
    font-size: 14px;
    font-family: inherit;
    cursor: pointer;
    transition: all 0.2s ease;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    box-shadow: 0 4px 14px rgba(79, 70, 229, 0.35);
}}
button:hover:not(:disabled) {{
    transform: translateY(-1px);
    box-shadow: 0 6px 20px rgba(79, 70, 229, 0.45);
    filter: brightness(1.08);
}}
button:active:not(:disabled) {{
    transform: translateY(0);
}}
button:disabled {{
    opacity: 0.5;
    cursor: not-allowed;
    transform: none;
    box-shadow: none;
}}
.badge {{
    padding: 5px 12px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(255, 255, 255, 0.08);
    color: #cbd5e1;
}}
.camera-box {{
    position: relative;
    width: 100%;
    max-width: 680px;
    margin: 0 auto;
    border-radius: 16px;
    overflow: hidden;
    background: #000;
    border: 2px solid rgba(99, 102, 241, 0.3);
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.6);
}}
.camera-box video {{
    width: 100%;
    display: block;
    border-radius: 14px;
    background: #000;
    object-fit: cover;
    min-height: 280px;
}}
.reticle {{
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: 180px;
    height: 180px;
    border: 2px dashed rgba(99, 102, 241, 0.5);
    border-radius: 20px;
    pointer-events: none;
}}
.reticle::before, .reticle::after {{
    content: '';
    position: absolute;
    width: 20px;
    height: 20px;
    border-color: #38bdf8;
    border-style: solid;
}}
.reticle::before {{
    top: -2px; left: -2px;
    border-width: 3px 0 0 3px;
    border-top-left-radius: 10px;
}}
.reticle::after {{
    bottom: -2px; right: -2px;
    border-width: 0 3px 3px 0;
    border-bottom-right-radius: 10px;
}}
@media (max-width: 820px) {{
    .grid {{ grid-template-columns: repeat(2, 1fr); }}
    .actions {{ grid-template-columns: 1fr; }}
    .container {{ padding: 20px 16px 40px; }}
    table {{ font-size: 13px; }}
    th, td {{ padding: 10px 12px; }}
}}
@media (max-width: 520px) {{
    .grid {{ grid-template-columns: 1fr; }}
    .nav {{ padding: 12px 16px; flex-direction: column; gap: 10px; }}
    .nav-links {{ width: 100%; justify-content: center; flex-wrap: wrap; }}
}}
</style>
</head>
<body>
{body}
<script>{extra_js}</script>
</body>
</html>
"""


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    body = """
<div class="container" style="max-width:440px;padding-top:70px">
    <div class="card" style="box-shadow: 0 20px 40px -15px rgba(0,0,0,0.6)">
        <div style="text-align:center;margin-bottom:24px">
            <div class="brand-badge" style="width:48px;height:48px;font-size:22px;margin:0 auto 14px">
                <i class="fa-solid fa-robot"></i>
            </div>
            <h1 style="font-size:24px;font-weight:800;color:#fff;letter-spacing:-0.02em">AI Face Attendance</h1>
            <p class="muted" style="margin-top:4px">Login as Teacher or Student</p>
        </div>

        <label for="role">Select Portal Role</label>
        <select id="role" name="role" autocomplete="off">
            <option value="teacher">👨‍🏫 Teacher Portal</option>
            <option value="student">👨‍🎓 Student Portal</option>
        </select>

        <div id="teacherFields">
            <label for="username">Teacher Username</label>
            <input id="username" name="username" placeholder="e.g. teacher" autocomplete="username">
            <label for="password">Password</label>
            <input id="password" name="password" type="password" placeholder="••••••••" autocomplete="current-password">
        </div>

        <div id="studentFields" style="display:none">
            <label for="roll_no">Student Roll Number</label>
            <input id="roll_no" name="roll_no" placeholder="e.g. CS-101" autocomplete="off">
            <p class="muted" style="font-size:12px;margin-top:-6px;margin-bottom:12px">Student roll number se login hoga.</p>
        </div>

        <button onclick="login()" style="width:100%;margin-top:6px">
            <i class="fa-solid fa-arrow-right-to-bracket"></i> Sign In
        </button>
        <div id="msg" class="muted" style="margin-top:14px;text-align:center;min-height:20px;font-weight:600"></div>
    </div>
</div>
"""
    js = """
const role = document.getElementById('role');
role.addEventListener('change', () => {
    document.getElementById('teacherFields').style.display = role.value === 'teacher' ? 'block' : 'none';
    document.getElementById('studentFields').style.display = role.value === 'student' ? 'block' : 'none';
});
async function login() {
    const fd = new FormData();
    fd.append('role', role.value);
    if (role.value === 'teacher') {
        fd.append('username', document.getElementById('username').value);
        fd.append('password', document.getElementById('password').value);
    } else {
        fd.append('roll_no', document.getElementById('roll_no').value);
    }
    const msg = document.getElementById('msg');
    msg.textContent = 'Authenticating...';
    try {
        const r = await fetch('/auth/login', { method: 'POST', body: fd });
        const d = await r.json();
        msg.textContent = d.message || '';
        if (d.success) {
            msg.style.color = '#34d399';
            location.href = d.redirect;
        } else {
            msg.style.color = '#f87171';
        }
    } catch (e) {
        msg.textContent = 'Login server connection failed';
        msg.style.color = '#f87171';
    }
}
"""
    return page_shell("AI Attendance Login", body, js)


@app.post("/auth/login")
async def auth_login(
    response: Request,
    role: str = Form(...),
    username: str = Form(""),
    password: str = Form(""),
    roll_no: str = Form("")
):
    if role == "teacher":
        if username.strip() == TEACHER_USERNAME and password == TEACHER_PASSWORD:
            response = JSONResponse({
                "success": True,
                "message": "Teacher login successful",
                "redirect": "/teacher-dashboard",
                "role": "teacher"
            })
            response.set_cookie("attendance_role", "teacher", httponly=True, samesite="lax")
            return response
        return {"success": False, "message": "Teacher username/password galat hai."}

    if role == "student":
        roll_no = roll_no.strip()
        if not roll_no:
            return {"success": False, "message": "Roll number dalo."}
        try:
            result = supabase.table("students").select("id,name,roll_no,class_name,email,face_registered").eq("roll_no", roll_no).limit(1).execute()
            if not result.data:
                return {"success": False, "message": "Student nahi mila."}
            response = JSONResponse({
                "success": True,
                "message": "Student login successful",
                "redirect": "/student-dashboard",
                "role": "student",
                "student_roll_no": roll_no
            })
            response.set_cookie("attendance_role", "student", httponly=True, samesite="lax")
            response.set_cookie("student_roll_no", roll_no, httponly=True, samesite="lax")
            return response
        except Exception as error:
            return {"success": False, "message": f"{type(error).__name__}: {str(error)}"}

    return {"success": False, "message": "Invalid role."}


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("attendance_role")
    response.delete_cookie("student_roll_no")
    return response


def teacher_required(request: Request):
    return request.cookies.get("attendance_role") == "teacher"


def student_required(request: Request):
    return request.cookies.get("attendance_role") == "student"


@app.get("/teacher-dashboard", response_class=HTMLResponse)
async def teacher_dashboard(request: Request):
    if not teacher_required(request):
        return RedirectResponse("/login", status_code=303)

    total = 0
    students = []
    try:
        result = supabase.table("students").select("id,name,roll_no,class_name,email,face_registered,created_at").order("created_at", desc=True).execute()
        students = result.data or []
        total = len(students)
    except Exception as error:
        print("Teacher dashboard error:", repr(error))

    present = 0
    try:
        att = supabase.table("attendance").select("id,status").eq("attendance_date", datetime.now().date().isoformat()).execute()
        rows = att.data or []
        present = sum(1 for x in rows if str(x.get("status","")).lower() == "present")
    except Exception:
        pass

    absent = max(total - present, 0)
    pct = round((present / total) * 100, 1) if total else 0

    rows_html = "".join(
        f"<tr><td><strong>{s.get('name','')}</strong></td><td><code>{s.get('roll_no','')}</code></td>"
        f"<td>{s.get('class_name','')}</td>"
        f"<td>{'<span class=\"badge present\"><i class=\"fa-solid fa-circle-check\"></i> Registered</span>' if s.get('face_registered') else '<span class=\"badge absent\"><i class=\"fa-solid fa-circle-xmark\"></i> Pending</span>'}</td></tr>"
        for s in students[:100]
    ) or "<tr><td colspan='4' class='muted' style='text-align:center;padding:24px'>Abhi koi student registered nahi hai.</td></tr>"

    body = f"""
<div class="nav">
    <div class="brand">
        <div class="brand-badge"><i class="fa-solid fa-chalkboard-user"></i></div>
        <span>Teacher Dashboard</span>
    </div>
    <div class="nav-links">
        <a href="/register-page"><i class="fa-solid fa-user-plus"></i> Register</a>
        <a href="/take-attendance"><i class="fa-solid fa-camera"></i> Take Attendance</a>
        <a href="/logout" class="logout-link"><i class="fa-solid fa-arrow-right-from-bracket"></i> Logout</a>
    </div>
</div>

<div class="container">
    <div class="header-box">
        <h1>Attendance Overview</h1>
        <p class="muted">Live classroom monitoring & automated facial attendance stats</p>
    </div>

    <div class="grid">
        <div class="card"><div class="muted">Total Students</div><div class="stat">{total}</div></div>
        <div class="card"><div class="muted">Present Today</div><div class="stat present">{present}</div></div>
        <div class="card"><div class="muted">Absent Today</div><div class="stat absent">{absent}</div></div>
        <div class="card"><div class="muted">Attendance Rate</div><div class="stat">{pct}%</div></div>
    </div>

    <div class="actions">
        <a class="action" href="/take-attendance"><i class="fa-solid fa-camera"></i> Take AI Attendance</a>
        <a class="action green" href="/register-page"><i class="fa-solid fa-user-plus"></i> Register Student</a>
        <a class="action purple" href="/attendance-history"><i class="fa-solid fa-clock-rotate-left"></i> Attendance History</a>
        <a class="action" href="/student-management"><i class="fa-solid fa-users-gear"></i> Manage Students</a>
        <a class="action green" href="/reports"><i class="fa-solid fa-chart-line"></i> Reports & Analytics</a>
        <a class="action purple" href="/low-attendance"><i class="fa-solid fa-triangle-exclamation"></i> Low Attendance</a>
        <a class="action" href="/subjects"><i class="fa-solid fa-book-open"></i> Manage Subjects</a>
    </div>

    <div class="card">
        <h2 style="margin-bottom:4px">Registered Students Roster</h2>
        <p class="muted" style="margin-bottom:12px">Total {total} students in system</p>
        <div class="table-responsive">
            <table>
                <thead><tr><th>Name</th><th>Roll No.</th><th>Class</th><th>Face Status</th></tr></thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
    </div>
</div>
"""
    return page_shell("Teacher Dashboard", body)


@app.get("/subjects", response_class=HTMLResponse)
async def subjects_page(request: Request):
    if not teacher_required(request):
        return RedirectResponse("/login", status_code=303)

    body = """
<div class="nav">
    <div class="brand">
        <div class="brand-badge"><i class="fa-solid fa-book-open"></i></div>
        <span>Subject Management</span>
    </div>
    <div class="nav-links">
        <a href="/teacher-dashboard"><i class="fa-solid fa-chart-pie"></i> Dashboard</a>
        <a href="/take-attendance"><i class="fa-solid fa-camera"></i> Take Attendance</a>
        <a href="/logout" class="logout-link"><i class="fa-solid fa-arrow-right-from-bracket"></i> Logout</a>
    </div>
</div>

<div class="container">
    <div class="header-box">
        <h1>📚 Manage Classroom Subjects</h1>
        <p class="muted">Add subjects to enable subject-wise biometric attendance</p>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1.3fr;gap:24px;align-items:start">
        <div class="card">
            <h2>Add New Subject</h2>
            <p class="muted" style="margin-bottom:16px">Configured subjects automatically appear in attendance scanner</p>
            
            <label for="subjectName">Subject Name</label>
            <input id="subjectName" placeholder="e.g. Python Programming" autocomplete="off">
            
            <label for="subjectClass">Class / Section</label>
            <input id="subjectClass" placeholder="e.g. BCA 2nd Year A" autocomplete="off">
            
            <button onclick="addSubject()" style="background:linear-gradient(135deg, #059669, #10b981);width:100%;margin-top:4px">
                <i class="fa-solid fa-plus"></i> Add Subject
            </button>
            <div id="subjectMsg" class="muted" style="margin-top:14px;text-align:center;font-weight:600"></div>
        </div>

        <div class="card">
            <h2>Configured Subjects</h2>
            <div class="table-responsive">
                <table>
                    <thead><tr><th>Subject</th><th>Class / Section</th></tr></thead>
                    <tbody id="subjectRows"><tr><td colspan="2" class="muted">Loading subjects...</td></tr></tbody>
                </table>
            </div>
        </div>
    </div>
</div>
"""
    js = """
async function loadSubjects(){
    const tb = document.getElementById('subjectRows');
    try {
        const r = await fetch('/api/subjects');
        const d = await r.json();
        if (!d.success) throw new Error();
        const rows = d.subjects || [];
        tb.innerHTML = rows.length ? rows.map(s => `<tr><td><strong>${String(s.name||'')}</strong></td><td>${String(s.class_name||'All Classes')}</td></tr>`).join('') : '<tr><td colspan="2" class="muted" style="text-align:center;padding:20px">No subjects configured yet.</td></tr>';
    } catch(e) {
        tb.innerHTML = '<tr><td colspan="2" style="color:#f87171;text-align:center">❌ Subject load failed</td></tr>';
    }
}
async function addSubject(){
    const name = document.getElementById('subjectName').value.trim();
    const cls = document.getElementById('subjectClass').value.trim();
    const m = document.getElementById('subjectMsg');
    if (!name) {
        m.innerHTML = '<span style="color:#f87171">❌ Subject name required</span>';
        return;
    }
    const fd = new FormData();
    fd.append('name', name);
    fd.append('class_name', cls);
    m.innerHTML = 'Adding subject...';
    try {
        const r = await fetch('/api/subjects', { method: 'POST', body: fd });
        const d = await r.json();
        if (d.success) {
            m.innerHTML = '<span style="color:#34d399">✅ ' + name + ' add ho gaya!</span>';
            document.getElementById('subjectName').value = '';
            loadSubjects();
        } else {
            m.innerHTML = '<span style="color:#f87171">❌ ' + (d.message || 'Subject add failed') + '</span>';
        }
    } catch(e) {
        m.innerHTML = '<span style="color:#f87171">❌ Server connection failed</span>';
    }
}
loadSubjects();
"""
    return page_shell("Subject Management", body, js)


@app.get("/register-page", response_class=HTMLResponse)
async def register_page(request: Request):
    if not teacher_required(request):
        return RedirectResponse("/login", status_code=303)
    return await home()


@app.get("/take-attendance", response_class=HTMLResponse)
async def take_attendance(request: Request):
    if not teacher_required(request):
        return RedirectResponse("/login", status_code=303)

    body = """
<div class="nav">
    <div class="brand">
        <div class="brand-badge"><i class="fa-solid fa-camera"></i></div>
        <span>AI Face Attendance</span>
    </div>
    <div class="nav-links">
        <a href="/teacher-dashboard"><i class="fa-solid fa-chart-pie"></i> Dashboard</a>
        <a href="/attendance-history"><i class="fa-solid fa-clock-rotate-left"></i> History</a>
        <a href="/logout" class="logout-link"><i class="fa-solid fa-arrow-right-from-bracket"></i> Logout</a>
    </div>
</div>

<div class="container" style="max-width:760px">
    <div class="card" style="text-align:center">
        <h1 style="font-size:26px;font-weight:800;color:#fff;margin-bottom:6px">📷 Take AI Attendance</h1>
        <p class="muted" style="margin-bottom:20px">Select subject, start camera, and scan student face</p>

        <div style="text-align:left;max-width:480px;margin:0 auto 18px">
            <label for="attendanceSubject">📚 Select Subject</label>
            <select id="attendanceSubject">
                <option value="">Loading subjects...</option>
            </select>
        </div>

        <div class="camera-box">
            <video id="video" autoplay playsinline></video>
            <div class="reticle"></div>
        </div>
        <canvas id="canvas" style="display:none"></canvas>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;max-width:480px;margin:18px auto 0">
            <button id="start">
                <i class="fa-solid fa-video"></i> Start Camera
            </button>
            <button id="capture" disabled style="background:linear-gradient(135deg, #059669, #10b981);box-shadow:0 4px 14px rgba(16,185,129,0.35)">
                <i class="fa-solid fa-expand"></i> Recognize Face
            </button>
        </div>

        <div id="msg" class="muted" style="margin-top:18px;min-height:24px;font-size:15px;font-weight:600"></div>
    </div>
</div>
"""
    js = """
let stream = null;
const video = document.getElementById('video');
const start = document.getElementById('start');
const capture = document.getElementById('capture');
const msg = document.getElementById('msg');
const subjectSelect = document.getElementById('attendanceSubject');

async function loadSubjects() {
    try {
        const r = await fetch('/api/subjects');
        const d = await r.json();
        subjectSelect.innerHTML = '<option value="">Select Subject</option>';
        if (d.success && (d.subjects || []).length) {
            (d.subjects || []).forEach(s => {
                const o = document.createElement('option');
                o.value = s.name;
                o.textContent = s.name + (s.class_name ? ' — ' + s.class_name : '');
                subjectSelect.appendChild(o);
            });
        } else {
            subjectSelect.innerHTML = '<option value="">No subjects found — add subject first</option>';
        }
    } catch(e) {
        subjectSelect.innerHTML = '<option value="">Subject loading failed</option>';
    }
}
loadSubjects();

start.onclick = async () => {
    try {
        stream = await navigator.mediaDevices.getUserMedia({
            video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' },
            audio: false
        });
        video.srcObject = stream;
        capture.disabled = false;
        msg.innerHTML = '<span style="color:#34d399"><i class="fa-solid fa-circle-check"></i> Camera ready. Face camera ke saamne rakho.</span>';
    } catch(e) {
        console.error(e);
        msg.innerHTML = '<span style="color:#f87171"><i class="fa-solid fa-triangle-exclamation"></i> Camera permission allow karo.</span>';
    }
};

capture.onclick = async () => {
    if (!stream) return;

    const c = document.getElementById('canvas');
    if (!video.videoWidth || !video.videoHeight) {
        msg.innerHTML = '<span style="color:#fbbf24">Camera image ready nahi hai. 1-2 second wait karo.</span>';
        return;
    }

    c.width = video.videoWidth;
    c.height = video.videoHeight;
    c.getContext('2d').drawImage(video, 0, 0, c.width, c.height);

    const image = c.toDataURL('image/jpeg', 0.9);
    const fd = new FormData();
    const selectedSubject = subjectSelect.value;
    if (!selectedSubject) {
        msg.innerHTML = '<span style="color:#f87171"><i class="fa-solid fa-circle-exclamation"></i> Pehle subject select karo!</span>';
        return;
    }
    fd.append('image_data', image);
    fd.append('subject', selectedSubject);

    capture.disabled = true;
    msg.innerHTML = '<span style="color:#60a5fa"><i class="fa-solid fa-spinner fa-spin"></i> Face matching ho raha hai...</span>';

    try {
        const response = await fetch('/recognize', {
            method: 'POST',
            body: fd
        });

        const data = await response.json();

        if (data.recognized) {
            const s = data.student || {};
            const a = data.attendance || {};

            if (a.already_marked) {
                msg.innerHTML = '<span style="color:#34d399">✅ <strong>' + (s.name||'Student') + '</strong> recognized. Attendance already marked today.</span>';
            } else if (a.success) {
                msg.innerHTML = '<span style="color:#34d399">✅ <strong>' + (s.name||'Student') + '</strong> recognized — ' + selectedSubject + ' attendance marked!</span>';
            } else {
                msg.innerHTML = '<span style="color:#fbbf24">⚠️ <strong>' + (s.name||'Student') + '</strong> recognized, but attendance save failed: ' + (a.message||'') + '</span>';
            }
        } else {
            msg.innerHTML = '<span style="color:#f87171">❌ ' + (data.message || 'Face not recognized.') + '</span>';
        }
    } catch(e) {
        console.error(e);
        msg.innerHTML = '<span style="color:#f87171">❌ Recognition server connection failed</span>';
    } finally {
        capture.disabled = false;
    }
};
"""
    return page_shell("Take Attendance", body, js)


@app.get("/attendance-history", response_class=HTMLResponse)
async def attendance_history(request: Request):
    if not teacher_required(request):
        return RedirectResponse("/login", status_code=303)

    rows = []
    try:
        result = supabase.table("attendance").select("*").order("attendance_date", desc=True).order("marked_at", desc=True).limit(200).execute()
        rows = result.data or []
    except Exception:
        pass

    html = "".join(
        f"<tr><td>{r.get('attendance_date','')}</td>"
        f"<td><span class=\"badge\">{r.get('subject','General')}</span></td>"
        f"<td><strong>{r.get('student_name',r.get('roll_no',''))}</strong></td>"
        f"<td><code>{r.get('roll_no','')}</code></td>"
        f"<td>{str(r.get('marked_at',''))[:19]}</td>"
        f"<td><span class=\"badge {'present' if str(r.get('status','')).lower()=='present' else 'absent'}\">{r.get('status','')}</span></td></tr>"
        for r in rows
    ) or "<tr><td colspan='6' class='muted' style='text-align:center;padding:24px'>Attendance table/data abhi available nahi hai.</td></tr>"

    body = f"""
<div class="nav">
    <div class="brand">
        <div class="brand-badge"><i class="fa-solid fa-clock-rotate-left"></i></div>
        <span>Attendance History</span>
    </div>
    <div class="nav-links">
        <a href="/teacher-dashboard"><i class="fa-solid fa-chart-pie"></i> Dashboard</a>
        <a href="/take-attendance"><i class="fa-solid fa-camera"></i> Take Attendance</a>
        <a href="/logout" class="logout-link"><i class="fa-solid fa-arrow-right-from-bracket"></i> Logout</a>
    </div>
</div>

<div class="container">
    <div class="card">
        <h1 style="font-size:26px;font-weight:800;color:#fff;margin-bottom:4px">📊 Attendance History</h1>
        <p class="muted" style="margin-bottom:12px">All logged attendance scans and timestamps</p>
        <div class="table-responsive">
            <table>
                <thead><tr><th>Date</th><th>Subject</th><th>Student</th><th>Roll No.</th><th>Time</th><th>Status</th></tr></thead>
                <tbody>{html}</tbody>
            </table>
        </div>
    </div>
</div>
"""
    return page_shell("Attendance History", body)


@app.get("/student-dashboard", response_class=HTMLResponse)
async def student_dashboard(request: Request):
    if not student_required(request):
        return RedirectResponse("/login", status_code=303)

    roll_no = request.cookies.get("student_roll_no", "")
    student = None
    attendance_rows = []

    try:
        result = supabase.table("students").select("id,name,roll_no,class_name,email,face_registered").eq("roll_no", roll_no).limit(1).execute()
        student = (result.data or [None])[0]
        if student:
            try:
                att = supabase.table("attendance").select("*").eq("roll_no", roll_no).order("attendance_date", desc=True).limit(100).execute()
                attendance_rows = att.data or []
            except Exception:
                pass
    except Exception:
        pass

    name = student.get("name","Student") if student else "Student"
    cls = student.get("class_name","-") if student else "-"
    total = len(attendance_rows)
    present = sum(1 for r in attendance_rows if str(r.get("status","")).lower()=="present")
    pct = round(present/total*100,1) if total else 0

    rows_html = "".join(
        f"<tr><td>{r.get('attendance_date','')}</td>"
        f"<td><span class=\"badge\">{r.get('subject','General')}</span></td>"
        f"<td>{str(r.get('marked_at',''))[:19]}</td>"
        f"<td><span class=\"badge {'present' if str(r.get('status','')).lower()=='present' else 'absent'}\">{r.get('status','')}</span></td></tr>"
        for r in attendance_rows
    ) or "<tr><td colspan='4' class='muted' style='text-align:center;padding:24px'>Attendance record abhi nahi mila.</td></tr>"

    body = f"""
<div class="nav">
    <div class="brand">
        <div class="brand-badge"><i class="fa-solid fa-graduation-cap"></i></div>
        <span>Student Portal</span>
    </div>
    <div class="nav-links">
        <a href="/logout" class="logout-link"><i class="fa-solid fa-arrow-right-from-bracket"></i> Logout</a>
    </div>
</div>

<div class="container">
    <div class="header-box">
        <h1>Welcome, {name}</h1>
        <p class="muted">Roll Number: <strong>{roll_no}</strong> &nbsp;·&nbsp; Class: <strong>{cls}</strong></p>
    </div>

    <div class="grid">
        <div class="card"><div class="muted">My Present</div><div class="stat present">{present}</div></div>
        <div class="card"><div class="muted">Total Lectures</div><div class="stat">{total}</div></div>
        <div class="card"><div class="muted">My Attendance</div><div class="stat">{pct}%</div></div>
        <div class="card"><div class="muted">Face Status</div><div class="stat" style="font-size:22px">{'<span style="color:#34d399">✅ Ready</span>' if student and student.get('face_registered') else '<span style="color:#f87171">❌ Pending</span>'}</div></div>
    </div>

    <div class="card">
        <h2 style="margin-bottom:4px">My Attendance History</h2>
        <p class="muted" style="margin-bottom:12px">Past records for your account</p>
        <div class="table-responsive">
            <table>
                <thead><tr><th>Date</th><th>Subject</th><th>Marked At</th><th>Status</th></tr></thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
    </div>
</div>
"""
    return page_shell("Student Dashboard", body)


# =====================================================
# ATTENDANCE MARKING API
# =====================================================

@app.post("/api/attendance/mark")
async def mark_attendance(
    request: Request,
    roll_no: str = Form(...),
    status: str = Form("Present"),
    subject: str = Form("General")
):
    if not teacher_required(request):
        return {"success": False, "message": "Teacher login required"}

    roll_no = roll_no.strip()
    status = status.strip().title()

    if status not in {"Present", "Absent"}:
        return {"success": False, "message": "Invalid attendance status"}

    try:
        student_result = (
            supabase.table("students")
            .select("id,name,roll_no,class_name")
            .eq("roll_no", roll_no)
            .limit(1)
            .execute()
        )

        if not student_result.data:
            return {"success": False, "message": "Student nahi mila."}

        student = student_result.data[0]
        today = datetime.now(timezone.utc).date().isoformat()
        selected_subject = " ".join(str(subject or "General").strip().split())[:100] or "General"

        existing = (
            supabase.table("attendance")
            .select("id")
            .eq("roll_no", roll_no)
            .eq("attendance_date", today)
            .limit(1)
            .execute()
        )

        payload = {
            "student_id": student.get("id"),
            "student_name": student.get("name"),
            "roll_no": student.get("roll_no"),
            "class_name": student.get("class_name"),
            "attendance_date": today,
            "subject": selected_subject,
            "marked_at": datetime.now(timezone.utc).isoformat(),
            "status": status
        }

        if existing.data:
            result = (
                supabase.table("attendance")
                .update(payload)
                .eq("id", existing.data[0]["id"])
                .execute()
            )
            action = "updated"
        else:
            result = supabase.table("attendance").insert(payload).execute()
            action = "marked"

        return {
            "success": bool(result.data),
            "message": f"{student.get('name')} ki attendance {action}.",
            "student": student,
            "status": status
        }

    except Exception as error:
        return {
            "success": False,
            "message": f"{type(error).__name__}: {str(error)}"
        }


@app.post("/api/attendance/mark-by-id")
async def mark_attendance_by_id(
    request: Request,
    student_id: str = Form(...),
    status: str = Form("Present"),
    subject: str = Form("General")
):
    if not teacher_required(request):
        return {"success": False, "message": "Teacher login required"}

    try:
        try:
            uuid.UUID(str(student_id).strip())
        except Exception:
            return {"success": False, "message": f"Invalid student UUID: {student_id}"}

        student_result = (
            supabase.table("students")
            .select("id,name,roll_no,class_name")
            .eq("id", str(student_id).strip())
            .limit(1)
            .execute()
        )

        if not student_result.data:
            return {"success": False, "message": "Student nahi mila."}

        student = student_result.data[0]
        roll_no = student["roll_no"]

        today = datetime.now(timezone.utc).date().isoformat()
        selected_subject = " ".join(str(subject or "General").strip().split())[:100] or "General"
        payload = {
            "student_id": student["id"],
            "student_name": student["name"],
            "roll_no": roll_no,
            "class_name": student.get("class_name"),
            "attendance_date": today,
            "subject": selected_subject,
            "marked_at": datetime.now(timezone.utc).isoformat(),
            "status": status.strip().title()
        }

        existing = (
            supabase.table("attendance")
            .select("id")
            .eq("roll_no", roll_no)
            .eq("attendance_date", today)
            .limit(1)
            .execute()
        )

        if existing.data:
            result = (
                supabase.table("attendance")
                .update(payload)
                .eq("id", existing.data[0]["id"])
                .execute()
            )
        else:
            result = supabase.table("attendance").insert(payload).execute()

        return {
            "success": bool(result.data),
            "message": f"{student['name']} ki attendance save ho gayi.",
            "student": student,
            "status": payload["status"]
        }

    except Exception as error:
        return {
            "success": False,
            "message": f"{type(error).__name__}: {str(error)}"
        }


# =====================================================
# DASHBOARD STATS
# =====================================================

@app.get("/api/dashboard-stats")
async def dashboard_stats(request: Request):
    if not teacher_required(request):
        return {"success": False, "message": "Teacher login required"}

    try:
        students_result = supabase.table("students").select("id").execute()
        total = len(students_result.data or [])
    except Exception:
        total = 0

    present = 0
    try:
        att = supabase.table("attendance").select("id,status").eq("attendance_date", datetime.now().date().isoformat()).execute()
        present = sum(1 for x in (att.data or []) if str(x.get("status","")).lower()=="present")
    except Exception:
        pass

    return {
        "success": True,
        "total_students": total,
        "present_today": present,
        "absent_today": max(total-present, 0),
        "attendance_percentage": round(present/total*100,1) if total else 0
    }


# =====================================================
# ADVANCED STUDENT MANAGEMENT + REPORTS + ANALYTICS
# =====================================================

def _today_utc():
    return datetime.now(timezone.utc).date().isoformat()


def _safe_text(value):
    from html import escape
    return escape(str(value or ""))


@app.get("/api/students")
async def api_students(request: Request, search: str = "", class_name: str = ""):
    if not teacher_required(request):
        return {"success": False, "message": "Teacher login required"}

    try:
        query = supabase.table("students").select(
            "id,name,roll_no,class_name,email,face_registered,created_at"
        ).order("created_at", desc=True)

        if search.strip():
            term = search.strip()
            query = query.or_(
                f"name.ilike.%{term}%,roll_no.ilike.%{term}%,class_name.ilike.%{term}%"
            )
        if class_name.strip():
            query = query.eq("class_name", class_name.strip())

        result = query.limit(500).execute()
        return {"success": True, "students": result.data or []}
    except Exception as error:
        return {"success": False, "message": f"{type(error).__name__}: {str(error)}"}


@app.post("/api/student/update")
async def api_student_update(
    request: Request,
    student_id: str = Form(...),
    name: str = Form(...),
    roll_no: str = Form(...),
    class_name: str = Form(...),
    email: str = Form("")
):
    if not teacher_required(request):
        return {"success": False, "message": "Teacher login required"}

    try:
        sid = str(student_id).strip()
        uuid.UUID(sid)
        name, roll_no, class_name = name.strip(), roll_no.strip(), class_name.strip()
        email = email.strip().lower()
        if not name or not roll_no or not class_name or not email:
            return {"success": False, "message": "Name, Roll Number aur Class required hain."}

        duplicate = (
            supabase.table("students")
            .select("id")
            .eq("roll_no", roll_no)
            .neq("id", sid)
            .limit(1)
            .execute()
        )
        if duplicate.data:
            return {"success": False, "message": "Ye Roll Number kisi aur student ka hai."}

        result = (
            supabase.table("students")
            .update({"name": name, "roll_no": roll_no, "class_name": class_name, "email": email})
            .eq("id", sid)
            .execute()
        )
        return {
            "success": bool(result.data),
            "message": "Student details updated successfully." if result.data else "Student update nahi hua."
        }
    except Exception as error:
        return {"success": False, "message": f"{type(error).__name__}: {str(error)}"}


@app.post("/api/student/delete")
async def api_student_delete(request: Request, student_id: str = Form(...)):
    if not teacher_required(request):
        return {"success": False, "message": "Teacher login required"}

    try:
        sid = str(student_id).strip()
        uuid.UUID(sid)

        student = (
            supabase.table("students")
            .select("id,name,roll_no")
            .eq("id", sid)
            .limit(1)
            .execute()
        )
        if not student.data:
            return {"success": False, "message": "Student nahi mila."}

        result = supabase.table("students").delete().eq("id", sid).execute()
        return {
            "success": bool(result.data),
            "message": "Student delete ho gaya." if result.data else "Student delete nahi hua."
        }
    except Exception as error:
        return {"success": False, "message": f"{type(error).__name__}: {str(error)}"}


@app.post("/api/attendance/update")
async def api_attendance_update(
    request: Request,
    attendance_id: str = Form(...),
    status: str = Form(...)
):
    if not teacher_required(request):
        return {"success": False, "message": "Teacher login required"}

    status = status.strip().title()
    if status not in {"Present", "Absent", "Late"}:
        return {"success": False, "message": "Status Present, Absent ya Late hona chahiye."}

    try:
        result = (
            supabase.table("attendance")
            .update({"status": status})
            .eq("id", attendance_id.strip())
            .execute()
        )
        return {
            "success": bool(result.data),
            "message": "Attendance updated." if result.data else "Attendance update nahi hui."
        }
    except Exception as error:
        return {"success": False, "message": f"{type(error).__name__}: {str(error)}"}


@app.get("/student-management", response_class=HTMLResponse)
async def student_management(request: Request):
    if not teacher_required(request):
        return RedirectResponse("/login", status_code=303)

    body = """
    <div class="nav">
      <div class="brand">
        <div class="brand-badge"><i class="fa-solid fa-users-gear"></i></div>
        <span>Student Management</span>
      </div>
      <div class="nav-links">
        <a href="/teacher-dashboard"><i class="fa-solid fa-chart-pie"></i> Dashboard</a>
        <a href="/register-page"><i class="fa-solid fa-user-plus"></i> Register</a>
        <a href="/logout" class="logout-link"><i class="fa-solid fa-arrow-right-from-bracket"></i> Logout</a>
      </div>
    </div>

    <div class="container">
      <div class="card">
        <h1 style="font-size:26px;font-weight:800;color:#fff;margin-bottom:4px">🧑‍🎓 Student Roster & Management</h1>
        <p class="muted" style="margin-bottom:18px">Search, edit, and manage enrolled students</p>
        <div style="display:grid;grid-template-columns:2fr 1fr auto;gap:12px;align-items:center">
          <input id="search" placeholder="Search by Name / Roll Number / Class..." style="margin:0">
          <input id="className" placeholder="Filter by Exact Class" style="margin:0">
          <button onclick="loadStudents()" style="height:46px">
            <i class="fa-solid fa-magnifying-glass"></i> Search
          </button>
        </div>
      </div>

      <div class="card" style="margin-top:18px">
        <div class="table-responsive">
          <table>
            <thead><tr><th>Name</th><th>Roll No.</th><th>Class</th><th>Face Status</th><th>Actions</th></tr></thead>
            <tbody id="rows"><tr><td colspan="5" class="muted" style="text-align:center;padding:24px">Loading students...</td></tr></tbody>
          </table>
        </div>
      </div>
    </div>
    """
    js = """
    async function loadStudents(){
      const q = document.getElementById('search').value.trim();
      const c = document.getElementById('className').value.trim();
      const rows = document.getElementById('rows');
      rows.innerHTML = '<tr><td colspan="5" class="muted" style="text-align:center;padding:20px">Searching students...</td></tr>';
      try {
        const r = await fetch('/api/students?search='+encodeURIComponent(q)+'&class_name='+encodeURIComponent(c));
        const d = await r.json();
        if(!d.success){ rows.innerHTML='<tr><td colspan="5" style="color:#f87171;text-align:center">❌ '+(d.message||'Error')+'</td></tr>'; return; }
        if(!d.students.length){ rows.innerHTML='<tr><td colspan="5" class="muted" style="text-align:center;padding:24px">Student nahi mila.</td></tr>'; return; }
        rows.innerHTML = d.students.map(s => `<tr>
          <td><strong>${esc(s.name)}</strong></td>
          <td><code>${esc(s.roll_no)}</code></td>
          <td>${esc(s.class_name)}</td>
          <td>${s.face_registered?'<span class="badge present"><i class="fa-solid fa-circle-check"></i> Registered</span>':'<span class="badge absent"><i class="fa-solid fa-circle-xmark"></i> Pending</span>'}</td>
          <td>
            <button onclick='editStudent(${JSON.stringify(s)})' style="padding:7px 14px;font-size:12.5px;margin-right:6px">✏️ Edit</button>
            <button style="background:linear-gradient(135deg, #dc2626, #ef4444);padding:7px 14px;font-size:12.5px" onclick='deleteStudent(${JSON.stringify(s.id)})'>🗑️ Delete</button>
          </td>
        </tr>`).join('');
      } catch(e) {
        rows.innerHTML = '<tr><td colspan="5" style="color:#f87171;text-align:center">❌ Server connection failed</td></tr>';
      }
    }
    function esc(v){ return String(v??'').replace(/[&<>'"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[m])); }
    async function editStudent(s){
      const name = prompt('Student Name:', s.name); if(name===null)return;
      const roll = prompt('Roll Number:', s.roll_no); if(roll===null)return;
      const cls = prompt('Class:', s.class_name); if(cls===null)return;
      const email = prompt('Student Email:', s.email||''); if(email===null)return;
      const fd = new FormData(); fd.append('student_id',s.id); fd.append('name',name); fd.append('roll_no',roll); fd.append('class_name',cls); fd.append('email',email);
      const r = await fetch('/api/student/update',{method:'POST',body:fd}); const d=await r.json(); alert(d.message||'Done'); if(d.success)loadStudents();
    }
    async function deleteStudent(id){
      if(!confirm('Is student ko delete karna hai?'))return;
      const fd = new FormData(); fd.append('student_id',id);
      const r = await fetch('/api/student/delete',{method:'POST',body:fd}); const d=await r.json(); alert(d.message||'Done'); if(d.success)loadStudents();
    }
    loadStudents();
    """
    return page_shell("Student Management", body, js)


async def _fetch_attendance(date_value="", class_name="", roll_no=""):
    query = supabase.table("attendance").select("*").order("attendance_date", desc=True).order("marked_at", desc=True)
    if date_value:
        query = query.eq("attendance_date", date_value)
    if class_name:
        query = query.eq("class_name", class_name)
    if roll_no:
        query = query.eq("roll_no", roll_no)
    return (query.limit(2000).execute().data or [])


@app.get("/api/attendance/analytics")
async def attendance_analytics(
    request: Request,
    date_value: str = "",
    class_name: str = ""
):
    if not teacher_required(request):
        return {"success": False, "message": "Teacher login required"}

    try:
        target_date = date_value or _today_utc()
        rows = await _fetch_attendance(target_date, class_name.strip())
        present = sum(1 for r in rows if str(r.get("status","")).lower()=="present")
        late = sum(1 for r in rows if str(r.get("status","")).lower()=="late")
        absent = sum(1 for r in rows if str(r.get("status","")).lower()=="absent")
        return {
            "success": True,
            "date": target_date,
            "total_records": len(rows),
            "present": present,
            "late": late,
            "absent": absent,
            "attendance_percentage": round((present + late) / len(rows) * 100, 1) if rows else 0
        }
    except Exception as error:
        return {"success": False, "message": f"{type(error).__name__}: {str(error)}"}


@app.get("/reports", response_class=HTMLResponse)
async def reports_page(
    request: Request,
    date_value: str = "",
    class_name: str = "",
    subject: str = ""
):
    if not teacher_required(request):
        return RedirectResponse("/login", status_code=303)

    target_date = date_value or _today_utc()
    rows = []
    students = []
    try:
        rows = await _fetch_attendance(target_date, class_name.strip())
        if subject.strip():
            rows = [r for r in rows if str(r.get("subject", "")).strip() == subject.strip()]
    except Exception:
        rows = []
    try:
        sr = supabase.table("students").select("id,name,roll_no,class_name").order("name").execute()
        students = sr.data or []
    except Exception:
        students = []

    present = sum(1 for r in rows if str(r.get("status","")).lower()=="present")
    late = sum(1 for r in rows if str(r.get("status","")).lower()=="late")
    absent_marked = sum(1 for r in rows if str(r.get("status","")).lower()=="absent")
    total_students = len([s for s in students if not class_name.strip() or s.get("class_name")==class_name.strip()])
    missing = max(total_students - len(rows), 0)
    pct = round((present + late) / total_students * 100, 1) if total_students else 0

    rows_html = "".join(
        f"<tr><td>{_safe_text(r.get('attendance_date'))}</td>"
        f"<td><span class=\"badge\">{_safe_text(r.get('subject','General'))}</span></td>"
        f"<td><strong>{_safe_text(r.get('student_name'))}</strong></td>"
        f"<td><code>{_safe_text(r.get('roll_no'))}</code></td>"
        f"<td>{_safe_text(r.get('class_name'))}</td>"
        f"<td><span class=\"badge {'present' if str(r.get('status','')).lower()=='present' else 'absent'}\">{_safe_text(r.get('status'))}</span></td>"
        f"<td><button onclick=\"editAttendance('{_safe_text(r.get('id'))}','{_safe_text(r.get('status'))}')\" style=\"padding:6px 12px;font-size:12px\">✏️ Edit</button></td></tr>"
        for r in rows
    ) or "<tr><td colspan='7' class='muted' style='text-align:center;padding:24px'>Is date par attendance record nahi hai.</td></tr>"

    body = f"""
    <div class="nav">
      <div class="brand">
        <div class="brand-badge"><i class="fa-solid fa-chart-line"></i></div>
        <span>Reports & Analytics</span>
      </div>
      <div class="nav-links">
        <a href="/teacher-dashboard"><i class="fa-solid fa-chart-pie"></i> Dashboard</a>
        <a href="/attendance-history"><i class="fa-solid fa-clock-rotate-left"></i> History</a>
        <a href="/logout" class="logout-link"><i class="fa-solid fa-arrow-right-from-bracket"></i> Logout</a>
      </div>
    </div>

    <div class="container">
      <div class="card">
        <h1 style="font-size:26px;font-weight:800;color:#fff;margin-bottom:4px">📈 Attendance Reports</h1>
        <p class="muted" style="margin-bottom:16px">Filter attendance by date, class, or subject</p>
        <form method="get" style="display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:12px;align-items:center">
          <input type="date" name="date_value" value="{_safe_text(target_date)}" style="margin:0">
          <input name="class_name" value="{_safe_text(class_name)}" placeholder="Class (optional)" style="margin:0">
          <input name="subject" value="{_safe_text(subject)}" placeholder="Subject (optional)" style="margin:0">
          <button type="submit" style="height:46px"><i class="fa-solid fa-filter"></i> Apply Filter</button>
        </form>
      </div>

      <div class="grid">
        <div class="card"><div class="muted">Total Students</div><div class="stat">{total_students}</div></div>
        <div class="card"><div class="muted">Present</div><div class="stat present">{present}</div></div>
        <div class="card"><div class="muted">Late</div><div class="stat">{late}</div></div>
        <div class="card"><div class="muted">Attendance Rate</div><div class="stat">{pct}%</div></div>
      </div>

      <div class="actions">
        <a class="action green" href="/reports/export.csv?date_value={_safe_text(target_date)}&class_name={_safe_text(class_name)}"><i class="fa-solid fa-file-csv"></i> Download CSV</a>
        <a class="action purple" href="/reports/export.pdf?date_value={_safe_text(target_date)}&class_name={_safe_text(class_name)}"><i class="fa-solid fa-file-pdf"></i> Download PDF</a>
        <a class="action" href="/low-attendance"><i class="fa-solid fa-triangle-exclamation"></i> Low Attendance Students</a>
      </div>

      <div class="card">
        <h2 style="margin-bottom:4px">{_safe_text(target_date)} Attendance Records</h2>
        <p class="muted" style="margin-bottom:12px">Marked: {len(rows)} &nbsp;·&nbsp; Unmarked: {missing} &nbsp;·&nbsp; Absent marked: {absent_marked}</p>
        <div class="table-responsive">
          <table>
            <thead><tr><th>Date</th><th>Subject</th><th>Student</th><th>Roll No.</th><th>Class</th><th>Status</th><th>Edit</th></tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>
      </div>
    </div>
    """
    js = """
    async function editAttendance(id, current){
      const status = prompt('Status enter karo (Present / Absent / Late):', current);
      if(status === null) return;
      const fd = new FormData();
      fd.append('attendance_id', id);
      fd.append('status', status);
      const r = await fetch('/api/attendance/update', { method: 'POST', body: fd });
      const d = await r.json();
      alert(d.message || 'Done');
      if(d.success) location.reload();
    }
    """
    return page_shell("Reports & Analytics", body, js)


@app.get("/reports/export.csv")
async def export_csv(
    request: Request,
    date_value: str = "",
    class_name: str = ""
):
    if not teacher_required(request):
        return JSONResponse({"success": False, "message": "Teacher login required"}, status_code=403)
    try:
        target_date = date_value or _today_utc()
        rows = await _fetch_attendance(target_date, class_name.strip())
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Date", "Student Name", "Roll No", "Class", "Status", "Marked At"])
        for r in rows:
            writer.writerow([
                r.get("attendance_date", ""), r.get("student_name", ""),
                r.get("roll_no", ""), r.get("class_name", ""),
                r.get("status", ""), r.get("marked_at", "")
            ])
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="attendance_{target_date}.csv"'}
        )
    except Exception as error:
        return JSONResponse({"success": False, "message": str(error)}, status_code=500)


@app.get("/reports/export.pdf")
async def export_pdf(
    request: Request,
    date_value: str = "",
    class_name: str = ""
):
    if not teacher_required(request):
        return JSONResponse({"success": False, "message": "Teacher login required"}, status_code=403)

    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.pdfgen import canvas
    except ImportError:
        return JSONResponse({
            "success": False,
            "message": "PDF ke liye reportlab install karo: pip install reportlab"
        }, status_code=500)

    try:
        target_date = date_value or _today_utc()
        rows = await _fetch_attendance(target_date, class_name.strip())
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=landscape(A4))
        width, height = landscape(A4)
        y = height - 45
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(35, y, "AI Face Attendance Report")
        y -= 25
        pdf.setFont("Helvetica", 10)
        pdf.drawString(35, y, f"Date: {target_date}    Class: {class_name or 'All'}")
        y -= 25
        headers = ["Date", "Student", "Roll No", "Class", "Status", "Marked At"]
        xs = [35, 105, 275, 360, 455, 530]
        pdf.setFont("Helvetica-Bold", 9)
        for x, h in zip(xs, headers):
            pdf.drawString(x, y, h)
        y -= 16
        pdf.setFont("Helvetica", 8)
        for r in rows:
            values = [
                str(r.get("attendance_date", "")), str(r.get("student_name", "")),
                str(r.get("roll_no", "")), str(r.get("class_name", "")),
                str(r.get("status", "")), str(r.get("marked_at", ""))[:19]
            ]
            for x, value in zip(xs, values):
                pdf.drawString(x, y, value[:24])
            y -= 13
            if y < 35:
                pdf.showPage()
                y = height - 45
                pdf.setFont("Helvetica", 8)
        pdf.save()
        buffer.seek(0)
        return Response(
            content=buffer.getvalue(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="attendance_{target_date}.pdf"'}
        )
    except Exception as error:
        return JSONResponse({"success": False, "message": str(error)}, status_code=500)


@app.get("/low-attendance", response_class=HTMLResponse)
async def low_attendance(request: Request, threshold: float = 75):
    if not teacher_required(request):
        return RedirectResponse("/login", status_code=303)

    try:
        students_result = supabase.table("students").select("id,name,roll_no,class_name").order("name").execute()
        students = students_result.data or []
        attendance_result = supabase.table("attendance").select("roll_no,status").limit(10000).execute()
        all_attendance = attendance_result.data or []
    except Exception as error:
        students, all_attendance = [], []
        load_error = str(error)
    else:
        load_error = ""

    stats = {}
    for r in all_attendance:
        roll = str(r.get("roll_no", ""))
        stats.setdefault(roll, {"total": 0, "present": 0})
        stats[roll]["total"] += 1
        if str(r.get("status", "")).lower() in {"present", "late"}:
            stats[roll]["present"] += 1

    low = []
    for s in students:
        st = stats.get(str(s.get("roll_no")), {"total": 0, "present": 0})
        pct = round(st["present"] / st["total"] * 100, 1) if st["total"] else 0
        if st["total"] and pct < threshold:
            low.append((s, pct, st["total"], st["present"]))

    rows = "".join(
        f"<tr><td><strong>{_safe_text(s.get('name'))}</strong></td>"
        f"<td><code>{_safe_text(s.get('roll_no'))}</code></td>"
        f"<td>{_safe_text(s.get('class_name'))}</td>"
        f"<td>{total}</td>"
        f"<td>{present}</td>"
        f"<td><span class=\"badge absent\">{pct}%</span></td></tr>"
        for s, pct, total, present in low
    ) or "<tr><td colspan='6' class='muted' style='text-align:center;padding:24px'>No low attendance students found below threshold.</td></tr>"

    body = f"""
    <div class="nav">
      <div class="brand">
        <div class="brand-badge"><i class="fa-solid fa-triangle-exclamation"></i></div>
        <span>Low Attendance Warning</span>
      </div>
      <div class="nav-links">
        <a href="/teacher-dashboard"><i class="fa-solid fa-chart-pie"></i> Dashboard</a>
        <a href="/reports"><i class="fa-solid fa-chart-line"></i> Reports</a>
        <a href="/logout" class="logout-link"><i class="fa-solid fa-arrow-right-from-bracket"></i> Logout</a>
      </div>
    </div>

    <div class="container">
      <div class="card">
        <h1 style="font-size:26px;font-weight:800;color:#fff;margin-bottom:4px">⚠️ Low Attendance Tracker</h1>
        <p class="muted" style="margin-bottom:18px">Filter students below acceptable attendance percentage</p>
        <form method="get" style="display:flex;gap:12px;align-items:flex-end">
          <div style="flex:1">
            <label>Attendance Threshold Percentage (%)</label>
            <input type="number" step="0.1" name="threshold" value="{threshold}" min="0" max="100" style="margin:0">
          </div>
          <button type="submit" style="height:46px"><i class="fa-solid fa-filter"></i> Check Threshold</button>
        </form>
        {('<p style="color:#f87171;margin-top:12px">'+_safe_text(load_error)+'</p>') if load_error else ''}
      </div>

      <div class="card" style="margin-top:18px">
        <div class="table-responsive">
          <table>
            <thead><tr><th>Student Name</th><th>Roll No.</th><th>Class</th><th>Total Lectures</th><th>Present Count</th><th>Percentage</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </div>
    </div>
    """
    return page_shell("Low Attendance", body)


# =====================================================
# EMAIL TEST ENDPOINTS
# =====================================================

@app.get("/test-email")
async def test_email(to: str):
    """Send a test attendance email from the Python backend."""
    student = {
        "email": to,
        "name": "Test Student",
        "roll_no": "TEST-001",
        "class_name": "Test Class",
    }
    ok, message = send_attendance_email(
        student,
        "Test Subject",
        datetime.now()
    )
    return {
        "success": ok,
        "email_sent": ok,
        "provider": "EmailJS",
        "message": message,
    }


@app.get("/email-status")
async def email_status():
    """Show EmailJS configuration status without exposing credentials."""
    configured = bool(
        EMAILJS_SERVICE_ID and EMAILJS_TEMPLATE_ID and EMAILJS_PUBLIC_KEY
    )
    return {
        "success": True,
        "provider": "EmailJS",
        "emailjs_configured": configured,
        "service_id": EMAILJS_SERVICE_ID,
        "template_id": EMAILJS_TEMPLATE_ID,
        "message": (
            "EmailJS settings are present. Use /test-email?to=YOUR_EMAIL to test delivery."
            if configured else "EmailJS configuration is incomplete in app.py."
        ),
    }


# =====================================================
# RUN SERVER
# =====================================================

if __name__ == "__main__":
    import uvicorn

    print()
    print("======================================")
    print("       AI FACE ATTENDANCE")
    print("======================================")
    print("✅ FULL RECOGNITION VERSION LOADED")
    print("✅ /recognize = REAL FACE MATCHING")
    print()
    print("Server starting...")
    print("Subject Management: http://127.0.0.1:8000/subjects")
    print("Take Attendance:    http://127.0.0.1:8000/take-attendance")
    print()
    print("Open browser:")
    print("http://127.0.0.1:8000/login")
    print()

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000
    )
    