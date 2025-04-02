Move to correct directory

Install grpc, websocket, flask dependencies
Run: python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. atc.proto

First run server:
python .\atc_server.py

Second run flask (for client):
python .\client_flask.py

ATC dashboard can be accessed on localhost:5000/dashboard
Client can be accessed on localhost:5000/client

Azure Deployment Link: coe892atcproject.azurewebsites.net
