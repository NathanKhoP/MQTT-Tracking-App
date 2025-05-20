from flask import Flask, render_template, Response, g
import paho.mqtt.client as mqtt
import time
import json
import ssl
import threading
from queue import Queue
import os # Import os
from dotenv import load_dotenv # Import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Configuration ---
# Get credentials from environment variables
HIVEMQ_HOST = os.getenv("HIVEMQ_HOST")
HIVEMQ_PORT_MQTTS = int(os.getenv("HIVEMQ_PORT_MQTTS", "8883")) # Ensure port is an integer
HIVEMQ_USERNAME = os.getenv("HIVEMQ_USERNAME")
HIVEMQ_PASSWORD = os.getenv("HIVEMQ_PASSWORD")
CA_CERT_PATH = os.getenv("CA_CERT_PATH") # Will be None if not set in .env

# Check if essential variables are loaded
if not all([HIVEMQ_HOST, HIVEMQ_USERNAME, HIVEMQ_PASSWORD]):
    print("Error: Missing HiveMQ credentials in .env file or environment variables.")
    print("Please ensure HIVEMQ_HOST, HIVEMQ_USERNAME, and HIVEMQ_PASSWORD are set.")
    exit(1)

DASHBOARD_CLIENT_ID = "fleet_dashboard_client"

app = Flask(__name__)

vehicle_data_store = {}
data_lock = threading.Lock()

sse_queues = []
sse_queues_lock = threading.Lock()

def on_connect_dashboard(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"Dashboard: Connected to HiveMQ Broker ({HIVEMQ_HOST})!")
        client.subscribe("fleet/vehicle/+/location", qos=0)
        client.subscribe("fleet/vehicle/+/status", qos=1)
        client.subscribe("fleet/vehicle/+/maintenance", qos=2)
        client.subscribe("fleet/vehicle/+/alert", qos=1)  # Subscribe to alert messages
        print("Dashboard: Subscribed to fleet/vehicle/+/location, fleet/vehicle/+/status, fleet/vehicle/+/maintenance, fleet/vehicle/+/alert")
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

        with data_lock:
            if vehicle_id not in vehicle_data_store:
                vehicle_data_store[vehicle_id] = {
                    "location": None, 
                    "status": None, 
                    "maintenance_logs": [],
                    "alerts": []  # Add alerts array
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

    while True:
        try:
            client.connect(HIVEMQ_HOST, HIVEMQ_PORT_MQTTS, 60)
            client.loop_forever()
        except ConnectionRefusedError:
            print("Dashboard: MQTT connection refused. Retrying in 5 seconds...")
        except Exception as e:
            print(f"Dashboard: MQTT connection error: {e}. Retrying in 5 seconds...")
        time.sleep(5)


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_all_data')
def get_all_data():
    with data_lock:
        return json.dumps(dict(vehicle_data_store))


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


if __name__ == '__main__':
    mqtt_thread = threading.Thread(target=mqtt_thread_function, daemon=True)
    mqtt_thread.start()

    print("Starting Flask web server for dashboard...")
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)