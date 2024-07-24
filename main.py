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
    incremental_id = Column(Integer, unique=True)
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
        
        # Get the next incremental ID
        next_id = session.query(func.coalesce(func.max(Measurement.incremental_id), 0)).scalar() + 1
        
        # Write data to SQLite
        measurement = Measurement(
            incremental_id=next_id,
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
    return jsonify({"status": "stopped"})

@app.route('/reset_all', methods=['POST'])
def reset_all():
    global data, instrument, port
    session = Session()
    try:
        # Delete all measurements from the database
        session.query(Measurement).delete()
        session.commit()
        
        # Reset all values in the global data
        data['voltage'] = 0
        data['current'] = 0
        data['power'] = 0
        data['energy'] = 0
        
        if port.lower() != 'mock':
            try:
                # Reset energy on the device (assuming 0x42 is the command for energy reset)
                instrument._perform_command(0x42, b'')
                if instrument:
                    instrument.serial.close()
                    instrument = None
                # You might need additional commands here to reset other values on the device
            except Exception as e:
                return jsonify({"status": "error", "message": f"Error resetting device values: {str(e)}"}), 400
        
        return jsonify({"status": "success", "message": "All values reset successful"})
    except Exception as e:
        session.rollback()
        return jsonify({"status": "error", "message": f"Error resetting values: {str(e)}"}), 500
    finally:
        session.close()

@app.route('/data')
def get_data():
    return jsonify(data)

@app.route('/graph_data')
def get_graph_data():
    session = Session()
    
    try:
        # Get the maximum ID and the total count of measurements
        max_id = session.query(func.max(Measurement.incremental_id)).scalar()
        total_count = session.query(func.count(Measurement.id)).scalar()
        
        if not max_id or total_count == 0:
            return jsonify([])
        
        # Calculate the multiplication factor
        factor = max_id / 100  # We'll reserve 2 spots for first and last points
        
        if total_count <= 100:
            # If we have 100 or fewer points, return all of them
            measurements = session.query(Measurement).order_by(Measurement.incremental_id).all()
        else:
            # Generate the list of IDs to query (38 points)
            ids_to_query = [round(i * factor) for i in range(1, 98)]
            
            # Add first and last IDs if they're not already included
            if 1 not in ids_to_query:
                ids_to_query.append(1)
            if max_id not in ids_to_query:
                ids_to_query.append(max_id)
            
            # Query the database for these specific IDs
            measurements = session.query(Measurement).filter(
                Measurement.incremental_id.in_(ids_to_query)
            ).order_by(Measurement.incremental_id).all()
        
        graph_data = [{
            'id': m.incremental_id,
            'time': m.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'voltage': round(m.voltage, 2) if m.voltage is not None else None,
            'current': round(m.current, 2) if m.current is not None else None,
            'power': round(m.power, 2) if m.power is not None else None,
            'energy': round(m.energy, 2) if m.energy is not None else None
        } for m in measurements]
        
        return jsonify(graph_data)
    except Exception as e:
        print(f"Error in get_graph_data: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()

if __name__ == '__main__':
    # Open the web page in the default browser
    webbrowser.open('http://127.0.0.1:5000')
    
    # Run the Flask app
    app.run(debug=True, use_reloader=False)