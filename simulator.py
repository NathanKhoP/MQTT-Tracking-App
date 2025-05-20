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
        "timestamp": time.time()
    })
    client.will_set(lwt_topic, payload=lwt_payload, qos=1, retain=True)
    print(f"Vehicle {vehicle_id}: LWT set to topic {lwt_topic}")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_publish = on_publish

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