from flask import Flask, render_template, jsonify, request
import random
import threading
import time
import webbrowser
from sqlalchemy import create_engine, Column, Integer, Float, DateTime, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import math


try:
    import minimalmodbus
    import serial
except ImportError:
    print("minimalmodbus or pyserial not found. Only Mock mode will be available.")

app = Flask(__name__)

# SQLite and SQLAlchemy setup
engine = create_engine('sqlite:///aac_capacity_tester.db', echo=True)
Base = declarative_base()

class Measurement(Base):
    __tablename__ = 'measurements'

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    voltage = Column(Float)
    current = Column(Float)
    power = Column(Float)
    energy = Column(Float)

Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# Global variables to store the latest data
data = {
    'voltage': 0,
    'current': 0,
    'power': 0,
    'energy': 0
}
running = False
port = 'Mock'
instrument = None

def read_data():
    global running, data, port, instrument
    session = Session()
    while running:
        if port.lower() == 'mock':
            data['voltage'] = random.uniform(20, 200)
            data['current'] = random.uniform(50, 100)
            data['power'] = data['voltage'] * data['current']
            data['energy'] = random.uniform(1000, 10000)
        else:
            try:
                data['voltage'] = instrument.read_register(0x0000, number_of_decimals=2, functioncode=4)
                data['current'] = instrument.read_register(0x0001, number_of_decimals=2, functioncode=4)
                power_low = instrument.read_register(0x0002, functioncode=4)
                power_high = instrument.read_register(0x0003, functioncode=4)
                data['power'] = ((power_high << 16) + power_low) * 0.1
                energy_low = instrument.read_register(0x0004, functioncode=4)
                energy_high = instrument.read_register(0x0005, functioncode=4)
                data['energy'] = (energy_high << 16) + energy_low
            except Exception as e:
                print(f"Error reading data: {e}")
                running = False
                break
        
        # Write data to SQLite
        measurement = Measurement(
            voltage=data['voltage'],
            current=data['current'],
            power=data['power'],
            energy=data['energy']
        )
        session.add(measurement)
        session.commit()
        
        time.sleep(1)
    
    session.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start', methods=['POST'])
def start():
    global running, port, instrument
    if not running:
        port = request.json['port']
        running = True
        if port.lower() != 'mock':
            try:
                instrument = minimalmodbus.Instrument(port, 0x01)
                instrument.serial.baudrate = 9600
                instrument.serial.bytesize = 8
                instrument.serial.parity = serial.PARITY_NONE
                instrument.serial.stopbits = 2
                instrument.serial.timeout = 1
            except Exception as e:
                return jsonify({"status": "error", "message": str(e)}), 400
        threading.Thread(target=read_data, daemon=True).start()
    return jsonify({"status": "started"})

@app.route('/stop', methods=['POST'])
def stop():
    global running, instrument
    running = False
    if instrument:
        instrument.serial.close()
        instrument = None
    return jsonify({"status": "stopped"})

@app.route('/reset_energy', methods=['POST'])
def reset_energy():
    global instrument, port
    if port.lower() == 'mock':
        # For mock mode, just reset the energy to 0
        data['energy'] = 0
        return jsonify({"status": "success", "message": "Energy reset in mock mode"})
    else:
        try:
            instrument._perform_command(0x42, '')
            return jsonify({"status": "success", "message": "Energy reset successful"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/data')
def get_data():
    return jsonify(data)

@app.route('/graph_data')
def get_graph_data():
    session = Session()
    
    # Get the start and end times of the discharge process
    start_time = session.query(func.min(Measurement.timestamp)).scalar()
    end_time = session.query(func.max(Measurement.timestamp)).scalar()
    
    if not start_time or not end_time:
        session.close()
        return jsonify([])
    
    total_duration = (end_time - start_time).total_seconds()
    
    # Calculate the interval to get approximately 40 data points
    interval_seconds = math.ceil(total_duration / 40)
    
    # Ensure the interval is at least 1 second
    interval_seconds = max(interval_seconds, 1)
    
    # Query the database with the calculated interval
    query = session.query(
        func.datetime(Measurement.timestamp, 'unixepoch', 'localtime').label('time'),
        func.avg(Measurement.voltage).label('voltage'),
        func.avg(Measurement.current).label('current'),
        func.avg(Measurement.power).label('power'),
        func.max(Measurement.energy).label('energy')
    ).group_by(
        (func.strftime('%s', Measurement.timestamp) - func.strftime('%s', start_time)) / interval_seconds
    ).order_by(Measurement.timestamp)
    
    results = query.all()
    
    graph_data = [{
        'time': result.time.strftime('%Y-%m-%d %H:%M:%S'),
        'voltage': round(result.voltage, 2),
        'current': round(result.current, 2),
        'power': round(result.power, 2),
        'energy': round(result.energy, 2)
    } for result in results]
    
    session.close()
    return jsonify(graph_data)

if __name__ == '__main__':
    # Open the web page in the default browser
    webbrowser.open('http://127.0.0.1:5000')
    
    # Run the Flask app
    app.run(debug=True, use_reloader=False)