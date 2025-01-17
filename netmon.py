"""
Network monitoring script that captures ping and internet speed metrics and logs them to InfluxDB.

This script was generated with assistance from Claude AI and has not been thoroughly reviewed
or tested. Use at your own risk.

Features:
- Periodic ping tests to monitor latency (default: every 10 seconds)
- Regular speed tests to measure bandwidth (default: every 60 seconds) 
- Metrics stored in InfluxDB time series database
- Multithreaded design to prevent speed tests from blocking ping measurements
- Configuration stored in netmon.ini file

Requirements:
- Python 3.6+
- InfluxDB Cloud or local InfluxDB instance
- Required packages: influxdb-client-3, speedtest-cli, ping3

Usage:
    python netmon.py [-d/--debug]
"""

import time
import psutil
import ping3
from influxdb_client_3 import InfluxDBClient3, Point
import logging
from datetime import datetime, timedelta
import configparser
import os
import argparse
import speedtest
import subprocess
import json
import threading
from queue import Queue, Empty

# Add argument parsing before logging config
parser = argparse.ArgumentParser(description='Network monitoring tool')
parser.add_argument('-d', '--debug', action='store_true', help='Enable debug logging')
args = parser.parse_args()

# Configure logging with debug level if requested
logging.basicConfig(
    level=logging.DEBUG if args.debug else logging.INFO,
    format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
)

# Monitoring configuration
PING_TARGET = "8.8.8.8"  # Google DNS server
PING_INTERVAL = 10  # seconds
SPEEDTEST_INTERVAL = 60  # seconds
CONFIG_PATH = "netmon.ini"

def _enter_user_config():
    """Prompt user for InfluxDB configuration"""
    config = configparser.ConfigParser()
    config["INFLUXDB"] = {}
    influx_cfg = config["INFLUXDB"]
    
    print("\nEnter InfluxDB Configuration:")
    print("URL (default: http://localhost:8086):")
    url = input() or "http://localhost:8086"
    influx_cfg["url"] = url
    
    print("Token:")
    token = input()
    influx_cfg["token"] = token
    
    print("Organization:")
    org = input()
    influx_cfg["org"] = org
    
    print("Bucket:")
    bucket = input()
    influx_cfg["bucket"] = bucket
    
    print("\nEntered config:")
    _print_config(config)
    print("Enter Y to save, any other key to re-enter configuration:")
    if input().lower() == "y":
        return config
    return _enter_user_config()

def _print_config(config):
    """Print configuration (excluding sensitive values)"""
    for section in config.sections():
        print(f"[{section}]")
        for key, value in config[section].items():
            # Mask token value
            if key == "token":
                print(f"{key} = ****")
            else:
                print(f"{key} = {value}")
        print()

class NetworkMonitor:
    def __init__(self):
        # Load configuration
        config = configparser.ConfigParser()
        if os.path.exists(CONFIG_PATH):
            config.read(CONFIG_PATH)
        else:
            logging.info("No config file found. Please enter InfluxDB details.")
            config = _enter_user_config()
            with open(CONFIG_PATH, "w", encoding="utf-8") as configfile:
                config.write(configfile)
        
        influx_config = config["INFLUXDB"]
        
        # Change to debug logging
        logging.debug("InfluxDB Configuration:")
        logging.debug(f"Host: {influx_config['url']}")
        logging.debug(f"Organization: {influx_config['org']}")
        logging.debug(f"Bucket: {influx_config['bucket']}")
        
        # Initialize InfluxDB client
        self.influx_client = InfluxDBClient3(
            host=influx_config["url"],
            token=influx_config["token"],
            org=influx_config["org"]
        )
        self.influx_bucket = influx_config["bucket"]
        
        # Initialize network monitoring
        self.previous_io = psutil.net_io_counters()
        self.previous_time = time.time()
        
        # Initialize speedtest
        self.speedtest = speedtest.Speedtest()
        self.last_speedtest = None
        self.last_ping = None
        
        # Add queues for metric collection
        self.metric_queue = Queue()
        
        # Initialize threads
        self.ping_thread = None
        self.speedtest_thread = None
        self.running = False

    def get_ping_stats(self):
        """Get ping statistics to target"""
        try:
            now = datetime.now()
            if (self.last_ping is None or 
                (now - self.last_ping).total_seconds() >= PING_INTERVAL):
                ping_time = ping3.ping(PING_TARGET) * 1000  # Convert to milliseconds
                self.last_ping = now
                return ping_time
            return None
        except Exception as e:
            logging.error(f"Error measuring ping: {e}")
            return None

    def write_to_influx(self, ping_time=None, download_speed=None, upload_speed=None):
        """Write metrics to InfluxDB"""
        try:
            # Create point only if we have data to write
            if ping_time is not None or download_speed is not None or upload_speed is not None:
                point = Point("network_metrics")
                
                if ping_time is not None:
                    point = point.field("ping_ms", ping_time)
                if download_speed is not None:
                    point = point.field("download_speed", download_speed)
                if upload_speed is not None:
                    point = point.field("upload_speed", upload_speed)

                logging.debug(f"Writing to InfluxDB - Database: {self.influx_bucket}")
                logging.debug(f"Point: {point}")

                self.influx_client.write(
                    database=self.influx_bucket,
                    record=point
                )
            
        except Exception as e:
            logging.error(f"Error writing to InfluxDB: {e}")

    def get_speed_test(self):
        """Run a speedtest and return download/upload speeds in bits/sec"""
        try:
            now = datetime.now()
            if (self.last_speedtest is None or 
                (now - self.last_speedtest).total_seconds() >= SPEEDTEST_INTERVAL):
                logging.info("Running speed test...")
                self.speedtest.get_best_server()
                download_speed = self.speedtest.download()
                upload_speed = self.speedtest.upload()
                self.last_speedtest = now
                return download_speed, upload_speed
            return None, None
        except Exception as e:
            logging.error(f"Error during speed test: {e}")
            return None, None

    def ping_worker(self):
        """Worker thread for ping measurements"""
        while self.running:
            try:
                ping_time = self.get_ping_stats()
                if ping_time is not None:
                    logging.info(f"Network metrics - Ping: {ping_time:.2f}ms")
                    self.metric_queue.put(('ping', ping_time))
                time.sleep(PING_INTERVAL)
            except Exception as e:
                logging.error(f"Error in ping worker: {e}")
                time.sleep(PING_INTERVAL)

    def speedtest_worker(self):
        """Worker thread for speed tests"""
        while self.running:
            try:
                download_speed, upload_speed = self.get_speed_test()
                if download_speed is not None and upload_speed is not None:
                    logging.info(
                        f"Network metrics - Download: {download_speed/1_000_000:.2f} Mbps, "
                        f"Upload: {upload_speed/1_000_000:.2f} Mbps"
                    )
                    self.metric_queue.put(('speed', (download_speed, upload_speed)))
                time.sleep(1)  # Short sleep to prevent CPU spinning
            except Exception as e:
                logging.error(f"Error in speedtest worker: {e}")
                time.sleep(SPEEDTEST_INTERVAL)

    def run(self):
        """Main monitoring loop"""
        try:
            logging.info("Starting network monitoring...")
            self.running = True
            
            # Start worker threads
            self.ping_thread = threading.Thread(target=self.ping_worker, daemon=True)
            self.speedtest_thread = threading.Thread(target=self.speedtest_worker, daemon=True)
            
            self.ping_thread.start()
            self.speedtest_thread.start()
            
            # Main loop processes the metric queue and writes to InfluxDB
            while self.running:
                try:
                    # Get metric from queue with timeout
                    metric_type, value = self.metric_queue.get(timeout=1)
                    
                    # Write to InfluxDB based on metric type
                    if metric_type == 'ping':
                        self.write_to_influx(ping_time=value)
                    elif metric_type == 'speed':
                        download_speed, upload_speed = value
                        self.write_to_influx(
                            download_speed=download_speed,
                            upload_speed=upload_speed
                        )
                    
                except Empty:
                    continue  # No metrics to process
                except Exception as e:
                    logging.error(f"Error processing metrics: {e}")
                    time.sleep(1)
                    
        except KeyboardInterrupt:
            logging.info("Received shutdown signal, cleaning up...")
        finally:
            # Ensure cleanup happens whether we get KeyboardInterrupt or another exception
            self.running = False
            logging.info("Waiting for threads to finish...")
            
            # Give threads a chance to finish cleanly
            try:
                self.ping_thread.join(timeout=5)
                self.speedtest_thread.join(timeout=5)
            except Exception as e:
                logging.error(f"Error during thread cleanup: {e}")
            
            logging.info("Network monitoring stopped.")

if __name__ == "__main__":
    monitor = NetworkMonitor()
    try:
        monitor.run()
    except KeyboardInterrupt:
        pass  # The cleanup is handled in run()
