import grpc
import atc_pb2
import atc_pb2_grpc
from datetime import datetime
#Client CLI Program

channel = grpc.insecure_channel("localhost:50051")
stub = atc_pb2_grpc.ATCServiceStub(channel)

#Gather details to create aircraft
print("Create Aircraft-")
flightNum = input("Enter flight_number: ")
size = int(input("Enter aircraft size(1, 2, or 3): "))
status = input("Enter aircraft status (ENROUTE or DEPART): ").upper()

response = stub.CreateAircraft(atc_pb2.Aircraft(flight_number = flightNum, size=size, status=status, 
                                                requested_operation = "", runway_assigned=0, delay=0, timestamp=datetime.now().isoformat()))

option = -1

while(option != 0):
  print("Options:")
  print("Option 1 - Landing Request")
  print("Option 2 - Takeoff Request")
  print("Option 0 - Exit")

  option = int(input("Choose an option to communicate ATC: "))

  if option == 1:
    print("Initiating landing request")
    response = stub.RequestLanding(atc_pb2.LandingRequest(flight_number=flightNum, aircraft_type=str(size), size=size))
    print(f"Assigned runway: {response.assigned_runway}, Instruction: {response.instruction}, Estimated Wait Time: {response.estimated_wait_time}")
  elif option == 2:
    print("Initiating takeoff request")
    response = stub.RequestTakeoff(atc_pb2.TakeoffRequest(flight_number=flightNum, aircraft_type=str(size), size=size))
    print(f"Assigned runway: {response.assigned_runway}, Instruction: {response.instruction}, Estimated Wait Time: {response.estimated_wait_time}")
