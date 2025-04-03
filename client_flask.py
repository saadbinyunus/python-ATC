from flask import Flask, render_template, request, session, redirect, url_for, send_from_directory, jsonify
import grpc
import atc_pb2
import atc_pb2_grpc
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

channel = grpc.insecure_channel("localhost:50051")
stub = atc_pb2_grpc.ATCServiceStub(channel)

@app.route('/client', methods=['GET', 'POST'])
def client_interface():
    response_msg = None
    error_msg = None
    
    if request.method == 'POST':
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

@app.route('/dashboard')
def atc_dashboard():
    return send_from_directory('.', 'dashboard.html')

@app.route('/api/system-status')
def system_status():
    try:
        status = stub.GetSystemStatus(atc_pb2.StatusRequest())
        return jsonify({
            "aircrafts": [{
                "flight_number": ac.flight_number,
                "size": ac.size,
                "status": ac.status,
                "runway_assigned": ac.runway_assigned,
                "delay": ac.delay
            } for ac in status.aircrafts],
            "runways": [{
                "id": rw.id,
                "length": rw.length,
                "status": rw.status,
                "current_operation": rw.current_operation,
                "current_aircraft": rw.current_aircraft
            } for rw in status.runways],
            "landing_queue": list(status.landing_queue),
            "takeoff_queue": list(status.takeoff_queue),
            "logs": list(status.logs)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
# @app.route('/api/grpc-proxy', methods=['POST'])
# def grpc_proxy():
#     try:
#         data = request.json
        
#         if 'action' in data:
#             response = stub.ToggleGenerator(atc_pb2.GeneratorRequest(
#                 action=data['action']
#             ))
#         elif 'status' in data:
#             response = stub.UpdateStatus(atc_pb2.StatusUpdate(
#                 flight_number=data['flight_number'],
#                 status=data['status']
#             ))
#         elif 'aircraft_type' in data:
#             if 'takeoff' in request.path:
#                 response = stub.RequestTakeoff(atc_pb2.TakeoffRequest(
#                     flight_number=data['flight_number'],
#                     aircraft_type=data['aircraft_type'],
#                     size=data['size']
#                 ))
#             else:
#                 response = stub.RequestLanding(atc_pb2.LandingRequest(
#                     flight_number=data['flight_number'],
#                     aircraft_type=data['aircraft_type'],
#                     size=data['size']
#                 ))
#         else:
#             return jsonify({"error": "Unknown request type"}), 400
        
#         return jsonify({
#             "flight_number": response.flight_number,
#             "instruction": response.instruction,
#             "assigned_runway": response.assigned_runway,
#             "estimated_wait_time": response.estimated_wait_time
#         })
#     except Exception as e:
#         return jsonify({"error": str(e)}), 500

@app.route('/health')
def health_check():
    return 'OK', 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)