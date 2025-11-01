from flask import Blueprint, request, render_template, send_file, redirect
import xml.etree.ElementTree as ET
import zipfile
import subprocess
import logging
import json
import os
import re

# Get version from directory name
CURRENT_VERSION = os.path.basename(os.path.dirname(os.path.abspath(__file__)))

app = Blueprint(CURRENT_VERSION, __name__)

# Module-level logger
logger = logging.getLogger(__name__)
# If the application hasn't configured logging, provide a sensible default
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO)

def run_orcaslicer(input_file_path, process, filament):
    arrange = "1"
    orient = "1"
    while True:
        printer_config = 'files/printers/Bambu Lab P1S 0.4 nozzle.json'
        process_config = f'files/process/{process}'
        filament_config = f'files/filament/{filament}'
        # Extra logging for debugging
        logger.info(f"Resolved process_config: {process_config}")
        logger.info(f"Resolved filament_config: {filament_config}")

        # Defensive: ensure process is from process dir, filament from filament dir
        if not os.path.exists(process_config):
            logger.error(f"Process config does not exist: {process_config}")
            return False
        if not os.path.exists(filament_config):
            logger.error(f"Filament config does not exist: {filament_config}")
            return False
        
        load_settings_arg = f'{printer_config};{process_config}'
        #load_settings_arg = '/app/Bambu Lab P1S 0.4 nozzle.orca_printer'
        load_filaments_arg = filament_config
        cmd = [
            "orcaslicer",
            "--arrange", arrange,
            "--orient", orient,
            "--export-slicedata", "./temp",
            "--load-settings", load_settings_arg,
            "--load-filaments", load_filaments_arg,
            "--slice", "0",
            "--debug", "2",
            "--export-3mf", "./temp/output.3mf",
            "--info",
            input_file_path
        ]
        logger.info("Running command: %s", ' '.join(cmd))

        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True)
            logger.info("OrcaSlicer STDOUT:\n%s", result.stdout)
            logger.info("OrcaSlicer STDERR:\n%s", result.stderr)
            return True
        except subprocess.CalledProcessError as e:
            logger.error("OrcaSlicer failed with return code %s", e.returncode)
            logger.error("STDOUT:\n%s", e.stdout)
            logger.error("STDERR:\n%s", e.stderr)

        if arrange == "1" and orient == "1":
            logger.info("Attempting to slice again FIRST...")
            arrange = "0"
            orient = "1"
        elif arrange == "0" and orient == "1":
            logger.info("Attempting to slice again SECOND...")
            arrange = "1"
            orient = "0"
        elif arrange == "1" and orient == "0":
            logger.info("Attempting to slice again THIRD...")
            arrange = "0"
            orient = "0"
        else:
            return False
def get_data_from_orcaslicer_output() -> dict:

    # Check if output.3mf exists
    if not os.path.exists('temp/output.3mf'):
        return "'temp/output.3mf' does not exist", 500
    
    # Get filament data from output.3mf
    with zipfile.ZipFile('temp/output.3mf', 'r') as z:
        
        with z.open('Metadata/plate_1.gcode', 'r') as f:
            gcode = f.read().decode('utf-8')
            # Example lines to parse:
            # ; filament used [mm] = 5082.89
            # ; filament used [cm3] = 12.23
            # ; model printing time: 1h 38m 18s
            # ; total estimated time: 1h 44m 27s
            # ; estimated first layer printing time (normal mode) = 6m 9s
            # ; filament_type = PLA
            # ; default_print_profile = 0.20mm Standard @BBL X1C
            # extract material data
            used_mm = re.search(r"filament used \[mm\] = ([0-9.]+)", gcode).group(1)
            used_cm3 = re.search(r"filament used \[cm3\] = ([0-9.]+)", gcode).group(1)        
            max_z = re.search(r"max_z_height:\s+([0-9.]+)", gcode).group(1)
            type = re.search(r"filament_type = (\w+)", gcode).group(1)
            default_print_profile = re.search(r"default_print_profile = (.+)", gcode).group(1)

            # extract time data
            model_time = re.search(r"model printing time:\s+([0-9hms\s]+);", gcode).group(1)
            total_time = re.search(r"total estimated time:\s+([0-9hms\s]+)", gcode).group(1)
            first_layer_time = re.search(r"first layer printing time.*?=\s+([0-9hms\s]+)", gcode).group(1)

            # conwert time data into minutes
            model_time = time_to_minutes(model_time)
            total_time = time_to_minutes(total_time)
            first_layer_time = time_to_minutes(first_layer_time)
            
            return_data = {
                "used_mm": float(used_mm),
                "used_cm3": float(used_cm3),
                "max_z": float(max_z),
                "default_print_profile": default_print_profile,
                "type": type,
                "model_time": model_time,
                "total_time": total_time,
                "first_layer_time": first_layer_time
            }
            return return_data
def time_to_minutes(time_str):
    hours = re.search(r"([0-9]+)h", time_str)
    minutes = re.search(r"([0-9]+)m", time_str)
    seconds = re.search(r"([0-9]+)s", time_str)
    total_minutes = 0
    if hours:
        total_minutes += int(hours.group(1)) * 60
    if minutes:
        total_minutes += int(minutes.group(1))
    if seconds:
        total_minutes += int(seconds.group(1)) / 60
    return round(total_minutes, 2)

@app.route('/', methods=['GET', 'POST'])
def home():
    if request.method == 'POST':
        stlFile = request.files['stlFile']
        filament = request.form.get('filament')
        process = request.form.get('process')
        settings = request.form.get('settings')
        settings = json.loads(settings) if settings else {}
        logger.info(f"filament: '{filament}' process: '{process}'")

        # Validate file presence
        if not stlFile:
            logger.error("'stlFile' is required")
            return "'stlFile' is required", 400
        
        # Validate file extension
        if not stlFile.filename.lower().endswith('.stl'):
            logger.error("Uploaded file is not an STL file")
            return "Uploaded file must be an STL file", 400
        
        # set to default if not provided
        if not filament:
            filament = 'Bambu PETG Basic @BBL X1C.json'
        if not process:
            process = '0.08mm Extra Fine @BBL X1C.json'

        
        # Save the uploaded file to a temporary location
        input_file_path = 'temp/file.stl'
        stlFile.save(input_file_path)

        logger.info(f"Running OrcaSlicer with file: {input_file_path}, filament: {filament}, process: {process}")
        success = run_orcaslicer(input_file_path, process, filament)
        if not success:
            with open('result.json', 'rb') as f:
                output_data = f.read()
            return output_data, 500, {'Content-Type': 'application/json'}
        else:
            with open('result.json', 'rb') as f:
                output_data = f.read()
                error_string = json.loads(output_data.decode('utf-8')).get("error_string")
                if error_string == "Success.":
                    logger.info("Slicing completed successfully.")
                    return get_data_from_orcaslicer_output()

                return output_data, 500, {'Content-Type': 'application/json'}

    # get list of files in files/filament
    if not os.path.exists('files/filament'):
        return "'files/filament' directory does not exist", 500
    filament = os.listdir('files/filament')

    if not os.path.exists('files/process'):
        return "'files/process' directory does not exist", 500
    process = os.listdir('files/process')

    return render_template('index.html', filements=filament, processes=process, current_version=CURRENT_VERSION)

@app.route('/3mf', methods=['GET'])
def get_3mf():
    # Extract only the Metadata directory from the 3mf file and send as a zip
    import io
    metadata_zip_bytes = io.BytesIO()
    with zipfile.ZipFile('temp/output.3mf', 'r') as z_in:
        with zipfile.ZipFile(metadata_zip_bytes, 'w') as z_out:
            for file_info in z_in.infolist():
                if file_info.filename.startswith('Metadata/'):
                    z_out.writestr(file_info, z_in.read(file_info.filename))
    metadata_zip_bytes.seek(0)
    return send_file(metadata_zip_bytes, as_attachment=True, download_name='metadata.zip', mimetype='application/zip')


@app.route('/debug', methods=['GET'])
def debug():
    return get_data_from_orcaslicer_output()