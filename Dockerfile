FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    nginx \
    && rm -rf /var/lib/apt/lists/*

#Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py /app/
COPY *.html /app/
COPY *.proto /app/

RUN mkdir -p /app/templates /var/log/nginx

COPY templates/ /app/templates/

#Gen gRPC files
RUN pip install grpcio-tools && \
    python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. atc.proto

#Configure Nginx
COPY nginx.conf /etc/nginx/sites-available/default

#Startup script
RUN echo '#!/bin/bash\n\
# Start Nginx in background\n\
nginx\n\
\n\
# Start the ATC server in background\n\
python atc_server.py & \n\
ATC_SERVER_PID=$!\n\
\n\
# Short pause to ensure ATC server is running\n\
sleep 3\n\
\n\
# Start the Flask application\n\
export FLASK_APP=client_flask.py\n\
export FLASK_ENV=production\n\
python -m flask run --host=0.0.0.0 --port=5000 & \n\
FLASK_PID=$!\n\
\n\
# Wait for processes to finish\n\
wait $ATC_SERVER_PID $FLASK_PID\n\
' > /app/start.sh && \
chmod +x /app/start.sh

#nginx on port 80
EXPOSE 80

ENV PYTHONUNBUFFERED=1

#Run startup script
CMD ["/app/start.sh"]