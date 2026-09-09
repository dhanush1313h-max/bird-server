import os
import uuid
import shutil
from datetime import datetime, timedelta

from fastapi import FastAPI, File, UploadFile, Form
from pydantic import BaseModel
from google.cloud import firestore
from birdnetlib import Recording
from birdnetlib.analyzer import Analyzer

# ==========================================
# --- APP & DATABASE SETUP ---
# ==========================================

# Use the Docker service name 'firestore' instead of 127.0.0.1
os.environ["FIRESTORE_EMULATOR_HOST"] = "firestore:8080"
os.environ["GOOGLE_CLOUD_PROJECT"] = "bird-project-local"

app = FastAPI()

# Connect to the database
db = firestore.Client()

# Load the AI model
analyzer = Analyzer()

# Create an 'uploads' folder to act as our fake Cloud Storage
os.makedirs("uploads", exist_ok=True)


# ==========================================
# --- DATA MODELS ---
# ==========================================

class GoogleLoginRequest(BaseModel):
    user_id: str
    email: str
    name: str

class TargetCommit(BaseModel):
    user_id: str
    target_name: str

class RecordingSubmit(BaseModel):
    user_id: str
    target_name: str
    timestamp: str
    audio_file_path: str


# ==========================================
# --- ENDPOINTS ---
# ==========================================

@app.get("/")
def read_root():
    return {"message": "Hello, Server is running!"}


# --- 1. AUTHENTICATION (LOGIN) ---

@app.post("/login")
def login_with_google(auth_data: GoogleLoginRequest):
    # 1. Generate a session token
    session_id = str(uuid.uuid4())
    expiry = datetime.now() + timedelta(days=7) # 7-day inactivity window
    
    # 2. Store user profile and session details in Firestore
    user_ref = db.collection("users").document(auth_data.user_id)
    user_ref.set({
        "user_id": auth_data.user_id,
        "email": auth_data.email,
        "name": auth_data.name,
        "last_login": datetime.now().isoformat()
    }, merge=True)
    
    session_ref = db.collection("sessions").document(session_id)
    session_ref.set({
        "session_id": session_id,
        "user_id": auth_data.user_id,
        "expires_at": expiry.isoformat(),
        "status": "active"
    })
    
    return {
        "status": "success",
        "message": "Authentication successful",
        "session_id": session_id,
        "expires_at": expiry.isoformat()
    }


# --- 2. SYSTEM SETUP & REPORTING ---

@app.get("/setup")
def setup_database():
    # This is a temporary tool to fill our empty database!
    parks = ["Cubbon Park", "Lalbagh Botanical Garden", "Ulsoor Lake"]
    
    for park in parks:
        db.collection("targets").document(park).set({
            "name": park,
            "status": "open",
            "committed_by": None
        })
        
    return {"message": "Database loaded with initial parks!"}

@app.get("/reports/{report_type}")
def generate_server_report(report_type: str):
    if report_type == "users":
        users_docs = db.collection("users").stream()
        return {"status": "success", "report": "users", "data": [doc.to_dict() for doc in users_docs]}
        
    elif report_type == "recordings":
        rec_docs = db.collection("recordings").stream()
        return {"status": "success", "report": "recordings", "data": [doc.to_dict() for doc in rec_docs]}
        
    elif report_type == "targets":
        target_docs = db.collection("targets").stream()
        return {"status": "success", "report": "targets", "data": [doc.to_dict() for doc in target_docs]}
        
    else:
        return {"status": "fail", "message": "Invalid report type. Choose: 'users', 'recordings', 'targets'."}


# --- 3. TARGET MANAGEMENT ---

@app.get("/targets")
def get_open_targets():
    open_docs = db.collection("targets").where("status", "==", "open").stream()
    open_list = [doc.to_dict()["name"] for doc in open_docs]
    return {
        "status": "success",
        "open_target_list": open_list
    }

@app.post("/commit")
def commit_to_target(commitment: TargetCommit):
    doc_ref = db.collection("targets").document(commitment.target_name)
    doc = doc_ref.get()
    
    if doc.exists and doc.to_dict().get("status") == "open":
        doc_ref.update({
            "status": "committed",
            "committed_by": commitment.user_id
        })
        return {"status": "success", "message": f"{commitment.target_name} successfully reserved!"}
    else:
        return {"status": "fail", "message": "Sorry, that target is no longer available."}

@app.get("/check-lapsed")
def check_lapsed_commitments():
    now = datetime.now()
    committed_docs = db.collection("targets").where("status", "==", "committed").stream()
    
    lapsed_count = 0
    for doc in committed_docs:
        data = doc.to_dict()
        target_name = data.get("name")
        target_date_str = data.get("date") 
        
        if target_date_str:
            target_date = datetime.strptime(target_date_str, "%Y-%m-%d")
            if now > target_date:
                next_date = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")
                db.collection("targets").document(target_name).set({
                    "name": target_name,
                    "status": "open",
                    "committed_by": None,
                    "date": next_date
                })
                lapsed_count += 1

    return {"status": "success", "message": f"Rolled over {lapsed_count} expired targets."}


# --- 4. RECORDINGS & AI ANALYSIS ---

@app.post("/submit")
def submit_recording(
    target_name: str = Form(...), 
    user_id: str = Form(...), 
    audio_file: UploadFile = File(...)
):
    doc_ref = db.collection("targets").document(target_name)
    doc = doc_ref.get()
    
    if doc.exists and doc.to_dict().get("status") == "committed" and doc.to_dict().get("committed_by") == user_id:
        # Save the audio file locally
        file_location = f"uploads/{audio_file.filename}"
        with open(file_location, "wb") as buffer:
            shutil.copyfileobj(audio_file.file, buffer)
            
        doc_ref.update({"status": "completed"})
        
        # --- THE REAL BIRDNET AI ---
        print(f"Analyzing {audio_file.filename} with BirdNET...")
        recording = Recording(
            analyzer,
            file_location,
            lat=12.9716, # Bengaluru coordinates
            lon=77.5946,
            date=datetime.now(),
            min_conf=0.25
        )
        recording.analyze()
        identified_birds = [detection["common_name"] for detection in recording.detections]
        
        # Store in master recordings collection for history
        db.collection("recordings").add({
            "user_id": user_id,
            "type": "targeted",
            "target_name": target_name,
            "filename": audio_file.filename,
            "timestamp": datetime.now().isoformat(),
            "species_found": identified_birds
        })
        
        return {
            "status": "success",
            "message": f"Audio file '{audio_file.filename}' analyzed successfully!",
            "species_found": identified_birds
        }
    else:
        return {"status": "fail", "message": "Error: You must commit to this target before submitting."}

@app.post("/exploratory-submit")
def submit_exploratory_recording(
    user_id: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    audio_file: UploadFile = File(...)
):
    file_location = f"uploads/{audio_file.filename}"
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(audio_file.file, buffer)
        
    print(f"Analyzing exploratory recording {audio_file.filename} at ({latitude}, {longitude})...")
    recording = Recording(
        analyzer,
        file_location,
        lat=latitude,
        lon=longitude,
        date=datetime.now(),
        min_conf=0.25
    )
    recording.analyze()
    identified_birds = [detection["common_name"] for detection in recording.detections]
    
    # Store metadata and findings in Firestore
    db.collection("recordings").add({
        "user_id": user_id,
        "type": "exploratory",
        "latitude": latitude,
        "longitude": longitude,
        "filename": audio_file.filename,
        "timestamp": datetime.now().isoformat(),
        "species_found": identified_birds
    })
    
    return {
        "status": "success",
        "message": f"Exploratory recording '{audio_file.filename}' analyzed successfully!",
        "species_found": identified_birds
    }

@app.get("/history/{user_id}")
def get_user_recording_history(user_id: str):
    try:
        docs = (
            db.collection("recordings")
            .where("user_id", "==", user_id)
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(20)
            .stream()
        )
        history_list = [doc.to_dict() for doc in docs]
        return {"status": "success", "count": len(history_list), "recordings": history_list}
    except Exception as e:
        docs = db.collection("recordings").where("user_id", "==", user_id).stream()
        history_list = [doc.to_dict() for doc in docs]
        return {"status": "success", "count": len(history_list), "recordings": history_list}


# --- 5. AUTHENTICATION (LOGOUT) ---

@app.post("/logout")
def logout(session_id: str = Form(...)):
    session_ref = db.collection("sessions").document(session_id)
    doc = session_ref.get()
    
    if doc.exists:
        session_ref.update({"status": "terminated"})
        return {"status": "success", "message": "Successfully logged out and session terminated."}
    return {"status": "fail", "message": "Invalid session ID."}