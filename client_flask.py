from flask import Flask, render_template, request, session, redirect, url_for
import grpc
import atc_pb2
import atc_pb2_grpc
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

channel = grpc.insecure_channel("localhost:50051")
stub = atc_pb2_grpc.ATCServiceStub(channel)

@app.route('/', methods=['GET', 'POST'])
def index():
    response_msg = None
    error_msg = None
    
    if request.method == 'POST':
        # Handle aircraft creation
        if 'create_aircraft' in request.form:
            session.clear()
            try:
                flight_number = request.form['flight_number']
                size = int(request.form['size'])
                status = request.form['status'].upper()
                
                stub.CreateAircraft(atc_pb2.Aircraft(
                    flight_number=flight_number,
                    size=size,
                    status=status,
                    requested_operation="",
                    runway_assigned=0,
                    delay=0,
                    timestamp=datetime.now().isoformat()
                ))
                session['flight_number'] = flight_number
                session['size'] = size
                session['status'] = status
                response_msg = "Aircraft created successfully!"
            except Exception as e:
                error_msg = str(e)
        
        # Handle landing request
        elif 'request_landing' in request.form:
            try:
                response = stub.RequestLanding(atc_pb2.LandingRequest(
                    flight_number=session['flight_number'],
                    aircraft_type=str(session['size']),
                    size=session['size']
                ))
                response_msg = (f"Landing Response: Assigned runway {response.assigned_runway}, "
                               f"Instruction: {response.instruction}, "
                               f"Wait Time: {response.estimated_wait_time}s")
            except Exception as e:
                error_msg = str(e)
        
        # Handle takeoff request
        elif 'request_takeoff' in request.form:
            try:
                response = stub.RequestTakeoff(atc_pb2.TakeoffRequest(
                    flight_number=session['flight_number'],
                    aircraft_type=str(session['size']),
                    size=session['size']
                ))
                response_msg = (f"Takeoff Response: Assigned runway {response.assigned_runway}, "
                                f"Instruction: {response.instruction}, "
                                f"Wait Time: {response.estimated_wait_time}s")
            except Exception as e:
                error_msg = str(e)
    
    return render_template('aircraftClient.html',
                         response=response_msg,
                         error=error_msg,
                         aircraft=session.get('flight_number'))

if __name__ == '__main__':
    app.run(debug=True, port=5000)