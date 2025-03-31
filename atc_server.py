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
import traceback

# Debugging setup
DEBUG = True

def debug_log(message):
    if DEBUG:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        print(f"[DEBUG {timestamp}] {message}")

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

class ATCCore:
    def __init__(self):
        debug_log("Initializing ATCCore")
        self.aircrafts = {}
        self.runways = [
            {"id": 1, "length": 3000, "status": RunwayStatus.AVAILABLE.value, "current_operation": None, "current_aircraft": None},
            {"id": 2, "length": 2500, "status": RunwayStatus.AVAILABLE.value, "current_operation": None, "current_aircraft": None}
        ]
        self.landing_queue = []
        self.takeoff_queue = []
        self.logs = []
        self.websocket_connections = set()
        debug_log("ATCCore initialization complete")

    def _can_accommodate(self, aircraft_size, runway):
        if runway["status"] != RunwayStatus.AVAILABLE.value:
            return False
        if aircraft_size == AircraftSize.LARGE.value and runway["length"] < 2500:
            return False
        if aircraft_size == AircraftSize.MEDIUM.value and runway["length"] < 1500:
            return False
        return True

    def _assign_runway(self, aircraft):
        for runway in sorted(self.runways, key=lambda x: x["length"], reverse=True):
            if self._can_accommodate(aircraft["size"], runway):
                runway["status"] = RunwayStatus.OCCUPIED.value
                runway["current_operation"] = aircraft["requested_operation"]
                runway["current_aircraft"] = aircraft["flight_number"]
                return runway["id"]
        return 0

    def _add_log(self, message):
        self.logs.append(f"{datetime.now().isoformat()} - {message}")
        if len(self.logs) > 100:
            self.logs.pop(0)

    async def broadcast_status(self):
        status = self.get_system_status()
        for ws in self.websocket_connections:
            try:
                await ws.send(json.dumps(status))
            except:
                self.websocket_connections.remove(ws)

    def get_system_status(self):
        return {
            "aircrafts": list(self.aircrafts.values()),
            "runways": self.runways,
            "landing_queue": self.landing_queue,
            "takeoff_queue": self.takeoff_queue,
            "logs": self.logs[-10:]
        }

class ATCServicer(atc_pb2_grpc.ATCServiceServicer):
    def __init__(self, atc_core):
        debug_log("Initializing ATCServicer")
        self.atc = atc_core


    def RequestLanding(self, request, context):
        aircraft = {
            "flight_number": request.flight_number,
            "size": request.size,
            "status": AircraftStatus.ENROUTE.value,
            "requested_operation": "landing",
            "runway_assigned": 0,
            "delay": 0,
            "timestamp": datetime.now().isoformat()
        }
        self.atc.aircrafts[request.flight_number] = aircraft
        
        assigned_runway = self.atc._assign_runway(aircraft)
        
        if assigned_runway:
            aircraft["status"] = AircraftStatus.LANDING.value
            aircraft["runway_assigned"] = assigned_runway
            instruction = f"cleared_for_landing runway_{assigned_runway}"
            self.atc._add_log(f"{request.flight_number} cleared for landing on runway {assigned_runway}")
        else:
            self.atc.landing_queue.append(request.flight_number)
            instruction = f"hold position_{len(self.atc.landing_queue)}"
            aircraft["status"] = AircraftStatus.HOLDING.value
            aircraft["delay"] = len(self.atc.landing_queue) * 2
            self.atc._add_log(f"{request.flight_number} holding for landing, position {len(self.atc.landing_queue)}")
        
        asyncio.create_task(self.atc.broadcast_status())
        
        return atc_pb2.ATCResponse(
            flight_number=request.flight_number,
            instruction=instruction,
            assigned_runway=assigned_runway,
            estimated_wait_time=len(self.atc.landing_queue) * 2
        )

    def GetSystemStatus(self, request, context):
        status = self.atc.get_system_status()
        return atc_pb2.SystemStatus(
            aircrafts=[atc_pb2.Aircraft(**ac) for ac in status["aircrafts"]],
            runways=[atc_pb2.Runway(**rw) for rw in status["runways"]],
            landing_queue=status["landing_queue"],
            takeoff_queue=status["takeoff_queue"],
            logs=status["logs"]
        )

async def websocket_server(atc_core):
    async with websockets.serve(
        lambda ws, path: websocket_handler(ws, path, atc_core),
        "localhost", 6789
    ):
        await asyncio.Future()  # run forever

async def websocket_handler(websocket, path):
    """Handle WebSocket connections"""
    debug_log(f"New WebSocket connection from {websocket.remote_address}, path: {path}")
    debug_log(f"Current atc_core: {atc_core is not None}")
    
    try:
        debug_log("Adding connection to websocket_connections")
        atc_core.websocket_connections.add(websocket)
        debug_log(f"Total connections: {len(atc_core.websocket_connections)}")
        
        # Send initial status
        debug_log("Getting initial system status")
        status = atc_core.get_system_status()
        debug_log(f"Sending initial status: {json.dumps(status)[:100]}...")
        await websocket.send(json.dumps(status))
        
        debug_log("Entering message loop")
        while True:
            try:
                message = await websocket.recv()
                debug_log(f"Received message: {message}")
            except websockets.exceptions.ConnectionClosed:
                debug_log("Connection closed by client")
                break
            except Exception as e:
                debug_log(f"Error in message handling: {str(e)}")
                debug_log(traceback.format_exc())
                break
                
    except Exception as e:
        debug_log(f"Error in WebSocket handler: {str(e)}")
        debug_log(traceback.format_exc())
    finally:
        debug_log("Cleaning up connection")
        try:
            atc_core.websocket_connections.remove(websocket)
            debug_log(f"Removed connection. Total connections: {len(atc_core.websocket_connections)}")
        except:
            debug_log("Error removing connection")
            debug_log(traceback.format_exc())

async def serve():
    global atc_core
    debug_log("Starting serve()")
    
    debug_log("Initializing ATCCore")
    atc_core = ATCCore()
    
    # Start gRPC server
    debug_log("Starting gRPC server")
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    atc_pb2_grpc.add_ATCServiceServicer_to_server(ATCServicer(atc_core), server)
    server.add_insecure_port('[::]:50051')
    await server.start()
    debug_log("gRPC server started on port 50051")
    
    # Start WebSocket server
    debug_log("Starting WebSocket server")
    try:
        ws_server = await websockets.serve(
            websocket_handler,
            "localhost",
            6789
        )
        debug_log(f"WebSocket server started: {ws_server}")
    except Exception as e:
        debug_log(f"Failed to start WebSocket server: {str(e)}")
        debug_log(traceback.format_exc())
        raise
    
    # Open dashboard in browser
    debug_log("Preparing to open dashboard")
    try:
        dashboard_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard.html')
        debug_log(f"Dashboard path: {dashboard_path}")
        webbrowser.open(f'file://{dashboard_path}')
        debug_log("Browser opened")
    except Exception as e:
        debug_log(f"Error opening browser: {str(e)}")
        debug_log(traceback.format_exc())
    
    print("Servers started. gRPC on port 50051, WebSocket on port 6789")
    print(f"Dashboard opened at: file://{dashboard_path}")
    
    try:
        debug_log("Entering server wait loop")
        await server.wait_for_termination()
    except asyncio.CancelledError:
        debug_log("Server termination requested")
    except Exception as e:
        debug_log(f"Server error: {str(e)}")
        debug_log(traceback.format_exc())
    finally:
        debug_log("Shutting down servers")
        await server.stop(5)
        ws_server.close()
        await ws_server.wait_closed()
        debug_log("Servers stopped")

if __name__ == "__main__":
    debug_log("Script starting")
    atc_core = None
    try:
        asyncio.run(serve())
    except Exception as e:
        debug_log(f"Fatal error: {str(e)}")
        debug_log(traceback.format_exc())
        raise