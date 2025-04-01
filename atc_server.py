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

    def get_system_status(self):
        return {
            "aircrafts": list(self.aircrafts.values()),
            "runways": self.runways,
            "landing_queue": self.landing_queue,
            "takeoff_queue": self.takeoff_queue,
            "logs": self.logs[-20:]
        }

    def process_landing_request(self, flight_number, size):
        aircraft = {
            "flight_number": flight_number,
            "size": size,
            "status": AircraftStatus.ENROUTE.value,
            "requested_operation": "landing",
            "runway_assigned": 0,
            "delay": 0,
            "timestamp": datetime.now().isoformat()
        }
        self.aircrafts[flight_number] = aircraft
        
        assigned_runway = self._assign_runway(aircraft)
        
        if assigned_runway:
            aircraft["status"] = AircraftStatus.LANDING.value
            aircraft["runway_assigned"] = assigned_runway
            instruction = f"cleared_for_landing runway_{assigned_runway}"
            self._add_log(f"{flight_number} cleared for landing on runway {assigned_runway}")
        else:
            self.landing_queue.append(flight_number)
            instruction = f"hold position_{len(self.landing_queue)}"
            aircraft["status"] = AircraftStatus.HOLDING.value
            aircraft["delay"] = len(self.landing_queue) * 2
            self._add_log(f"{flight_number} holding for landing, position {len(self.landing_queue)}")
        
        return instruction, assigned_runway

    def _assign_runway(self, aircraft):
        for runway in sorted(self.runways, key=lambda x: x["length"], reverse=True):
            if self._can_accommodate(aircraft["size"], runway):
                runway["status"] = RunwayStatus.OCCUPIED.value
                runway["current_operation"] = aircraft["requested_operation"]
                runway["current_aircraft"] = aircraft["flight_number"]
                return runway["id"]
        return 0

    def _can_accommodate(self, aircraft_size, runway):
        if runway["status"] != RunwayStatus.AVAILABLE.value:
            return False
        if aircraft_size == AircraftSize.LARGE.value and runway["length"] < 2500:
            return False
        if aircraft_size == AircraftSize.MEDIUM.value and runway["length"] < 1500:
            return False
        return True

    def _add_log(self, message):
        self.logs.append(f"{datetime.now().isoformat()} - {message}")
        if len(self.logs) > 100:
            self.logs.pop(0)

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
    """Simplified handler that only takes websocket parameter"""
    atc_core.connections.add(websocket)
    try:
        # Send initial state
        await websocket.send(json.dumps(atc_core.get_system_status()))
        
        # Keep connection alive
        while True:
            await asyncio.sleep(1)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        atc_core.connections.remove(websocket)

async def broadcast_updates():
    """Periodically send updates like the Node.js broadcastData"""
    while True:
        status = atc_core.get_system_status()
        for ws in atc_core.connections:
            try:
                await ws.send(json.dumps(status))
            except:
                atc_core.connections.remove(ws)
        await asyncio.sleep(5)  # Update every 5 seconds like Node.js example

async def serve():
    global atc_core
    atc_core = ATCCore()
    
    # Start gRPC server
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    atc_pb2_grpc.add_ATCServiceServicer_to_server(ATCServicer(atc_core), server)
    server.add_insecure_port('[::]:50051')
    await server.start()
    
    ws_server = await websockets.serve(
        handle_client,  # Now using the single-parameter version
        "localhost",
        6789
    )
    
    # Start broadcaster
    asyncio.create_task(broadcast_updates())
    
    # Open dashboard
    dashboard_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard.html')
    webbrowser.open(f'file://{dashboard_path}')
    
    print("Servers started. gRPC on port 50051, WebSocket on port 6789")
    await server.wait_for_termination()

if __name__ == "__main__":
    asyncio.run(serve())