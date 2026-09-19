import os
import base64
import traceback
from datetime import datetime, timezone

import cv2
import numpy as np

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
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
# FASTAPI
# =====================================================

app = FastAPI(
    title="AI Face Attendance",
    version="1.0.0"
)


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
                    placeholder="Student Name"
                    required
                >

                <input
                    type="text"
                    name="roll_no"
                    placeholder="Roll Number"
                    required
                >

                <input
                    type="text"
                    name="class_name"
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
# RUN SERVER
# =====================================================

if __name__ == "__main__":

    import uvicorn

    print()
    print("======================================")
    print("       AI FACE ATTENDANCE")
    print("======================================")
    print()
    print("Server starting...")
    print()
    print("Open browser:")
    print("http://127.0.0.1:8000")
    print()

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000
    )