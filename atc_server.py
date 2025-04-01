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
            {"id": 1, "length": 3000, "status": RunwayStatus.AVAILABLE.value},
            {"id": 2, "length": 2500, "status": RunwayStatus.AVAILABLE.value}
        ]
        self.landing_queue = []
        self.takeoff_queue = []
        self.logs = []
        self.connections = set()
        self._init_dummy_data()
        # Track runway operations
        self.runway_operations = {}  # {runway_id: {"end_time": float, "aircraft": str}}

    async def _complete_runway_operation(self, runway_id):
        """Mark runway as available after operation completes"""
        await asyncio.sleep(5)  # Operation takes 5 seconds
        self.runways[runway_id-1]["status"] = RunwayStatus.AVAILABLE.value
        self.runways[runway_id-1]["current_operation"] = None
        self.runways[runway_id-1]["current_aircraft"] = None
        self._process_queues()  # Check if any aircraft are waiting

    def _process_queues(self):
        """Process next in queue if runways are available"""
        # Process landing queue first
        for runway in self.runways:
            if runway["status"] == RunwayStatus.AVAILABLE.value and self.landing_queue:
                next_aircraft = self.landing_queue.pop(0)
                self._assign_landing(next_aircraft, runway["id"])
        
        # Then process takeoff queue
        for runway in self.runways:
            if runway["status"] == RunwayStatus.AVAILABLE.value and self.takeoff_queue:
                next_aircraft = self.takeoff_queue.pop(0)
                self._assign_takeoff(next_aircraft, runway["id"])
                
    def _assign_landing(self, flight_number, runway_id):
        """Assign landing to available runway"""
        aircraft = self.aircrafts[flight_number]  # Get aircraft dictionary
        aircraft["status"] = AircraftStatus.LANDING.value
        aircraft["runway_assigned"] = runway_id
        self.runways[runway_id-1]["status"] = RunwayStatus.OCCUPIED.value
        self.runways[runway_id-1]["current_operation"] = "landing"
        self.runways[runway_id-1]["current_aircraft"] = flight_number
        self._add_log(f"{flight_number} cleared to land on runway {runway_id}")
        asyncio.create_task(self._complete_runway_operation(runway_id))

    def _assign_takeoff(self, flight_number, runway_id):
        """Assign takeoff to available runway"""
        aircraft = self.aircrafts[flight_number]
        aircraft["status"] = AircraftStatus.TAKEOFF.value
        aircraft["runway_assigned"] = runway_id
        self.runways[runway_id-1]["status"] = RunwayStatus.OCCUPIED.value
        self.runways[runway_id-1]["current_operation"] = "takeoff"
        self.runways[runway_id-1]["current_aircraft"] = flight_number
        self._add_log(f"{flight_number} cleared for takeoff on runway {runway_id}")
        asyncio.create_task(self._complete_runway_operation(runway_id))

    def _init_dummy_data(self):
        """Initialize with 5 random aircraft"""
        airlines = ['AC', 'WS', 'DL', 'AA', 'UA', 'BA', 'AF']
        for i in range(1, 6):
            flight_num = f"{random.choice(airlines)}{random.randint(100, 999)}"
            size = random.randint(1, 3)
            # Create aircraft first
            self.aircrafts[flight_num] = {
                "flight_number": flight_num,
                "size": size,
                "status": AircraftStatus.ENROUTE.value,
                "requested_operation": "landing",
                "runway_assigned": 0,
                "delay": 0,
                "timestamp": datetime.now().isoformat()
            }
            # Then process landing request
            self.process_landing_request(flight_num, size)
            self._add_log(f"Dummy aircraft {flight_num} entered airspace")

    def generate_dummy_activity(self):
        """Randomly generate aircraft movements"""
        actions = [
            self._generate_arrival,
            self._generate_departure,
            self._generate_status_change,
            lambda: None  # Do nothing sometimes
        ]
        random.choice(actions)()

    def _generate_arrival(self):
        if random.random() < 0.3:  # 30% chance
            airlines = ['AC', 'WS', 'DL', 'AA']
            flight_num = f"{random.choice(airlines)}{random.randint(100, 999)}"
            self.add_aircraft(flight_num, random.randint(1, 3))
            self._add_log(f"New arrival: {flight_num} requesting landing")

    def _generate_departure(self):
        if self.aircrafts and random.random() < 0.2:  # 20% chance
            flight_num = random.choice(list(self.aircrafts.keys()))
            if self.aircrafts[flight_num]['status'] == AircraftStatus.LANDED.value:
                self.aircrafts[flight_num]['status'] = AircraftStatus.TAKEOFF.value
                self._add_log(f"Departure cleared: {flight_num}")

    def _generate_status_change(self):
        if self.aircrafts:
            flight_num = random.choice(list(self.aircrafts.keys()))
            current_status = self.aircrafts[flight_num]['status']
            
            if current_status == AircraftStatus.ENROUTE.value:
                if random.random() < 0.4:
                    self.aircrafts[flight_num]['status'] = AircraftStatus.HOLDING.value
                    self._add_log(f"{flight_num} holding due to traffic")
            elif current_status == AircraftStatus.HOLDING.value:
                if random.random() < 0.3:
                    runway = random.choice([r for r in self.runways if r['status'] == RunwayStatus.AVAILABLE.value])
                    if runway:
                        self.aircrafts[flight_num]['status'] = AircraftStatus.LANDING.value
                        self.aircrafts[flight_num]['runway_assigned'] = runway['id']
                        runway['status'] = RunwayStatus.OCCUPIED.value
                        self._add_log(f"{flight_num} cleared to land on runway {runway['id']}")


    def get_system_status(self):
        return {
            "aircrafts": list(self.aircrafts.values()),
            "runways": self.runways,
            "landing_queue": self.landing_queue,
            "takeoff_queue": self.takeoff_queue,
            "logs": self.logs[-20:]
        }

    def process_landing_request(self, flight_number, size):
        if flight_number not in self.aircrafts:
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
        else:
            aircraft = self.aircrafts[flight_number]
        
        assigned_runway = self._assign_runway(flight_number)
        
        if assigned_runway:
            self._assign_landing(flight_number, assigned_runway)
            instruction = f"cleared_for_landing runway_{assigned_runway}"
        else:
            self.landing_queue.append(flight_number)
            position = len(self.landing_queue)
            instruction = f"hold position_{position}"
            aircraft["status"] = AircraftStatus.HOLDING.value
            aircraft["delay"] = position * 5  # 5 seconds per position
        
        return instruction, assigned_runway

    def _assign_runway(self, flight_number):
        """Assign runway to aircraft"""
        aircraft = self.aircrafts[flight_number]  # Get the aircraft dictionary
        for runway in sorted(self.runways, key=lambda x: x["length"], reverse=True):
            if self._can_accommodate(aircraft["size"], runway):
                runway["status"] = RunwayStatus.OCCUPIED.value
                runway["current_operation"] = aircraft["requested_operation"]
                runway["current_aircraft"] = flight_number
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


async def dummy_data_generator():
    """Periodically generate dummy activity"""
    while True:
        atc_core.generate_dummy_activity()
        await asyncio.sleep(random.uniform(2, 5))  # Random interval between 2-5 seconds        

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