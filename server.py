from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from enum import Enum
from typing import Dict, List, Optional
from datetime import datetime
import asyncio
import uvicorn
import os

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
app.mount("/static", StaticFiles(directory="."), name="static")

# Data Models
class AircraftSize(str, Enum):
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"

class AircraftStatus(str, Enum):
    ENROUTE = "ENROUTE"
    HOLDING = "HOLDING"
    LANDING = "LANDING"
    LANDED = "LANDED"
    TAKEOFF = "TAKEOFF"
    DEPARTED = "DEPARTED"
    GO_AROUND = "GO_AROUND"
    DELAYED = "DELAYED"

class RunwayStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    MAINTENANCE = "MAINTENANCE"

class Aircraft(BaseModel):
    flight_number: str
    size: AircraftSize
    status: AircraftStatus
    requested_operation: str  # "landing" or "takeoff"
    timestamp: datetime
    runway_assigned: Optional[int] = None
    delay: int = 0  # in minutes

class Runway(BaseModel):
    id: int
    length: int  # in meters
    status: RunwayStatus
    current_operation: Optional[str] = None
    current_aircraft: Optional[str] = None

# Database simulation
aircraft_db: Dict[str, Aircraft] = {}
runways_db: Dict[int, Runway] = {
    1: Runway(id=1, length=3000, status=RunwayStatus.AVAILABLE),
    2: Runway(id=2, length=2500, status=RunwayStatus.AVAILABLE)
}
landing_queue: List[str] = []
takeoff_queue: List[str] = []
logs: List[str] = []

# WebSocket connections
websocket_connections = set()

# Helper functions
def can_accommodate(aircraft: Aircraft, runway: Runway) -> bool:
    """Check if runway can accommodate the aircraft"""
    if runway.status != RunwayStatus.AVAILABLE:
        return False
    if aircraft.size == AircraftSize.LARGE and runway.length < 2500:
        return False
    if aircraft.size == AircraftSize.MEDIUM and runway.length < 1500:
        return False
    return True

def assign_runway(aircraft: Aircraft) -> Optional[int]:
    """Try to assign an available runway"""
    for runway in sorted(runways_db.values(), key=lambda x: x.length, reverse=True):
        if can_accommodate(aircraft, runway):
            runway.status = RunwayStatus.OCCUPIED
            runway.current_operation = aircraft.requested_operation
            runway.current_aircraft = aircraft.flight_number
            return runway.id
    return None

def add_log(message: str):
    """Add message to logs with timestamp"""
    logs.append(f"{datetime.now().isoformat()} - {message}")
    if len(logs) > 100:  # Keep only last 100 logs
        logs.pop(0)

async def broadcast_status():
    """Broadcast current system status to all WebSocket clients"""
    status = {
        "aircrafts": {k: v.dict() for k, v in aircraft_db.items()},
        "runways": {k: v.dict() for k, v in runways_db.items()},
        "landing_queue": landing_queue,
        "takeoff_queue": takeoff_queue,
        "logs": logs[-10:]  # Return last 10 logs
    }
    for connection in websocket_connections:
        try:
            await connection.send_json(status)
        except:
            websocket_connections.remove(connection)

# API Endpoints
@app.get("/")
async def serve_dashboard():
    """Serve the dashboard HTML file"""
    file_path = os.path.join(os.getcwd(), "dashboard.html")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Dashboard file not found")
    return FileResponse(file_path)

@app.post("/request_landing")
async def request_landing(flight_number: str, aircraft_type: str, size: AircraftSize):
    """Handle landing request"""
    aircraft = Aircraft(
        flight_number=flight_number,
        size=size,
        status=AircraftStatus.ENROUTE,
        requested_operation="landing",
        timestamp=datetime.now()
    )
    aircraft_db[flight_number] = aircraft
    
    assigned_runway = assign_runway(aircraft)
    
    if assigned_runway:
        aircraft.status = AircraftStatus.LANDING
        instruction = f"cleared_for_landing runway_{assigned_runway}"
        add_log(f"{flight_number} cleared for landing on runway {assigned_runway}")
    else:
        landing_queue.append(flight_number)
        instruction = f"hold position_{len(landing_queue)}"
        aircraft.status = AircraftStatus.HOLDING
        add_log(f"{flight_number} holding for landing, position {len(landing_queue)}")
    
    await broadcast_status()
    return {
        "instruction": instruction,
        "assigned_runway": assigned_runway,
        "wait_time": len(landing_queue) * 2  # 2 minutes per aircraft
    }

@app.post("/request_takeoff")
async def request_takeoff(flight_number: str, aircraft_type: str, size: AircraftSize):
    """Handle takeoff request"""
    # Similar to landing request but for takeoffs
    pass

@app.get("/status")
async def get_status():
    """Get current system status"""
    return {
        "aircrafts": {k: v.dict() for k, v in aircraft_db.items()},
        "runways": {k: v.dict() for k, v in runways_db.items()},
        "landing_queue": landing_queue,
        "takeoff_queue": takeoff_queue,
        "logs": logs[-10:]
    }

# WebSocket Endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_connections.add(websocket)
    try:
        while True:
            # Just keep connection open, we'll push updates
            await websocket.receive_text()
    except:
        websocket_connections.remove(websocket)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)