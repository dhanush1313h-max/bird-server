import os
from fastapi import FastAPI, File, UploadFile, Form
from pydantic import BaseModel
from google.cloud import firestore
import shutil
from birdnetlib import Recording
from birdnetlib.analyzer import Analyzer
from datetime import datetime

# Load the AI model into memory
analyzer = Analyzer()
# ---------------------------

app = FastAPI()

# --- DATABASE SETUP ---
# Tell Python to use the local Docker emulator instead of the real cloud
os.environ["FIRESTORE_EMULATOR_HOST"] = "127.0.0.1:8080"
os.environ["GOOGLE_CLOUD_PROJECT"] = "bird-project-local"

app = FastAPI()

# Connect to the database!
db = firestore.Client()

# --- OUR FAKE DATABASE (We will delete this soon!) ---
open_targets = ["Cubbon Park", "Lalbagh Botanical Garden", "Ulsoor Lake"]
committed_targets = []

# --- DATA MODELS ---
class TargetCommit(BaseModel):
    user_id: str
    target_name: str

class RecordingSubmit(BaseModel):
    user_id: str
    target_name: str
    timestamp: str
    audio_file_path: str

# --- ENDPOINTS ---

@app.get("/")
def read_root():
    return {"message": "Hello, Server is running!"}

# --- ENDPOINTS ---

@app.get("/setup")
def setup_database():
    # This is a temporary tool to fill our empty database!
    parks = ["Cubbon Park", "Lalbagh Botanical Garden", "Ulsoor Lake"]
    
    for park in parks:
        # We put a document inside the 'targets' collection for each park
        db.collection("targets").document(park).set({
            "name": park,
            "status": "open",
            "committed_by": None
        })
        
    return {"message": "Database loaded with initial parks!"}


@app.get("/targets")
def get_open_targets():
    # 1. Ask the database for all targets where the status is 'open'
    open_docs = db.collection("targets").where("status", "==", "open").stream()
    
    # 2. Extract just the names to send to the Android app
    open_list = []
    for doc in open_docs:
        open_list.append(doc.to_dict()["name"])
        
    return {
        "status": "success",
        "open_target_list": open_list
    }

@app.post("/commit")
def commit_to_target(commitment: TargetCommit):
    # 1. Point to the specific park document in the database
    doc_ref = db.collection("targets").document(commitment.target_name)
    doc = doc_ref.get()
    
    # 2. Check if it exists and is currently 'open'
    if doc.exists and doc.to_dict().get("status") == "open":
        
        # 3. Update the database document!
        doc_ref.update({
            "status": "committed",
            "committed_by": commitment.user_id
        })
        
        return {
            "status": "success", 
            "message": f"{commitment.target_name} successfully reserved for {commitment.user_id}!"
        }
    else:
        return {
            "status": "fail", 
            "message": "Sorry, that target is no longer available."
        }

# Create an 'uploads' folder on your laptop to act as our fake Cloud Storage
os.makedirs("uploads", exist_ok=True)

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
            
        # Update database status to completed
        doc_ref.update({
            "status": "completed"
        })
        
        # --- THE REAL BIRDNET AI ---
        print(f"Analyzing {audio_file.filename} with BirdNET...")
        recording = Recording(
            analyzer,
            file_location,
            lat=12.9716, # Bengaluru coordinates
            lon=77.5946,
            date=datetime.now(),
            min_conf=0.25 # Only keep predictions > 25% confidence
        )
        recording.analyze()
        
        # Extract the results into a clean list
        identified_birds = [detection["common_name"] for detection in recording.detections]
        # ---------------------------
        
        return {
            "status": "success",
            "message": f"Audio file '{audio_file.filename}' analyzed successfully!",
            "species_found": identified_birds
        }
    else:
        return {
            "status": "fail",
            "message": "Error: You must commit to this target before submitting."
        }