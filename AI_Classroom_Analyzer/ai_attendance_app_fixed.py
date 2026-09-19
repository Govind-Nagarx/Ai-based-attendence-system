
# =====================================================
# SUBJECT-WISE ATTENDANCE - SUPABASE SQL
# =====================================================
# IMPORTANT: run this once in Supabase SQL Editor.
#
# ALTER TABLE attendance
# ADD COLUMN IF NOT EXISTS subject TEXT NOT NULL DEFAULT 'General';
#
# -- OLD constraint allows only one row per student per day.
# -- That MUST be removed for subject-wise attendance.
# ALTER TABLE attendance
# DROP CONSTRAINT IF EXISTS attendance_one_per_student_per_day;
#
# CREATE UNIQUE INDEX IF NOT EXISTS attendance_student_date_subject_unique
# ON attendance(roll_no, attendance_date, subject);
#
# CREATE TABLE IF NOT EXISTS subjects (
#     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
#     name TEXT NOT NULL,
#     class_name TEXT,
#     teacher_id UUID,
#     created_at TIMESTAMPTZ NOT NULL DEFAULT now()
# );
#
# CREATE UNIQUE INDEX IF NOT EXISTS subjects_name_class_unique
# ON subjects(name, COALESCE(class_name, ''));
#
import os
import base64
import traceback
import uuid
import csv
import io
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

# IMPORTANT:
# Apni Supabase key ko environment variable me rakho.
#
# Windows CMD:
# set SUPABASE_KEY=YOUR_SUPABASE_KEY
#
# PowerShell:
# $env:SUPABASE_KEY="YOUR_SUPABASE_KEY"

SUPABASE_URL = "https://ebezaegdrcpssmtlotvt.supabase.co"

SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImViZXphZWdkcmNwc3NtdGxvdHZ0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg3Nzc4MTgsImV4cCI6MjEwNDM1MzgxOH0.XzcXhF1qWhSmDlWCxNXN7tEia1XiigC8hg5KnNbRgdo"

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




# =====================================================
# SUPABASE: SUBJECT-WISE ATTENDANCE SCHEMA
# =====================================================
# Run the following SQL once in Supabase SQL Editor:
#
# ALTER TABLE attendance
# ADD COLUMN IF NOT EXISTS subject TEXT NOT NULL DEFAULT 'General';
#
# CREATE INDEX IF NOT EXISTS attendance_subject_date_idx
# ON attendance(subject, attendance_date);
#
# Recommended future tables:
#   subjects(id, name, class_name, teacher_id)
#   class_subjects(id, class_name, subject_id, teacher_id)
#
# IMPORTANT:
# The face-recognition endpoint currently uses "General" because the
# camera page does not yet send a selected subject. The next UI step is
# to add a subject dropdown and send its value to /recognize.

# =====================================================
# SMART CLASSROOM CONFIG
# =====================================================
# Planned/implemented modules for the next phase:
# - Student & teacher management
# - Class/section & subject management
# - Attendance analytics
# - CSV/PDF reporting
# - Low-attendance detection
# - Attendance audit trail
# - QR/manual fallback
# - Liveness/anti-spoofing hook
# - Engagement/attention analytics hook
#
# Keep these feature flags centralized so modules can be
# enabled independently later.
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
    """
    Subject-wise attendance report.

    Expected attendance columns:
      student_id, student_name, roll_no, class_name,
      subject, attendance_date, status, marked_at

    If your existing table does not yet have `subject`, add it in Supabase
    before using subject-wise marking.
    """
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


@app.post("/recognize")
async def recognize_face(
    request: Request,
    image_data: str = Form(...),
    subject: str = Form("General")
):
    """
    Recognize a face from the camera image and mark attendance.

    IMPORTANT:
    - students.id is treated as UUID/string.
    - Registered face files are stored in:
      face_images/student_id_<UUID>/face_1.jpg
    """

    if not teacher_required(request):
        return {
            "success": False,
            "recognized": False,
            "message": "Teacher login required"
        }

    try:
        # =================================================
        # 1. Decode camera image
        # =================================================
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

        # =================================================
        # 2. Detect face in live camera image
        # =================================================
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

        # =================================================
        # 3. Crop live face
        # =================================================
        x, y, w, h = faces[0]

        live_face = gray[y:y+h, x:x+w]

        if live_face.size == 0:
            return {
                "success": False,
                "recognized": False,
                "message": "Face crop nahi ho paya."
            }

        # Normalize size for comparison
        live_face = cv2.resize(live_face, (200, 200))

        # =================================================
        # 4. Get registered students
        # =================================================
        students_result = (
            supabase
            .table("students")
            .select("id,name,roll_no,class_name,face_registered")
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

        # =================================================
        # 5. Compare live face with stored face images
        #
        # Uses OpenCV LBPH if available.
        # =================================================
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
                # Download registered face from Supabase Storage
                downloaded = (
                    supabase
                    .storage
                    .from_("face_images")
                    .download(file_path)
                )

                if not downloaded:
                    print("⚠️ Empty face file:", file_path)
                    continue

                stored_array = np.frombuffer(
                    downloaded,
                    np.uint8
                )

                stored_image = cv2.imdecode(
                    stored_array,
                    cv2.IMREAD_GRAYSCALE
                )

                if stored_image is None:
                    print("⚠️ Invalid stored image:", file_path)
                    continue

                stored_faces = face_cascade.detectMultiScale(
                    stored_image,
                    scaleFactor=1.1,
                    minNeighbors=5,
                    minSize=(80, 80)
                )

                if len(stored_faces) == 0:
                    print("⚠️ No face in registered image:", file_path)
                    continue

                sx, sy, sw, sh = stored_faces[0]

                stored_face = stored_image[
                    sy:sy+sh,
                    sx:sx+sw
                ]

                if stored_face.size == 0:
                    continue

                stored_face = cv2.resize(
                    stored_face,
                    (200, 200)
                )

                # Train a fresh one-image LBPH model for this student.
                # A recognizer cannot be trained repeatedly after it is trained.
                recognizer = cv2.face.LBPHFaceRecognizer_create()

                recognizer.train(
                    [stored_face],
                    np.array([1], dtype=np.int32)
                )

                label, confidence = recognizer.predict(live_face)

                print(
                    "👤 Candidate:",
                    student.get("name"),
                    "| confidence:",
                    confidence
                )

                if confidence < best_confidence:
                    best_confidence = confidence
                    best_student = student

            except Exception as error:
                print(
                    "⚠️ Face compare failed for",
                    student_id,
                    ":",
                    repr(error)
                )
                continue

        # =================================================
        # 6. Confidence check
        #
        # LBPH lower confidence = better match.
        # 65 is a reasonably strict starting point.
        # =================================================
        MATCH_THRESHOLD = 85.0

        if (
            best_student is None
            or best_confidence > MATCH_THRESHOLD
        ):
            print(
                "❌ No match. Best confidence:",
                best_confidence
            )

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

        # =================================================
        # 7. Mark attendance using UUID-safe endpoint logic
        # =================================================
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
            else:
                attendance = {
                    "success": False,
                    "already_marked": False,
                    "message": "Attendance save nahi hui."
                }

        # =================================================
        # 8. Final response
        # =================================================
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

        print()
        print("========== RECOGNITION ERROR ==========")
        print("ERROR TYPE:", type(error).__name__)
        print("ERROR:", repr(error))
        traceback.print_exc()
        print("========================================")
        print()

        return {
            "success": False,
            "recognized": False,
            "message": f"{type(error).__name__}: {str(error)}"
        }
# =====================================================
# FACE DETECTOR - FIXED
# =====================================================

import cv2
import os
import urllib.request


def load_face_detector():

    filename = "haarcascade_frontalface_default.xml"

    base_dir = os.path.dirname(
        os.path.abspath(__file__)
    )

    # ---------------------------------------------
    # Possible locations
    # ---------------------------------------------

    paths = [

        # Same folder as app.py
        os.path.join(
            base_dir,
            filename
        ),

        # OpenCV haarcascade folder
        os.path.join(
            cv2.data.haarcascades,
            filename
        ),

        # Current folder
        os.path.join(
            os.getcwd(),
            filename
        )
    ]


    # ---------------------------------------------
    # Try existing files
    # ---------------------------------------------

    for path in paths:

        print(
            "🔎 Checking:",
            path
        )

        if os.path.exists(path):

            try:

                detector = cv2.CascadeClassifier(
                    path
                )

                if not detector.empty():

                    print(
                        "✅ FACE DETECTOR LOADED"
                    )

                    print(
                        "📁",
                        path
                    )

                    return detector

            except Exception as error:

                print(
                    "⚠️ Detector error:",
                    error
                )


    # ---------------------------------------------
    # If XML missing, download it automatically
    # ---------------------------------------------

    download_path = os.path.join(
        base_dir,
        filename
    )

    url = (
        "https://raw.githubusercontent.com/"
        "opencv/opencv/master/data/haarcascades/"
        "haarcascade_frontalface_default.xml"
    )

    print()
    print(
        "⚠️ Haar Cascade XML nahi mila."
    )

    print(
        "⬇️ XML automatically download ho raha hai..."
    )


    try:

        urllib.request.urlretrieve(
            url,
            download_path
        )

        print(
            "✅ XML downloaded:"
        )

        print(
            download_path
        )


        detector = cv2.CascadeClassifier(
            download_path
        )


        if detector.empty():

            raise RuntimeError(
                "XML download hua lekin OpenCV load nahi kar pa raha."
            )


        print(
            "✅ FACE DETECTOR LOADED"
        )

        return detector


    except Exception as error:

        print()
        print(
            "❌ FACE DETECTOR LOAD FAILED"
        )

        print(
            "ERROR:",
            type(error).__name__,
            str(error)
        )

        print()
        print(
            "Expected XML file:"
        )

        print(
            download_path
        )

        raise RuntimeError(
            "Face detector load nahi hua. "
            "Internet ON karke app dobara start karo."
        )


# ---------------------------------------------
# LOAD DETECTOR
# ---------------------------------------------

face_cascade = load_face_detector()
# =====================================================
# AUTH LANDING PAGE
# =====================================================

@app.get("/app", response_class=HTMLResponse)
async def app_landing():
    return RedirectResponse("/login", status_code=303)


# =====================================================
# HOME PAGE
# =====================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
async def home():

    return """
<!DOCTYPE html>

<html lang="en">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>AI Face Attendance</title>

<style>

* {
    box-sizing: border-box;
}

body {

    margin: 0;

    min-height: 100vh;

    font-family:
        Arial,
        sans-serif;

    background:
        #0f172a;

    color: white;

    display: flex;

    justify-content: center;

    align-items: center;

    padding: 25px;
}

.container {

    width: 100%;

    max-width: 900px;

    background:
        #1e293b;

    padding: 30px;

    border-radius: 20px;

    box-shadow:
        0 20px 60px
        rgba(0,0,0,.4);
}

h1 {

    text-align: center;

    margin-top: 0;

    margin-bottom: 8px;
}

.subtitle {

    text-align: center;

    color: #94a3b8;

    margin-bottom: 30px;
}

.grid {

    display: grid;

    grid-template-columns:
        1fr 1fr;

    gap: 25px;
}

.card {

    background:
        #0f172a;

    padding: 22px;

    border-radius: 15px;
}

.card h3 {

    margin-top: 0;

    margin-bottom: 18px;
}

input {

    width: 100%;

    padding: 14px;

    margin-bottom: 12px;

    border:
        1px solid #475569;

    border-radius: 9px;

    background:
        #1e293b;

    color: white;

    font-size: 15px;

    outline: none;
}

input:focus {

    border-color:
        #60a5fa;
}

video {

    width: 100%;

    display: block;

    background:
        black;

    border-radius: 12px;

    min-height: 250px;

    object-fit: cover;
}

canvas {

    display: none;
}

button {

    width: 100%;

    padding: 14px;

    margin-top: 10px;

    border: none;

    border-radius: 9px;

    cursor: pointer;

    font-size: 15px;

    font-weight: bold;

    transition:
        .2s;
}

button:hover {

    opacity: .9;
}

button:disabled {

    opacity: .5;

    cursor:
        not-allowed;
}

#startCamera {

    background:
        #2563eb;

    color: white;
}

#capture {

    background:
        #059669;

    color: white;
}

#register {

    background:
        #7c3aed;

    color: white;
}

#message {

    margin-top: 20px;

    text-align: center;

    padding: 12px;

    border-radius: 8px;

    min-height: 20px;
}

.status {

    color:
        #94a3b8;

    text-align: center;

    margin-top: 12px;

    font-size: 14px;
}

.success {

    background:
        rgba(16,185,129,.15);

    color:
        #6ee7b7;
}

.error {

    background:
        rgba(239,68,68,.15);

    color:
        #fca5a5;
}

@media(max-width:700px) {

    .grid {

        grid-template-columns:
            1fr;
    }

    .container {

        padding: 20px;
    }
}

</style>

</head>


<body>


<div class="container">

    <h1>
        🤖 AI Face Attendance
    </h1>

    <div class="subtitle">
        Student Registration & Face Capture
    </div>


    <div class="grid">


        <!-- STUDENT DETAILS -->

        <div class="card">

            <h3>
                Student Details
            </h3>

            <form id="form">

                <input
                    type="text"
                    name="name"
                    autocomplete="name"
                    placeholder="Student Name"
                    required
                >

                <input
                    type="text"
                    name="roll_no"
                    autocomplete="off"
                    placeholder="Roll Number"
                    required
                >

                <input
                    type="text"
                    name="class_name"
                    autocomplete="off"
                    placeholder="Class"
                    required
                >

                <button
                    type="submit"
                    id="register"
                >
                    Register Student
                </button>

            </form>

        </div>


        <!-- CAMERA -->

        <div class="card">

            <h3>
                Face Capture
            </h3>

            <video
                id="video"
                autoplay
                playsinline
            ></video>

            <canvas
                id="canvas"
            ></canvas>

            <button
                id="startCamera"
                type="button"
            >
                📷 Start Camera
            </button>

            <button
                id="capture"
                type="button"
                disabled
            >
                📸 Capture Face
            </button>

            <div
                class="status"
                id="cameraStatus"
            >
                Camera not started
            </div>

        </div>

    </div>


    <div id="message"></div>

</div>


<script>

const form =
    document.getElementById("form");

const video =
    document.getElementById("video");

const canvas =
    document.getElementById("canvas");

const startCamera =
    document.getElementById("startCamera");

const capture =
    document.getElementById("capture");

const register =
    document.getElementById("register");

const message =
    document.getElementById("message");

const cameraStatus =
    document.getElementById(
        "cameraStatus"
    );


let stream = null;

let capturedImage = null;


// =====================================================
// MESSAGE
// =====================================================

function showMessage(
    text,
    type = ""
) {

    message.innerText = text;

    message.className = type;
}


// =====================================================
// START CAMERA
// =====================================================

startCamera.addEventListener(
    "click",
    async () => {

        try {

            if (
                !navigator.mediaDevices ||
                !navigator.mediaDevices.getUserMedia
            ) {

                showMessage(
                    "Browser camera support nahi karta.",
                    "error"
                );

                return;
            }


            stream =
                await navigator.mediaDevices
                    .getUserMedia({

                        video: {

                            width: {
                                ideal: 640
                            },

                            height: {
                                ideal: 480
                            },

                            facingMode:
                                "user"
                        },

                        audio: false

                    });


            video.srcObject =
                stream;


            capture.disabled =
                false;


            cameraStatus.innerText =
                "✅ Camera started. Face camera ke saamne rakho.";


            showMessage("");

        }

        catch(error) {

            console.error(error);


            cameraStatus.innerText =
                "❌ Camera start nahi hua.";


            showMessage(
                "Camera permission allow karo.",
                "error"
            );

        }

    }
);


// =====================================================
// CAPTURE FACE
// =====================================================

capture.addEventListener(
    "click",
    () => {

        if (!stream) {

            showMessage(
                "Pehle camera start karo.",
                "error"
            );

            return;
        }


        if (
            video.videoWidth === 0 ||
            video.videoHeight === 0
        ) {

            showMessage(
                "Camera image ready nahi hai. 1-2 second wait karo.",
                "error"
            );

            return;
        }


        canvas.width =
            video.videoWidth;

        canvas.height =
            video.videoHeight;


        const ctx =
            canvas.getContext("2d");


        ctx.drawImage(
            video,
            0,
            0,
            canvas.width,
            canvas.height
        );


        capturedImage =
            canvas.toDataURL(
                "image/jpeg",
                0.90
            );


        cameraStatus.innerText =
            "✅ Face photo captured.";


        showMessage(
            "Photo ready. Ab Register Student dabao.",
            "success"
        );

    }
);


// =====================================================
// REGISTER STUDENT
// =====================================================

form.addEventListener(
    "submit",
    async (event) => {

        event.preventDefault();


        if (!capturedImage) {

            showMessage(
                "Pehle face capture karo.",
                "error"
            );

            return;
        }


        register.disabled =
            true;


        showMessage(
            "Student + face save ho raha hai..."
        );


        try {

            const formData =
                new FormData(form);


            formData.append(
                "face_image",
                capturedImage
            );


            const response =
                await fetch(
                    "/register",
                    {
                        method:
                            "POST",

                        body:
                            formData
                    }
                );


            const data =
                await response.json();


            if (data.success) {

                showMessage(
                    "✅ " + data.message,
                    "success"
                );


                form.reset();


                capturedImage =
                    null;


                cameraStatus.innerText =
                    "Next student ke liye ready.";


                if (stream) {

                    capture.disabled =
                        false;
                }

            }

            else {

                showMessage(
                    "❌ " + data.message,
                    "error"
                );

            }

        }

        catch(error) {

            console.error(error);

            showMessage(
                "❌ Server se connection nahi ho raha.",
                "error"
            );

        }

        finally {

            register.disabled =
                false;

        }

    }
);

</script>


</body>

</html>
"""


# =====================================================
# REGISTER STUDENT
# =====================================================

@app.post("/register")
async def register_student(

    name: str = Form(...),

    roll_no: str = Form(...),

    class_name: str = Form(...),

    face_image: str = Form(...)

):

    # =================================================
    # CLEAN INPUT
    # =================================================

    name = name.strip()

    roll_no = roll_no.strip()

    class_name = class_name.strip()


    # =================================================
    # VALIDATION
    # =================================================

    if (
        not name
        or not roll_no
        or not class_name
    ):

        return {

            "success":
                False,

            "message":
                "Sabhi fields bharna zaroori hai."

        }


    if not face_image:

        return {

            "success":
                False,

            "message":
                "Face photo capture karo."

        }


    try:

        # =============================================
        # DUPLICATE ROLL NUMBER
        # =============================================

        existing = (
            supabase
            .table("students")
            .select("id")
            .eq("roll_no", roll_no)
            .execute()
        )


        if existing.data:

            return {

                "success":
                    False,

                "message":
                    "Ye Roll Number already registered hai."

            }


        # =============================================
        # DECODE IMAGE
        # =============================================

        if "," in face_image:

            image_data =face_image.split(
                    ",",
                    1
                )[1]

        else:

            image_data =  face_image


        try:

            image_bytes =   base64.b64decode(
                    image_data
                )

        except Exception:

            return {

                "success":
                    False,

                "message":
                    "Face image decode nahi hui."

            }


        # =============================================
        # IMAGE TO OPENCV
        # =============================================

        np_array =  np.frombuffer(
                image_bytes,
                np.uint8
            )


        frame =  cv2.imdecode(
                np_array,
                cv2.IMREAD_COLOR
            )


        if frame is None:

            return {

                "success":
                    False,

                "message":
                    "Captured image invalid hai."

            }


        # =============================================
        # GRAYSCALE
        # =============================================

        gray =  cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2GRAY
            )


        # =============================================
        # FACE DETECTION
        # =============================================

        faces =  face_cascade.detectMultiScale(

                gray,

                scaleFactor=1.1,

                minNeighbors=5,

                minSize=(80, 80)

            )


        print(
            "Detected faces:",
            len(faces)
        )


        # =============================================
        # NO FACE
        # =============================================

        if len(faces) == 0:

            return {

                "success":
                    False,

                "message":
                    "Face detect nahi hua. Camera ke saamne clearly dekho."

            }


        # =============================================
        # MULTIPLE FACES
        # =============================================

        if len(faces) > 1:

            return {

                "success":
                    False,

                "message":
                    "Ek time par sirf ek person camera ke saamne ho."

            }


        # =============================================
        # CREATE STUDENT
        # =============================================

        student = {

            "name":
                name,

            "roll_no":
                roll_no,

            "class_name":
                class_name,

            "face_registered":
                False,

            "created_at":
                datetime
                .now(timezone.utc)
                .isoformat()

        }


        result = (
            supabase
            .table("students")
            .insert(student)
            .execute()
        )


        if not result.data:

            return {

                "success":
                    False,

                "message":
                    "Student save nahi hua."

            }


        student_id = result.data[0]["id"]


        # =============================================
        # STORAGE FILE PATH
        # =============================================

        file_path = f"student_id_{student_id}/face_1.jpg"


        # =============================================
        # UPLOAD FACE IMAGE
        # =============================================

        upload_result = (

            supabase
            .storage
            .from_("face_images")
            .upload(

                file_path,

                image_bytes,

                {
                    "content-type":
                        "image/jpeg",

                    "upsert":
                        "true"
                }

            )

        )


        print(
            "✅ Face uploaded:",
            file_path
        )


        # =============================================
        # UPDATE STUDENT
        # =============================================

        update_result = (

            supabase
            .table("students")
            .update({

                "face_registered":
                    True

            })
            .eq(
                "id",
                student_id
            )
            .execute()

        )


        return {

            "success":
                True,

            "message":
                "Student aur face successfully registered!",

            "student_id":
                student_id,

            "face_path":
                file_path

        }


    except Exception as error:

        print()
        print(
            "========== FULL ERROR =========="
        )

        print(
            "ERROR TYPE:",
            type(error).__name__
        )

        print(
            "ERROR:",
            repr(error)
        )

        traceback.print_exc()

        print(
            "================================"
        )

        print()


        return {

            "success":
                False,

            "message":
                f"{type(error).__name__}: {str(error)}"

        }



# =====================================================
# AUTH + TEACHER / STUDENT DASHBOARDS
# =====================================================


# Demo credentials can be changed with environment variables.
TEACHER_USERNAME = os.getenv("TEACHER_USERNAME", "teacher")
TEACHER_PASSWORD = os.getenv("TEACHER_PASSWORD", "teacher123")

# Simple cookie-based role session for the current prototype.
# For production, replace this with Supabase Auth.
def page_shell(title, body, extra_js=""):
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;font-family:Arial,sans-serif;background:#0f172a;color:#f8fafc}}
.nav{{background:#1e293b;padding:14px 20px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}}
.brand{{font-size:20px;font-weight:700}}
.nav a{{color:#cbd5e1;text-decoration:none;margin-left:16px}}
.container{{max-width:1200px;margin:auto;padding:24px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}}
.card{{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:20px}}
.stat{{font-size:30px;font-weight:700;margin-top:8px}}
.muted{{color:#94a3b8}}
.actions{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:20px 0}}
.action{{display:block;text-decoration:none;color:white;background:#2563eb;padding:18px;border-radius:14px;font-weight:700}}
.action.green{{background:#059669}}
.action.purple{{background:#7c3aed}}
table{{width:100%;border-collapse:collapse;margin-top:12px}}
th,td{{padding:12px;border-bottom:1px solid #334155;text-align:left}}
input,select{{width:100%;padding:12px;border:1px solid #475569;border-radius:9px;background:#0f172a;color:white;margin:5px 0 12px}}
button{{padding:12px 16px;border:0;border-radius:9px;background:#2563eb;color:white;font-weight:700;cursor:pointer}}
.badge{{padding:5px 9px;border-radius:999px;font-size:12px;background:#334155}}
.present{{color:#6ee7b7}}
.absent{{color:#fca5a5}}
@media(max-width:800px){{
 .grid{{grid-template-columns:repeat(2,1fr)}}
 .actions{{grid-template-columns:1fr}}
 .container{{padding:14px}}
 table{{font-size:13px}}
 th,td{{padding:9px}}
}}
@media(max-width:500px){{
 .grid{{grid-template-columns:1fr}}
 .nav{{padding:12px}}
 .nav a{{margin-left:8px;font-size:13px}}
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
<div class="container" style="max-width:460px;padding-top:60px">
<div class="card">
<h1>🤖 AI Attendance</h1>
<p class="muted">Login as Teacher or Student</p>

<label for="role">Role</label>
<select id="role" name="role" autocomplete="off">
<option value="teacher">Teacher</option>
<option value="student">Student</option>
</select>

<div id="teacherFields">
<label for="username">Username</label>
<input id="username" name="username" placeholder="Teacher username" autocomplete="username">
<label for="password">Password</label>
<input id="password" name="password" type="password" placeholder="Password" autocomplete="current-password">
</div>

<div id="studentFields" style="display:none">
<label for="roll_no">Roll Number</label>
<input id="roll_no" name="roll_no" placeholder="Student roll number" autocomplete="off">
<p class="muted">Prototype login: student roll number se login hoga.</p>
</div>

<button onclick="login()" style="width:100%">Login</button>
<div id="msg" class="muted" style="margin-top:14px;text-align:center"></div>
</div>
</div>
"""
    js = """
const role=document.getElementById('role');
role.addEventListener('change',()=>{
 document.getElementById('teacherFields').style.display=role.value==='teacher'?'block':'none';
 document.getElementById('studentFields').style.display=role.value==='student'?'block':'none';
});
async function login(){
 const fd=new FormData();
 fd.append('role',role.value);
 if(role.value==='teacher'){
   fd.append('username',document.getElementById('username').value);
   fd.append('password',document.getElementById('password').value);
 }else{
   fd.append('roll_no',document.getElementById('roll_no').value);
 }
 const r=await fetch('/auth/login',{method:'POST',body:fd});
 const d=await r.json();
 document.getElementById('msg').textContent=d.message||'';
 if(d.success) location.href=d.redirect;
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
            result = supabase.table("students").select("id,name,roll_no,class_name,face_registered").eq("roll_no", roll_no).limit(1).execute()
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
        result = supabase.table("students").select("id,name,roll_no,class_name,face_registered,created_at").order("created_at", desc=True).execute()
        students = result.data or []
        total = len(students)
    except Exception as error:
        print("Teacher dashboard error:", repr(error))

    # Attendance stats are read if the optional attendance table exists.
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
        f"<tr><td>{s.get('name','')}</td><td>{s.get('roll_no','')}</td>"
        f"<td>{s.get('class_name','')}</td>"
        f"<td>{'✅ Registered' if s.get('face_registered') else '❌ Pending'}</td></tr>"
        for s in students[:100]
    ) or "<tr><td colspan='4' class='muted'>Abhi koi student nahi hai.</td></tr>"

    body = f"""
<div class="nav">
 <div class="brand">👨‍🏫 Teacher Dashboard</div>
 <div><a href="/register-page">➕ Register</a><a href="/logout">Logout</a></div>
</div>
<div class="container">
<h1>Attendance Overview</h1>
<p class="muted">Aaj ka teacher control panel</p>

<div class="grid">
 <div class="card"><div class="muted">Total Students</div><div class="stat">{total}</div></div>
 <div class="card"><div class="muted">Present Today</div><div class="stat present">{present}</div></div>
 <div class="card"><div class="muted">Absent Today</div><div class="stat absent">{absent}</div></div>
 <div class="card"><div class="muted">Attendance %</div><div class="stat">{pct}%</div></div>
</div>

<div class="actions">
 <a class="action" href="/take-attendance">📷 Take AI Attendance</a>
 <a class="action green" href="/register-page">👨‍🎓 Register Student</a>
 <a class="action purple" href="/attendance-history">📊 Attendance History</a>
 <a class="action" href="/student-management">🧑‍🎓 Manage Students</a>
 <a class="action green" href="/reports">📈 Reports & Analytics</a>
 <a class="action purple" href="/low-attendance">⚠️ Low Attendance</a>
 <a class="action" href="/subjects">📚 Manage Subjects</a>
</div>

<div class="card">
<h2>Registered Students</h2>
<table>
<thead><tr><th>Name</th><th>Roll No.</th><th>Class</th><th>Face</th></tr></thead>
<tbody>{rows_html}</tbody>
</table>
</div>
</div>
"""
    return page_shell("Teacher Dashboard", body)


@app.get("/subjects", response_class=HTMLResponse)
async def subjects_page(request: Request):
    if not teacher_required(request):
        return RedirectResponse("/login", status_code=303)
    body = """<div class="nav"><div class="brand">📚 Subject Management</div><div><a href="/teacher-dashboard">Dashboard</a><a href="/take-attendance">Take Attendance</a><a href="/logout">Logout</a></div></div><div class="container"><div class="card"><h1>📚 Manage Subjects</h1><p class="muted">Subject add karo. Ye Take Attendance page me automatically dikhega.</p><label>Subject Name</label><input id="subjectName" placeholder="e.g. Python">
<label>Class / Section</label><input id="subjectClass" placeholder="e.g. BCA 2nd Year A"><button onclick="addSubject()" style="background:#059669;color:white">➕ Add Subject</button><div id="subjectMsg" class="muted" style="margin-top:12px"></div></div><div class="card"><h2>Available Subjects</h2><table><thead><tr><th>Subject</th><th>Class</th></tr></thead><tbody id="subjectRows"><tr><td colspan="2">Loading...</td></tr></tbody></table></div></div>"""
    js = """async function loadSubjects(){const tb=document.getElementById('subjectRows');try{const r=await fetch('/api/subjects');const d=await r.json();if(!d.success)throw new Error();const rows=d.subjects||[];tb.innerHTML=rows.length?rows.map(s=>`<tr><td>${String(s.name||'')}</td><td>${String(s.class_name||'All')}</td></tr>`).join(''):'<tr><td colspan="2">No subjects</td></tr>';}catch(e){tb.innerHTML='<tr><td colspan="2">❌ Subject load failed</td></tr>';}}async function addSubject(){const name=document.getElementById('subjectName').value.trim();const cls=document.getElementById('subjectClass').value.trim();const m=document.getElementById('subjectMsg');if(!name){m.textContent='❌ Subject name required';return;}const fd=new FormData();fd.append('name',name);fd.append('class_name',cls);try{const r=await fetch('/api/subjects',{method:'POST',body:fd});const d=await r.json();if(d.success){m.textContent='✅ '+name+' add ho gaya';document.getElementById('subjectName').value='';loadSubjects();}else m.textContent='❌ '+(d.message||'Subject add failed');}catch(e){m.textContent='❌ Server error';}}loadSubjects();"""
    return page_shell("Subject Management", body, js)
    

@app.get("/register-page", response_class=HTMLResponse)
async def register_page(request: Request):
    if not teacher_required(request):
        return RedirectResponse("/login", status_code=303)
    # Reuse the existing registration UI already present on the home page.
    return await home()


@app.get("/take-attendance", response_class=HTMLResponse)
async def take_attendance(request: Request):
    if not teacher_required(request):
        return RedirectResponse("/login", status_code=303)

    body = """
<div class="nav">
 <div class="brand">📷 AI Attendance</div>
 <div><a href="/teacher-dashboard">Dashboard</a><a href="/logout">Logout</a></div>
</div>
<div class="container">
<div class="card">
<h1>Take Attendance</h1>
<p class="muted">Pehle subject select karo, phir face scan karo.</p>
<label for="attendanceSubject" style="display:block;margin:12px 0 6px;font-weight:700">📚 Select Subject</label>
<select id="attendanceSubject" style="width:100%;padding:13px;border-radius:10px;border:1px solid #475569;background:#0f172a;color:white;margin-bottom:12px">
 <option value="">Loading subjects...</option>
</select>
<video id="video" autoplay playsinline style="width:100%;max-width:700px;border-radius:14px;background:#000"></video>
<canvas id="canvas" style="display:none"></canvas>
<button id="start" style="width:100%;margin-top:12px">📷 Start Camera</button>
<button id="capture" disabled style="width:100%;margin-top:10px;background:#059669">🔎 Recognize Face</button>
<div id="msg" class="muted" style="margin-top:15px;text-align:center"></div>
</div>
</div>
"""
    js = """
let stream=null;
const video=document.getElementById('video');
const start=document.getElementById('start');
const capture=document.getElementById('capture');
const msg=document.getElementById('msg');
const subjectSelect=document.getElementById('attendanceSubject');
async function loadSubjects(){try{const r=await fetch('/api/subjects');const d=await r.json();subjectSelect.innerHTML='<option value="">Select Subject</option>';if(d.success&&(d.subjects||[]).length){(d.subjects||[]).forEach(s=>{const o=document.createElement('option');o.value=s.name;o.textContent=s.name+(s.class_name?' — '+s.class_name:'');subjectSelect.appendChild(o);});}else subjectSelect.innerHTML='<option value="">No subjects found — add subject first</option>';}catch(e){subjectSelect.innerHTML='<option value="">Subject loading failed</option>';}}
loadSubjects();

start.onclick=async()=>{
 try{
  stream=await navigator.mediaDevices.getUserMedia({
   video:{width:{ideal:640},height:{ideal:480},facingMode:'user'},
   audio:false
  });
  video.srcObject=stream;
  capture.disabled=false;
  msg.textContent='Camera ready. Face ko camera ke saamne rakho.';
 }catch(e){
  console.error(e);
  msg.textContent='Camera permission allow karo.';
 }
};

capture.onclick=async()=>{
 if(!stream)return;

 const c=document.getElementById('canvas');
 if(!video.videoWidth || !video.videoHeight){
  msg.textContent='Camera image ready nahi hai. 1-2 second wait karo.';
  return;
 }

 c.width=video.videoWidth;
 c.height=video.videoHeight;
 c.getContext('2d').drawImage(video,0,0,c.width,c.height);

 const image=c.toDataURL('image/jpeg',0.9);
 const fd=new FormData();
 const selectedSubject=subjectSelect.value;
 if(!selectedSubject){msg.textContent='❌ Pehle subject select karo.';return;}
 fd.append('image_data',image);
 fd.append('subject',selectedSubject);

 capture.disabled=true;
 msg.textContent='🔎 Face matching ho raha hai...';

 try{
  const response=await fetch('/recognize',{
   method:'POST',
   body:fd
  });

  const data=await response.json();

  if(data.recognized){
   const s=data.student || {};
   const a=data.attendance || {};

   if(a.already_marked){
    msg.textContent='✅ '+(s.name||'Student')+' recognized. Attendance already marked today.';
   }else if(a.success){
    msg.textContent='✅ '+(s.name||'Student')+' recognized — '+selectedSubject+' attendance marked.';
   }else{
    msg.textContent='⚠️ '+(s.name||'Student')+' recognized, but attendance save failed: '+(a.message||'Unknown error');
   }
  }else{
   msg.textContent='❌ '+(data.message||'Face not recognized.');
  }
 }catch(e){
  console.error(e);
  msg.textContent='❌ Recognition server se connection nahi ho raha.';
 }finally{
  capture.disabled=false;
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
        f"<tr><td>{r.get('attendance_date','')}</td><td>{r.get('subject','General')}</td>"
        f"<td>{r.get('student_name',r.get('roll_no',''))}</td><td>{r.get('roll_no','')}</td>"
        f"<td>{r.get('marked_at','')}</td><td>{r.get('status','')}</td></tr>"
        for r in rows
    ) or "<tr><td colspan='5' class='muted'>Attendance table/data abhi available nahi hai.</td></tr>"

    body = f"""
<div class="nav">
 <div class="brand">📊 Attendance History</div>
 <div><a href="/teacher-dashboard">Dashboard</a><a href="/logout">Logout</a></div>
</div>
<div class="container">
<div class="card">
<h1>Attendance History</h1>
<table>
<thead><tr><th>Date</th><th>Subject</th><th>Student</th><th>Roll No.</th><th>Time</th><th>Status</th></tr></thead>
<tbody>{html}</tbody>
</table>
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
        result = supabase.table("students").select("id,name,roll_no,class_name,face_registered").eq("roll_no", roll_no).limit(1).execute()
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
        f"<tr><td>{r.get('attendance_date','')}</td><td>{r.get('marked_at','')}</td><td>{r.get('status','')}</td></tr>"
        for r in attendance_rows
    ) or "<tr><td colspan='3' class='muted'>Attendance record abhi nahi mila.</td></tr>"

    body = f"""
<div class="nav">
 <div class="brand">👨‍🎓 Student Dashboard</div>
 <div><a href="/logout">Logout</a></div>
</div>
<div class="container">
<h1>Welcome, {name}</h1>
<p class="muted">Roll No: {roll_no} · Class: {cls}</p>

<div class="grid">
 <div class="card"><div class="muted">My Present</div><div class="stat present">{present}</div></div>
 <div class="card"><div class="muted">Total Records</div><div class="stat">{total}</div></div>
 <div class="card"><div class="muted">My Attendance</div><div class="stat">{pct}%</div></div>
 <div class="card"><div class="muted">Face Status</div><div class="stat" style="font-size:22px">{'✅ Ready' if student and student.get('face_registered') else '❌ Pending'}</div></div>
</div>

<div class="card" style="margin-top:20px">
<h2>My Attendance History</h2>
<table>
<thead><tr><th>Date</th><th>Time</th><th>Status</th></tr></thead>
<tbody>{rows_html}</tbody>
</table>
</div>
</div>
"""
    return page_shell("Student Dashboard", body)



# =====================================================
# ATTENDANCE MARKING API
# CCTV/AI LATER CAN CALL THIS SAME ENDPOINT
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

        # One attendance row per student per day.
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

        # Reuse the same daily-record behavior.
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
            .eq("subject", selected_subject)
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
# OPTIONAL SUPABASE ATTENDANCE API
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
    """Small HTML escape helper for dashboard output."""
    from html import escape
    return escape(str(value or ""))


@app.get("/api/students")
async def api_students(request: Request, search: str = "", class_name: str = ""):
    if not teacher_required(request):
        return {"success": False, "message": "Teacher login required"}

    try:
        query = supabase.table("students").select(
            "id,name,roll_no,class_name,face_registered,created_at"
        ).order("created_at", desc=True)

        if search.strip():
            term = search.strip()
            # Supabase OR syntax for name / roll / class search.
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
    class_name: str = Form(...)
):
    if not teacher_required(request):
        return {"success": False, "message": "Teacher login required"}

    try:
        sid = str(student_id).strip()
        uuid.UUID(sid)
        name, roll_no, class_name = name.strip(), roll_no.strip(), class_name.strip()
        if not name or not roll_no or not class_name:
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
            .update({"name": name, "roll_no": roll_no, "class_name": class_name})
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
      <div class="brand">🧑‍🎓 Student Management</div>
      <div><a href="/teacher-dashboard">Dashboard</a><a href="/register-page">Register</a><a href="/logout">Logout</a></div>
    </div>
    <div class="container">
      <div class="card">
        <h1>Student Management</h1>
        <p class="muted">Search, edit aur delete students.</p>
        <div style="display:grid;grid-template-columns:2fr 1fr 120px;gap:10px">
          <input id="search" placeholder="Name / Roll Number / Class">
          <input id="className" placeholder="Exact Class">
          <button onclick="loadStudents()">Search</button>
        </div>
      </div>
      <div class="card" style="margin-top:18px;overflow:auto">
        <table>
          <thead><tr><th>Name</th><th>Roll No.</th><th>Class</th><th>Face</th><th>Actions</th></tr></thead>
          <tbody id="rows"><tr><td colspan="5" class="muted">Loading...</td></tr></tbody>
        </table>
      </div>
    </div>
    """
    js = """
    async function loadStudents(){
      const q=document.getElementById('search').value.trim();
      const c=document.getElementById('className').value.trim();
      const r=await fetch('/api/students?search='+encodeURIComponent(q)+'&class_name='+encodeURIComponent(c));
      const d=await r.json();
      const rows=document.getElementById('rows');
      if(!d.success){ rows.innerHTML='<tr><td colspan="5">❌ '+(d.message||'Error')+'</td></tr>'; return; }
      if(!d.students.length){ rows.innerHTML='<tr><td colspan="5" class="muted">Student nahi mila.</td></tr>'; return; }
      rows.innerHTML=d.students.map(s=>`<tr>
        <td>${esc(s.name)}</td><td>${esc(s.roll_no)}</td><td>${esc(s.class_name)}</td>
        <td>${s.face_registered?'✅ Registered':'❌ Pending'}</td>
        <td><button onclick='editStudent(${JSON.stringify(s)})'>✏️ Edit</button>
        <button style="background:#dc2626;margin-left:5px" onclick='deleteStudent(${JSON.stringify(s.id)})'>🗑️ Delete</button></td>
      </tr>`).join('');
    }
    function esc(v){return String(v??'').replace(/[&<>'"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[m]));}
    async function editStudent(s){
      const name=prompt('Student Name:',s.name); if(name===null)return;
      const roll=prompt('Roll Number:',s.roll_no); if(roll===null)return;
      const cls=prompt('Class:',s.class_name); if(cls===null)return;
      const fd=new FormData(); fd.append('student_id',s.id); fd.append('name',name); fd.append('roll_no',roll); fd.append('class_name',cls);
      const r=await fetch('/api/student/update',{method:'POST',body:fd}); const d=await r.json(); alert(d.message||'Done'); if(d.success)loadStudents();
    }
    async function deleteStudent(id){
      if(!confirm('Is student ko delete karna hai?'))return;
      const fd=new FormData(); fd.append('student_id',id);
      const r=await fetch('/api/student/delete',{method:'POST',body:fd}); const d=await r.json(); alert(d.message||'Done'); if(d.success)loadStudents();
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
        f"<tr><td>{_safe_text(r.get('attendance_date'))}</td><td>{_safe_text(r.get('subject','General'))}</td>"
        f"<td>{_safe_text(r.get('student_name'))}</td><td>{_safe_text(r.get('roll_no'))}</td>"
        f"<td>{_safe_text(r.get('class_name'))}</td><td>{_safe_text(r.get('status'))}</td><td><button onclick=\"editAttendance('{_safe_text(r.get('id'))}','{_safe_text(r.get('status'))}')\">✏️</button></td></tr>"
        for r in rows
    ) or "<tr><td colspan='6' class='muted'>Is date par attendance record nahi hai.</td></tr>"

    body = f"""
    <div class="nav">
      <div class="brand">📈 Reports & Analytics</div>
      <div><a href="/teacher-dashboard">Dashboard</a><a href="/attendance-history">History</a><a href="/logout">Logout</a></div>
    </div>
    <div class="container">
      <div class="card">
        <h1>Attendance Reports</h1>
        <form method="get" style="display:grid;grid-template-columns:1fr 1fr 120px;gap:10px">
          <input type="date" name="date_value" value="{_safe_text(target_date)}">
          <input name="class_name" value="{_safe_text(class_name)}" placeholder="Class (optional)">
          <input name="subject" value="{_safe_text(subject)}" placeholder="Subject (optional)">
          <button type="submit">Apply</button>
        </form>
      </div>
      <div class="grid" style="margin-top:18px">
        <div class="card"><div class="muted">Total Students</div><div class="stat">{total_students}</div></div>
        <div class="card"><div class="muted">Present</div><div class="stat present">{present}</div></div>
        <div class="card"><div class="muted">Late</div><div class="stat">{late}</div></div>
        <div class="card"><div class="muted">Attendance %</div><div class="stat">{pct}%</div></div>
      </div>
      <div class="actions">
        <a class="action green" href="/reports/export.csv?date_value={_safe_text(target_date)}&class_name={_safe_text(class_name)}">📥 Download CSV</a>
        <a class="action purple" href="/reports/export.pdf?date_value={_safe_text(target_date)}&class_name={_safe_text(class_name)}">📄 Download PDF</a>
        <a class="action" href="/low-attendance">⚠️ Low Attendance Students</a>
      </div>
      <div class="card" style="overflow:auto">
        <h2>{_safe_text(target_date)} Attendance</h2>
        <p class="muted">Marked: {len(rows)} · Unmarked: {missing} · Absent marked: {absent_marked}</p>
        <table><thead><tr><th>Date</th><th>Subject</th><th>Student</th><th>Roll No.</th><th>Class</th><th>Status</th><th>Edit</th></tr></thead>
        <tbody>{rows_html}</tbody></table>
      </div>
    </div>
    """
    js = """
    async function editAttendance(id,current){
      const status=prompt('Status: Present / Absent / Late',current); if(status===null)return;
      const fd=new FormData(); fd.append('attendance_id',id); fd.append('status',status);
      const r=await fetch('/api/attendance/update',{method:'POST',body:fd}); const d=await r.json(); alert(d.message||'Done'); if(d.success)location.reload();
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
        f"<tr><td>{_safe_text(s.get('name'))}</td><td>{_safe_text(s.get('roll_no'))}</td>"
        f"<td>{_safe_text(s.get('class_name'))}</td><td>{total}</td><td>{present}</td><td>{pct}%</td></tr>"
        for s, pct, total, present in low
    ) or "<tr><td colspan='6' class='muted'>Low attendance student nahi mila.</td></tr>"

    body = f"""
    <div class="nav"><div class="brand">⚠️ Low Attendance</div>
      <div><a href="/teacher-dashboard">Dashboard</a><a href="/reports">Reports</a><a href="/logout">Logout</a></div>
    </div>
    <div class="container">
      <div class="card">
        <h1>Low Attendance Students</h1>
        <form method="get" style="display:flex;gap:10px;align-items:end">
          <div style="flex:1"><label>Threshold %</label><input type="number" step="0.1" name="threshold" value="{threshold}" min="0" max="100"></div>
          <button type="submit">Check</button>
        </form>
        {('<p class="error">'+_safe_text(load_error)+'</p>') if load_error else ''}
      </div>
      <div class="card" style="margin-top:18px;overflow:auto">
        <table><thead><tr><th>Name</th><th>Roll No.</th><th>Class</th><th>Total</th><th>Present</th><th>Percentage</th></tr></thead>
        <tbody>{rows}</tbody></table>
      </div>
    </div>
    """
    return page_shell("Low Attendance", body)


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