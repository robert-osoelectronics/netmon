# netmon
Network monitor utility

## Metrics Collected
- **Ping**: Latency to Google DNS (8.8.8.8) in milliseconds
- **Download Speed**: Network download bandwidth in bits per second
- **Upload Speed**: Network upload bandwidth in bits per second

## Features
- Multithreaded design ensures speed tests don't block ping measurements
- Configurable measurement intervals
- Debug logging option for troubleshooting
- Clean shutdown handling with Ctrl+C
- Automatic InfluxDB configuration management

## Data Visualization
The metrics can be visualized using:
- InfluxDB's built-in Data Explorer
- Grafana with InfluxDB data source
- Any other tool that supports InfluxDB

## License
GNU General Public License v3.0
