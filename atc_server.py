import asyncio
import websockets
import json
import grpc
from concurrent import futures
from datetime import datetime
from enum import Enum
import atc_pb2
import atc_pb2_grpc
import webbrowser
import os
import random
import time

class AircraftSize(Enum):
    SMALL = 1
    MEDIUM = 2
    LARGE = 3

class AircraftStatus(Enum):
    ENROUTE = "ENROUTE"
    HOLDING = "HOLDING"
    LANDING = "LANDING"
    LANDED = "LANDED"
    TAKEOFF = "TAKEOFF"
    DEPARTED = "DEPARTED"
    GO_AROUND = "GO_AROUND"
    DELAYED = "DELAYED"

class RunwayStatus(Enum):
    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    MAINTENANCE = "MAINTENANCE"

DEBUG = True

def debug_log(message):
    if DEBUG:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        print(f"[DEBUG {timestamp}] {message}")

class ATCCore:
    def __init__(self):
        self.aircrafts = {}
        self.runways = [
            {"id": 1, "length": 3000, "status": RunwayStatus.AVAILABLE.value, "current_operation": None, "current_aircraft": None},
            {"id": 2, "length": 2500, "status": RunwayStatus.AVAILABLE.value, "current_operation": None, "current_aircraft": None}
        ]
        self.landing_queue = []
        self.takeoff_queue = []
        self.logs = []
        self.connections = set()
        self.runway_operations = {}  # Initialize this before it's used
        self._init_dummy_data()  # Now this can safely use runway_operations
        
        # Start background tasks
        asyncio.create_task(self._generate_test_flights())
        asyncio.create_task(self._runway_monitor())

    async def _generate_test_flights(self):
        """Auto-generate test flights"""
        airlines = ['AC', 'WS', 'DL', 'AA', 'UA', 'BA', 'AF']
        while True:
            await asyncio.sleep(10)  # Every 10 seconds
            flight_num = f"{random.choice(airlines)}{random.randint(100, 999)}"
            size = random.randint(1, 3)
            self.process_landing_request(flight_num, size)
            self._add_log(f"Test flight {flight_num} entered airspace")

    async def _runway_monitor(self):
        """Monitor runway operations and complete them after delay"""
        while True:
            await asyncio.sleep(1)
            now = time.time()
            completed = []
            
            for runway_id, operation in list(self.runway_operations.items()):
                if operation["end_time"] <= now:
                    runway = next(r for r in self.runways if r["id"] == runway_id)
                    aircraft_id = operation["aircraft"]
                    
                    if aircraft_id in self.aircrafts:
                        if operation["type"] == "landing":
                            self.aircrafts[aircraft_id]["status"] = AircraftStatus.LANDED.value
                            self._add_log(f"{aircraft_id} has landed on runway {runway_id}")
                        else:  # takeoff
                            self.aircrafts[aircraft_id]["status"] = AircraftStatus.DEPARTED.value
                            self._add_log(f"{aircraft_id} has departed from runway {runway_id}")
                            # Remove departed aircraft
                            del self.aircrafts[aircraft_id]
                    
                    # Free the runway
                    runway["status"] = RunwayStatus.AVAILABLE.value
                    runway["current_operation"] = None
                    runway["current_aircraft"] = None
                    completed.append(runway_id)
            
            # Remove completed operations
            for runway_id in completed:
                del self.runway_operations[runway_id]
            
            # Process queues if any runways freed
            if completed:
                self._process_queues()

    def _process_queues(self):
        """Process next aircraft in queues"""
        # Process landing queue first (priority)
        for runway in sorted(self.runways, key=lambda x: x["length"], reverse=True):
            if runway["status"] == RunwayStatus.AVAILABLE.value and self.landing_queue:
                next_flight = self.landing_queue.pop(0)
                if next_flight in self.aircrafts:
                    self._assign_landing(next_flight, runway["id"])
                    return  # Only assign one at a time
        
        # Then process takeoff queue
        for runway in self.runways:
            if runway["status"] == RunwayStatus.AVAILABLE.value and self.takeoff_queue:
                next_flight = self.takeoff_queue.pop(0)
                if next_flight in self.aircrafts:
                    self._assign_takeoff(next_flight, runway["id"])
                    return  # Only assign one at a time

    def _assign_landing(self, flight_number, runway_id):
        """Assign landing to available runway"""
        if flight_number not in self.aircrafts:
            return
        
        aircraft = self.aircrafts[flight_number]
        runway = next(r for r in self.runways if r["id"] == runway_id)
        
        aircraft["status"] = AircraftStatus.LANDING.value
        aircraft["runway_assigned"] = runway_id
        
        runway["status"] = RunwayStatus.OCCUPIED.value
        runway["current_operation"] = "landing"
        runway["current_aircraft"] = flight_number
        
        # Set operation end time (landing takes 10-15 seconds)
        operation_time = random.uniform(10, 15)
        self.runway_operations[runway_id] = {
            "type": "landing",
            "aircraft": flight_number,
            "end_time": time.time() + operation_time
        }
        
        self._add_log(f"{flight_number} cleared to land on runway {runway_id}")

    def _assign_takeoff(self, flight_number, runway_id):
        """Assign takeoff to available runway"""
        if flight_number not in self.aircrafts:
            return
        
        aircraft = self.aircrafts[flight_number]
        runway = next(r for r in self.runways if r["id"] == runway_id)
        
        aircraft["status"] = AircraftStatus.TAKEOFF.value
        aircraft["runway_assigned"] = runway_id
        
        runway["status"] = RunwayStatus.OCCUPIED.value
        runway["current_operation"] = "takeoff"
        runway["current_aircraft"] = flight_number
        
        # Set operation end time (takeoff takes 8-12 seconds)
        operation_time = random.uniform(8, 12)
        self.runway_operations[runway_id] = {
            "type": "takeoff",
            "aircraft": flight_number,
            "end_time": time.time() + operation_time
        }
        
        self._add_log(f"{flight_number} cleared for takeoff on runway {runway_id}")

    def _init_dummy_data(self):
        """Initialize with 5 random aircraft"""
        airlines = ['AC', 'WS', 'DL', 'AA', 'UA', 'BA', 'AF']
        for i in range(1, 6):
            flight_num = f"{random.choice(airlines)}{random.randint(100, 999)}"
            size = random.randint(1, 3)
            self.aircrafts[flight_num] = {
                "flight_number": flight_num,
                "size": size,
                "status": AircraftStatus.ENROUTE.value,
                "requested_operation": "landing",
                "runway_assigned": 0,
                "delay": 0,
                "timestamp": datetime.now().isoformat()
            }
            self.process_landing_request(flight_num, size)
            self._add_log(f"Dummy aircraft {flight_num} entered airspace")

    def process_landing_request(self, flight_number, size):
        """Process a new landing request"""
        if flight_number not in self.aircrafts:
            self.aircrafts[flight_number] = {
                "flight_number": flight_number,
                "size": size,
                "status": AircraftStatus.ENROUTE.value,
                "requested_operation": "landing",
                "runway_assigned": 0,
                "delay": 0,
                "timestamp": datetime.now().isoformat()
            }
        
        assigned_runway = self._assign_runway(flight_number)
        
        if assigned_runway:
            self._assign_landing(flight_number, assigned_runway)
            instruction = f"cleared_for_landing runway_{assigned_runway}"
        else:
            self.landing_queue.append(flight_number)
            position = len(self.landing_queue)
            instruction = f"hold position_{position}"
            self.aircrafts[flight_number]["status"] = AircraftStatus.HOLDING.value
            self.aircrafts[flight_number]["delay"] = position * 5
        
        return instruction, assigned_runway

    def _assign_runway(self, flight_number):
        """Find suitable runway for aircraft"""
        if flight_number not in self.aircrafts:
            return 0
        
        aircraft = self.aircrafts[flight_number]
        
        for runway in sorted(self.runways, key=lambda x: x["length"], reverse=True):
            if (runway["status"] == RunwayStatus.AVAILABLE.value and 
                self._can_accommodate(aircraft["size"], runway)):
                return runway["id"]
        return 0

    def _can_accommodate(self, aircraft_size, runway):
        """Check if runway can accommodate aircraft size"""
        if runway["status"] != RunwayStatus.AVAILABLE.value:
            return False
        if aircraft_size == AircraftSize.LARGE.value and runway["length"] < 2500:
            return False
        if aircraft_size == AircraftSize.MEDIUM.value and runway["length"] < 1500:
            return False
        return True

    def _add_log(self, message):
        """Add message to log"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.logs.append(f"{timestamp} - {message}")
        if len(self.logs) > 100:  # Keep log size manageable
            self.logs.pop(0)

    def get_system_status(self):
        """Get current system status for clients"""
        return {
            "aircrafts": list(self.aircrafts.values()),
            "runways": self.runways,
            "landing_queue": [f for f in self.landing_queue 
                              if f in self.aircrafts and 
                              self.aircrafts[f]["status"] == AircraftStatus.HOLDING.value],
            "takeoff_queue": [f for f in self.takeoff_queue 
                            if f in self.aircrafts],
            "logs": self.logs[-20:]
        }

class ATCServicer(atc_pb2_grpc.ATCServiceServicer):
    def __init__(self, atc_core):
        self.atc = atc_core

    def RequestLanding(self, request, context):
        instruction, assigned_runway = self.atc.process_landing_request(
            request.flight_number,
            request.size
        )
        return atc_pb2.ATCResponse(
            flight_number=request.flight_number,
            instruction=instruction,
            assigned_runway=assigned_runway,
            estimated_wait_time=len(self.atc.landing_queue) * 2
        )

async def handle_client(websocket):
    """Simplified WebSocket handler without path parameter"""
    atc_core.connections.add(websocket)
    try:
        # Send initial state
        await websocket.send(json.dumps(atc_core.get_system_status()))
        
        # Keep connection alive
        while True:
            try:
                # Wait for any message (or timeout) to keep connection alive
                await asyncio.wait_for(websocket.recv(), timeout=15)
            except asyncio.TimeoutError:
                # Just continue to keep connection alive
                continue
            except websockets.exceptions.ConnectionClosed:
                break
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        atc_core.connections.remove(websocket)
        await websocket.close()
async def broadcast_updates():
    """Periodically send updates to all connected clients"""
    while True:
        if atc_core.connections:
            status = atc_core.get_system_status()
            disconnected = set()
            
            for ws in atc_core.connections:
                try:
                    await ws.send(json.dumps(status))
                except:
                    disconnected.add(ws)
            
            # Remove any disconnected clients
            for ws in disconnected:
                try:
                    atc_core.connections.remove(ws)
                except:
                    pass
        
        await asyncio.sleep(1)  # Update every second

async def serve():
    global atc_core
    atc_core = ATCCore()
    
    # Start gRPC server
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    atc_pb2_grpc.add_ATCServiceServicer_to_server(ATCServicer(atc_core), server)
    server.add_insecure_port('[::]:50051')
    await server.start()
    
    # Start WebSocket server with lambda to ignore path
    ws_server = await websockets.serve(
        lambda ws, path: handle_client(ws),  # Lambda to discard path parameter
        "localhost",
        6789
    )
    
    # Start broadcaster
    asyncio.create_task(broadcast_updates())
    
    # Open dashboard
    try:
        dashboard_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard.html')
        webbrowser.open(f'file://{dashboard_path}')
    except Exception as e:
        print(f"Could not open dashboard: {e}")
    
    print("Servers started. gRPC on port 50051, WebSocket on port 6789")
    
    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        print("Shutting down servers...")
        await ws_server.wait_closed()
        await server.stop(0)

if __name__ == "__main__":
    asyncio.run(serve())