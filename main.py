from flask import Flask, render_template, jsonify, request
import random
import threading
import time
import webbrowser

try:
    import minimalmodbus
    import serial
except ImportError:
    print("minimalmodbus or pyserial not found. Only Mock mode will be available.")

app = Flask(__name__)

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
        time.sleep(1)

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

if __name__ == '__main__':
    # Open the web page in the default browser
    webbrowser.open('http://127.0.0.1:5000')
    # Run the Flask app
    app.run(debug=True, use_reloader=False)