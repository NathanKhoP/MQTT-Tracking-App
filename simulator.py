import paho.mqtt.client as mqtt
from paho.mqtt.properties import Properties # Import Properties
from paho.mqtt.packettypes import PacketTypes # For Property Gette
import time
import json
import random
import ssl
import threading
import os # Import os
from dotenv import load_dotenv # Import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Configuration ---
# Get credentials from environment variables with fallbacks (optional, but good practice)
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

NUM_VEHICLES = 3
PUBLISH_INTERVAL_SECONDS = 10

BASE_LAT = 34.0522
BASE_LON = -118.2437


def on_connect(client, userdata, flags, rc, properties=None):
    vehicle_id = userdata["vehicle_id"]
    if rc == 0:
        print(f"Vehicle {vehicle_id}: Connected to HiveMQ Broker ({HIVEMQ_HOST})!")
        # Subscribe to request and ping topics
        client.subscribe(f"fleet/vehicle/{vehicle_id}/request", qos=1)
        client.subscribe(f"fleet/vehicle/{vehicle_id}/ping", qos=1)
        print(f"Vehicle {vehicle_id}: Subscribed to request and ping topics")
        
        status_payload = {
            "vehicle_id": vehicle_id,
            "engine_on": True,
            "fuel_level": random.randint(70, 100),
            "timestamp": time.time(),
            "online": True
        }
        client.publish(f"fleet/vehicle/{vehicle_id}/status", json.dumps(status_payload), qos=1, retain=True)
        print(f"Vehicle {vehicle_id}: Published initial retained status.")
    else:
        print(f"Vehicle {vehicle_id}: Failed to connect, return code {rc}\n")

def on_disconnect(client, userdata, rc, properties=None):
    vehicle_id = userdata["vehicle_id"]
    print(f"Vehicle {vehicle_id}: Disconnected with result code {rc}")
    if rc != 0:
        print(f"Vehicle {vehicle_id}: Unexpected disconnection.")

def on_publish(client, userdata, mid, properties=None, reason_code=None):
    vehicle_id = userdata["vehicle_id"]
    # print(f"Vehicle {vehicle_id}: Message {mid} published.")

def on_message(client, userdata, msg):
    """Handle incoming messages like requests and pings"""
    vehicle_id = userdata["vehicle_id"]
    topic = msg.topic
    
    try:
        payload = json.loads(msg.payload.decode())
        topic_parts = topic.split('/')
        
        if len(topic_parts) < 4:
            print(f"Vehicle {vehicle_id}: Unexpected topic format {topic}")
            return
            
        message_type = topic_parts[3]
        
        # Handle ping messages (for latency testing)
        if message_type == "ping":
            print(f"Vehicle {vehicle_id}: Received ping, sending pong response")
            request_id = payload.get("request_id")
            
            # Prepare pong response with the original request_id
            pong_payload = {
                "request_id": request_id,
                "vehicle_id": vehicle_id,
                "timestamp": time.time(),
                "original_timestamp": payload.get("timestamp")
            }
            
            # Publish pong response
            client.publish(
                f"fleet/vehicle/{vehicle_id}/ping/pong", 
                json.dumps(pong_payload), 
                qos=1
            )
            return
            
        # Handle vehicle requests
        if message_type == "request":
            request_id = payload.get("request_id")
            request_type = payload.get("request_type")
            params = payload.get("params", {})
            
            print(f"Vehicle {vehicle_id}: Received {request_type} request")
            
            # Process different request types
            response_data = {
                "request_id": request_id,
                "vehicle_id": vehicle_id,
                "timestamp": time.time(),
                "request_type": request_type,
                "status": "success"
            }
            
            # Handle different request types
            if request_type == "diagnostics":
                # Simulate diagnostic data
                response_data["data"] = {
                    "engine_temp": random.uniform(80, 105),
                    "oil_pressure": random.uniform(35, 65),
                    "battery_voltage": random.uniform(12.1, 14.2),
                    "tire_pressure": {
                        "front_left": random.uniform(30, 36),
                        "front_right": random.uniform(30, 36),
                        "rear_left": random.uniform(30, 36),
                        "rear_right": random.uniform(30, 36)
                    }
                }
            elif request_type == "route_info":
                # Simulate route information
                response_data["data"] = {
                    "current_destination": "Distribution Center",
                    "eta_minutes": random.randint(10, 45),
                    "distance_remaining_km": random.uniform(5, 30),
                    "route_efficiency": f"{random.randint(80, 99)}%"
                }
            elif request_type == "driver_info":
                # Simulate driver information
                response_data["data"] = {
                    "driver_id": f"DRV{random.randint(1000, 9999)}",
                    "name": "John Driver",
                    "hours_driven_today": random.uniform(1, 8),
                    "break_status": random.choice(["On Break", "Active", "Required Soon"])
                }
            else:
                # Unknown request type
                response_data["status"] = "error"
                response_data["message"] = f"Unknown request type: {request_type}"
            
            # Send response
            client.publish(
                f"fleet/vehicle/{vehicle_id}/response",
                json.dumps(response_data),
                qos=1
            )
    except json.JSONDecodeError:
        print(f"Vehicle {vehicle_id}: Received non-JSON message: {msg.payload}")
    except Exception as e:
        print(f"Vehicle {vehicle_id}: Error processing message: {e}")

def simulate_vehicle(vehicle_id_num):
    vehicle_id = f"vehicle_{str(vehicle_id_num).zfill(3)}"
    client_id = f"sim_vehicle_{vehicle_id}"

    userdata = {"vehicle_id": vehicle_id}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, userdata=userdata)

    client.username_pw_set(HIVEMQ_USERNAME, HIVEMQ_PASSWORD)

    client.tls_set(
        ca_certs=CA_CERT_PATH, # Uses value from .env or None
        cert_reqs=ssl.CERT_REQUIRED,
        tls_version=ssl.PROTOCOL_TLS_CLIENT
    )

    lwt_topic = f"fleet/vehicle/{vehicle_id}/status"
    lwt_payload = json.dumps({
        "vehicle_id": vehicle_id,
        "online": False,
        "reason": "connection_lost_LWT",
        "timestamp": time.time()    })
    client.will_set(lwt_topic, payload=lwt_payload, qos=1, retain=True)
    print(f"Vehicle {vehicle_id}: LWT set to topic {lwt_topic}")
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_publish = on_publish
    client.on_message = on_message

    try:
        client.connect(HIVEMQ_HOST, HIVEMQ_PORT_MQTTS, 60)
    except Exception as e:
        print(f"Vehicle {vehicle_id}: Connection failed: {e}")
        return

    client.loop_start()

    current_lat = BASE_LAT + (random.random() - 0.5) * 0.1
    current_lon = BASE_LON + (random.random() - 0.5) * 0.1
    current_fuel = 100

    try:
        while True:
            current_lat += (random.random() - 0.5) * 0.005
            current_lon += (random.random() - 0.5) * 0.005
            current_speed = random.randint(20, 80)
            current_fuel = max(0, current_fuel - random.uniform(0.1, 0.5))

            location_payload = {
                "vehicle_id": vehicle_id,
                "latitude": round(current_lat, 6),
                "longitude": round(current_lon, 6),
                "speed_kmh": current_speed,
                "timestamp": time.time()
            }
            client.publish(f"fleet/vehicle/{vehicle_id}/location", json.dumps(location_payload), qos=0, retain=True)

            status_payload = {
                "vehicle_id": vehicle_id,
                "engine_on": True if current_fuel > 0 else False,
                "fuel_level": round(current_fuel, 1),
                "timestamp": time.time(),
                "online": True
            }
            client.publish(f"fleet/vehicle/{vehicle_id}/status", json.dumps(status_payload), qos=1, retain=True)

            if random.random() < 0.05: # Occasionally send an alert
                alert_payload = {
                    "vehicle_id": vehicle_id,
                    "alert_type": "MINOR_OBSTRUCTION_DETECTED",
                    "message": "Minor obstruction detected, proceed with caution.",
                    "timestamp": time.time()
                }
                # Create Properties object
                props = Properties(PacketTypes.PUBLISH)
                props.MessageExpiryInterval = 60 # Expires in 60 seconds

                client.publish(f"fleet/vehicle/{vehicle_id}/alert",
                            json.dumps(alert_payload),
                            qos=1,
                            properties=props) # Pass properties here
                print(f"Vehicle {vehicle_id}: Published alert with 60s expiry.")

            if random.random() < 0.1:
                maintenance_payload = {
                    "vehicle_id": vehicle_id,
                    "message": "Scheduled maintenance check data",
                    "check_id": random.randint(1000,9999),
                    "timestamp": time.time()
                }
                client.publish(f"fleet/vehicle/{vehicle_id}/maintenance", json.dumps(maintenance_payload), qos=2)
                print(f"Vehicle {vehicle_id}: Published maintenance data (QoS 2).")

            time.sleep(PUBLISH_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print(f"Vehicle {vehicle_id}: Simulation stopped.")
    except Exception as e:
        print(f"Vehicle {vehicle_id}: An error occurred: {e}")
    finally:
        final_status = {
            "vehicle_id": vehicle_id,
            "online": False,
            "reason": "graceful_shutdown",
            "timestamp": time.time()
        }
        client.publish(f"fleet/vehicle/{vehicle_id}/status", json.dumps(final_status), qos=1, retain=True)
        print(f"Vehicle {vehicle_id}: Published final offline status.")
        time.sleep(0.5)
        client.loop_stop()
        client.disconnect()
        print(f"Vehicle {vehicle_id}: Disconnected.")


if __name__ == "__main__":
    threads = []
    for i in range(1, NUM_VEHICLES + 1):
        thread = threading.Thread(target=simulate_vehicle, args=(i,))
        threads.append(thread)
        thread.start()
        time.sleep(0.5)

    for thread in threads:
        thread.join()

    print("All vehicle simulations finished.")