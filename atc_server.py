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
import platform
import signal

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
        self.landing_in_progress = {}
        self._init_dummy_data()

    async def _complete_landing(self, flight_number, runway_id):
        """Handle landing completion with immediate queue processing"""
        try:
            # Landing procedure (10 seconds total)
            await asyncio.sleep(5)  # Approach phase
            self._add_log(f"{flight_number} on final approach to runway {runway_id}")
            await asyncio.sleep(5)  # Touchdown and clearance
            
            # Update aircraft status
            if flight_number in self.aircrafts:
                self.aircrafts[flight_number]["status"] = AircraftStatus.LANDED.value
                self._add_log(f"{flight_number} has landed on runway {runway_id}")
            
            # Clear runway
            self.runways[runway_id-1]["status"] = RunwayStatus.AVAILABLE.value
            self.runways[runway_id-1]["current_operation"] = None
            self.runways[runway_id-1]["current_aircraft"] = None
            
            # Immediately process next in queue with priority
            self._process_queues(immediate_mode=True)
            
        except Exception as e:
            self._add_log(f"Landing error {flight_number}: {str(e)}")
            # Ensure runway clears even on error
            self.runways[runway_id-1]["status"] = RunwayStatus.AVAILABLE.value

    def _assign_landing(self, flight_number, runway_id):
        """Assign landing to available runway"""
        if flight_number in self.landing_in_progress:
            return
            
        aircraft = self.aircrafts[flight_number]
        aircraft["status"] = AircraftStatus.LANDING.value
        aircraft["runway_assigned"] = runway_id
        self.runways[runway_id-1]["status"] = RunwayStatus.OCCUPIED.value
        self.runways[runway_id-1]["current_operation"] = "landing"
        self.runways[runway_id-1]["current_aircraft"] = flight_number
        
        self.landing_in_progress[flight_number] = asyncio.create_task(
            self._complete_landing(flight_number, runway_id)
        )
        self._add_log(f"{flight_number} cleared to land on runway {runway_id}")

    def _assign_takeoff(self, flight_number, runway_id):
        """Assign takeoff to available runway"""
        aircraft = self.aircrafts[flight_number]
        aircraft["status"] = AircraftStatus.TAKEOFF.value
        aircraft["runway_assigned"] = runway_id
        self.runways[runway_id-1]["status"] = RunwayStatus.OCCUPIED.value
        self.runways[runway_id-1]["current_operation"] = "takeoff"
        self.runways[runway_id-1]["current_aircraft"] = flight_number
        self._add_log(f"{flight_number} cleared for takeoff on runway {runway_id}")
        asyncio.create_task(self._complete_takeoff(flight_number, runway_id))

    async def _complete_takeoff(self, flight_number, runway_id):
        """Complete takeoff after 5 seconds"""
        await asyncio.sleep(5)
        if flight_number in self.aircrafts:
            self.aircrafts[flight_number]["status"] = AircraftStatus.DEPARTED.value
        self.runways[runway_id-1]["status"] = RunwayStatus.AVAILABLE.value
        self.runways[runway_id-1]["current_operation"] = None
        self.runways[runway_id-1]["current_aircraft"] = None
        self._process_queues()

    def _process_queues(self, immediate_mode=False):
        """
        Process aircraft queues with HOLDING priority
        Args:
            immediate_mode: If True, processes just one HOLDING aircraft then exits
        """
        for runway in self.runways:
            if runway["status"] != RunwayStatus.AVAILABLE.value:
                continue
                
            # PHASE 1: Process HOLDING aircraft (highest priority)
            for i in range(len(self.landing_queue)):
                flight_number = self.landing_queue[i]
                aircraft = self.aircrafts.get(flight_number)
                
                if (aircraft and 
                    aircraft["status"] == AircraftStatus.HOLDING.value and
                    flight_number not in self.landing_in_progress):
                    
                    # Assign this aircraft
                    self.landing_queue.pop(i)
                    self._assign_landing(flight_number, runway["id"])
                    self._add_log(f"Immediate assignment: {flight_number} to runway {runway['id']}")
                    
                    if immediate_mode:
                        return  # In immediate mode, just process one
                    break  # Otherwise continue processing
                
            # PHASE 2: Process ENROUTE if no HOLDING aircraft found
            if runway["status"] == RunwayStatus.AVAILABLE.value:
                for i in range(len(self.landing_queue)):
                    flight_number = self.landing_queue[i]
                    aircraft = self.aircrafts.get(flight_number)
                    
                    if (aircraft and
                        aircraft["status"] == AircraftStatus.ENROUTE.value and
                        flight_number not in self.landing_in_progress):
                        
                        self.landing_queue.pop(i)
                        self._assign_landing(flight_number, runway["id"])
                        break
            
            # PHASE 3: Process takeoffs only if no landings waiting
            if (runway["status"] == RunwayStatus.AVAILABLE.value and 
                not any(a["status"] in (AircraftStatus.HOLDING.value, AircraftStatus.ENROUTE.value) 
                    for a in self.aircrafts.values()) and
                self.takeoff_queue):
                
                next_aircraft = self.takeoff_queue.pop(0)
                self._assign_takeoff(next_aircraft, runway["id"])

    def _init_dummy_data(self):
        """Initialize with 5 random aircraft"""
        airlines = ['AC', 'WS', 'DL', 'AA', 'UA', 'BA', 'AF']
        for i in range(1, 6):
            flight_num = f"{random.choice(airlines)}{random.randint(100, 999)}"
            size = random.randint(1, 3)
            self.add_aircraft(flight_num, size)
            self.process_landing_request(flight_num, size)

    def generate_dummy_activity(self):
        """Safely generate random aircraft movements"""
        try:
            actions = [
                self._generate_arrival,
                self._generate_departure,
                self._generate_status_change,
                lambda: None  # No action
            ]
            
            # Only choose from available actions
            available_actions = [a for a in actions if a != self._generate_status_change or 
                            any(r['status'] == RunwayStatus.AVAILABLE.value for r in self.runways)]
            
            if available_actions:
                random.choice(available_actions)()
        except Exception as e:
            self._add_log(f"Dummy activity error: {str(e)}")

    def _generate_arrival(self):
        if random.random() < 0.3:
            airlines = ['AC', 'WS', 'DL', 'AA']
            flight_num = f"{random.choice(airlines)}{random.randint(100, 999)}"
            size = random.randint(1, 3)
            self.add_aircraft(flight_num, size)
            self._add_log(f"New arrival: {flight_num} requesting landing")

    def _generate_departure(self):
        if self.aircrafts and random.random() < 0.2:
            landed_aircraft = [f for f, a in self.aircrafts.items() 
                             if a['status'] == AircraftStatus.LANDED.value]
            if landed_aircraft:
                flight_num = random.choice(landed_aircraft)
                self.process_takeoff_request(flight_num, self.aircrafts[flight_num]['size'])
                self._add_log(f"Departure cleared: {flight_num}")

    def _generate_status_change(self):
        """Safely change status of a random aircraft"""
        try:
            if not self.aircrafts:
                return
                
            flight_num = random.choice(list(self.aircrafts.keys()))
            current_status = self.aircrafts[flight_num]['status']
            
            # Only proceed if we have available runways
            available_runways = [r for r in self.runways 
                            if r['status'] == RunwayStatus.AVAILABLE.value]
            if not available_runways:
                return
                
            if current_status == AircraftStatus.ENROUTE.value:
                if random.random() < 0.4:
                    self.aircrafts[flight_num]['status'] = AircraftStatus.HOLDING.value
                    self._add_log(f"{flight_num} holding due to traffic")
                    
            elif current_status == AircraftStatus.HOLDING.value:
                if random.random() < 0.3:
                    runway = random.choice(available_runways)
                    self._assign_landing(flight_num, runway['id'])
                    
        except Exception as e:
            self._add_log(f"Status change error for {flight_num}: {str(e)}")

    def add_aircraft(self, flight_number, size):
        """Add a new aircraft to the system"""
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
            self._add_log(f"Aircraft {flight_number} added to system")
            return True
        return False

    def process_landing_request(self, flight_number, size):
        if flight_number not in self.aircrafts:
            self.add_aircraft(flight_number, size)
            aircraft = self.aircrafts[flight_number]
        else:
            aircraft = self.aircrafts[flight_number]
        
        if aircraft["status"] == AircraftStatus.ENROUTE.value:
            assigned_runway = self._assign_runway(flight_number)
            
            if assigned_runway:
                self._assign_landing(flight_number, assigned_runway)
                instruction = f"cleared_for_landing runway_{assigned_runway}"
            else:
                self.landing_queue.append(flight_number)
                position = len(self.landing_queue)
                instruction = f"hold position_{position}"
                aircraft["status"] = AircraftStatus.HOLDING.value
                aircraft["delay"] = position * 5
            
            return instruction, assigned_runway
        return None, 0

    def process_takeoff_request(self, flight_number, size):
        if flight_number not in self.aircrafts:
            self.add_aircraft(flight_number, size)
            aircraft = self.aircrafts[flight_number]
        else:
            aircraft = self.aircrafts[flight_number]
        
        assigned_runway = self._assign_runway(flight_number)
        
        if assigned_runway:
            self._assign_takeoff(flight_number, assigned_runway)
            instruction = f"cleared_for_takeoff runway_{assigned_runway}"
        else:
            self.takeoff_queue.append(flight_number)
            position = len(self.takeoff_queue)
            instruction = f"hold position_{position}"
            aircraft["status"] = AircraftStatus.DELAYED.value
            aircraft["delay"] = position * 5
        
        return instruction, assigned_runway

    def update_aircraft_status(self, flight_number, status):
        if flight_number in self.aircrafts:
            self.aircrafts[flight_number]["status"] = status
            self._add_log(f"{flight_number} status updated to {status}")
            return True
        return False

    def _assign_runway(self, flight_number):
        aircraft = self.aircrafts.get(flight_number)
        if not aircraft:
            return 0
            
        available_runways = [
            r for r in self.runways 
            if r["status"] == RunwayStatus.AVAILABLE.value 
            and self._can_accommodate(aircraft["size"], r)
        ]
        
        if not available_runways:
            return 0
            
        if aircraft["size"] == AircraftSize.LARGE.value:
            available_runways.sort(key=lambda x: x["length"], reverse=True)
        
        selected = available_runways[0]
        selected["status"] = RunwayStatus.OCCUPIED.value
        selected["current_operation"] = aircraft["requested_operation"]
        selected["current_aircraft"] = flight_number
        return selected["id"]

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

    def get_system_status_json(self):
        return {
            "aircrafts": [{
                "flight_number": ac["flight_number"],
                "size": ac["size"],
                "status": ac["status"],
                "requested_operation": ac.get("requested_operation", ""),
                "runway_assigned": ac["runway_assigned"],
                "delay": ac["delay"],
                "timestamp": ac["timestamp"]
            } for ac in self.aircrafts.values()],
            "runways": [{
                "id": rw["id"],
                "length": rw["length"],
                "status": rw["status"],
                "current_operation": rw.get("current_operation", ""),
                "current_aircraft": rw.get("current_aircraft", "")
            } for rw in self.runways],
            "landing_queue": self.landing_queue,
            "takeoff_queue": self.takeoff_queue,
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

    def RequestTakeoff(self, request, context):
        instruction, assigned_runway = self.atc.process_takeoff_request(
            request.flight_number,
            request.size
        )
        return atc_pb2.ATCResponse(
            flight_number=request.flight_number,
            instruction=instruction,
            assigned_runway=assigned_runway,
            estimated_wait_time=len(self.atc.takeoff_queue) * 2
        )

    def UpdateStatus(self, request, context):
        success = self.atc.update_aircraft_status(
            request.flight_number,
            request.status
        )
        return atc_pb2.ATCResponse(
            flight_number=request.flight_number,
            instruction="status_updated" if success else "status_update_failed",
            assigned_runway=0,
            estimated_wait_time=0
        )

    def GetSystemStatus(self, request, context):
        return self.atc.get_system_status()

async def handle_client(websocket):
    atc_core.connections.add(websocket)
    try:
        initial_status = {
            "type": "status_update",
            "data": atc_core.get_system_status_json()
        }
        await websocket.send(json.dumps(initial_status))
        
        while True:
            await asyncio.sleep(1)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        atc_core.connections.remove(websocket)

async def broadcast_updates():
    while True:
        status = {
            "type": "status_update",
            "data": atc_core.get_system_status_json()
        }
        connections = list(atc_core.connections)
        for ws in connections:
            try:
                await ws.send(json.dumps(status))
            except:
                if ws in atc_core.connections:
                    atc_core.connections.remove(ws)
        await asyncio.sleep(1)

async def dummy_data_generator():
    while True:
        atc_core.generate_dummy_activity()
        await asyncio.sleep(random.uniform(2, 5))

async def shutdown(server, ws_server):
    print("Shutting down servers...")
    if ws_server:
        ws_server.close()
        await ws_server.wait_closed()
    if server:
        await server.stop(5)
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]
    await asyncio.gather(*tasks, return_exceptions=True)

async def serve():
    global atc_core
    atc_core = ATCCore()
    
    server = None
    ws_server = None
    
    try:
        server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
        atc_pb2_grpc.add_ATCServiceServicer_to_server(ATCServicer(atc_core), server)
        server.add_insecure_port('[::]:50051')
        await server.start()
        
        ws_server = await websockets.serve(
            handle_client,
            "localhost",
            6789
        )
        
        asyncio.create_task(broadcast_updates())
        asyncio.create_task(dummy_data_generator())
        
        dashboard_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard.html')
        webbrowser.open(f'file://{dashboard_path}')
        
        print("Servers started. gRPC on port 50051, WebSocket on port 6789")
        print("Press Ctrl+C to stop the server")
        
        if platform.system() == 'Windows':
            while True:
                await asyncio.sleep(3600)
        else:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(
                    sig,
                    lambda: asyncio.create_task(shutdown(server, ws_server))
                )
            await server.wait_for_termination()
            
    except asyncio.CancelledError:
        print("Server shutdown requested")
    except Exception as e:
        print(f"Server error: {e}")
    finally:
        print("Cleaning up...")
        await shutdown(server, ws_server)

if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        print("Server stopped by user")