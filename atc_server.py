import asyncio
import json
import logging
import random
from collections import deque
from concurrent import futures
from datetime import datetime
import grpc
import websockets

# Import generated protobuf classes
from atc_pb2 import (
    LandingRequest,
    TakeoffRequest,
    StatusUpdate,
    ATCResponse,
    StatusRequest,
    SystemStatus,
    Aircraft as AircraftProto,
    Runway as RunwayProto,
    GeneratorRequest
)
from atc_pb2_grpc import (
    ATCServiceServicer,
    add_ATCServiceServicer_to_server
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ATC-Server")

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
        self.flight_generator = FlightGenerator(self)
        self.operation_timeouts = {}
        self.landing_duration =  35 # seconds
        self.takeoff_duration = 25  # seconds
        self.queue_check_interval = 1  # seconds
        
        # Start background tasks
        asyncio.create_task(self._check_operation_timeouts())
        asyncio.create_task(self._process_queues_continuously())

    async def _process_queues_continuously(self):
        """Continuously process both queues with priority to landings"""
        while True:
            await asyncio.sleep(self.queue_check_interval)
            try:
                await self._process_queues()
            except Exception as e:
                logger.error(f"Error processing queues: {str(e)}")

    async def _process_queues(self):
        """Process both queues in priority order"""
        # Process landing queue first (higher priority)
        processed = await self._process_single_queue(self.landing_queue, "LANDING")
        
        # Only process takeoff queue if no landing was processed
        if not processed:
            await self._process_single_queue(self.takeoff_queue, "TAKEOFF")

    async def _process_single_queue(self, queue, operation_type):
        """Process one aircraft from the specified queue"""
        if not queue:
            return False

        # Work with a copy to avoid modification during iteration
        for flight_number in list(queue):
            if flight_number not in self.aircrafts:
                queue.remove(flight_number)
                continue

            aircraft = self.aircrafts[flight_number]
            if aircraft["status"] != "HOLDING":
                continue

            # Try to assign runway
            if self._assign_runway_to_aircraft(flight_number, operation_type):
                queue.remove(flight_number)
                return True  # Successfully processed one aircraft
        
        return False  # No aircraft processed

    def _assign_runway_to_aircraft(self, flight_number, operation_type):
        """Attempt to assign a runway to the specified aircraft"""
        aircraft = self.aircrafts.get(flight_number)
        if not aircraft or aircraft["status"] != "HOLDING":
            return False

        runway_id = self._find_available_runway(operation_type, aircraft["size"])
        if not runway_id:
            return False

        # Assign the runway
        self.runways[runway_id].update({
            "status": "OCCUPIED",
            "current_operation": operation_type,
            "current_aircraft": flight_number
        })

        self.aircrafts[flight_number].update({
            "runway_assigned": runway_id,
            "status": operation_type,
            "delay": 0  # Reset delay counter
        })

        # Track operation start time
        self.operation_timeouts[flight_number] = (
            operation_type,
            datetime.now()
        )

        self._log(f"Assigned runway {runway_id} to {flight_number} for {operation_type}")
        return True

    async def _check_operation_timeouts(self):
        """Automatically complete operations that exceed their duration"""
        while True:
            await asyncio.sleep(1)
            current_time = datetime.now()
            
            for flight_number, (op_type, start_time) in list(self.operation_timeouts.items()):
                duration = (current_time - start_time).total_seconds()
                required_duration = self.landing_duration if op_type == "LANDING" else self.takeoff_duration
                
                if duration > required_duration:
                    self._complete_operation(flight_number, op_type)
                    del self.operation_timeouts[flight_number]

    def _complete_operation(self, flight_number, operation_type):
        """Mark an operation as completed and free up resources"""
        new_status = "LANDED" if operation_type == "LANDING" else "DEPARTED"
        
        # Free up the runway
        if flight_number in self.aircrafts:
            runway_id = self.aircrafts[flight_number].get("runway_assigned")
            if runway_id and runway_id in self.runways:
                self.runways[runway_id].update({
                    "status": "AVAILABLE",
                    "current_operation": None,
                    "current_aircraft": None
                })
        
        self.update_status(flight_number, new_status)
        self._log(f"Completed {operation_type.lower()} for {flight_number}")

    def _find_available_runway(self, operation_type, size):
        """Find a suitable runway based on operation type and aircraft size"""
        for runway_id, runway in self.runways.items():
            if runway["status"] == "AVAILABLE":
                if (size == 1 and runway["length"] >= 1500) or \
                   (size == 2 and runway["length"] >= 2000) or \
                   (size == 3 and runway["length"] >= 2500):
                    return runway_id
        return None

    def add_aircraft(self, flight_number, size):
        """Register a new aircraft in the system"""
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
        """Process a landing request"""
        if flight_number not in self.aircrafts:
            self.add_aircraft(flight_number, size)
            
        self.aircrafts[flight_number].update({
            "status": "HOLDING",
            "requested_operation": "LANDING"
        })
        
        if flight_number not in self.landing_queue:
            self.landing_queue.append(flight_number)
            self._log(f"Aircraft {flight_number} requested landing, added to queue")
        
        return self._process_request(flight_number, "LANDING")

    def request_takeoff(self, flight_number, aircraft_type, size):
        """Process a takeoff request"""
        if flight_number not in self.aircrafts:
            self.add_aircraft(flight_number, size)
            
        self.aircrafts[flight_number].update({
            "status": "HOLDING",
            "requested_operation": "TAKEOFF"
        })
        
        if flight_number not in self.takeoff_queue:
            self.takeoff_queue.append(flight_number)
            self._log(f"Aircraft {flight_number} requested takeoff, added to queue")
        
        return self._process_request(flight_number, "TAKEOFF")

    def _process_request(self, flight_number, operation_type):
        """Handle the request and return response"""
        if self._assign_runway_to_aircraft(flight_number, operation_type):
            return {
                "flight_number": flight_number,
                "instruction": f"cleared_for_{operation_type.lower()}",
                "assigned_runway": self.aircrafts[flight_number]["runway_assigned"],
                "estimated_wait_time": 0
            }
        else:
            queue = self.landing_queue if operation_type == "LANDING" else self.takeoff_queue
            position = queue.index(flight_number) if flight_number in queue else len(queue)
            wait_time = (position + 1) * 15  # 15 seconds per position in queue
            
            self.aircrafts[flight_number]["delay"] = wait_time
            return {
                "flight_number": flight_number,
                "instruction": "hold",
                "assigned_runway": None,
                "estimated_wait_time": wait_time
            }

    def update_status(self, flight_number, status):
        """Update aircraft status and handle runway release"""
        if flight_number not in self.aircrafts:
            return False
            
        old_status = self.aircrafts[flight_number]["status"]
        self.aircrafts[flight_number]["status"] = status
        self._log(f"Aircraft {flight_number} status changed from {old_status} to {status}")
        
        if status in ["LANDED", "DEPARTED"]:
            runway_id = self.aircrafts[flight_number].get("runway_assigned")
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
        """Return current system state for dashboard"""
        return {
            "aircrafts": list(self.aircrafts.values()),
            "runways": list(self.runways.values()),
            "landing_queue": list(self.landing_queue),
            "takeoff_queue": list(self.takeoff_queue),
            "logs": list(self.logs)
        }

    def _log(self, message):
        """Add message to log and notify all connected clients"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        self.logs.append(log_entry)
        logger.info(log_entry)
        asyncio.create_task(self._notify_clients())

    async def _notify_clients(self):
        """Push system state updates to all connected clients"""
        if not self.connected_clients:
            return

        message = json.dumps({
            "type": "status_update",
            "data": self.get_system_status()
        })

        tasks = []
        for client in self.connected_clients:
            try:
                tasks.append(client.send(message))
            except Exception as e:
                logger.warning(f"Error sending to client: {str(e)}")
                self.connected_clients.remove(client)
        
        await asyncio.gather(*tasks, return_exceptions=True)

class FlightGenerator:
    def __init__(self, database):
        self.db = database
        self.airlines = ['AC', 'WS', 'DL', 'AA', 'UA', 'BA', 'AF', 'LH', 'EK', 'QF']
        self.aircraft_types = {
            1: ['C172', 'PA28', 'SR22'],
            2: ['B737', 'A320', 'E190'],
            3: ['B777', 'A350', 'B787']
        }
        self.active = False
        self.task = None

    async def generate_flights(self, interval=5, max_flights=20):
        self.active = True
        while self.active and len(self.db.aircrafts) < max_flights:
            await asyncio.sleep(interval)
            
            flight_number = f"{random.choice(self.airlines)}{random.randint(100, 999)}"
            size = random.choices([1, 2, 3], weights=[0.2, 0.6, 0.2])[0]
            aircraft_type = random.choice(self.aircraft_types[size])
            operation = random.choice(['LANDING', 'TAKEOFF'])
            
            if operation == 'LANDING':
                self.db.request_landing(flight_number, aircraft_type, size)
            else:
                self.db.request_takeoff(flight_number, aircraft_type, size)

    def start(self, interval=5, max_flights=20):
        if not self.active:
            self.task = asyncio.create_task(self.generate_flights(interval, max_flights))
            logger.info("Flight generator started")

    def stop(self):
        self.active = False
        if self.task:
            self.task.cancel()
            logger.info("Flight generator stopped")

class ATCService(ATCServiceServicer):
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
            aircrafts=[AircraftProto(**ac) for ac in status["aircrafts"]],
            runways=[RunwayProto(**rw) for rw in status["runways"]],
            landing_queue=status["landing_queue"],
            takeoff_queue=status["takeoff_queue"],
            logs=status["logs"]
        )
    
    def ToggleGenerator(self, request, context):
        if request.action == 'start':
            self.db.flight_generator.start()
            return ATCResponse(
                flight_number="SYSTEM",
                instruction="generator_started",
                assigned_runway=0,
                estimated_wait_time=0
            )
        else:
            self.db.flight_generator.stop()
            return ATCResponse(
                flight_number="SYSTEM",
                instruction="generator_stopped",
                assigned_runway=0,
                estimated_wait_time=0
            )

async def websocket_handler(websocket):
    db = app_state['database']
    db.connected_clients.add(websocket)
    logger.info(f"New WebSocket connection from {websocket.remote_address}")
    
    try:
        # Send initial status
        await websocket.send(json.dumps({
            "type": "status_update",
            "data": db.get_system_status()
        }))
        
        # Keep connection alive
        async for _ in websocket:
            pass
            
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"WebSocket connection closed by {websocket.remote_address}")
    finally:
        db.connected_clients.remove(websocket)

async def start_servers():
    db = ATCDatabase()
    app_state['database'] = db
    

    
    # Start gRPC server
    grpc_server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    add_ATCServiceServicer_to_server(ATCService(db), grpc_server)
    grpc_server.add_insecure_port('[::]:50051')
    await grpc_server.start()
    logger.info("gRPC server started on port 50051")
    
    # Start WebSocket server
    ws_server = await websockets.serve(
        websocket_handler,
        "localhost",
        6789
    )
    logger.info("WebSocket server started on port 6789")
    
    # Start flight generator
    db.flight_generator.start(interval=7)
    
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("Shutting down servers...")
        await grpc_server.stop(0)
        ws_server.close()
        await ws_server.wait_closed()

app_state = {'database': None}

if __name__ == '__main__':
    try:
        asyncio.run(start_servers())
    except KeyboardInterrupt:
        logger.info("Server shutdown by user")
    except Exception as e:
        logger.error(f"Server crashed: {str(e)}")