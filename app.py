from flask import Flask, render_template, Response, g, request, jsonify, session, redirect, url_for
import paho.mqtt.client as mqtt
import time
import json
import ssl
import threading
from queue import Queue
import os # Import os
from dotenv import load_dotenv # Import load_dotenv
import uuid # For generating request IDs

# Load environment variables from .env file
load_dotenv()

# --- Configuration ---
# Get credentials from environment variables
HIVEMQ_HOST = os.getenv("HIVEMQ_HOST")
HIVEMQ_PORT_MQTTS = int(os.getenv("HIVEMQ_PORT_MQTTS", "8883")) # Ensure port is an integer
HIVEMQ_USERNAME = os.getenv("HIVEMQ_USERNAME")
HIVEMQ_PASSWORD = os.getenv("HIVEMQ_PASSWORD")
CA_CERT_PATH = os.getenv("CA_CERT_PATH") # Will be None if not set in .env

# Local user credentials - in a real app, these would be stored securely
USERS = {
    "admin": "fleet123",
    "operator": "track456"
}

# Secret key for session management
SECRET_KEY = os.getenv("SECRET_KEY", "fleet_tracking_default_key")

# Check if essential variables are loaded
if not all([HIVEMQ_HOST, HIVEMQ_USERNAME, HIVEMQ_PASSWORD]):
    print("Error: Missing HiveMQ credentials in .env file or environment variables.")
    print("Please ensure HIVEMQ_HOST, HIVEMQ_USERNAME, and HIVEMQ_PASSWORD are set.")
    exit(1)

DASHBOARD_CLIENT_ID = "fleet_dashboard_client"

app = Flask(__name__)
app.secret_key = SECRET_KEY  # Set secret key for session management

vehicle_data_store = {}
data_lock = threading.Lock()

sse_queues = []
sse_queues_lock = threading.Lock()

# Store for pending requests
pending_requests = {}
pending_requests_lock = threading.Lock()

def on_connect_dashboard(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"Dashboard: Connected to HiveMQ Broker ({HIVEMQ_HOST})!")
        client.subscribe("fleet/vehicle/+/location", qos=0)
        client.subscribe("fleet/vehicle/+/status", qos=1)
        client.subscribe("fleet/vehicle/+/maintenance", qos=2)
        client.subscribe("fleet/vehicle/+/alert", qos=1)  # Subscribe to alert messages
        client.subscribe("fleet/vehicle/+/response", qos=1)  # Subscribe to response messages
        client.subscribe("fleet/vehicle/+/ping/pong", qos=1)  # Subscribe to ping-pong responses
        print("Dashboard: Subscribed to fleet topics including response and ping/pong")
    else:
        print(f"Dashboard: Failed to connect, return code {rc}\n")

def on_message_dashboard(client, userdata, msg):
    try:
        payload_data = json.loads(msg.payload.decode())
        topic_parts = msg.topic.split('/')
        
        if len(topic_parts) < 4:
            print(f"Dashboard: Received message on unexpected topic format: {msg.topic}")
            return

        vehicle_id = topic_parts[2]
        message_type = topic_parts[3]

        # Handle ping-pong response
        if message_type == "ping" and len(topic_parts) > 4 and topic_parts[4] == "pong":
            # Process ping-pong response
            received_time = time.time()
            request_id = payload_data.get("request_id")
            
            with pending_requests_lock:
                if request_id in pending_requests:
                    sent_time = pending_requests[request_id]["timestamp"]
                    latency = (received_time - sent_time) * 1000  # Convert to milliseconds
                    payload_data["latency_ms"] = round(latency, 2)
                    payload_data["received_timestamp"] = received_time
                    
                    # Update with the complete response including latency
                    pending_requests[request_id]["response"] = payload_data
                    pending_requests[request_id]["completed"] = True
            
            # Also send this through SSE for real-time UI updates
            update_message = {"vehicle_id": vehicle_id, "type": "ping_pong", "data": payload_data, "topic": msg.topic}
            with sse_queues_lock:
                for q in sse_queues:
                    q.put(json.dumps(update_message))
            
            return

        # Handle regular response to a request
        if message_type == "response":
            request_id = payload_data.get("request_id")
            
            with pending_requests_lock:
                if request_id in pending_requests:
                    pending_requests[request_id]["response"] = payload_data
                    pending_requests[request_id]["completed"] = True
            
            # Add to vehicle data store as a response
            with data_lock:
                if vehicle_id not in vehicle_data_store:
                    vehicle_data_store[vehicle_id] = {
                        "location": None, 
                        "status": None, 
                        "maintenance_logs": [],
                        "alerts": [],
                        "responses": []
                    }
                
                if "responses" not in vehicle_data_store[vehicle_id]:
                    vehicle_data_store[vehicle_id]["responses"] = []
                
                vehicle_data_store[vehicle_id]["responses"].append(payload_data)
                # Keep only the last 5 responses
                vehicle_data_store[vehicle_id]["responses"] = vehicle_data_store[vehicle_id]["responses"][-5:]
            
            # Normal SSE update for UI
            update_message = {"vehicle_id": vehicle_id, "type": message_type, "data": payload_data, "topic": msg.topic}
            with sse_queues_lock:
                for q in sse_queues:
                    q.put(json.dumps(update_message))
            
            return

        # Handle regular data messages
        with data_lock:
            if vehicle_id not in vehicle_data_store:
                vehicle_data_store[vehicle_id] = {
                    "location": None, 
                    "status": None, 
                    "maintenance_logs": [],
                    "alerts": [],  # Add alerts array
                    "responses": [] # Add responses array
                }
            
            if message_type == "location":
                vehicle_data_store[vehicle_id]["location"] = payload_data
            elif message_type == "status":
                vehicle_data_store[vehicle_id]["status"] = payload_data
            elif message_type == "maintenance":
                vehicle_data_store[vehicle_id]["maintenance_logs"].append(payload_data)
                vehicle_data_store[vehicle_id]["maintenance_logs"] = vehicle_data_store[vehicle_id]["maintenance_logs"][-5:]
            elif message_type == "alert":
                vehicle_data_store[vehicle_id]["alerts"].append(payload_data)
                # Keep only the last 5 alerts
                vehicle_data_store[vehicle_id]["alerts"] = vehicle_data_store[vehicle_id]["alerts"][-5:]

            vehicle_data_store[vehicle_id]["last_seen"] = time.time()

        update_message = {"vehicle_id": vehicle_id, "type": message_type, "data": payload_data, "topic": msg.topic}
        with sse_queues_lock:
            for q in sse_queues:
                q.put(json.dumps(update_message))

    except json.JSONDecodeError:
        print(f"Dashboard: Received non-JSON message on {msg.topic}: {msg.payload.decode()}")
    except Exception as e:
        print(f"Dashboard: Error processing message on {msg.topic}: {e}")


def mqtt_thread_function():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=DASHBOARD_CLIENT_ID)
    client.username_pw_set(HIVEMQ_USERNAME, HIVEMQ_PASSWORD)
    client.tls_set(
        ca_certs=CA_CERT_PATH, # Uses value from .env or None
        cert_reqs=ssl.CERT_REQUIRED,
        tls_version=ssl.PROTOCOL_TLS_CLIENT
    )

    client.on_connect = on_connect_dashboard
    client.on_message = on_message_dashboard
    
    # Store the MQTT client in the Flask application context
    app.mqtt_client = client

    while True:
        try:
            client.connect(HIVEMQ_HOST, HIVEMQ_PORT_MQTTS, 60)
            client.loop_forever()
        except ConnectionRefusedError:
            print("Dashboard: MQTT connection refused. Retrying in 5 seconds...")
        except Exception as e:
            print(f"Dashboard: MQTT connection error: {e}. Retrying in 5 seconds...")
        time.sleep(5)


@app.before_request
def before_request():
    """Make the MQTT client available to all routes and handle authentication"""
    g.mqtt_client = getattr(app, 'mqtt_client', None)
    
    # Check if authentication is required
    if request.endpoint and request.endpoint not in ['login', 'static', 'stream'] and 'logged_in' not in session:
        # For API endpoints, return unauthorized status
        if request.endpoint.startswith('api/'):
            return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
        # For HTML pages, redirect to login
        return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login"""
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username in USERS and USERS[username] == password:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('index'))
        else:
            error = 'Invalid username or password'
    
    return render_template('login.html', error=error)


@app.route('/logout', methods=['GET', 'POST'])
def logout():
    """Handle user logout"""
    session.pop('logged_in', None)
    session.pop('username', None)
    return redirect(url_for('login'))


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_all_data')
def get_all_data():
    with data_lock:
        return json.dumps(dict(vehicle_data_store))

@app.route('/api/request_data', methods=['POST'])
def request_data():
    request_id = str(uuid.uuid4())
    requested_vehicles = request.json.get("vehicles", [])
    response_format = request.json.get("format", "full")

    with pending_requests_lock:
        pending_requests[request_id] = {
            "vehicles": requested_vehicles,
            "format": response_format,
            "timestamp": time.time()
        }

    return jsonify({"request_id": request_id}), 202


@app.route('/api/pending_requests')
def get_pending_requests():
    with pending_requests_lock:
        requests_copy = pending_requests.copy()
    return jsonify(requests_copy), 200


@app.route('/stream')
def stream():
    def event_stream():
        q = Queue()
        with sse_queues_lock:
            sse_queues.append(q)
        try:
            while True:
                message = q.get()
                yield f"data: {message}\n\n"
        except GeneratorExit:
            print("SSE client disconnected")
        finally:
            with sse_queues_lock:
                if q in sse_queues:
                    sse_queues.remove(q)
    return Response(event_stream(), mimetype="text/event-stream")


@app.route('/api/send_request', methods=['POST'])
def send_request():
    """Send a request to a specific vehicle and wait for response"""
    try:
        request_data = request.json
        vehicle_id = request_data.get('vehicle_id')
        request_type = request_data.get('request_type')
        request_params = request_data.get('params', {})
        timeout_seconds = float(request_data.get('timeout', 10))
        
        if not all([vehicle_id, request_type]):
            return jsonify({'status': 'error', 'message': 'Missing required parameters'}), 400
        
        # Create a new request record
        request_id = str(uuid.uuid4())
        request_obj = {
            'vehicle_id': vehicle_id,
            'request_type': request_type,
            'params': request_params,
            'timestamp': time.time(),
            'completed': False,
            'response': None
        }
        
        # Prepare message payload
        message = {
            'request_id': request_id,
            'request_type': request_type,
            'params': request_params,
            'timestamp': time.time()
        }
        
        # Store the pending request
        with pending_requests_lock:
            pending_requests[request_id] = request_obj
        
        # Get the mqtt client from the global state
        client = g.get('mqtt_client')
        if not client:
            return jsonify({'status': 'error', 'message': 'MQTT client not available'}), 500
        
        # Publish the request
        topic = f"fleet/vehicle/{vehicle_id}/request"
        client.publish(topic, json.dumps(message), qos=1)
        
        # Wait for the response (with timeout)
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            time.sleep(0.1)  # Small sleep to avoid busy waiting
            with pending_requests_lock:
                if request_id in pending_requests and pending_requests[request_id]['completed']:
                    response_data = pending_requests[request_id]['response']
                    # Clean up the request from our store
                    del pending_requests[request_id]
                    return jsonify({
                        'status': 'success',
                        'request_id': request_id,
                        'response': response_data
                    })
        
        # If we get here, the request timed out
        with pending_requests_lock:
            if request_id in pending_requests:
                del pending_requests[request_id]
        
        return jsonify({'status': 'timeout', 'message': 'Request timed out'}), 408
    
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/ping', methods=['POST'])
def ping_vehicle():
    """Send a ping to measure latency to a vehicle"""
    try:
        ping_data = request.json
        vehicle_id = ping_data.get('vehicle_id')
        timeout_seconds = float(ping_data.get('timeout', 5))
        
        if not vehicle_id:
            return jsonify({'status': 'error', 'message': 'Vehicle ID required'}), 400
        
        # Create a ping request
        request_id = str(uuid.uuid4())
        ping_request = {
            'vehicle_id': vehicle_id,
            'timestamp': time.time(),
            'completed': False,
            'response': None
        }
        
        # Prepare ping message
        message = {
            'request_id': request_id,
            'timestamp': time.time()
        }
        
        # Store the pending request
        with pending_requests_lock:
            pending_requests[request_id] = ping_request
        
        # Get the mqtt client from the global state
        client = g.get('mqtt_client')
        if not client:
            return jsonify({'status': 'error', 'message': 'MQTT client not available'}), 500
        
        # Publish the ping
        topic = f"fleet/vehicle/{vehicle_id}/ping"
        client.publish(topic, json.dumps(message), qos=1)
        
        # Wait for the pong response (with timeout)
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            time.sleep(0.05)  # Small sleep to avoid busy waiting
            with pending_requests_lock:
                if request_id in pending_requests and pending_requests[request_id]['completed']:
                    response_data = pending_requests[request_id]['response']
                    # Clean up the request from our store
                    del pending_requests[request_id]
                    return jsonify({
                        'status': 'success',
                        'latency_ms': response_data.get('latency_ms'),
                        'request_id': request_id
                    })
        
        # If we get here, the ping timed out
        with pending_requests_lock:
            if request_id in pending_requests:
                del pending_requests[request_id]
        
        return jsonify({'status': 'timeout', 'message': 'Ping timed out'}), 408
    
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    

if __name__ == '__main__':
    mqtt_thread = threading.Thread(target=mqtt_thread_function, daemon=True)
    mqtt_thread.start()

    print("Starting Flask web server for dashboard...")
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)