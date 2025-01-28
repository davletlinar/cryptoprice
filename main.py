import asyncio
import websockets
import json
from influxdb_client_3 import InfluxDBClient3, Point
import os
import datetime
from typing import NoReturn
import logging
import time
import requests
from requests.exceptions import RequestException
from websockets.exceptions import WebSocketException, ConnectionClosed

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Configure assets
ASSETS = ['bitcoin', 'solana', 'ethereum', 'dogecoin']

# WebSocket URL
websocket_url = f"wss://ws.coincap.io/prices?assets={','.join(ASSETS)}"

# InfluxDB configuration
INFLUXDB2_HOST = os.getenv("INFLUXDB2_HOST")
INFLUXDB2_PORT = os.getenv("INFLUXDB2_PORT")
INFLUXDB2_ADMIN_TOKEN = os.getenv("INFLUXDB2_ADMIN_TOKEN")
INFLUXDB2_ORG = os.getenv("INFLUXDB2_ORG")
INFLUXDB2_BUCKET = os.getenv("INFLUXDB2_BUCKET")
INFLUXDB2_URL = f"http://{INFLUXDB2_HOST}:{INFLUXDB2_PORT}"

def wait_for_influxdb(url, max_retries=30, delay=2):
    """Wait for InfluxDB to be ready"""
    retry_count = 0
    while retry_count < max_retries:
        try:
            response = requests.get(f"{url}/health")
            if response.status_code == 200:
                logging.info("InfluxDB is ready!")
                return True
        except RequestException as e:
            logging.warning(f"InfluxDB not ready yet: {e}")
        
        retry_count += 1
        logging.info(f"Waiting for InfluxDB... attempt {retry_count}/{max_retries}")
        time.sleep(delay)
    
    raise Exception("InfluxDB failed to become ready in time")

# Initialize InfluxDB client
def init_influxdb_client() -> InfluxDBClient3:
    """Initialize InfluxDB client with retry logic"""
    wait_for_influxdb(f"{INFLUXDB2_URL}")
    
    logging.info("Connecting to InfluxDB...")
    return InfluxDBClient3(
        host=INFLUXDB2_URL,
        token=INFLUXDB2_ADMIN_TOKEN,
        org=INFLUXDB2_ORG,
        database=INFLUXDB2_BUCKET
    )
    
INFLUX_DB_CLIENT = init_influxdb_client()

def calculate_average(prices) -> float | None:
    if len(prices) == 0:
        return None
    return round(sum(prices) / len(prices), 2)

def insert_data(client, asset, avg_price, max_retries=3):
    """Insert data into InfluxDB with retry logic"""
    point = Point("prices")\
        .tag("asset", asset)\
        .field("price", avg_price)
    
    for attempt in range(max_retries):
        try:
            client.write(
                database=INFLUXDB2_BUCKET,
                record=point
            )
            break  # Exit loop if write is successful
        except Exception as e:
            logging.error(f"Error writing to InfluxDB (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                logging.error("Max retries reached. Data not written.")
            time.sleep(2 ** attempt)  # Exponential backoff

async def handle_websocket_connection(websocket, state):
    """Handle individual WebSocket connection"""
    try:
        message = await websocket.recv()
        data = json.loads(message)

        # Add price to list for averaging
        for key, value in data.items():
            if key in state["assets_prices"]:  # Only process known assets
                state["assets_prices"][key].append(float(value))

        if datetime.datetime.now() - state["time_flag"] > datetime.timedelta(minutes=1):
            state["time_flag"] = datetime.datetime.now()
            # Calculate average price for each asset
            for key, prices in state["assets_prices"].items():
                avg_price = calculate_average(prices)
                if avg_price is not None:
                    logging.info(f"{key}: {avg_price}")
                    insert_data(INFLUX_DB_CLIENT, key, avg_price)
            # Clear the lists after calculating averages
            state["assets_prices"] = {key: [] for key in ASSETS}

        return state
    
    except json.JSONDecodeError as e:
        logging.error(f"JSON decode error: {e}")
        return state

async def connect_websocket() -> NoReturn:
    """Main WebSocket connection loop with reconnection logic"""
    retry_delay = 1
    max_retry_delay = 30
    max_retries = 10
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            logging.info("Connecting to WebSocket...")
            async with websockets.connect(
                uri=websocket_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=10
            ) as websocket:
                logging.info("Successfully connected to WebSocket")
                retry_delay = 1  # Reset retry delay on successful connection
                
                # Initialize state
                state = {
                    "assets_prices": {key: [] for key in ASSETS},
                    "time_flag": datetime.datetime.now()
                }
                
                while True:
                    try:
                        state = await handle_websocket_connection(websocket, state)
                    except ConnectionClosed as e:
                        logging.error(f"WebSocket connection closed: {e}")
                        break
                    except Exception as e:
                        logging.error(f"Error in WebSocket loop: {e}")
                        continue

        except WebSocketException as e:
            logging.error(f"WebSocket connection error: {e}")
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
        
        retry_count += 1
        if retry_count >= max_retries:
            logging.error("Max retries reached. Exiting...")
            break
        logging.info(f"Reconnecting in {retry_delay} seconds...")
        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, max_retry_delay)

if __name__ == "__main__":
    try:
        asyncio.run(connect_websocket())
    except KeyboardInterrupt:
        logging.info("Shutting down gracefully...")
    except Exception as e:
        logging.error(f"Fatal error: {e}")
    finally:
        # Close InfluxDB client
        if INFLUX_DB_CLIENT:
            INFLUX_DB_CLIENT.close()
            logging.info("InfluxDB client closed.")