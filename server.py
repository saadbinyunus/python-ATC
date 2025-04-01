import asyncio
import json
import logging
from concurrent import futures
import grpc
import websockets
from collections import deque
from datetime import datetime, timedelta
import random

# Mock database for our ATC system
class ATCDatabase:
    def __init__(self):
        self.aircrafts = {}
        self.runways = {
            1: {"id": 1, "length": 3000, "status": "AVAILABLE", "current_operation": None, "current_aircraft": None},
            2: {"id": 2, "length": 2500, "status": "AVAILABLE", "current_operation": None, "current_aircraft": None},
            3: {"id": 3, "length": 2000, "status": "AVAILABLE", "current_operation": None, "current_aircraft": None}
        }
        self.landing_queue = deque()
        self.takeoff_queue = deque()
        self.logs = deque(maxlen=100)
        self.connected_clients = set()
        
    def add_aircraft(self, flight_number, size):
        self.aircrafts[flight_number] = {
            "flight_number": flight_number,
            "size": size,
            "status": "ENROUTE",
            "requested_operation": None,
            "runway_assigned": None,
            "delay": 0,
            "timestamp": datetime.now().isoformat()
        }
        self._log(f"Aircraft {flight_number} (Size: {size}) created")
        
    def request_landing(self, flight_number, aircraft_type, size):
        if flight_number not in self.aircrafts:
            self.add_aircraft(flight_number, size)
            
        self.aircrafts[flight_number].update({
            "status": "HOLDING",
            "requested_operation": "LANDING"
        })
        
        # Add to landing queue if not already there
        if flight_number not in self.landing_queue:
            self.landing_queue.append(flight_number)
            self._log(f"Aircraft {flight_number} requested landing, added to queue")
        
        return self._process_request(flight_number, "LANDING")
    
    def request_takeoff(self, flight_number, aircraft_type, size):
        if flight_number not in self.aircrafts:
            self.add_aircraft(flight_number, size)
            
        self.aircrafts[flight_number].update({
            "status": "HOLDING",
            "requested_operation": "TAKEOFF"
        })
        
        # Add to takeoff queue if not already there
        if flight_number not in self.takeoff_queue:
            self.takeoff_queue.append(flight_number)
            self._log(f"Aircraft {flight_number} requested takeoff, added to queue")
        
        return self._process_request(flight_number, "TAKEOFF")
    
    def update_status(self, flight_number, status):
        if flight_number not in self.aircrafts:
            return False
            
        old_status = self.aircrafts[flight_number]["status"]
        self.aircrafts[flight_number]["status"] = status
        self._log(f"Aircraft {flight_number} status changed from {old_status} to {status}")
        
        # If aircraft has landed or departed, free up the runway
        if status in ["LANDED", "DEPARTED"]:
            runway_id = self.aircrafts[flight_number]["runway_assigned"]
            if runway_id:
                self.runways[runway_id].update({
                    "status": "AVAILABLE",
                    "current_operation": None,
                    "current_aircraft": None
                })
                self.aircrafts[flight_number]["runway_assigned"] = None
                self._log(f"Runway {runway_id} freed by {flight_number}")
        
        return True
    
    def get_system_status(self):
        return {
            "aircrafts": list(self.aircrafts.values()),
            "runways": list(self.runways.values()),
            "landing_queue": list(self.landing_queue),
            "takeoff_queue": list(self.takeoff_queue),
            "logs": list(self.logs)
        }
    
    def _process_request(self, flight_number, operation_type):
        # Find suitable runway
        runway_id = self._find_available_runway(operation_type, self.aircrafts[flight_number]["size"])
        if runway_id:
            # Assign runway
            self.runways[runway_id].update({
                "status": "OCCUPIED",
                "current_operation": operation_type,
                "current_aircraft": flight_number
            })
            
            # Update aircraft
            self.aircrafts[flight_number].update({
                "runway_assigned": runway_id,
                "status": "LANDING" if operation_type == "LANDING" else "TAKEOFF"
            })
            
            # Remove from queue
            if operation_type == "LANDING" and flight_number in self.landing_queue:
                self.landing_queue.remove(flight_number)
            elif operation_type == "TAKEOFF" and flight_number in self.takeoff_queue:
                self.takeoff_queue.remove(flight_number)
            
            self._log(f"Aircraft {flight_number} assigned to runway {runway_id} for {operation_type}")
            
            return {
                "flight_number": flight_number,
                "instruction": f"cleared_for_{operation_type.lower()}",
                "assigned_runway": runway_id,
                "estimated_wait_time": 0
            }
        else:
            # No runway available, calculate estimated wait time
            wait_time = random.randint(30, 120)
            self.aircrafts[flight_number]["delay"] = wait_time
            self._log(f"No runway available for {flight_number}, estimated wait: {wait_time} sec")
            
            return {
                "flight_number": flight_number,
                "instruction": "hold",
                "assigned_runway": None,
                "estimated_wait_time": wait_time
            }
    
    def _find_available_runway(self, operation_type, size):
        # Simple runway assignment logic
        for runway_id, runway in self.runways.items():
            if runway["status"] == "AVAILABLE":
                # Check if runway length is sufficient for the aircraft size
                if (size == 1 and runway["length"] >= 1500) or \
                   (size == 2 and runway["length"] >= 2000) or \
                   (size == 3 and runway["length"] >= 2500):
                    return runway_id
        return None
    
    def _log(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        self.logs.append(log_entry)
        print(log_entry)
        
        # Notify all connected WebSocket clients
        asyncio.create_task(self._notify_clients())
    
    async def _notify_clients(self):
        if self.connected_clients:
            message = json.dumps({
                "type": "status_update",
                "data": self.get_system_status()
            })
            await asyncio.gather(
                *[client.send(message) for client in self.connected_clients],
                return_exceptions=True
            )

# gRPC Service Implementation
class ATCServiceServicer:
    def __init__(self, database):
        self.db = database
    
    def RequestLanding(self, request, context):
        response = self.db.request_landing(
            request.flight_number,
            request.aircraft_type,
            request.size
        )
        return ATCResponse(
            flight_number=response["flight_number"],
            instruction=response["instruction"],
            assigned_runway=response["assigned_runway"] or 0,
            estimated_wait_time=response["estimated_wait_time"]
        )
    
    def RequestTakeoff(self, request, context):
        response = self.db.request_takeoff(
            request.flight_number,
            request.aircraft_type,
            request.size
        )
        return ATCResponse(
            flight_number=response["flight_number"],
            instruction=response["instruction"],
            assigned_runway=response["assigned_runway"] or 0,
            estimated_wait_time=response["estimated_wait_time"]
        )
    
    def CreateAircraft(self, request, context):
        self.db.add_aircraft(request.flight_number, request.size)
        return StatusUpdate(
            flight_number=request.flight_number,
            status="ENROUTE"
        )
    
    def UpdateStatus(self, request, context):
        success = self.db.update_status(request.flight_number, request.status)
        return ATCResponse(
            flight_number=request.flight_number,
            instruction="status_updated" if success else "update_failed",
            assigned_runway=0,
            estimated_wait_time=0
        )
    
    def GetSystemStatus(self, request, context):
        status = self.db.get_system_status()
        return SystemStatus(
            aircrafts=[Aircraft(**ac) for ac in status["aircrafts"]],
            runways=[Runway(**rw) for rw in status["runways"]],
            landing_queue=status["landing_queue"],
            takeoff_queue=status["takeoff_queue"],
            logs=status["logs"]
        )

# WebSocket Server
async def websocket_handler(websocket):
    db = app_state['database']
    db.connected_clients.add(websocket)
    try:
        # Send initial status
        await websocket.send(json.dumps({
            "type": "status_update",
            "data": db.get_system_status()
        }))
        
        # Keep connection open
        async for message in websocket:
            pass  # We don't expect messages from clients
            
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        db.connected_clients.remove(websocket)

async def start_servers():
    # Initialize database
    db = ATCDatabase()
    app_state['database'] = db
    
    # Add some initial aircraft for testing
    db.add_aircraft("AC123", 2)
    db.add_aircraft("WS456", 1)
    db.add_aircraft("DL789", 3)
    
    # Start gRPC server
    grpc_server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    add_ATCServiceServicer_to_server(ATCServiceServicer(db), grpc_server)
    grpc_server.add_insecure_port('[::]:50051')
    await grpc_server.start()
    print("gRPC server started on port 50051")
    
    # Start WebSocket server
    ws_server = await websockets.serve(
        websocket_handler,
        "localhost",
        6789,
        ping_interval=None
    )
    print("WebSocket server started on port 6789")
    
    # Keep servers running
    try:
        while True:
            await asyncio.sleep(3600)  # Sleep for 1 hour
    except asyncio.CancelledError:
        await grpc_server.stop(0)
        ws_server.close()
        await ws_server.wait_closed()

# Protobuf-generated imports (these would be from your compiled protobuf)
# For the sake of this example, we'll create mock classes
class LandingRequest:
    def __init__(self, flight_number="", aircraft_type="", size=0):
        self.flight_number = flight_number
        self.aircraft_type = aircraft_type
        self.size = size

class TakeoffRequest:
    def __init__(self, flight_number="", aircraft_type="", size=0):
        self.flight_number = flight_number
        self.aircraft_type = aircraft_type
        self.size = size

class StatusUpdate:
    def __init__(self, flight_number="", status=""):
        self.flight_number = flight_number
        self.status = status

class ATCResponse:
    def __init__(self, flight_number="", instruction="", assigned_runway=0, estimated_wait_time=0):
        self.flight_number = flight_number
        self.instruction = instruction
        self.assigned_runway = assigned_runway
        self.estimated_wait_time = estimated_wait_time

class StatusRequest:
    pass

class SystemStatus:
    def __init__(self, aircrafts=None, runways=None, landing_queue=None, takeoff_queue=None, logs=None):
        self.aircrafts = aircrafts or []
        self.runways = runways or []
        self.landing_queue = landing_queue or []
        self.takeoff_queue = takeoff_queue or []
        self.logs = logs or []

class Aircraft:
    def __init__(self, flight_number="", size=0, status="", requested_operation="", runway_assigned=0, delay=0, timestamp=""):
        self.flight_number = flight_number
        self.size = size
        self.status = status
        self.requested_operation = requested_operation
        self.runway_assigned = runway_assigned
        self.delay = delay
        self.timestamp = timestamp

class Runway:
    def __init__(self, id=0, length=0, status="", current_operation="", current_aircraft=""):
        self.id = id
        self.length = length
        self.status = status
        self.current_operation = current_operation
        self.current_aircraft = current_aircraft

def add_ATCServiceServicer_to_server(servicer, server):
    pass

# Application state
app_state = {'database': None}

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_servers())